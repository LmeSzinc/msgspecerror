"""Tests for the `*_with_error_detail` family: first error, resolved location."""
import copy
from typing import Dict, List, Optional, Tuple, Union

import msgspec
import pytest
from msgspec import NODEFAULT, UNSET, Struct, ValidationError, convert

import msgspecerror.with_error as with_error_mod
from msgspecerror import (
    ErrorInfoDetail, convert_with_error, convert_with_error_detail, load_json_with_error_detail,
    load_msgpack_with_error_detail, parse_msgspec_error
)
from msgspecerror.const import ErrorType
from msgspecerror.parse_error import _has_placeholder
from msgspecerror.repair import load_msgpack_with_default

# --- Test Models ---


class Req(Struct, forbid_unknown_fields=True):
    a: int
    b: str = ''


class Box(Struct):
    inner: Req
    tag: str = 't'


class Item(Struct):
    amount: int = 0


class Inventory(Struct):
    items: Dict[str, Item] = {}


# --- Realistic task definition, nested dict / union / list ---


class Group(Struct, forbid_unknown_fields=True):
    task: str = ''
    group: str = ''
    model: str = ''


class Card(Struct, forbid_unknown_fields=True):
    info: str = ''
    group: str = ''
    task: str = ''


class Task(Struct, forbid_unknown_fields=True):
    groups: Optional[Dict[str, Union[None, str, Group]]] = msgspec.field(default_factory=dict)
    displays: Optional[Union[str, List[List[Union[str, Card]]]]] = 'flat'
    global_bind: bool = False


TasksDefinition = Dict[str, Task]


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


class TestCallerBugRaises:
    """A decoder of the other format is a bug, not bad data, so it is not converted."""

    def test_json_rejects_msgpack_decoder(self):
        with pytest.raises(
                TypeError,
                match=r'load_json_with_error_detail\(\) requires a msgspec\.json\.Decoder, '
                      r'got a msgspec\.msgpack\.Decoder'):
            load_json_with_error_detail(b'{"a": 1}', msgspec.msgpack.Decoder(Req))

    def test_msgpack_rejects_json_decoder(self):
        with pytest.raises(
                TypeError,
                match=r'load_msgpack_with_error_detail\(\) requires a msgspec\.msgpack\.Decoder, '
                      r'got a msgspec\.json\.Decoder'):
            load_msgpack_with_error_detail(msgspec.msgpack.encode({'a': 1}), msgspec.json.Decoder(Req))


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
        (List[int], [1, 'x']),
        (Tuple[int, str], [1, 2]),
        (Dict[str, Union[str, List[int]]], {'v': [1, 'x']}),
        (Union[Dict[str, int], List[int]], {'k': 'x'}),
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


class TestLocMatchesWithError:
    """When the location is concrete, both families report the same `loc`."""

    @pytest.mark.parametrize('model, data', [
        (Req, {'a': 'bad'}),
        (Req, {}),
        (Req, {'a': 1, 'z': 2}),
        (Box, {'inner': {}}),
        (Box, {'inner': {'a': 'bad'}}),
        (List[int], [1, 'x']),
        (Tuple[int, str], [1, 2]),
        (int, 'abc'),
    ])
    def test_concrete_loc_is_left_alone(self, model, data):
        _, error = convert_with_error(data, model)
        detail = assert_failed(convert_with_error_detail(data, model))

        assert not _has_placeholder(error.loc)
        assert detail.loc == error.loc

    def test_placeholder_loc_is_the_only_difference(self):
        """A `$[...]` path is the one case where the two families disagree."""
        _, error = convert_with_error({'k': 'x'}, Dict[str, int])
        detail = assert_failed(convert_with_error_detail({'k': 'x'}, Dict[str, int]))

        assert error.loc == ('...',)
        assert detail.loc == ('k',)


class TestRootError:
    """A root level validation error carries the whole document."""

    @pytest.mark.parametrize('payload, model, value', [
        (b'"x"', int, 'x'),
        (b'1', str, 1),
        (b'[1]', int, [1]),
        (b'null', int, None),
        (b'true', int, True),
    ], ids=['str-for-int', 'int-for-str', 'list-for-int', 'null-for-int', 'bool-for-int'])
    def test_json_root_error(self, payload, model, value):
        detail = assert_failed(load_json_with_error_detail(payload, model))

        assert detail.loc == ()
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True
        assert detail.error_value == value

    def test_msgpack_root_error(self):
        detail = assert_failed(
            load_msgpack_with_error_detail(msgspec.msgpack.encode('x'), int))
        assert detail.loc == ()
        assert detail.error_value == 'x'


