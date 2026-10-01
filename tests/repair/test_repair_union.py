"""Tests for repairing errors inside ``Union`` / ``Optional`` typed fields.

msgspec reports an error path relative to the union member being validated
(e.g. ``$.d[...]`` for an ``Optional[Dict[int, int]]`` field), so the repair
walk must resolve the union to that member. Otherwise the union args
(``(Dict[int, int], NoneType)``) are read as if they were the dict key and
value types, which deletes valid sibling entries.
"""
import copy
import sys
from typing import Dict, List, Optional, Union

import msgspec
import msgspec.msgpack
import pytest
from msgspec import NODEFAULT, Struct

from msgspecerror.const import ErrorType
from msgspecerror.parse_error import ErrorInfo
from msgspecerror.repair import _repair_once, load_json_with_default, load_msgpack_with_default


# --- Test Models ---

class Inner(Struct):
    """A struct that can be default-constructed."""
    x: int = 1


class InnerReq(Struct):
    """A struct that cannot be default-constructed."""
    x: int


class TaggedA(Struct, tag=True):
    x: int = 1


class TaggedB(Struct, tag=True):
    x: int = 2


class OptionalIntMap(Struct):
    """``Optional[Dict[int, int]]``, the reported broken case."""
    d: Optional[Dict[int, int]] = None


class OptionalStructMap(Struct):
    """``Optional[Dict[int, InnerReq]]``."""
    d: Optional[Dict[int, InnerReq]] = None


class OptionalInner(Struct):
    """``Optional[Inner]``, the inner struct has a field default."""
    s: Optional[Inner] = None


class OptionalInnerReq(Struct):
    """``Optional[InnerReq]``, the inner struct can't be default-constructed."""
    s: Optional[InnerReq] = None


class OptionalIntList(Struct):
    """``Optional[List[int]]``."""
    items: Optional[List[int]] = None


class OptionalStructList(Struct):
    """``Optional[List[InnerReq]]``."""
    items: Optional[List[InnerReq]] = None


class OptionalNested(Struct):
    """``Optional[Dict[str, List[InnerReq]]]``."""
    d: Optional[Dict[str, List[InnerReq]]] = None


class PlainNested(Struct):
    """The optional is a dict value instead of the field type."""
    d: Dict[str, Optional[InnerReq]] = {}


class DictOrList(Struct):
    """A union of two different container types."""
    v: Union[Dict[str, int], List[int]] = None


class StructOrList(Struct):
    """A union of a struct and a list."""
    v: Union[Inner, List[int]] = None


class TaggedUnion(Struct):
    """A union of two tagged structs that share the field name ``x``."""
    v: Union[TaggedA, TaggedB] = None


