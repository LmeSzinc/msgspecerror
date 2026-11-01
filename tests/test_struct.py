import dataclasses
import sys
from dataclasses import InitVar
from typing import ClassVar, Dict, ForwardRef, Generic, List, NamedTuple, Optional, Tuple, TypeVar, Union

import attrs
import pytest
from msgspec import NODEFAULT, UNSET, Meta, Struct, UnsetType, field
from msgspec.structs import fields as struct_fields
from typing_extensions import Annotated, NotRequired, TypedDict

from msgspecerror import parse_model, parse_model_dataclass, parse_model_struct
from msgspecerror.parse_model import get_field_default, get_field_name, get_field_typehint
from msgspecerror.parse_model_attrs import get_attrs_field_default
from msgspecerror.parse_model_dataclass import get_dataclass_field_default, get_dataclass_field_typehint
from msgspecerror.parse_model_namedtuple import get_namedtuple_field_default
from msgspecerror.parse_model_struct import get_arraylike_field_typehint, get_struct_field_default
from msgspecerror.parse_model_typeddict import get_typeddict_field_default


# --- Test Data Setup ---
# A comprehensive Struct for testing field names and defaults.
class SimpleStruct(Struct):
    a_required: int
    b_aliased: str = field(name="bField")
    c_default_value: float = 3.14
    d_default_factory: list = field(default_factory=list)
    e_unset: Union[str, UnsetType] = UNSET
    f_default_none: Optional[int] = None


# Structs for testing type hint resolution.
class AnotherStruct(Struct):
    id: int


class ForwardRefStruct(Struct):
    # Test for string-based forward reference
    ref_field_str: "AnotherStruct"
    # Test for ForwardRef object reference
    ref_field_obj: ForwardRef("AnotherStruct")


class NestedForwardRefStruct(Struct):
    """Forward references nested inside other type hints."""
    optional_ref: Optional["AnotherStruct"] = None
    list_ref: List["AnotherStruct"] = []
    dict_ref: Dict[str, "AnotherStruct"] = {}
    tuple_ref: Tuple[int, "AnotherStruct"] = (0, None)
    annotated_ref: Annotated["AnotherStruct", Meta()] = None


class RecursiveNode(Struct):
    """A self-referential forward reference."""
    value: int = 0
    next: Optional["RecursiveNode"] = None


class ClassLocalAliasStruct(Struct):
    """A forward reference to a name defined in the class body."""
    Alias = int
    aliased_field: "Alias"


# Generic Structs for testing TypeVar resolution.
T = TypeVar("T")


class GenericBase(Struct, Generic[T]):
    generic_data: T
    constant_field: int  # A non-generic field for control


class ConcreteInt(GenericBase[int]):
    pass


class ConcreteListStr(GenericBase[List[str]]):
    pass


class GenericForwardRef(Struct, Generic[T]):
    """A forward reference to a TypeVar, resolved per parametrized subclass."""
    generic_ref: List["T"]


class ConcreteForwardRefList(GenericForwardRef[List[str]]):
    pass


class GenericWithDefaults(Struct, Generic[T]):
    """A generic struct with defaults, used to look fields up via a parametrized alias."""
    data: List[T] = []
    count: int = 0


# Structs for testing inheritance (MRO traversal).
class Parent(Struct):
    parent_field: str
    overridden_field: str = "parent_default"


class Child(Parent, kw_only=True):
    child_field: int
    overridden_field: UnsetType = UNSET


# A simple class that is not a msgspec.Struct, for error testing.
class NotAStruct:
    pass


# A struct with a factory that will raise an error, for testing error handling.
def _failing_factory():
    raise ValueError("Factory intentionally fails")


class StructWithFailingFactory(Struct):
    failing_field: list = field(default_factory=_failing_factory)


class ConstrainedDefaultStruct(Struct):
    """The default `0` violates `ge=18`, msgspec rejects it as an explicit value."""
    v: Annotated[int, Meta(ge=18)] = 0
    plain: int = 1


# Structs for testing inheritance with aliases.
class ParentWithAlias(Struct):
    aliased_from_parent: str = field(name="parentAlias")
    aliased_with_default: int = field(name="parentDefaultAlias", default=99)


class ChildOfAliased(ParentWithAlias, kw_only=True):
    own_aliased: float = field(name="childAlias")


# =================================================================
# Test Class for `get_field_name`
# =================================================================
class TestGetFieldName:
    """Tests for the `get_field_name` utility function."""

    def test_get_name_with_alias(self):
        """
        Tests if the function correctly converts an aliased `encode_name`
        (defined with `field(name=...)`) back to its Python attribute name.
        """
        assert get_field_name(SimpleStruct, "bField") == "b_aliased"

    def test_get_name_without_alias(self):
        """
        Tests if the function correctly handles fields without an alias,
        where the `encode_name` is the same as the attribute name.
        """
        assert get_field_name(SimpleStruct, "a_required") == "a_required"

    def test_get_name_with_alias_fieldname(self):
        """
        Tests that passing a field name that has an alias returns the
        field name itself.
        """
        assert get_field_name(SimpleStruct, "b_aliased") == "b_aliased"

    def test_non_existent_encode_name_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised when the provided `encode_name`
        does not exist on the model.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with name="non_existent"',
        ):
            get_field_name(SimpleStruct, "non_existent")

    def test_invalid_model_type_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised if the provided `model` is not
        a valid `msgspec.Struct` subclass.
        """
        with pytest.raises(
                AttributeError, match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
            get_field_name(NotAStruct, "a")

    def test_inherited_alias_encode_name(self):
        """Encode name from parent resolves to field name on child."""
        assert get_field_name(ChildOfAliased, "parentAlias") == "aliased_from_parent"

    def test_inherited_alias_field_name(self):
        """Field name from parent returns itself on child."""
        assert get_field_name(ChildOfAliased, "aliased_from_parent") == "aliased_from_parent"

    def test_inherited_aliased_default_encode_name(self):
        """Encode name of inherited field with default resolves on child."""
        assert get_field_name(ChildOfAliased, "parentDefaultAlias") == "aliased_with_default"

    def test_child_own_alias_encode_name(self):
        """Child's own encode name resolves to field name."""
        assert get_field_name(ChildOfAliased, "childAlias") == "own_aliased"

    def test_child_own_alias_field_name(self):
        """Child's own field name (with alias) returns itself."""
        assert get_field_name(ChildOfAliased, "own_aliased") == "own_aliased"


