from typing import Dict, ForwardRef, Generic, List, Optional, Tuple, TypeVar, Union

import pytest
from msgspec import NODEFAULT, Meta, Struct, UNSET, UnsetType, field
from msgspec.structs import fields as struct_fields
from typing_extensions import Annotated

from msgspecerror.parse_struct import get_field_default, get_field_name, get_field_typehint


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


class UnjudgeableTypehintStruct(Struct):
    """msgspec can not convert to this field typehint, the default can not be judged."""
    items: list["AnotherStruct"] = []


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
# Test Class for `get_field_default`
# =================================================================
class TestGetFieldDefault:
    """Tests for the `get_field_default` utility function."""

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
        default = get_field_default(SimpleStruct, field_name)
        assert default == expected_value

    def test_default_unset_in_child_class(self):
        """
        Tests that a field with a default in the parent, when set to UNSET
        in the child, is correctly identified as having no default.
        """
        # The child makes `overridden_field` required, so it has no default.
        assert get_field_default(Child, "overridden_field") is NODEFAULT

    def test_failing_factory_returns_nodefault(self):
        """
        Tests that if a default_factory raises an exception during invocation,
        the function returns NODEFAULT.
        """
        assert get_field_default(StructWithFailingFactory, "failing_field") is NODEFAULT

    def test_non_existent_field_name_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised when the provided `field_name`
        does not exist on the model.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with name="non_existent"',
        ):
            get_field_default(SimpleStruct, "non_existent")

    def test_invalid_model_type_raises_attribute_error(self):
        """
        Tests that an AttributeError is raised if the provided `model` is not
        a valid `msgspec.Struct` subclass.
        """
        with pytest.raises(
                AttributeError, match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
            get_field_default(NotAStruct, "a")

    def test_get_default_with_encode_name(self):
        """
        Tests that a field default can be retrieved using its encode name
        (the serialization name defined via ``field(name=...)``).
        """
        # bField is the encode name for field `b_aliased`, which has no default
        assert get_field_default(SimpleStruct, "bField") is NODEFAULT

        # c_default_value has no alias, encode_name == field_name
        assert get_field_default(SimpleStruct, "c_default_value") == 3.14

    def test_non_existent_encode_name_raises_attribute_error_in_get_default(self):
        """
        Tests that a non-existent encode name raises AttributeError
        in `get_field_default`.
        """
        with pytest.raises(
                AttributeError,
                match=f'Type {SimpleStruct} has no field with name="non_existent_encode"',
        ):
            get_field_default(SimpleStruct, "non_existent_encode")

    def test_inherited_aliased_default_by_encode_name(self):
        """Default of inherited aliased field accessible via encode name on child."""
        assert get_field_default(ChildOfAliased, "parentDefaultAlias") == 99

    def test_inherited_aliased_default_by_field_name(self):
        """Default of inherited aliased field accessible via field name on child."""
        assert get_field_default(ChildOfAliased, "aliased_with_default") == 99

    def test_inherited_aliased_required_default_by_encode_name(self):
        """Inherited required (no default) aliased field returns NODEFAULT via encode name."""
        assert get_field_default(ChildOfAliased, "parentAlias") is NODEFAULT

    def test_validate_keeps_an_accepted_default(self):
        """`validate=True` returns the default when msgspec accepts it as an explicit value."""
        assert get_field_default(ConstrainedDefaultStruct, "plain", validate=True) == 1
        assert get_field_default(SimpleStruct, "c_default_value", validate=True) == 3.14
        assert get_field_default(SimpleStruct, "d_default_factory", validate=True) == []
        assert get_field_default(SimpleStruct, "f_default_none", validate=True) is None

    def test_validate_rejects_a_default_msgspec_does_not_accept(self):
        """A default that msgspec rejects as an explicit value returns NODEFAULT."""
        # without validation the default is returned as it is
        assert get_field_default(ConstrainedDefaultStruct, "v") == 0
        assert get_field_default(ConstrainedDefaultStruct, "v", validate=True) is NODEFAULT

    def test_validate_required_field_has_no_default(self):
        """A field without a default returns NODEFAULT with or without validation."""
        assert get_field_default(SimpleStruct, "a_required", validate=True) is NODEFAULT
        assert get_field_default(SimpleStruct, "e_unset", validate=True) is NODEFAULT

    def test_validate_with_encode_name(self):
        """The validation also works when the field is looked up by its encode name."""
        assert get_field_default(ChildOfAliased, "parentDefaultAlias", validate=True) == 99

    def test_validate_keeps_a_default_msgspec_can_not_judge(self):
        """A field type msgspec can not convert to keeps its default."""
        assert get_field_default(UnjudgeableTypehintStruct, "items", validate=True) == []

    def test_validate_failing_factory_returns_nodefault(self):
        """A factory that raises still returns NODEFAULT when validation is on."""
        assert get_field_default(StructWithFailingFactory, "failing_field", validate=True) is NODEFAULT


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
        Tests that the function raises a specific AttributeError for types
        lacking the `__annotations__` attribute.
        """
        # UPDATED: Check for the new explicit error from the try/except block.
        with pytest.raises(
                AttributeError,
                match=f"Type {NotAStruct} is not a valid msgspec.Struct"
        ):
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
