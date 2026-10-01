from typing import Dict, Generic, List, Optional, Tuple, TypeVar

import msgspec
from msgspec import Meta, Struct
from typing_extensions import Annotated

from msgspecerror.repair import load_json_with_default, load_msgpack_with_default


# --- Test Models ---
# These classes must be defined at the module level for msgspec to resolve the
# forward references.

class Child(Struct):
    """The forward-ref target, it can be default constructed."""
    x: int = 0


class HolderOptionalRef(Struct):
    """The forward ref is nested inside `Optional`."""
    child: Optional["Child"] = None


class HolderListRef(Struct):
    """The forward ref is nested inside `List`."""
    items: List["Child"] = []


class HolderDictRef(Struct):
    """The forward ref is nested inside `Dict`, msgspec reports the path as `...`."""
    items: Dict[str, "Child"] = {}


class HolderTupleRef(Struct):
    """The forward ref is nested inside a fixed-length `Tuple`."""
    pair: Tuple[int, "Child"] = (0, None)


class HolderAnnotatedRef(Struct):
    """The forward ref is nested inside `Annotated`."""
    child: Annotated["Child", Meta()] = None


class RecursiveNode(Struct):
    """A self-referential forward ref."""
    value: int = 0
    next: Optional["RecursiveNode"] = None


T = TypeVar("T")


class Box(Struct, Generic[T]):
    """A forward ref to a TypeVar, substituted per parametrized subclass."""
    item: List["T"] = []


class IntBox(Box[int]):
    pass


class ClassLocalAlias(Struct):
    """A forward ref to a name defined in the class body."""
    Alias = int
    v: "Alias"


class TestRepairForwardRef:
    """
    A failing value behind a field annotated with a forward reference is repaired
    in place, instead of falling back to the field default or the root default.
    """

    def test_optional_ref_is_repaired_in_place(self):
        data = b'{"child": {"x": "bad"}}'
        result, errors = load_json_with_default(data, HolderOptionalRef, guess_default=True)

        assert result == HolderOptionalRef(child=Child(x=0))
        assert len(errors) == 1
        assert errors[0].loc == ("child", "x")

    def test_list_ref_is_repaired_in_place(self):
        data = b'{"items": [{"x": "bad"}]}'
        result, errors = load_json_with_default(data, HolderListRef, guess_default=True)

        assert result == HolderListRef(items=[Child(x=0)])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 0, "x")

    def test_dict_ref_value_is_repaired_in_place(self):
        """
        The `...` placeholder path probes the dict values, the probe must not
        raise TypeError for the forward-referenced value type.
        """
        data = b'{"items": {"apple": {"x": "bad"}}}'
        result, errors = load_json_with_default(data, HolderDictRef, guess_default=True)

        assert result == HolderDictRef(items={"apple": Child(x=0)})
        assert len(errors) == 1
        assert errors[0].loc == ("items", "apple", "x")

    def test_tuple_ref_is_repaired_in_place(self):
        data = b'{"pair": [1, {"x": "bad"}]}'
        result, errors = load_json_with_default(data, HolderTupleRef, guess_default=True)

        assert result == HolderTupleRef(pair=(1, Child(x=0)))
        assert len(errors) == 1
        assert errors[0].loc == ("pair", 1, "x")

    def test_annotated_ref_is_repaired_in_place(self):
        data = b'{"child": {"x": "bad"}}'
        result, errors = load_json_with_default(data, HolderAnnotatedRef, guess_default=True)

        assert result == HolderAnnotatedRef(child=Child(x=0))
        assert len(errors) == 1
        assert errors[0].loc == ("child", "x")

    def test_recursive_ref_keeps_the_rest_of_the_data(self):
        """Only the failing value is repaired, its valid siblings are kept."""
        data = b'{"value": 1, "next": {"value": "bad"}}'
        result, errors = load_json_with_default(data, RecursiveNode, guess_default=True)

        assert result == RecursiveNode(value=1, next=RecursiveNode(value=0))
        assert len(errors) == 1
        assert errors[0].loc == ("next", "value")

    def test_typevar_inside_forward_ref_is_substituted(self):
        data = b'{"item": ["bad"]}'
        result, errors = load_json_with_default(data, IntBox, guess_default=True)

        assert result == IntBox(item=[0])
        assert len(errors) == 1
        assert errors[0].loc == ("item", 0)

    def test_class_local_alias_does_not_raise(self):
        """The annotation refers to a name defined in the class body."""
        data = b'{"v": "bad"}'
        result, errors = load_json_with_default(data, ClassLocalAlias, guess_default=True)

        assert result == ClassLocalAlias(v=0)
        assert len(errors) == 1
        assert errors[0].loc == ("v",)

    def test_msgpack_optional_ref_is_repaired_in_place(self):
        data = msgspec.msgpack.encode({"child": {"x": "bad"}})
        result, errors = load_msgpack_with_default(data, HolderOptionalRef, guess_default=True)

        assert result == HolderOptionalRef(child=Child(x=0))
        assert len(errors) == 1
        assert errors[0].loc == ("child", "x")