# =================================================================
# Test Class for `get_struct_field_default`
# =================================================================
class TestGetStructFieldDefault:
    """Tests for the `get_struct_field_default` utility function."""

    @pytest.mark.parametrize(
        "field_name, expected_value",
        [
            # A required field has no default.
            ("a_required", NODEFAULT),
            # A field with a simple default value.
            ("c_default_value", 3.14),
            # A field with a default_factory; the factory is called to get the value.
            ("d_default_factory", []),
            # A field marked as UNSET has no effective default.
            ("e_unset", NODEFAULT),
            # A field with `None` as its default value.
            ("f_default_none", None),
        ],
    )
    def test_get_defaults(self, field_name, expected_value):
        """
        Tests various default scenarios: required, simple value,
        default_factory, UNSET, and None.
        """
        default = get_struct_field_default(SimpleStruct, field_name)
        assert default == expected_value

    def test_default_unset_in_child_class(self):
        """
        Tests that a field with a default in the parent, when set to UNSET
        in the child, is correctly identified as having no default.
        """
        # The child makes `overridden_field` required, so it has no default.
        assert get_struct_field_default(Child, "overridden_field") is NODEFAULT

    def test_failing_factory_returns_nodefault(self):
        """
        Tests that if a default_factory raises an exception during invocation,
        the function returns NODEFAULT.
        """
        assert get_struct_field_default(StructWithFailingFactory, "failing_field") is NODEFAULT

    def test_non_existent_field_name_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised when the provided `field_name`
        does not exist on the model.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with name="non_existent"',
        ):
            get_struct_field_default(SimpleStruct, "non_existent")

    def test_invalid_model_type_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised if the provided `model` is not
        a valid `msgspec.Struct` subclass.
        """
        with pytest.raises(
                AttributeError, match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
            get_struct_field_default(NotAStruct, "a")

    def test_get_default_with_encode_name(self):
        """
        Tests that a field default can be retrieved using its encode name
        (the serialization name defined via ``field(name=...)``).
        """
        # bField is the encode name for field `b_aliased`, which has no default
        assert get_struct_field_default(SimpleStruct, "bField") is NODEFAULT

        # c_default_value has no alias, encode_name == field_name
        assert get_struct_field_default(SimpleStruct, "c_default_value") == 3.14

    def test_non_existent_encode_name_raises_attribute_error_in_get_default(self):
        """
        Tests that a non-existent encode name raises AttributeError
        in `get_struct_field_default`.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with name="non_existent_encode"',
        ):
            get_struct_field_default(SimpleStruct, "non_existent_encode")

    def test_inherited_aliased_default_by_encode_name(self):
        """Default of inherited aliased field accessible via encode name on child."""
        assert get_struct_field_default(ChildOfAliased, "parentDefaultAlias") == 99

    def test_inherited_aliased_default_by_field_name(self):
        """Default of inherited aliased field accessible via field name on child."""
        assert get_struct_field_default(ChildOfAliased, "aliased_with_default") == 99

    def test_inherited_aliased_required_default_by_encode_name(self):
        """Inherited required (no default) aliased field returns NODEFAULT via encode name."""
        assert get_struct_field_default(ChildOfAliased, "parentAlias") is NODEFAULT

    def test_validate_keeps_an_accepted_default(self):
        """`validate=True` returns the default when msgspec accepts it as an explicit value."""
        assert get_struct_field_default(ConstrainedDefaultStruct, "plain", validate=True) == 1
        assert get_struct_field_default(SimpleStruct, "c_default_value", validate=True) == 3.14
        assert get_struct_field_default(SimpleStruct, "d_default_factory", validate=True) == []
        assert get_struct_field_default(SimpleStruct, "f_default_none", validate=True) is None

    def test_validate_rejects_a_default_msgspec_does_not_accept(self):
        """A default that msgspec rejects as an explicit value returns NODEFAULT."""
        # without validation the default is returned as it is
        assert get_struct_field_default(ConstrainedDefaultStruct, "v") == 0
        assert get_struct_field_default(ConstrainedDefaultStruct, "v", validate=True) is NODEFAULT

    def test_validate_required_field_has_no_default(self):
        """A field without a default returns NODEFAULT with or without validation."""
        assert get_struct_field_default(SimpleStruct, "a_required", validate=True) is NODEFAULT
        assert get_struct_field_default(SimpleStruct, "e_unset", validate=True) is NODEFAULT

    def test_validate_with_encode_name(self):
        """The validation also works when the field is looked up by its encode name."""
        assert get_struct_field_default(ChildOfAliased, "parentDefaultAlias", validate=True) == 99

    @pytest.mark.skipif(sys.version_info < (3, 9), reason='builtin generics need Python 3.9+')
    def test_validate_keeps_a_default_msgspec_can_not_judge(self):
        """
        A field type msgspec can not convert to keeps its default.

        `list["AnotherStruct"]` keeps the quoted ref as a plain str, which
        msgspec does not support, so the default can not be judged.
        """
        class UnjudgeableTypehintStruct(Struct):
            items: list["AnotherStruct"] = []

        assert get_struct_field_default(UnjudgeableTypehintStruct, "items", validate=True) == []

    def test_validate_failing_factory_returns_nodefault(self):
        """A factory that raises still returns NODEFAULT when validation is on."""
        assert get_struct_field_default(StructWithFailingFactory, "failing_field", validate=True) is NODEFAULT


