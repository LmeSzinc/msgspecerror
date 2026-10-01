from typing import Generic, List, Optional, TypeVar

import msgspec
from msgspec import Struct

from msgspecerror.repair import load_json_with_default, load_msgpack_with_default


T = TypeVar("T")


class Box(Struct, Generic[T]):
    """A generic struct, `Box[int]` is a parametrized generic alias of it."""
    item: List[T] = []


class Holder(Struct):
    """A field typed with a parametrized generic alias."""
    box: Box[int]


class OptionalHolder(Struct):
    """A field typed with a parametrized generic alias inside a Union."""
    box: Optional[Box[int]] = None


class Outer(Struct, Generic[T]):
    """A field typed with an alias built from the outer TypeVar."""
    box: Box[T]


class TestRepairGenericAlias:
    """
    A parametrized generic alias (`Box[int]`) is repaired like a concrete struct:
    the error path walks into it and the TypeVars it carries are substituted.
    """

    def test_top_level_alias_is_repaired_in_place(self):
        data = b'{"item": ["bad"]}'
        result, errors = load_json_with_default(data, Box[int], guess_default=True)

        assert result == Box(item=[0])
        assert len(errors) == 1
        assert errors[0].loc == ("item", 0)

    def test_top_level_alias_minimal_repair(self):
        """Without guessing, the failing list item is deleted."""
        data = b'{"item": ["bad"]}'
        result, errors = load_json_with_default(data, Box[int])

        assert result == Box(item=[])
        assert len(errors) == 1
        assert errors[0].loc == ("item", 0)

    def test_alias_field_is_repaired_in_place(self):
        data = b'{"box": {"item": ["bad"]}}'
        result, errors = load_json_with_default(data, Holder, guess_default=True)

        assert result == Holder(box=Box(item=[0]))
        assert len(errors) == 1
        assert errors[0].loc == ("box", "item", 0)

    def test_optional_alias_field_is_repaired_in_place(self):
        data = b'{"box": {"item": ["bad"]}}'
        result, errors = load_json_with_default(data, OptionalHolder, guess_default=True)

        assert result == OptionalHolder(box=Box(item=[0]))
        assert len(errors) == 1
        assert errors[0].loc == ("box", "item", 0)

    def test_alias_of_alias_is_repaired_in_place(self):
        data = b'{"box": {"item": ["bad"]}}'
        result, errors = load_json_with_default(data, Outer[int], guess_default=True)

        assert result == Outer(box=Box(item=[0]))
        assert len(errors) == 1
        assert errors[0].loc == ("box", "item", 0)

    def test_msgpack_alias_is_repaired_in_place(self):
        data = msgspec.msgpack.encode({"item": ["bad"]})
        result, errors = load_msgpack_with_default(data, Box[int], guess_default=True)

        assert result == Box(item=[0])
        assert len(errors) == 1
        assert errors[0].loc == ("item", 0)
