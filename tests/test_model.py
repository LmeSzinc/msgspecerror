from typing import Union, Optional

import pytest
from msgspec import Struct, UNSET, UnsetType

from msgspecerror.parse_model import get_model_changes


# --- Test Data Setup ---

class EmptyStruct(Struct):
    pass


class SimpleStruct(Struct):
    x: int
    y: str
    z: float


class MixedStruct(Struct):
    a: int
    b: str
    c: float
    d: bool
    e: bytes


class NestedStruct(Struct):
    inner: SimpleStruct
    label: str


class FreezableStruct(Struct, frozen=True):
    x: int
    y: str


class StructWithUnset(Struct):
    value: Union[int, None, UnsetType] = UNSET


class NotAStruct:
    pass


# =================================================================
# Test Class for `get_model_changes`
# =================================================================

class TestGetModelChanges:
    """Tests for the `get_model_changes` utility function."""

    def test_equal_structs_returns_empty_dict(self):
        """Two identical structs should return an empty dict."""
        old = SimpleStruct(x=1, y="hello", z=3.14)
        new = SimpleStruct(x=1, y="hello", z=3.14)
        assert get_model_changes(old, new) == {}

    def test_same_object_returns_empty_dict(self):
        """Comparing a struct to itself should return an empty dict."""
        old = SimpleStruct(x=1, y="hello", z=3.14)
        assert get_model_changes(old, old) == {}

    def test_empty_struct_equal_returns_empty_dict(self):
        """Two identical empty structs should return an empty dict."""
        old = EmptyStruct()
        new = EmptyStruct()
        assert get_model_changes(old, new) == {}

    @pytest.mark.parametrize(
        "old, new, expected_changes",
        [
            (
                    SimpleStruct(x=1, y="hello", z=3.14),
                    SimpleStruct(x=2, y="hello", z=3.14),
                    {"x": 2},
            ),
            (
                    SimpleStruct(x=1, y="hello", z=3.14),
                    SimpleStruct(x=1, y="world", z=3.14),
                    {"y": "world"},
            ),
            (
                    SimpleStruct(x=1, y="hello", z=3.14),
                    SimpleStruct(x=1, y="hello", z=2.71),
                    {"z": 2.71},
            ),
        ],
    )
    def test_single_field_changes(self, old, new, expected_changes):
        """When exactly one field changes, only that field is in the result."""
        assert get_model_changes(old, new) == expected_changes

    def test_multiple_fields_change(self):
        """All changed fields should be in the result dict."""
        old = SimpleStruct(x=1, y="hello", z=3.14)
        new = SimpleStruct(x=99, y="world", z=2.71)
        assert get_model_changes(old, new) == {"x": 99, "y": "world", "z": 2.71}

    def test_bool_fields(self):
        """Bool fields should be correctly compared."""
        old = MixedStruct(a=1, b="x", c=1.0, d=True, e=b"data")
        new = MixedStruct(a=1, b="x", c=1.0, d=False, e=b"data")
        assert get_model_changes(old, new) == {"d": False}

    def test_bytes_fields(self):
        """Bytes fields should be correctly compared."""
        old = MixedStruct(a=1, b="x", c=1.0, d=True, e=b"alpha")
        new = MixedStruct(a=1, b="x", c=1.0, d=True, e=b"beta")
        assert get_model_changes(old, new) == {"e": b"beta"}

    def test_none_field(self):
        """None values should be correctly compared."""

        class NullableStruct(Struct):
            v: Optional[int]
            w: Optional[str]

        old = NullableStruct(v=1, w=None)
        new = NullableStruct(v=1, w="not_null")
        assert get_model_changes(old, new) == {"w": "not_null"}

        same_old = NullableStruct(v=1, w=None)
        same_new = NullableStruct(v=1, w=None)
        assert get_model_changes(same_old, same_new) == {}

    def test_large_integers(self):
        """Large ints (not cached by Python) should be compared by value, not identity."""
        old = SimpleStruct(x=999999, y="x", z=1.0)
        new = SimpleStruct(x=999999, y="x", z=1.0)
        assert get_model_changes(old, new) == {}

    @pytest.mark.parametrize(
        "bad_old",
        [
            1,
            "string",
            NotAStruct(),
        ],
    )
    def test_first_arg_not_struct_raises_type_error(self, bad_old):
        """When ``old`` is not a Struct, TypeError is raised."""
        with pytest.raises(TypeError, match="Both arguments must be msgspec.Struct instances"):
            get_model_changes(bad_old, SimpleStruct(x=1, y="x", z=1.0))

    def test_nested_struct_changes(self):
        """Nested Struct changes should include the whole nested object as value."""
        inner_old = SimpleStruct(x=1, y="hello", z=3.14)
        inner_new = SimpleStruct(x=99, y="hello", z=3.14)
        old = NestedStruct(inner=inner_old, label="test")
        new = NestedStruct(inner=inner_new, label="test")
        diff = get_model_changes(old, new)
        assert diff == {"inner": inner_new}
        # The value should be the new struct's inner, not a sub-diff
        assert diff["inner"] is inner_new

    def test_same_nested_struct_ignored(self):
        """When both share the same nested struct object, there is no diff."""
        inner = SimpleStruct(x=1, y="hello", z=3.14)
        old = NestedStruct(inner=inner, label="test")
        new = NestedStruct(inner=inner, label="test")
        assert get_model_changes(old, new) == {}

    def test_label_changes_in_nested_struct(self):
        """Top-level field diff works alongside unchanged nested field."""
        inner = SimpleStruct(x=1, y="hello", z=3.14)
        old = NestedStruct(inner=inner, label="alpha")
        new = NestedStruct(inner=inner, label="beta")
        assert get_model_changes(old, new) == {"label": "beta"}

    def test_frozen_structs(self):
        """Frozen structs should work identically."""
        old = FreezableStruct(x=1, y="hello")
        new = FreezableStruct(x=2, y="hello")
        assert get_model_changes(old, new) == {"x": 2}

        same_old = FreezableStruct(x=1, y="hello")
        same_new = FreezableStruct(x=1, y="hello")
        assert get_model_changes(same_old, same_new) == {}

    def test_unset_vs_value(self):
        """UNSET and an actual value should be detected as different."""
        old = StructWithUnset(value=42)
        new = StructWithUnset()
        assert get_model_changes(old, new) == {"value": UNSET}

    def test_both_unset(self):
        """Two structs with both fields unset should have no diff."""
        old = StructWithUnset()
        new = StructWithUnset()
        assert get_model_changes(old, new) == {}

    def test_unset_vs_none(self):
        """UNSET and None should be detected as different when they are."""

        class HasOptional(Struct):
            v: Union[int, None, UnsetType] = UNSET

        old = HasOptional(v=None)
        new = HasOptional()
        assert get_model_changes(old, new) == {"v": UNSET}

    def test_float_precision(self):
        """Float comparison should use normal == semantics."""
        old = SimpleStruct(x=1, y="x", z=1.0 / 3.0)
        new = SimpleStruct(x=1, y="x", z=1.0 / 3.0)
        assert get_model_changes(old, new) == {}

    def test_emptystruct_changes_returns_empty(self):
        """Two empty structs always produce empty changes."""
        old = EmptyStruct()
        new = EmptyStruct()
        assert get_model_changes(old, new) == {}

    def test_different_struct_missing_field_raises_attribute_error(self):
        """Different struct types where old has fields new lacks → AttributeError."""

        class StructA(Struct):
            a: int
            b: str

        class StructB(Struct):
            a: int

        old = StructA(a=1, b="x")
        new = StructB(a=99)
        with pytest.raises(AttributeError):
            get_model_changes(old, new)

    def test_different_struct_with_superset_fields_works(self):
        """Different struct types where new has all fields from old should work."""

        class Base(Struct):
            a: int
            b: str

        class Extended(Struct):
            a: int
            b: str
            c: float

        old = Base(a=1, b="hello")
        new = Extended(a=1, b="hello", c=3.14)
        # All old fields exist on new and are equal → empty dict
        assert get_model_changes(old, new) == {}

        old2 = Base(a=1, b="hello")
        new2 = Extended(a=99, b="world", c=3.14)
        # Fields differ → only old's fields are compared
        assert get_model_changes(old2, new2) == {"a": 99, "b": "world"}