# =================================================================
# Test Class for `get_field_typehint`
# =================================================================
class TestGetFieldTypehint:
    """Tests for the `get_field_typehint` utility function."""

    @pytest.mark.parametrize(
        "model, field_name, expected_type",
        [
            (SimpleStruct, "a_required", int),
            (SimpleStruct, "b_aliased", str),
            (SimpleStruct, "c_default_value", float),
            (SimpleStruct, "d_default_factory", list),
            (SimpleStruct, "e_unset", Union[str, UnsetType]),
            (SimpleStruct, "f_default_none", Optional[int]),
            (Child, "parent_field", str),
            (Child, "child_field", int),
        ],
    )
    def test_basic_and_union_types(self, model, field_name, expected_type):
        """Tests retrieval of basic types (int, str) and composite types (Union, Optional)."""
        assert get_field_typehint(model, field_name) == expected_type

    def test_typehint_of_field_unset_in_child(self):
        """
        Tests that the type hint for a field overridden with UNSET in a child
        class is correctly identified as `UnsetType`.
        """
        # The MRO search should find the annotation in `Child` first.
        assert get_field_typehint(Child, "overridden_field") is UnsetType

    def test_forward_ref_as_string(self):
        """Tests that a string-based forward reference is correctly resolved."""
        assert get_field_typehint(ForwardRefStruct, "ref_field_str") is AnotherStruct

    def test_forward_ref_as_object(self):
        """Tests that a `ForwardRef` object is correctly resolved."""
        assert get_field_typehint(ForwardRefStruct, "ref_field_obj") is AnotherStruct

    @pytest.mark.parametrize(
        "model, field_name, expected_type",
        [
            (NestedForwardRefStruct, "optional_ref", Optional[AnotherStruct]),
            (NestedForwardRefStruct, "list_ref", List[AnotherStruct]),
            (NestedForwardRefStruct, "dict_ref", Dict[str, AnotherStruct]),
            (NestedForwardRefStruct, "tuple_ref", Tuple[int, AnotherStruct]),
            (NestedForwardRefStruct, "annotated_ref", Annotated[AnotherStruct, Meta()]),
            (RecursiveNode, "next", Optional[RecursiveNode]),
            (ClassLocalAliasStruct, "aliased_field", int),
        ],
    )
    def test_forward_ref_nested_in_type_hints(self, model, field_name, expected_type):
        """
        Tests that forward references nested inside other type hints
        (e.g. `Optional["X"]`, `List["X"]`) are resolved, not only the
        top-level string / `ForwardRef` annotations.
        """
        assert get_field_typehint(model, field_name) == expected_type

    def test_forward_ref_typehint_matches_msgspec_resolution(self):
        """
        Tests that the returned typehint is the annotation msgspec itself
        resolved for the decoder, so the two can never drift apart.
        """
        for field_info in struct_fields(NestedForwardRefStruct):
            assert get_field_typehint(NestedForwardRefStruct, field_info.name) == field_info.type

    def test_typevar_inside_forward_ref_is_substituted(self):
        """
        Tests that a TypeVar referenced through a forward reference
        (`List["T"]`) is substituted for the parametrized subclass.
        """
        assert get_field_typehint(ConcreteForwardRefList, "generic_ref") == List[List[str]]

    def test_typevar_resolution_with_simple_type(self):
        """Tests that a TypeVar is correctly replaced by a concrete simple type (int)."""
        assert get_field_typehint(ConcreteInt, "generic_data") is int

    def test_typevar_resolution_with_complex_type(self):
        """Tests that a TypeVar is correctly replaced by a concrete complex type (List[str])."""
        assert get_field_typehint(ConcreteListStr, "generic_data") == List[str]

    def test_non_generic_field_in_generic_struct(self):
        """
        Tests that a non-generic field in a generic class returns its own
        type without being affected by TypeVar substitution.
        """
        assert get_field_typehint(ConcreteInt, "constant_field") is int

    def test_inheritance_from_parent_class(self):
        """Tests that the function can find a type hint for a field defined in a parent class."""
        assert get_field_typehint(Child, "parent_field") is str

    def test_field_on_child_class(self):
        """Tests that the function finds the type hint on the child class itself."""
        assert get_field_typehint(Child, "child_field") is int

    def test_non_existent_field_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised with the standardized message if a field
        cannot be found in the model's MRO.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {Child} has no field with name="non_existent"',
        ):
            get_field_typehint(Child, "non_existent")

    def test_model_without_annotations_raises_specific_error(self):
        """
        A type that carries none of the model metadata sets is refused before
        its fields are read.
        """
        with pytest.raises(AttributeError, match=f"Type {NotAStruct} is not a struct-like model"):
            get_field_typehint(NotAStruct, "any_field")

    def test_get_typehint_with_encode_name(self):
        """
        Tests that a type hint can be retrieved using its encode name
        (the serialization name defined via ``field(name=...)``).
        """
        # bField is the encode name for field `b_aliased`, which has type str
        assert get_field_typehint(SimpleStruct, "bField") is str

        # c_default_value has no alias, encode_name == field_name
        assert get_field_typehint(SimpleStruct, "c_default_value") is float

    def test_non_existent_encode_name_raises_attribute_error_in_get_typehint(self):
        """
        Tests that a non-existent encode name raises AttributeError
        in `get_field_typehint`.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with name="nonexistent_encode"',
        ):
            get_field_typehint(SimpleStruct, "nonexistent_encode")

    def test_inherited_aliased_typehint_by_encode_name(self):
        """Type hint of inherited aliased field via encode name on child."""
        assert get_field_typehint(ChildOfAliased, "parentAlias") is str

    def test_inherited_aliased_typehint_by_field_name(self):
        """Type hint of inherited aliased field via field name on child."""
        assert get_field_typehint(ChildOfAliased, "aliased_from_parent") is str

    def test_inherited_aliased_default_typehint_by_encode_name(self):
        """Type hint of inherited aliased field with default via encode name."""
        assert get_field_typehint(ChildOfAliased, "parentDefaultAlias") is int

    def test_child_own_aliased_typehint_by_encode_name(self):
        """Type hint of child's own aliased field via encode name."""
        assert get_field_typehint(ChildOfAliased, "childAlias") is float

    def test_child_own_aliased_typehint_by_field_name(self):
        """Type hint of child's own aliased field via field name."""
        assert get_field_typehint(ChildOfAliased, "own_aliased") is float


