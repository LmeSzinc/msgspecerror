"""Tests for the `*_with_error_detail` family: first error, resolved location."""
import copy
from typing import Dict, List

import msgspec
import pytest
from msgspec import NODEFAULT, UNSET, Struct, ValidationError, convert

from msgspecerror import (
    ErrorInfoDetail,
    convert_with_error_detail,
    load_json_with_error_detail,
    load_msgpack_with_error_detail,
    parse_msgspec_error,
)
from msgspecerror.const import ErrorType
from msgspecerror.parse_error import _has_placeholder
from msgspecerror.repair import load_msgpack_with_default


# --- Test Models ---

class Req(Struct, forbid_unknown_fields=True):
    a: int
    b: str = ''


class Item(Struct):
    amount: int = 0


class Inventory(Struct):
    items: Dict[str, Item] = {}


def assert_invariants(detail):
    """The three invariants of every delivered ErrorInfoDetail."""
    assert isinstance(detail, ErrorInfoDetail)
    assert detail.loc_resolved == (not _has_placeholder(detail.loc))
    assert detail.error_value_resolved == (detail.error_value is not UNSET)
    assert (not detail.error_value_resolved) or detail.loc_resolved


def assert_failed(result):
    obj, detail = result
    assert obj is NODEFAULT
    assert_invariants(detail)
    return detail


class TestSuccess:
    def test_convert(self):
        obj, detail = convert_with_error_detail({'a': 1}, Req)
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_load_json(self):
        obj, detail = load_json_with_error_detail(b'{"a": 1}', Req)
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_load_msgpack(self):
        obj, detail = load_msgpack_with_error_detail(
            msgspec.msgpack.encode({'a': 1}), Req)
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_json_decoder_is_accepted(self):
        obj, detail = load_json_with_error_detail(
            b'{"a": 1}', msgspec.json.Decoder(Req))
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_msgpack_decoder_is_accepted(self):
        obj, detail = load_msgpack_with_error_detail(
            msgspec.msgpack.encode({'a': 1}), msgspec.msgpack.Decoder(Req))
        assert obj == Req(a=1)
        assert detail is NODEFAULT


class TestConvert:
    @pytest.mark.parametrize('model, data, loc, value', [
        (Req, {'a': 'bad'}, ('a',), 'bad'),
        (Req, {'a': 1, 'z': 2}, ('z',), 2),
        (Req, {'a': [], 'b': 'x'}, ('a',), []),
        (Dict[str, int], {'k': 'x'}, ('k',), 'x'),
        (Dict[str, int], {1: 'x'}, (1,), 1),
        (Dict[str, List[int]], {'k': [1, 'x']}, ('k', 1), 'x'),
        (Dict[str, Dict[str, int]], {'a': {'b': 'x'}}, ('a', 'b'), 'x'),
        (Inventory, {'items': {'apple': {'amount': 'ABC'}}},
         ('items', 'apple', 'amount'), 'ABC'),
    ])
    def test_resolved(self, model, data, loc, value):
        detail = assert_failed(convert_with_error_detail(data, model))
        assert detail.loc == loc
        assert detail.error_value == value
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_missing_field(self):
        detail = assert_failed(convert_with_error_detail({}, Req))
        assert detail.loc == ('a',)
        assert detail.error_value is UNSET
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

    def test_root_error(self):
        detail = assert_failed(convert_with_error_detail('abc', int))
        assert detail.loc == ()
        assert detail.error_value == 'abc'

    def test_decoder_equals_model(self):
        from_model = assert_failed(convert_with_error_detail({'k': 'x'}, Dict[str, int]))
        from_decoder = assert_failed(convert_with_error_detail(
            {'k': 'x'}, msgspec.json.Decoder(Dict[str, int])))
        assert from_decoder.loc == from_model.loc
        assert from_decoder.error_value == from_model.error_value

    def test_does_not_modify_data(self):
        data = {'items': {'apple': {'amount': 'ABC'}}}
        snapshot = copy.deepcopy(data)
        assert_failed(convert_with_error_detail(data, Inventory))
        assert data == snapshot


