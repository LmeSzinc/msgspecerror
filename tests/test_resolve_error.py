"""Tests for `resolve_error` and `ErrorInfoDetail`."""
from typing import Dict, Final, Generic, List, NewType, Optional, Set, Tuple, TypeVar, Union

import msgspec
import pytest
from msgspec import NODEFAULT, UNSET, Struct, ValidationError, convert
from typing_extensions import Annotated

from msgspecerror import (
    ErrorInfo, ErrorInfoDetail, convert_with_error_detail, load_json_with_error_detail, load_msgpack_with_error_detail,
    parse_msgspec_error, resolve_error
)
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


T = TypeVar('T')


class GenericBox(Struct, Generic[T]):
    value: T


class GenericInner(Struct):
    n: int


class GenericOuter(Struct):
    box: GenericBox[GenericInner]


class GenericDictBox(Struct):
    box: GenericBox[Dict[str, int]]


class TaggedA(Struct, tag='a'):
    x: Dict[str, int] = {}


class TaggedB(Struct, tag='b'):
    x: Dict[str, int] = {}


class TaggedOther(Struct, tag='other'):
    y: Dict[str, int] = {}


class Renamed(Struct, rename='camel'):
    some_field: int = 0


class RenamedOuter(Struct):
    inner: Renamed


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
    # A placeholder is only kept when it could not be resolved. The reverse does
    # not hold: a dict key can literally be the string `...`.
    assert detail.loc_resolved or _has_placeholder(detail.loc)
    assert detail.error_value_resolved == (detail.error_value is not UNSET)
    # A value can only be collected by walking the whole path
    assert (not detail.error_value_resolved) or detail.loc_resolved


def assert_json_encodable(detail):
    """A detail is taken to logging, so json must accept it."""
    encoded = msgspec.json.encode(detail)
    assert encoded.startswith(b'{"msg":')


def assert_loc_segments(loc):
    """A delivered location holds int / str segments only, bool is not an int."""
    assert isinstance(loc, tuple)
    for segment in loc:
        assert isinstance(segment, (int, str))
        assert not isinstance(segment, bool)


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


class TestResolveLiteralPlaceholderKey:
    """A dict key can be the literal text of a placeholder.

    `'...'` and `'...key'` are ordinary strings, a data mapping is free to use
    them as keys. Such a key is a real key: the location points at one concrete
    entry, so it is resolved, and the value under it is obtained.
    """

    @pytest.mark.parametrize('key', ['...', '...key'])
    def test_dict_value_under_a_literal_key(self, key):
        detail = resolve(Dict[str, int], {key: 'x'})
        assert detail.loc == (key,)
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('key', ['...', '...key'])
    def test_literal_key_under_a_dict_value(self, key):
        detail = resolve(Dict[str, Dict[str, int]], {'outer': {key: 'x'}})
        assert detail.loc == ('outer', key)
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_literal_key_is_walked_further(self):
        """The literal key is an intermediate segment, not the last one."""
        detail = resolve(Dict[str, Dict[int, str]], {'...': {'k': 'x'}})
        assert detail.loc == ('...', 'k')
        assert detail.error_value == 'k'
        assert detail.loc_resolved is True

    def test_every_segment_is_a_literal_key(self):
        detail = resolve(Dict[str, Dict[str, int]], {'...': {'...': 'x'}})
        assert detail.loc == ('...', '...')
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True

    def test_a_literal_key_elsewhere_does_not_affect_the_location(self):
        """A valid entry named `...` is skipped, the failing key is reported."""
        detail = resolve(Dict[str, int], {'...': 1, 'bad': 'x'})
        assert detail.loc == ('bad',)
        assert detail.error_value == 'x'

    def test_second_walk_returns_the_same_object(self):
        """A resolved detail is idempotent even when its loc reads as `...`."""
        data = {'...': 'x'}
        detail = resolve(Dict[str, int], data)
        assert resolve_error(detail, data, Dict[str, int]) is detail

    def test_an_unresolvable_location_keeps_the_flag_false(self):
        """`loc_resolved` is False only when the placeholder was kept."""
        error = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',))
        detail = resolve_error(error, {'ok': 1}, Dict[str, int])
        assert detail.loc == ('...',)
        assert detail.loc_resolved is False
        assert detail.error_value_resolved is False


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