# =================================================================
# Test Class for `get_field_*` with a parametrized generic alias
# =================================================================
class TestGetFieldWithGenericAlias:
    """The `get_field_*` utilities accept a parametrized generic alias of a Struct."""

    def test_get_field_name(self):
        """A field of a generic alias resolves like one of the origin class."""
        assert get_field_name(GenericWithDefaults[str], "data") == "data"

    def test_get_field_typehint_substitutes_typevars(self):
        """The TypeVar of the alias is substituted in the field typehint."""
        assert get_field_typehint(GenericWithDefaults[str], "data") == List[str]
        assert get_field_typehint(GenericWithDefaults[int], "count") is int

    def test_get_field_typehint_matches_concrete_subclass(self):
        """The alias and the concrete subclass produce the same typehint."""
        assert get_field_typehint(GenericForwardRef[List[str]], "generic_ref") == \
            get_field_typehint(ConcreteForwardRefList, "generic_ref")

    def test_get_struct_field_default(self):
        """Defaults are read from the origin class of the alias."""
        assert get_struct_field_default(GenericWithDefaults[str], "data") == []
        assert get_struct_field_default(GenericWithDefaults[str], "count") == 0

    def test_get_struct_field_default_validate(self):
        """The default is validated against the parametrized field typehint."""
        assert get_struct_field_default(GenericWithDefaults[str], "data", validate=True) == []

    def test_non_struct_alias_raises_attribute_error(self):
        """An alias of a non-struct type is rejected like a non-struct class."""
        with pytest.raises(AttributeError, match="is not a valid msgspec.Struct"):
            get_field_name(List[int], "data")


# =================================================================
# `array_like=True` struct: a failing field is filled by position
# =================================================================
class ArrayLikeFilled(Struct, array_like=True):
    required: str
    with_default: int = 7
    with_factory: List[int] = field(default_factory=list)


class NoDefaultStruct(Struct, array_like=True):
    value: Annotated[int, Meta(ge=18)]


class ArrayLikeBox(Struct, Generic[T], array_like=True):
    item: T


class ArrayLikeConstrained(Struct, array_like=True):
    n: int
    constrained: Annotated[int, Meta(ge=18)] = 0


class TestGetStructFieldDefaultByIndex:
    """`get_struct_field_default` accepts the position an `array_like` error reports."""

    @pytest.mark.parametrize('index, expected', [
        (0, NODEFAULT), (1, 7), (2, []),
    ])
    def test_a_position(self, index, expected):
        assert get_struct_field_default(ArrayLikeFilled, index) == expected

    def test_a_position_with_a_guess(self):
        assert get_struct_field_default(ArrayLikeFilled, 0, guess_default=True) == ""

    def test_a_position_with_validate(self):
        """A default that fails its own constraint is not written into the input."""
        assert get_struct_field_default(ArrayLikeConstrained, 1, validate=True) is NODEFAULT

    def test_a_position_out_of_range(self):
        with pytest.raises(AttributeError, match="has no field at index 9"):
            get_struct_field_default(ArrayLikeFilled, 9)

    def test_a_negative_index_is_python_indexing(self):
        assert get_struct_field_default(ArrayLikeFilled, -1) == []

    def test_a_plain_struct_by_name_still_works(self):
        assert get_struct_field_default(SimpleStruct, "c_default_value") == 3.14

    @pytest.mark.parametrize('model', [List[int], int, None, NotAStruct])
    def test_a_model_without_fields(self, model):
        with pytest.raises(AttributeError):
            get_struct_field_default(model, 0)

    def test_an_unknown_name(self):
        with pytest.raises(AttributeError, match="has no field with name=\"ghost\""):
            get_struct_field_default(ArrayLikeFilled, "ghost")


class TestGetStructFieldDefaultGuess:
    """`get_struct_field_default(guess_default=True)` falls back to the field typehint."""

    def test_a_declared_default_wins(self):
        assert get_struct_field_default(ArrayLikeFilled, "with_default", guess_default=True) == 7

    def test_a_factory_default(self):
        assert get_struct_field_default(ArrayLikeFilled, "with_factory", guess_default=True) == []

    def test_a_declared_default(self):
        assert get_struct_field_default(ArrayLikeFilled, "with_default") == 7

    def test_a_guessed_value(self):
        assert get_struct_field_default(ArrayLikeFilled, "required", guess_default=True) == ""

    def test_guessing_is_off_by_default(self):
        assert get_struct_field_default(ArrayLikeFilled, "required") is NODEFAULT

    def test_a_type_without_a_guessable_value(self):
        """A constrained field has no value the typehint can supply."""
        assert get_struct_field_default(NoDefaultStruct, "value", guess_default=True) is NODEFAULT