class TestOptionalContainerRepair:
    """A failing item inside an ``Optional`` container is repaired item by item."""

    def test_dict_value_error_keeps_valid_entries(self):
        """Only the failing entry is removed, all valid entries are kept."""
        data = b'{"d": {"1": 1, "2": 2, "3": "bad"}}'
        result, errors = load_json_with_default(data, OptionalIntMap)

        assert result == OptionalIntMap(d={1: 1, 2: 2})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "3")

    def test_dict_invalid_key_removes_only_that_entry(self):
        """The entry with the invalid key is removed, valid entries are kept."""
        data = b'{"d": {"bad": 1, "2": 2}}'
        result, errors = load_json_with_default(data, OptionalIntMap)

        assert result == OptionalIntMap(d={2: 2})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad")

    def test_dict_struct_value_is_deleted_when_not_defaultable(self):
        """``InnerReq`` can't be default-constructed, the failing entry is deleted."""
        data = b'{"d": {"1": {"x": 1}, "2": {"x": "bad"}}}'
        result, errors = load_json_with_default(data, OptionalStructMap)

        assert result == OptionalStructMap(d={1: InnerReq(x=1)})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "2", "x")

    def test_list_item_error_is_deleted(self):
        data = b'{"items": [1, "bad", 3]}'
        result, errors = load_json_with_default(data, OptionalIntList)

        assert result == OptionalIntList(items=[1, 3])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)

    def test_list_item_error_is_guessed(self):
        data = b'{"items": [1, "bad", 3]}'
        result, errors = load_json_with_default(data, OptionalIntList, guess_default=True)

        assert result == OptionalIntList(items=[1, 0, 3])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)

    def test_list_of_structs_item_error_is_deleted(self):
        data = b'{"items": [{"x": 1}, {"x": "bad"}]}'
        result, errors = load_json_with_default(data, OptionalStructList)

        assert result == OptionalStructList(items=[InnerReq(x=1)])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1, "x")

    def test_nested_dict_list_struct(self):
        """A deep path through ``Optional[Dict[...]]`` repairs the innermost item."""
        data = b'{"d": {"k": [{"x": 1}, {"x": "bad"}]}}'
        result, errors = load_json_with_default(data, OptionalNested)

        assert result == OptionalNested(d={"k": [InnerReq(x=1)]})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "k", 1, "x")

    def test_valid_input_is_untouched(self):
        data = b'{"d": {"1": 1}}'
        result, errors = load_json_with_default(data, OptionalIntMap)

        assert result == OptionalIntMap(d={1: 1})
        assert errors == []


class TestOptionalStructRepair:
    """A failing field inside an ``Optional[Struct]`` is repaired in the struct member."""

    def test_field_with_default_is_repaired(self):
        """``Inner.x`` has a default, the repaired union member is kept."""
        data = b'{"s": {"x": "bad"}}'
        result, errors = load_json_with_default(data, OptionalInner)

        assert result == OptionalInner(s=Inner(x=1))
        assert len(errors) == 1
        assert errors[0].loc == ("s", "x")

    def test_required_field_falls_back_to_none(self):
        """``InnerReq.x`` has no default, the field falls back to its own default ``None``."""
        data = b'{"s": {"x": "bad"}}'
        result, errors = load_json_with_default(data, OptionalInnerReq)

        assert result == OptionalInnerReq(s=None)
        assert len(errors) == 1
        assert errors[0].loc == ("s", "x")

    def test_optional_dict_value_inside_plain_dict(self):
        """The optional is a dict value, the failing entry is deleted."""
        data = b'{"d": {"k": {"x": "bad"}}}'
        result, errors = load_json_with_default(data, PlainNested)

        assert result == PlainNested(d={})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "k", "x")


class TestUnionMemberSelection:
    """The kind of the path segment selects which union member the repair walks into."""

    def test_dict_member(self):
        data = b'{"v": {"k": "bad"}}'
        result, errors = load_json_with_default(data, DictOrList)

        assert result == DictOrList(v={})
        assert len(errors) == 1

    def test_list_member(self):
        data = b'{"v": [1, "bad"]}'
        result, errors = load_json_with_default(data, DictOrList)

        assert result == DictOrList(v=[1])
        assert len(errors) == 1

    def test_struct_member(self):
        data = b'{"v": {"x": "bad"}}'
        result, errors = load_json_with_default(data, StructOrList)

        assert result == StructOrList(v=Inner(x=1))
        assert len(errors) == 1
        assert errors[0].loc == ("v", "x")

    def test_list_member_of_struct_or_list(self):
        data = b'{"v": [1, "bad"]}'
        result, errors = load_json_with_default(data, StructOrList)

        assert result == StructOrList(v=[1])
        assert len(errors) == 1


