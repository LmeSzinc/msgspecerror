"""Tests for `resolve_error` and `ErrorInfoDetail`."""
from typing import Dict, List, Set, Tuple, Union

import msgspec
import pytest
from msgspec import UNSET, Struct, ValidationError, convert

from msgspecerror import ErrorInfo, ErrorInfoDetail, parse_msgspec_error, resolve_error
from msgspecerror.const import ErrorType
from msgspecerror.parse_error import _has_placeholder

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


def resolve(model, data):
    """Convert `data` into `model`, then resolve the resulting error."""
    try:
        convert(data, type=model)
    except ValidationError as e:
        error = parse_msgspec_error(e)
    else:
        raise AssertionError('data is expected to fail validation')
    detail = resolve_error(error, data, model)
    assert_invariants(detail)
    return detail


def assert_invariants(detail):
    """The three invariants of every delivered ErrorInfoDetail."""
    assert isinstance(detail, ErrorInfoDetail)
    assert detail.loc_resolved == (not _has_placeholder(detail.loc))
    assert detail.error_value_resolved == (detail.error_value is not UNSET)
    # A value can only be collected by walking the whole path
    assert (not detail.error_value_resolved) or detail.loc_resolved


class TestErrorInfoDetail:
    def test_is_error_info(self):
        detail = ErrorInfoDetail(msg='m', type=ErrorType.TYPE_MISMATCH)
        assert isinstance(detail, ErrorInfo)

    def test_defaults(self):
        detail = ErrorInfoDetail(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a',))
        assert detail.error_value is UNSET
        assert detail.loc_resolved is False
        assert detail.error_value_resolved is False

    def test_fields(self):
        names = [f.name for f in msgspec.structs.fields(ErrorInfoDetail)]
        assert names == ['msg', 'type', 'loc', 'ctx', 'error_value',
                         'loc_resolved', 'error_value_resolved']

    def test_flags_are_writable(self):
        """They are plain fields, the library doesn't attempt to protect them."""
        detail = ErrorInfoDetail(msg='m', type=ErrorType.TYPE_MISMATCH)
        detail.loc_resolved = True
        detail.error_value_resolved = True
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_json_omits_defaults(self):
        detail = ErrorInfoDetail(msg='m', type=ErrorType.MISSING_FIELD, loc=('a',))
        assert msgspec.json.encode(detail) == (
            b'{"msg":"m","type":"MISSING_FIELD","loc":["a"],"ctx":{}}'
        )

    def test_json_keeps_true_flags(self):
        detail = ErrorInfoDetail(
            msg='m', type=ErrorType.TYPE_MISMATCH, loc=('k',), error_value='bad',
            loc_resolved=True, error_value_resolved=True,
        )
        encoded = msgspec.json.encode(detail)
        assert b'"error_value":"bad"' in encoded
        assert b'"loc_resolved":true' in encoded
        assert b'"error_value_resolved":true' in encoded

    def test_json_round_trip(self):
        detail = ErrorInfoDetail(
            msg='m', type=ErrorType.TYPE_MISMATCH, loc=('k',), error_value='bad',
            loc_resolved=True, error_value_resolved=True,
        )
        decoded = msgspec.json.decode(msgspec.json.encode(detail), type=ErrorInfoDetail)
        assert decoded == detail

    def test_not_equal_to_error_info(self):
        detail = ErrorInfoDetail(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a',))
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a',))
        assert detail != error
        assert error != detail


class TestHasPlaceholder:
    @pytest.mark.parametrize('loc, expected', [
        ((), False),
        (('a',), False),
        ((1,), False),
        (('a', 0), False),
        (('...',), True),
        (('...key',), True),
        (('a', '...'), True),
        (('...', 'a'), True),
        (('...', 0, '...key'), True),
    ])
    def test_has_placeholder(self, loc, expected):
        assert _has_placeholder(loc) is expected

    def test_unhashable_segment(self):
        """A corrupted loc segment must not raise here."""
        assert _has_placeholder((['x'],)) is False


class TestResolveDictValue:
    def test_top_level_dict(self):
        detail = resolve(Dict[str, int], {'k': 'x'})
        assert detail.loc == ('k',)
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_nested_dict(self):
        detail = resolve(Dict[str, Dict[str, int]], {'a': {'b': 'x'}})
        assert detail.loc == ('a', 'b')
        assert detail.error_value == 'x'

    def test_placeholder_then_index(self):
        detail = resolve(Dict[str, List[int]], {'k': [1, 'x']})
        assert detail.loc == ('k', 1)
        assert detail.error_value == 'x'

    def test_placeholder_then_field(self):
        detail = resolve(Inventory, {'items': {'apple': {'amount': 'ABC'}}})
        assert detail.loc == ('items', 'apple', 'amount')
        assert detail.error_value == 'ABC'

    def test_first_failing_key_wins(self):
        detail = resolve(Dict[str, int], {'first': 'x', 'second': 'y'})
        assert detail.loc == ('first',)
        assert detail.error_value == 'x'

    def test_non_str_keys(self):
        detail = resolve(Dict[int, str], {1: 5})
        assert detail.loc == (1,)
        assert detail.error_value == 5

    def test_union_with_dict(self):
        detail = resolve(Union[Dict[str, int], List[int]], {'k': 'x'})
        assert detail.loc == ('k',)
        assert detail.error_value == 'x'

    def test_nested_union(self):
        detail = resolve(Dict[str, Union[str, List[int]]], {'v': [1, 'x']})
        assert detail.loc == ('v', 1)
        assert detail.error_value == 'x'

    def test_no_failing_key(self):
        """A loc that doesn't match the data can not be resolved."""
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',))
        detail = resolve_error(error, {'a': 1}, Dict[str, int])
        assert detail.error_value is UNSET
        assert detail.loc == ('...',)
        assert detail.loc_resolved is False


class TestResolveDictKey:
    def test_top_level_key(self):
        detail = resolve(Dict[str, int], {1: 'x'})
        assert detail.loc == (1,)
        assert detail.error_value == 1

    def test_nested_key(self):
        detail = resolve(Dict[str, Dict[str, int]], {'a': {1: 'x'}})
        assert detail.loc == ('a', 1)
        assert detail.error_value == 1

    def test_int_key_model(self):
        detail = resolve(Dict[int, str], {'x': 'v'})
        assert detail.loc == ('x',)
        assert detail.error_value == 'x'

    def test_key_placeholder_not_last(self):
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...key', 'x'))
        detail = resolve_error(error, {1: 'v'}, Dict[int, str])
        assert detail.error_value is UNSET
        assert detail.loc == ('...key', 'x')
        assert detail.loc_resolved is False


class TestResolveNoPlaceholder:
    def test_struct_field(self):
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a',))
        detail = resolve_error(error, {'a': 'bad'}, Req)
        assert detail.loc == ('a',)
        assert detail.error_value == 'bad'

    def test_error_info_is_upgraded_to_detail(self):
        """An ErrorInfo is never returned as is, a detail is built from it."""
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a',))
        detail = resolve_error(error, {'a': 'bad'}, Req)
        assert isinstance(detail, ErrorInfoDetail)
        assert detail.loc == ('a',)
        assert detail.error_value == 'bad'

    def test_resolved_detail_is_returned_as_is(self):
        detail = resolve(Dict[str, int], {'k': 'x'})
        assert resolve_error(detail, {'k': 'x'}, Dict[str, int]) is detail

    def test_list_index(self):
        detail = resolve(Tuple[int, str], [1, 2])
        assert detail.loc == (1,)
        assert detail.error_value == 2

    def test_root_error(self):
        detail = resolve(int, 'abc')
        assert detail.loc == ()
        assert detail.error_value == 'abc'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_missing_field(self):
        detail = resolve(Req, {})
        assert detail.loc == ('a',)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_nested_missing_field(self):
        detail = resolve(Box, {'inner': {}})
        assert detail.loc == ('inner', 'a')
        assert detail.error_value is UNSET

    def test_unknown_field(self):
        detail = resolve(Req, {'a': 1, 'z': 2})
        assert detail.loc == ('z',)
        assert detail.error_value == 2

    def test_set_not_indexable(self):
        """The loc is exact, but a set can not be indexed to fetch the value."""
        detail = resolve(Set[int], {1, 'x'})
        assert len(detail.loc) == 1
        assert detail.loc_resolved is True
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False


class TestInputContract:
    def test_input_error_not_modified(self):
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',))
        resolve_error(error, {'k': 'x'}, Dict[str, int])
        assert error.loc == ('...',)

    def test_input_data_not_modified(self):
        data = {'items': {'apple': {'amount': 'ABC'}}}
        snapshot = msgspec.json.encode(data)
        resolve(Inventory, data)
        assert msgspec.json.encode(data) == snapshot

    def test_validation_error_input(self):
        try:
            convert({'k': 'x'}, type=Dict[str, int])
        except ValidationError as e:
            detail = resolve_error(e, {'k': 'x'}, Dict[str, int])
        assert detail.loc == ('k',)
        assert detail.error_value == 'x'

    def test_str_input(self):
        detail = resolve_error(
            'Expected `int`, got `str` - at `$[...]`', {'k': 'x'}, Dict[str, int])
        assert detail.loc == ('k',)
        assert detail.error_value == 'x'

    def test_decoder_equals_model(self):
        payload = {'k': 'x'}
        from_model = resolve(Dict[str, int], payload)
        from_decoder = resolve_error(
            ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',)),
            payload, msgspec.json.Decoder(Dict[str, int]))
        assert from_decoder.loc == from_model.loc
        assert from_decoder.error_value == from_model.error_value

    def test_msgpack_decoder_equals_model(self):
        payload = {'k': 'x'}
        detail = resolve_error(
            ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',)),
            payload, msgspec.msgpack.Decoder(Dict[str, int]))
        assert detail.loc == ('k',)
        assert detail.error_value == 'x'

    def test_idempotent(self):
        detail = resolve(Dict[str, int], {'k': 'x'})
        again = resolve_error(detail, {'k': 'x'}, Dict[str, int])
        assert again is detail

    def test_half_resolved_struct_is_walked_again(self):
        """A concrete struct path without a value is resolved again."""
        error = ErrorInfoDetail(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a',),
                                loc_resolved=True)
        detail = resolve_error(error, {'a': 'bad'}, Req)
        assert detail.loc == ('a',)
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_half_resolved_dict_keeps_loc(self):
        """A resolved dict key can not be walked again, `...` is gone.

        With `('k',)` and a `Dict` model there is no way to tell the segment is
        a dict key rather than a struct field, so the location is kept and the
        value stays unavailable.
        """
        error = ErrorInfoDetail(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('k',),
                                loc_resolved=True)
        detail = resolve_error(error, {'k': 'x'}, Dict[str, int])
        assert detail.loc == ('k',)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False
        assert_invariants(detail)

    def test_msg_type_and_ctx_are_kept(self):
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',))
        detail = resolve_error(error, {'k': 'x'}, Dict[str, int])
        assert detail.msg == 'm'
        assert detail.type is ErrorType.TYPE_MISMATCH
        assert detail.ctx == error.ctx


class TestUnresolvable:
    """A corrupted (error, data, model) triple must never raise."""

    @pytest.mark.parametrize('model, data, loc', [
        # placeholders on a model without dict parameters
        (Req, {'a': 1}, ('...',)),
        (Req, {'a': 1}, ('...key',)),
        (dict, {'a': 1}, ('...',)),
        (dict, {'a': 1}, ('...key',)),
        (List[int], [1], ('...',)),
        (int, 1, ('...',)),
        # index on a model that is not a sequence
        (Dict[str, int], {'a': 1}, (0,)),
        (Set[int], {1}, (0,)),
        (List[int], [1], (5,)),
        # field name on a model that is not a struct
        (List[int], [1], ('a',)),
        (int, 1, ('a',)),
        # a corrupted loc
        (Req, {}, ('ghost',)),
        (Dict[str, Dict[str, int]], {'a': {'b': 1}}, ('...', 'ghost')),
        (Dict[str, int], {'a': 1}, ('...key', 'ghost')),
        (Req, {}, (None,)),
        (Req, {}, (True,)),
        (Req, {}, (['x'],)),
        (Dict[str, int], {'a': 1}, tuple(['...'] * 64)),
    ])
    def test_no_raise(self, model, data, loc):
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=loc)
        detail = resolve_error(error, data, model)
        assert_invariants(detail)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False
        # The input error is never modified
        assert error.loc == loc

    def test_placeholder_kept_when_unresolvable(self):
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',))
        detail = resolve_error(error, {'a': 1}, Req)
        assert detail.loc == ('...',)
        assert detail.loc_resolved is False

    def test_literal_tail_is_kept_when_missing(self):
        error = ErrorInfo(msg='m', type=ErrorType.MISSING_FIELD, loc=('a', 'b'))
        detail = resolve_error(error, {}, Req)
        assert detail.loc == ('a', 'b')
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False