class TestGetArraylikeFieldTypehint:
    """`get_arraylike_field_typehint` reads a field typehint by position."""

    @pytest.mark.parametrize('index, expected', [(0, str), (1, int), (2, List[int])])
    def test_the_field_at_a_position(self, index, expected):
        assert get_arraylike_field_typehint(ArrayLikeFilled, index) is expected

    def test_a_position_out_of_range(self):
        with pytest.raises(AttributeError, match="has no field at index 9"):
            get_arraylike_field_typehint(ArrayLikeFilled, 9)

    def test_a_negative_index_is_python_indexing(self):
        assert get_arraylike_field_typehint(ArrayLikeFilled, -1) is List[int]

    def test_a_generic_alias(self):
        """The alias resolves its TypeVar like the named lookup does."""
        assert get_arraylike_field_typehint(ArrayLikeBox[str], 0) is str
        assert get_arraylike_field_typehint(ArrayLikeBox[int], 0) is int

    @pytest.mark.parametrize('model', [List[int], int, None, NotAStruct])
    def test_a_model_that_is_not_a_struct(self, model):
        with pytest.raises(AttributeError, match="is not a valid msgspec.Struct"):
            get_arraylike_field_typehint(model, 0)


class TaggedArrayLike(Struct, tag=True, array_like=True):
    """A tagged array starts with the tag, so a field is one position past it."""
    first: str = 'a'
    second: int = 1


class TestTaggedArrayLikePosition:
    """An error reports the position in the encoded array, the tag included.

    `msgspec` encodes a tagged `array_like` struct as `[tag, field0, ...]`, so
    `$[1]` names the first field. Reading position 1 as `field_names[1]` picked
    the second field and wrote its default into the first one's slot.
    """

    @pytest.mark.parametrize('index, expected', [(1, str), (2, int)])
    def test_the_typehint_at_a_position(self, index, expected):
        assert get_arraylike_field_typehint(TaggedArrayLike, index) is expected

    @pytest.mark.parametrize('index, expected', [(1, 'a'), (2, 1)])
    def test_the_default_at_a_position(self, index, expected):
        assert get_struct_field_default(TaggedArrayLike, index) == expected

    def test_the_tag_position_is_no_field(self):
        """Position 0 holds the tag, it addresses nothing."""
        with pytest.raises(AttributeError, match='the array starts with the tag'):
            get_arraylike_field_typehint(TaggedArrayLike, 0)
        with pytest.raises(AttributeError, match='the array starts with the tag'):
            get_struct_field_default(TaggedArrayLike, 0)

    def test_a_position_past_the_end(self):
        with pytest.raises(AttributeError, match='has no field at index 3'):
            get_arraylike_field_typehint(TaggedArrayLike, 3)

    def test_a_name_still_works(self):
        assert get_struct_field_default(TaggedArrayLike, 'second') == 1

    def test_an_untagged_struct_keeps_python_indexing(self):
        """The offset belongs to the tag, not to every array position."""
        assert get_arraylike_field_typehint(ArrayLikeFilled, 0) is str
        assert get_arraylike_field_typehint(ArrayLikeFilled, -1) is List[int]


# =================================================================
# Models of the plain kinds msgspec can decode into
# =================================================================
@dataclasses.dataclass
class DCSimple:
    required: int
    plain: float = 3.14
    made: List[int] = dataclasses.field(default_factory=list)
    none: Optional[int] = None


def _raise_factory():
    raise RuntimeError('factory failed')


@dataclasses.dataclass
class DCFailingFactory:
    broken: List[int] = dataclasses.field(default_factory=_raise_factory)


@dataclasses.dataclass
class DCConstrained:
    plain: int = 1
    v: Annotated[int, Meta(ge=18)] = 0


@dataclasses.dataclass
class DCConstrainedRequired:
    v: Annotated[int, Meta(ge=18)]


@dataclasses.dataclass
class DCWithClassVar:
    counted: int = 1
    constant: ClassVar[int] = 5
    injected: InitVar[int] = 3


@dataclasses.dataclass
class DCGeneric(Generic[T]):
    v: Optional[T] = None


class TDRecord(TypedDict):
    required: int
    optional: NotRequired[str]


class TDPartial(TypedDict, total=False):
    loose: int


class NTSimple(NamedTuple):
    required: int
    plain: str = 'x'


class NTFilled(NamedTuple):
    first: int
    second: str = 'y'


@attrs.define
class ATSimple:
    required: int
    plain: str = 'x'
    made: List[int] = attrs.field(factory=list)


def _refuse_factory():
    raise RuntimeError('factory failed')


@attrs.define
class ATFailingFactory:
    broken: List[int] = attrs.field(factory=_refuse_factory)


@attrs.define
class ATNeedsSelf:
    seed: int = 1
    derived: int = attrs.field(default=attrs.Factory(lambda self: self.seed, takes_self=True))


@attrs.define
class ATConstrained:
    plain: int = 1
    v: Annotated[int, Meta(ge=18)] = 0


@attrs.define
class ATConstrainedRequired:
    v: Annotated[int, Meta(ge=18)]