class TestResolveNestedDictKey:
    """`...key` can follow a `...`, the key is always the last segment."""

    def test_key_under_a_dict_value(self):
        detail = resolve(Dict[str, Dict[str, int]], {'a': {1: 'x'}})
        assert detail.loc == ('a', 1)
        assert detail.error_value == 1
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_key_two_dict_values_deep(self):
        model = Dict[str, Dict[str, Dict[str, int]]]
        detail = resolve(model, {'a': {'b': {1: 'x'}}})
        assert detail.loc == ('a', 'b', 1)
        assert detail.error_value == 1

    def test_key_value_is_the_key_not_the_value(self):
        """For a `...key` error the offending value is the key itself."""
        detail = resolve(Dict[str, Dict[str, str]], {'a': {1: 'kept'}})
        assert detail.loc == ('a', 1)
        assert detail.error_value == 1


class TestResolveWrappedModels:
    """Annotated / NewType / Final / Optional wrap the dict typehint."""

    @pytest.mark.parametrize('model', [
        Annotated[Dict[str, int], msgspec.Meta()],
        NewType('WrappedDict', Dict[str, int]),
        Final[Dict[str, int]],
        Optional[Dict[str, int]],
        Union[List[int], Dict[str, int]],
    ], ids=['annotated', 'newtype', 'final', 'optional', 'union-second-member'])
    def test_placeholder_is_resolved(self, model):
        detail = resolve(model, {'k': 'x'})
        assert detail.loc == ('k',)
        assert detail.error_value == 'x'

    def test_annotated_dict_value(self):
        model = Dict[str, Annotated[int, msgspec.Meta(ge=18)]]
        detail = resolve(model, {'k': 10})
        assert detail.loc == ('k',)
        assert detail.error_value == 10


class TestResolveGenericStruct:
    """A parametrized generic struct keeps its parameters on the walk."""

    def test_generic_field_type(self):
        detail = resolve(GenericOuter, {'box': {'value': {'n': 'bad'}}})
        assert detail.loc == ('box', 'value', 'n')
        assert detail.error_value == 'bad'

    def test_generic_dict_value(self):
        detail = resolve(GenericDictBox, {'box': {'value': {'k': 'bad'}}})
        assert detail.loc == ('box', 'value', 'k')
        assert detail.error_value == 'bad'


class TestResolveTaggedUnion:
    """The tag picks the union member when several members share a field name."""

    @pytest.mark.parametrize('tag', ['a', 'b'])
    def test_same_field_on_both_members(self, tag):
        detail = resolve(Union[TaggedA, TaggedB], {'type': tag, 'x': {'k': 'bad'}})
        assert detail.loc == ('x', 'k')
        assert detail.error_value == 'bad'

    def test_unknown_tag_reports_the_tag_field(self):
        detail = resolve(Union[TaggedA, TaggedB], {'type': 'zzz', 'x': {'k': 'bad'}})
        assert detail.loc == ('type',)
        assert detail.error_value is UNSET
        assert detail.loc_resolved is True

    def test_distinct_field_names(self):
        detail = resolve(Union[TaggedA, TaggedOther], {'type': 'other', 'y': {'k': 'bad'}})
        assert detail.loc == ('y', 'k')
        assert detail.error_value == 'bad'


class TestResolveRenamedField:
    """A struct field is reported by its serialized name, and located by it."""

    def test_camel_case_field(self):
        detail = resolve(Renamed, {'someField': 'bad'})
        assert detail.loc == ('someField',)
        assert detail.error_value == 'bad'

    def test_nested_camel_case_field(self):
        detail = resolve(RenamedOuter, {'inner': {'someField': 'bad'}})
        assert detail.loc == ('inner', 'someField')
        assert detail.error_value == 'bad'