class TestJsonPayload:
    @pytest.mark.parametrize('payload, loc, value, loc_resolved', [
        (b'{"a": "bad"}', ('a',), 'bad', True),
        (b'{"a": "bad", "b": "\xff"}', ('a',), UNSET, True),
        (b'{"a": "bad", "b": 1', ('a',), UNSET, True),
        (b'{"a": ', (), UNSET, True),
        ('{"a": "bad"}', ('a',), 'bad', True),
    ])
    def test_req(self, payload, loc, value, loc_resolved):
        detail = assert_failed(load_json_with_error_detail(payload, Req))
        assert detail.loc == loc
        assert detail.error_value is value or detail.error_value == value
        assert detail.loc_resolved is loc_resolved

    def test_str_lone_surrogate(self):
        """
        msgspec encodes a str payload to utf-8 before parsing it, so the lone
        surrogate fails on the whole payload: the error is a codec error, not
        a validation error at a field, and the location is empty.
        """
        payload = '{"a": "bad", "b": "' + '\ud800' + '"}'
        detail = assert_failed(load_json_with_error_detail(payload, Req))
        assert detail.loc == ()
        assert detail.loc_resolved is True
        assert detail.type is ErrorType.UNICODE_DECODE_ERROR
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    @pytest.mark.parametrize('payload, loc, loc_resolved', [
        (b'{"k": "x"}', ('k',), True),
        (b'{"k": "x", "b": "\xff"}', ('...',), False),
        (b'{"k": "x", "b": 1', ('...',), False),
        (b'{"k": "x"} {"j": 1}', ('...',), False),
    ])
    def test_dict_model(self, payload, loc, loc_resolved):
        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, int]))
        assert detail.loc == loc
        assert detail.loc_resolved is loc_resolved
        assert detail.error_value_resolved is (loc_resolved and loc == ('k',))


class TestMsgpackPayload:
    @pytest.mark.parametrize('payload, loc, value, loc_resolved', [
        (msgspec.msgpack.encode({'a': 'bad'}), ('a',), 'bad', True),
        (b'\x82\xa1a\xa3bad\xa1b\xa1\xff', ('a',), UNSET, True),
        (b'\x82\xa1a\xa3bad', ('a',), UNSET, True),
        (b'\xc1', (), UNSET, True),
    ])
    def test_req(self, payload, loc, value, loc_resolved):
        detail = assert_failed(load_msgpack_with_error_detail(payload, Req))
        assert detail.loc == loc
        assert detail.error_value is value or detail.error_value == value
        assert detail.loc_resolved is loc_resolved

    @pytest.mark.parametrize('payload, loc, loc_resolved', [
        (msgspec.msgpack.encode({'k': 'x'}), ('k',), True),
        (b'\x82\xa1k\xa1x\xa1b\xa1\xff', ('...',), False),
        (b'\x82\xa1k\xa1x\xa1b', ('...',), False),
    ])
    def test_dict_model(self, payload, loc, loc_resolved):
        detail = assert_failed(load_msgpack_with_error_detail(payload, Dict[str, int]))
        assert detail.loc == loc
        assert detail.loc_resolved is loc_resolved


class TestSecondDecodeFailure:
    """The typed decode stops early, the raw one may still fail."""

    def test_json_dict_placeholder_kept(self):
        detail = assert_failed(
            load_json_with_error_detail(b'{"k": "x", "b": "\xff"}', Dict[str, int]))
        assert detail.loc == ('...',)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_json_concrete_loc_kept(self):
        """`$.a` is concrete already, so only the value is missing."""
        detail = assert_failed(
            load_json_with_error_detail(b'{"a": "bad", "b": "\xff"}', Req))
        assert detail.loc == ('a',)
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

    def test_msg_type_is_preserved(self):
        detail = assert_failed(
            load_json_with_error_detail(b'{"k": "x", "b": "\xff"}', Dict[str, int]))
        assert detail.msg.startswith('Expected `int`, got `str`')


class TestConsistencyWithRepair:
    """The resolved location must match the one the repair engine finds."""

    @pytest.mark.parametrize('model, data', [
        (Req, {'a': 'bad'}),
        (Req, {'a': 1, 'z': 2}),
        (Req, {}),
        (Dict[str, int], {'k': 'x'}),
        (Dict[str, int], {1: 'x'}),
        (Dict[str, List[int]], {'k': [1, 'x']}),
        (Dict[str, Dict[str, int]], {'a': {'b': 'x'}}),
        (Inventory, {'items': {'apple': {'amount': 'ABC'}}}),
        (Dict[str, int], {'a': 'x', 'b': 'y'}),
    ])
    def test_loc_matches(self, model, data):
        try:
            convert(data, type=model)
        except ValidationError as e:
            raw = parse_msgspec_error(e)
        else:
            raise AssertionError('data is expected to fail validation')

        detail = assert_failed(convert_with_error_detail(data, model))
        _, errors = load_msgpack_with_default(msgspec.msgpack.encode(data), model)
        # The repair engine reports one error per failing entry, the detail
        # family reports only the first one, so compare with the first error
        assert errors
        if detail.loc_resolved:
            assert detail.loc == errors[0].loc
        else:
            assert _has_placeholder(errors[0].loc)
            assert raw.loc == detail.loc

    def test_multiple_failing_entries_report_the_first(self):
        """
        The repair engine reports one error per failing entry, the detail
        family reports only the first one. They must agree on that first entry.
        """
        data = {'a': 'x', 'b': 'y'}
        detail = assert_failed(convert_with_error_detail(data, Dict[str, int]))
        _, errors = load_msgpack_with_default(
            msgspec.msgpack.encode(data), Dict[str, int])
        assert len(errors) == 2
        assert detail.loc == errors[0].loc == ('a',)
        assert detail.error_value == 'x'