# =================================================================
# Test Class for `get_dataclass_field_default`
# =================================================================
class TestGetDataclassFieldDefault:
    """`get_dataclass_field_default` reads a field default of a dataclass."""

    @pytest.mark.parametrize("field_name, expected_value", [
        # a field without a default, the dataclass requires it
        ('required', NODEFAULT),
        # a plain default value
        ('plain', 3.14),
        # a default_factory, the factory is called to get the value
        ('made', []),
        # `None` is a default like any other
        ('none', None),
    ])
    def test_get_defaults(self, field_name, expected_value):
        assert get_dataclass_field_default(DCSimple, field_name) == expected_value

    def test_a_factory_is_called_for_each_call(self):
        """A factory is not cached, each call returns what it makes."""
        first = get_dataclass_field_default(DCSimple, 'made')
        second = get_dataclass_field_default(DCSimple, 'made')
        assert first == [] and second == []
        assert first is not second

    def test_a_failing_factory_returns_nodefault(self):
        assert get_dataclass_field_default(DCFailingFactory, 'broken') is NODEFAULT

    def test_a_position_is_not_a_field(self):
        """Only an `array_like` struct is decoded from an array, a dataclass is read by name."""
        with pytest.raises(AttributeError, match='has no field with name="1"'):
            get_dataclass_field_default(DCSimple, 1)

    def test_a_non_existent_field_name_raises_attribute_error(self):
        with pytest.raises(AttributeError, match=f'Type {DCSimple} has no field with name="missing"'):
            get_dataclass_field_default(DCSimple, 'missing')

    @pytest.mark.parametrize('name', ['constant', 'injected'])
    def test_a_pseudo_field_is_not_a_field(self, name):
        """`__dataclass_fields__` holds ClassVar and InitVar too, msgspec decodes neither."""
        with pytest.raises(AttributeError, match=f'has no field with name="{name}"'):
            get_dataclass_field_default(DCWithClassVar, name)

    @pytest.mark.parametrize('name', ['constant', 'injected'])
    def test_a_pseudo_field_has_no_typehint_either(self, name):
        """The typehint is read from the same field lookup, so it refuses them too."""
        with pytest.raises(AttributeError, match=f'has no field with name="{name}"'):
            get_dataclass_field_typehint(DCWithClassVar, name)

    def test_a_real_field_typehint(self):
        assert get_dataclass_field_typehint(DCWithClassVar, 'counted') is int

    def test_the_decoded_field_is_read(self):
        assert get_dataclass_field_default(DCWithClassVar, 'counted') == 1

    def test_a_parametrized_generic_alias(self):
        assert get_dataclass_field_default(DCGeneric[int], 'v') is None

    @pytest.mark.parametrize('model', [SimpleStruct, NotAStruct, int, List[int], None])
    def test_a_model_that_is_not_a_dataclass(self, model):
        with pytest.raises(AttributeError, match='is not a valid dataclass'):
            get_dataclass_field_default(model, 0)

    def test_validate_keeps_an_accepted_default(self):
        assert get_dataclass_field_default(DCConstrained, 'plain', validate=True) == 1

    def test_validate_rejects_a_default_msgspec_does_not_accept(self):
        """msgspec does not validate a dataclass default, the explicit value is checked."""
        assert get_dataclass_field_default(DCConstrained, 'v') == 0
        assert get_dataclass_field_default(DCConstrained, 'v', validate=True) is NODEFAULT

    def test_guess_default_fills_a_required_field(self):
        assert get_dataclass_field_default(DCSimple, 'required', guess_default=True) == 0

    def test_a_field_object_without_a_default_is_required(self, monkeypatch):
        """The attributes are read optimistically, a missing one means no default."""

        class Bare:
            name = 'plain'
            _field_type = type('_FieldType', (), {'name': '_FIELD'})()

        monkeypatch.setattr(DCSimple, '__dataclass_fields__', {'plain': Bare()})
        assert get_dataclass_field_default(DCSimple, 'plain') is NODEFAULT
        assert get_dataclass_field_default(DCSimple, 'plain', guess_default=True) == 0

    def test_a_guessed_value_is_still_checked(self):
        """The guessed `0` of a constrained type is refused, so there is no default."""
        assert get_dataclass_field_default(DCConstrainedRequired, 'v', guess_default=True) is NODEFAULT


# =================================================================
# Test Class for `get_typeddict_field_default`
# =================================================================
class TestGetTypedDictFieldDefault:
    """A TypedDict has no field default, `NotRequired` only says the key may be absent."""

    @pytest.mark.parametrize('field_name', ['required', 'optional'])
    def test_a_field_has_no_default(self, field_name):
        assert get_typeddict_field_default(TDRecord, field_name) is NODEFAULT

    def test_total_false_is_still_no_default(self):
        """An optional key is not a default, msgspec leaves it out instead of filling it."""
        assert get_typeddict_field_default(TDPartial, 'loose') is NODEFAULT

    def test_guess_default(self):
        assert get_typeddict_field_default(TDRecord, 'required', guess_default=True) == 0
        assert get_typeddict_field_default(TDRecord, 'optional', guess_default=True) == ''

    def test_validate_has_nothing_to_check(self):
        """There is no default value, so validation does not change the answer."""
        assert get_typeddict_field_default(TDRecord, 'required', validate=True) is NODEFAULT

    def test_a_position_is_not_a_key(self):
        with pytest.raises(AttributeError, match='has no field with name="1"'):
            get_typeddict_field_default(TDRecord, 1, guess_default=True)

    def test_a_non_existent_field_name_raises_attribute_error(self):
        with pytest.raises(AttributeError, match=f'Type {TDRecord} has no field with name="missing"'):
            get_typeddict_field_default(TDRecord, 'missing')

    @pytest.mark.parametrize('model', [int, List[int], Dict[str, int], list, None])
    def test_a_model_that_is_not_a_typeddict(self, model):
        """The annotations are read directly, so a type without them is the refusal."""
        with pytest.raises(AttributeError, match='is not a valid TypedDict'):
            get_typeddict_field_default(model, 'a')

    def test_a_class_with_annotations_is_read_as_one(self):
        """The caller is optimistic, a class that has the annotations is answered."""
        assert get_typeddict_field_default(SimpleStruct, 'a_required', guess_default=True) == 0

    def test_a_required_marker_is_peeled(self):
        """`Required[str]` marks the key, the value type is the `str` inside."""
        from typing_extensions import Required

        class Strict(TypedDict, total=False):
            forced: Required[str]

        assert get_typeddict_field_default(Strict, 'forced', guess_default=True) == ''

    def test_a_marker_without_args_is_used_as_it_is(self):
        """An object that only looks like the marker does not break the read."""
        from typing_extensions import Required

        class Fake:
            __origin__ = Required

        class Stranger:
            __annotations__ = {'odd': Fake()}
            __required_keys__ = frozenset({'odd'})
            __optional_keys__ = frozenset()

        assert get_typeddict_field_default(Stranger, 'odd', guess_default=True) is NODEFAULT