class TestDecodeCount:
    """One typed decode, plus one untyped decode only on the failure path."""

    def test_json_success_decodes_once(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_json

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_json', counting)
        load_json_with_error_detail(b'{"a": 1}', Req)
        assert len(calls) == 1

    def test_json_failure_decodes_twice(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_json

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_json', counting)
        load_json_with_error_detail(b'{"a": "bad"}', Req)

        assert len(calls) == 2
        # The second decode is untyped: it has no `type` argument
        assert calls[1][1] == {}

    def test_msgpack_success_decodes_once(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_msgpack

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_msgpack', counting)
        load_msgpack_with_error_detail(msgspec.msgpack.encode({'a': 1}), Req)
        assert len(calls) == 1

    def test_msgpack_failure_decodes_twice(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_msgpack

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_msgpack', counting)
        load_msgpack_with_error_detail(msgspec.msgpack.encode({'a': 'bad'}), Req)

        assert len(calls) == 2
        assert calls[1][1] == {}


class TestRealisticModel:
    """A real task definition: a dict of structs holding dicts, unions and lists."""

    @pytest.mark.parametrize('data, loc, value', [
        # A dict value of `groups`, the whole path has two placeholders
        ({'Task': {'groups': {'G': 1}}}, ('Task', 'groups', 'G'), 1),
        # A dict key of `groups`, the offending value is the key itself
        ({'Task': {'groups': {1: None}}}, ('Task', 'groups', 1), 1),
        # A list of lists of a tagged union member
        ({'Task': {'displays': [[{'info': 1}]]}},
         ('Task', 'displays', 0, 0, 'info'), 1),
        # The failing entry is the second task of the mapping
        ({'Good': {}, 'Bad': {'global_bind': 1}}, ('Bad', 'global_bind'), 1),
    ])
    def test_convert(self, data, loc, value):
        detail = assert_failed(convert_with_error_detail(data, TasksDefinition))

        assert detail.loc == loc
        assert detail.error_value == value
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('data, loc, value', [
        ({'Task': {'groups': {'G': 1}}}, ('Task', 'groups', 'G'), 1),
        ({'Task': {'displays': [[{'info': 1}]]}},
         ('Task', 'displays', 0, 0, 'info'), 1),
        ({'Good': {}, 'Bad': {'global_bind': 1}}, ('Bad', 'global_bind'), 1),
    ])
    def test_json_payload(self, data, loc, value):
        """Json keys are always str, the int key row can only go through msgpack."""
        payload = msgspec.json.encode(data)
        detail = assert_failed(load_json_with_error_detail(payload, TasksDefinition))

        assert detail.loc == loc
        assert detail.error_value == value

    @pytest.mark.parametrize('data, loc, value', [
        ({'Task': {'groups': {'G': 1}}}, ('Task', 'groups', 'G'), 1),
        ({'Task': {'groups': {1: None}}}, ('Task', 'groups', 1), 1),
        ({'Task': {'displays': [[{'info': 1}]]}},
         ('Task', 'displays', 0, 0, 'info'), 1),
        ({'Good': {}, 'Bad': {'global_bind': 1}}, ('Bad', 'global_bind'), 1),
    ])
    def test_msgpack_payload(self, data, loc, value):
        payload = msgspec.msgpack.encode(data)
        detail = assert_failed(load_msgpack_with_error_detail(payload, TasksDefinition))

        assert detail.loc == loc
        assert detail.error_value == value

    def test_with_error_keeps_the_placeholders(self):
        data = {'Task': {'groups': {'G': 1}}}
        _, error = convert_with_error(data, TasksDefinition)
        assert error.loc == ('...', 'groups', '...')

    def test_data_is_not_modified(self):
        data = {'Task': {'groups': {'G': 1}}}
        snapshot = copy.deepcopy(data)
        assert_failed(convert_with_error_detail(data, TasksDefinition))
        assert data == snapshot