class TestTaggedUnionRepair:
    """A tagged union repairs the member the tag points to."""

    @pytest.mark.parametrize('tag,expected', [
        ('TaggedA', TaggedA(x=1)),
        ('TaggedB', TaggedB(x=2)),
    ])
    def test_member_field_error_uses_tagged_member(self, tag, expected):
        """Both members share the field ``x``, the tag decides which default is used."""
        data = ('{"v": {"type": "%s", "x": "bad"}}' % tag).encode()
        result, errors = load_json_with_default(data, TaggedUnion)

        assert result == TaggedUnion(v=expected)
        assert len(errors) == 1
        assert errors[0].loc == ("v", "x")

    def test_invalid_tag_falls_back_to_field_default(self):
        data = b'{"v": {"type": "Nope"}}'
        result, errors = load_json_with_default(data, TaggedUnion)

        assert result == TaggedUnion(v=None)
        assert len(errors) == 1
        assert errors[0].type is ErrorType.INVALID_TAG_VALUE


class TestRootUnionModel:
    """A union as the root model is resolved the same way."""

    def test_optional_dict(self):
        data = b'{"1": "bad", "2": 2}'
        result, errors = load_json_with_default(data, Optional[Dict[int, int]])

        assert result == {2: 2}
        assert len(errors) == 1
        assert errors[0].loc == ("1",)

    def test_union_dict_member(self):
        data = b'{"k": "bad", "j": 1}'
        result, errors = load_json_with_default(data, Union[Dict[str, int], List[int]])

        assert result == {"j": 1}
        assert len(errors) == 1

    def test_union_list_member(self):
        data = b'[1, "bad"]'
        result, errors = load_json_with_default(data, Union[Dict[str, int], List[int]])

        assert result == [1]
        assert len(errors) == 1
        assert errors[0].loc == (1,)


class TestPep604Union:
    """PEP 604 unions (``X | None``) are resolved like ``Optional[X]``."""

    @pytest.mark.skipif(sys.version_info < (3, 10), reason='PEP 604 unions need Python 3.10+')
    def test_optional_struct_field(self):
        class Model(Struct):
            v: InnerReq | None = None

        data = b'{"v": {"x": "bad"}}'
        result, errors = load_json_with_default(data, Model)

        assert result == Model(v=None)
        assert len(errors) == 1
        assert errors[0].loc == ("v", "x")


class TestUnionRepairMsgpack:
    """The msgpack pipeline resolves union members the same way."""

    def test_optional_dict_value_error(self):
        data = msgspec.msgpack.encode({'d': {1: 1, 2: 'bad'}})
        result, errors = load_msgpack_with_default(data, OptionalIntMap)

        assert result == OptionalIntMap(d={1: 1})
        assert len(errors) == 1
        assert errors[0].loc == ("d", 2)

    def test_optional_list_item_error(self):
        data = msgspec.msgpack.encode({'items': [1, 'bad', 3]})
        result, errors = load_msgpack_with_default(data, OptionalIntList)

        assert result == OptionalIntList(items=[1, 3])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)


class TestUnionMalformedLoc:
    """The union resolution must also fail softly when the loc doesn't match."""

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('model,data,loc', [
        (OptionalInner, {'s': {'x': 1}}, ('s', 'ghost')),
        (OptionalInner, {'s': {'x': 1}}, ('s', '...')),
        (OptionalIntMap, {'d': {'1': 1}}, ('d', '...key')),
        (OptionalIntList, {'items': [1]}, ('items', 'ghost')),
        (OptionalStructList, {'items': [{'x': 1}]}, ('items', 999)),
        (TaggedUnion, {'v': {'type': 'TaggedB', 'x': 1}}, ('v', 'ghost')),
        (TaggedUnion, {'v': {'type': 'TaggedB', 'x': 1}}, ('v', '...')),
    ])
    def test_union_loc_mismatch_never_raises(self, model, data, loc, guess_default):
        """A loc that matches no member gives up instead of raising."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=loc)

        result_obj, result_error = _repair_once(
            copy.deepcopy(data), model, error, guess_default=guess_default)

        assert result_error is NODEFAULT or isinstance(result_error, ErrorInfo)
        if isinstance(result_error, ErrorInfo):
            assert isinstance(result_error.loc, tuple)
        if result_obj is NODEFAULT:
            assert isinstance(result_error, ErrorInfo)