# =================================================================
# Test Class for `get_namedtuple_field_default`
# =================================================================
class TestGetNamedTupleFieldDefault:
    """`get_namedtuple_field_default` reads the trailing defaults of a NamedTuple."""

    @pytest.mark.parametrize("field_name, expected_value", [
        # only the trailing fields can have a default
        ('required', NODEFAULT),
        ('plain', 'x'),
    ])
    def test_get_defaults(self, field_name, expected_value):
        assert get_namedtuple_field_default(NTSimple, field_name) == expected_value

    def test_a_position(self):
        """A NamedTuple is decoded from an array, so an error reports a position."""
        assert get_namedtuple_field_default(NTSimple, 0) is NODEFAULT
        assert get_namedtuple_field_default(NTSimple, 1) == 'x'

    def test_a_position_out_of_range_raises_attribute_error(self):
        with pytest.raises(AttributeError, match='has no field at index 9'):
            get_namedtuple_field_default(NTSimple, 9)

    def test_a_non_existent_field_name_raises_attribute_error(self):
        with pytest.raises(AttributeError, match='has no field with name="missing"'):
            get_namedtuple_field_default(NTSimple, 'missing')

    @pytest.mark.parametrize('model', [SimpleStruct, NotAStruct, int, Tuple[int, str], None])
    def test_a_model_that_is_not_a_namedtuple(self, model):
        with pytest.raises(AttributeError, match='is not a valid NamedTuple'):
            get_namedtuple_field_default(model, 0)

    def test_guess_default_fills_a_required_field(self):
        assert get_namedtuple_field_default(NTSimple, 'required', guess_default=True) == 0

    def test_validate_keeps_an_accepted_default(self):
        assert get_namedtuple_field_default(NTFilled, 'second', validate=True) == 'y'


# =================================================================
# Test Class for `get_attrs_field_default`
# =================================================================
class TestGetAttrsFieldDefault:
    """`get_attrs_field_default` reads a field default of an attrs class."""

    @pytest.mark.parametrize("field_name, expected_value", [
        # `attrs.NOTHING` means the field is required
        ('required', NODEFAULT),
        ('plain', 'x'),
        ('made', []),
    ])
    def test_get_defaults(self, field_name, expected_value):
        assert get_attrs_field_default(ATSimple, field_name) == expected_value

    def test_a_failing_factory_returns_nodefault(self):
        assert get_attrs_field_default(ATFailingFactory, 'broken') is NODEFAULT

    def test_a_factory_that_needs_the_instance_returns_nodefault(self):
        """`takes_self=True` reads the object being built, which does not exist yet."""
        assert get_attrs_field_default(ATNeedsSelf, 'derived') is NODEFAULT
        assert get_attrs_field_default(ATNeedsSelf, 'seed') == 1

    def test_a_position_is_not_a_field(self):
        with pytest.raises(AttributeError, match='has no field with name="1"'):
            get_attrs_field_default(ATSimple, 1)

    def test_a_non_existent_field_name_raises_attribute_error(self):
        with pytest.raises(AttributeError, match=f'Type {ATSimple} has no field with name="missing"'):
            get_attrs_field_default(ATSimple, 'missing')

    def test_a_field_object_without_a_default_is_required(self, monkeypatch):
        """The attributes are read optimistically, a missing one means no default."""

        class Bare:
            name = 'plain'

        monkeypatch.setattr(ATSimple, '__attrs_attrs__', (Bare(),))
        assert get_attrs_field_default(ATSimple, 'plain') is NODEFAULT
        # `plain` is a str, the guessed value is the empty string
        assert get_attrs_field_default(ATSimple, 'plain', guess_default=True) == ''

    @pytest.mark.parametrize('model', [SimpleStruct, NotAStruct, int, List[int], None])
    def test_a_model_that_is_not_an_attrs_class(self, model):
        with pytest.raises(AttributeError, match='is not a valid attrs class'):
            get_attrs_field_default(model, 0)

    def test_validate_keeps_an_accepted_default(self):
        assert get_attrs_field_default(ATConstrained, 'plain', validate=True) == 1

    def test_validate_rejects_a_default_msgspec_does_not_accept(self):
        assert get_attrs_field_default(ATConstrained, 'v') == 0
        assert get_attrs_field_default(ATConstrained, 'v', validate=True) is NODEFAULT

    def test_guess_default_fills_a_required_field(self):
        assert get_attrs_field_default(ATSimple, 'required', guess_default=True) == 0