class TestDeliveryInvariants:
    """Every delivered ErrorInfoDetail keeps its three invariants.

    A resolved location, a value when one was obtained, and never a value
    without a concrete location.
    """

    @pytest.mark.parametrize('model, data', [
        (Req, {}),
        (Box, {'inner': {}}),
        (Req, {'a': 1, 'z': 2}),
        (Dict[str, int], {'k': 'x'}),
        (Dict[str, int], {1: 'x'}),
        (Dict[str, List[int]], {'k': [1, 'x']}),
        (Dict[str, Dict[str, int]], {'a': {'b': 'x'}}),
        (Tuple[int, str], [1, 2]),
        (Box, {'inner': {'a': 'bad'}}),
        (Dict[str, Union[str, List[int]]], {'v': [1, 'x']}),
        (Union[Dict[str, int], List[int]], {'k': 'x'}),
        (Inventory, {'items': {'apple': {'amount': 'ABC'}}}),
        (int, 'abc'),
        (Set[int], {1, 'x'}),
    ])
    def test_convert(self, model, data):
        obj, detail = convert_with_error_detail(data, model)
        assert obj is NODEFAULT
        assert_invariants(detail)
        assert_json_encodable(detail)
        assert_loc_segments(detail.loc)

    @pytest.mark.parametrize('payload, model', [
        (b'{"a": "bad"}', Req),
        (b'{"a": "bad", "b": "\xff"}', Req),
        (b'{"a": "bad", "b": 1', Req),
        (b'{"a": ', Req),
        ('{"a": "bad"}', Req),
        (b'{"k": "x"}', Dict[str, int]),
        (b'{"k": "x", "b": "\xff"}', Dict[str, int]),
        (b'{"k": "x", "b": 1', Dict[str, int]),
        (b'{"k": "x"} {"j": 1}', Dict[str, int]),
        ('{"a": "bad", "b": "\ud800"}', Req),
    ], ids=[
        'valid', 'bad-tail-byte', 'truncated', 'malformed', 'str-payload',
        'dict-valid', 'dict-bad-tail-byte', 'dict-truncated', 'dict-trailing',
        'str-payload-lone-surrogate',
    ])
    def test_json_payload(self, payload, model):
        obj, detail = load_json_with_error_detail(payload, model)
        assert obj is NODEFAULT
        assert_invariants(detail)
        assert_json_encodable(detail)
        assert_loc_segments(detail.loc)

    @pytest.mark.parametrize('payload, model', [
        (msgspec.msgpack.encode({'a': 'bad'}), Req),
        (b'\x82\xa1a\xa3bad\xa1b\xa1\xff', Req),
        (b'\x82\xa1a\xa3bad', Req),
        (b'\xc1', Req),
        (msgspec.msgpack.encode({'k': 'x'}), Dict[str, int]),
        (b'\x82\xa1k\xa1x\xa1b\xa1\xff', Dict[str, int]),
        (b'\x82\xa1k\xa1x\xa1b', Dict[str, int]),
        (msgspec.msgpack.encode({1: 'x'}), Dict[str, int]),
    ], ids=[
        'valid', 'bad-tail-byte', 'truncated', 'malformed',
        'dict-valid', 'dict-bad-tail-byte', 'dict-truncated', 'dict-key',
    ])
    def test_msgpack_payload(self, payload, model):
        obj, detail = load_msgpack_with_error_detail(payload, model)
        assert obj is NODEFAULT
        assert_invariants(detail)
        assert_json_encodable(detail)
        assert_loc_segments(detail.loc)

    def test_the_two_flags_are_booleans(self):
        """The flags are plain bool fields, not truthy sentinels."""
        detail = resolve(Dict[str, int], {'k': 'x'})
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_the_flags_are_plain_fields(self):
        names = [f.name for f in msgspec.structs.fields(ErrorInfoDetail)]
        assert names == ['msg', 'type', 'loc', 'ctx', 'error_value',
                         'loc_resolved', 'error_value_resolved']


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