# =================================================================
# Test Class for `get_field_default` over all model kinds
# =================================================================
class TestGetFieldDefaultDispatch:
    """`get_field_default` names the model kind and delegates to the same field."""

    @pytest.mark.parametrize("model, field_name, expected_value", [
        (SimpleStruct, 'c_default_value', 3.14),
        (DCSimple, 'plain', 3.14),
        (TDRecord, 'required', NODEFAULT),
        (NTSimple, 'plain', 'x'),
        (ATSimple, 'plain', 'x'),
    ])
    def test_every_kind(self, model, field_name, expected_value):
        assert get_field_default(model, field_name) == expected_value

    @pytest.mark.parametrize("model, field_name", [
        (DCSimple, 'required'),
        (NTSimple, 'required'),
        (ATSimple, 'required'),
        (TDRecord, 'required'),
    ])
    def test_guess_default_is_forwarded(self, model, field_name):
        """Every kind falls back to the typehint when allowed, none of them has a value."""
        assert get_field_default(model, field_name, guess_default=True) == 0

    def test_a_guessed_value_is_checked_for_every_kind(self):
        """The guessed `0` is refused by a constrained type, whatever the model kind."""
        assert get_field_default(DCConstrainedRequired, 'v', guess_default=True) is NODEFAULT
        assert get_field_default(ATConstrainedRequired, 'v', guess_default=True) is NODEFAULT

    def test_validate_is_forwarded(self):
        assert get_field_default(DCConstrained, 'plain', validate=True) == 1
        assert get_field_default(DCConstrained, 'v', validate=True) is NODEFAULT
        assert get_field_default(ConstrainedDefaultStruct, 'v', validate=True) is NODEFAULT

    def test_known_struct_type_skips_the_lookup(self, monkeypatch):
        """A caller that already knows the kind does not pay for the check."""

        def refuse(model):
            raise AssertionError('is_struct_like should not be called')

        monkeypatch.setattr(parse_model, 'is_struct_like', refuse)
        assert get_field_default(DCSimple, 'plain', known_struct_type='dataclass') == 3.14

    def test_the_kind_is_looked_up_when_it_is_not_given(self, monkeypatch):
        calls = []
        original = parse_model.is_struct_like

        def counting(model, unwrap=True):
            calls.append(model)
            return original(model, unwrap=unwrap)

        monkeypatch.setattr(parse_model, 'is_struct_like', counting)
        assert get_field_default(DCSimple, 'plain') == 3.14
        assert calls == [DCSimple]

    @pytest.mark.parametrize('model', [NotAStruct, int, List[int], Dict[str, int], None])
    def test_a_model_that_is_not_struct_like(self, model):
        with pytest.raises(AttributeError, match='is not a struct-like model'):
            get_field_default(model, 0)


class TestGetFieldTypehintDispatch:
    """`get_field_typehint` names the model kind and delegates to the same field."""

    @pytest.mark.parametrize("model, name, expected", [
        (SimpleStruct, 'a_required', int),
        (DCSimple, 'plain', float),
        (TDRecord, 'optional', str),
        (NTSimple, 'plain', str),
        (ATSimple, 'plain', str),
    ])
    def test_every_kind(self, model, name, expected):
        assert get_field_typehint(model, name) is expected

    @pytest.mark.parametrize("model, name", [
        (SimpleStruct, 'missing'),
        (DCSimple, 'missing'),
        (TDRecord, 'missing'),
        (NTSimple, 'missing'),
        (ATSimple, 'missing'),
    ])
    def test_a_missing_field(self, model, name):
        with pytest.raises(AttributeError, match='has no field with name="missing"'):
            get_field_typehint(model, name)

    def test_a_position_of_a_namedtuple(self):
        """A NamedTuple is decoded from an array, so a position addresses it."""
        assert get_field_typehint(NTSimple, 1) is str

    def test_known_struct_type_skips_the_lookup(self, monkeypatch):
        def refuse(model, unwrap=True):
            raise AssertionError('is_struct_like should not be called')

        monkeypatch.setattr(parse_model, 'is_struct_like', refuse)
        assert get_field_typehint(DCSimple, 'plain', known_struct_type='dataclass') is float

    def test_a_parametrized_generic_alias(self):
        """The alias is unwrapped once, so the field of the origin class is read."""
        assert get_field_default(ArrayLikeBox[str], 0) is NODEFAULT
        assert get_field_default(DCGeneric[int], 'v') is None

    def test_unwrap_false_skips_the_unwrap(self, monkeypatch):
        """A caller that already holds the origin class pays nothing for the alias."""

        def refuse(model):
            raise AssertionError('unwrap_model should not be called')

        monkeypatch.setattr(parse_model_dataclass, 'unwrap_model', refuse)
        monkeypatch.setattr(parse_model_struct, 'unwrap_model', refuse)
        assert get_field_default(
            DCSimple, 'plain', known_struct_type='dataclass', unwrap=False) == 3.14
        assert get_field_default(
            SimpleStruct, 'c_default_value', known_struct_type='struct', unwrap=False) == 3.14

    def test_unwrap_true_accepts_a_generic_alias(self):
        assert get_field_default(DCGeneric[int], 'v', known_struct_type='dataclass') is None


# =================================================================
# Test Class for the model kind helpers
# =================================================================
class TestIsArraylike:
    """`is_arraylike` tells whether a model's fields are addressed by position."""

    @pytest.mark.parametrize('model', [ArrayLikeFilled, NoDefaultStruct, NTSimple])
    def test_an_array_model(self, model):
        assert parse_model.is_arraylike(model) is True

    @pytest.mark.parametrize('model', [SimpleStruct, DCSimple, TDRecord, ATSimple])
    def test_an_object_model(self, model):
        assert parse_model.is_arraylike(model) is False

    @pytest.mark.parametrize('model', [int, str, List[int], Dict[str, int], None])
    def test_neither(self, model):
        assert parse_model.is_arraylike(model) is False

    def test_known_struct_type_skips_the_lookup(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError('is_struct_like should not be called')

        monkeypatch.setattr(parse_model, 'is_struct_like', refuse)
        assert parse_model.is_arraylike(NTSimple, known_struct_type='namedtuple') is True
        assert parse_model.is_arraylike(ArrayLikeFilled, known_struct_type='struct') is True

    def test_a_parametrized_alias(self):
        assert parse_model.is_arraylike(ArrayLikeBox[str]) is True
