from typing import Dict, List, Optional

import msgspec
import msgspec.msgpack
from msgspec import NODEFAULT, Struct, field

from msgspecerror.repair import load_msgpack_with_default


# --- Test Models ---
# Shared models matching test_repair_json.py

class Simple(Struct):
    """A simple struct with no default values for its fields."""
    a: int
    b: str


class WithDefaults(Struct):
    """A struct with explicit defaults and a default_factory."""
    a: int = 42
    b: str = "default"
    c: List[int] = field(default_factory=list)
    d: Optional[int] = None  # Optional[T] can be repaired to None


class Nested(Struct):
    """A nested struct where the inner struct has defaults."""
    s: WithDefaults
    d: int = 99


# --- Models for Deeply Nested Tests ---

class DeepStruct3(Struct):
    """Innermost struct with a default value, allowing repair."""
    z: int = 100


class DeepStruct2(Struct):
    """Middle nested struct."""
    y: DeepStruct3


class DeepStruct1(Struct):
    """Outermost struct for deep nesting tests."""
    x: DeepStruct2


class DeepDict(Struct):
    """Model for testing deeply nested dictionaries."""
    data: Dict[str, Dict[str, Optional[int]]]


class DeepList(Struct):
    """Model for testing deeply nested lists."""
    data: List[List[WithDefaults]]


class IntKeyDictModel(Struct):
    """Model for testing dictionary with non-string keys."""
    mapping: Dict[int, str]


# --- Models for Testing Field Aliases ---

class WithAliases(Struct):
    """A struct with aliased field names to test repair via encode names."""
    name: str = field(name="userName")
    age: int = field(name="userAge", default=18)


class NestedAlias(Struct):
    """A nested struct whose inner struct uses aliases."""
    inner: WithAliases
    tag: str = field(name="tagField", default="none")


class DeepAliasList(Struct):
    """Model for testing deeply nested aliased fields in a list."""
    items: List[WithAliases]


# --- Helper ---

def _msgpack(obj):
    """Encode a Python object to msgpack bytes."""
    return msgspec.msgpack.encode(obj)


# --- Test Suite ---

class TestLoadMsgpackWithDefault:
    """
    Test suite for the `load_msgpack_with_default` function.
    """

    # --- Basic Scenarios ---

    def test_valid_msgpack_no_errors(self):
        """Test decoding a valid msgpack that perfectly matches the model."""
        data = _msgpack({"a": 1, "b": "test"})
        result, errors = load_msgpack_with_default(data, Simple)
        assert result == Simple(a=1, b="test")
        assert errors == []

    def test_repair_fails_for_simple_type_without_default(self):
        """
        Repair fails for a simple type field that has no default value.
        """
        data = _msgpack({"a": "not-an-int", "b": "test"})
        result, errors = load_msgpack_with_default(data, Simple)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_repair_fails_for_missing_required_field_without_default(self):
        """
        Repair fails when a required field without a default is missing.
        """
        data = _msgpack({"b": "test"})
        result, errors = load_msgpack_with_default(data, Simple)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_repair_succeeds_with_field_default(self):
        """A field is repaired using its explicit default value from the model."""
        data = _msgpack({"a": "not-an-int", "b": "test"})
        result, errors = load_msgpack_with_default(data, WithDefaults)

        assert result == WithDefaults(a=42, b="test", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_repair_succeeds_for_optional_field(self):
        """An Optional[T] field with a type mismatch is repaired to None."""
        data = _msgpack({"a": 1, "d": "not-an-int"})
        result, errors = load_msgpack_with_default(data, WithDefaults)

        assert result == WithDefaults(a=1, b="default", c=[], d=None)
        assert len(errors) == 1
        assert errors[0].loc == ("d",)

    # --- Complex Scenarios: Multiple Errors & Deeply Nested Data ---

    def test_repair_multiple_errors_in_one_object(self):
        """Multiple errors in a single object are all repaired correctly."""
        data = _msgpack({
            "a": "wrong-type", "b": "good",
            "c": "not-a-list", "d": "not-an-int",
        })
        result, errors = load_msgpack_with_default(data, WithDefaults)

        expected = WithDefaults(a=42, b="good", c=[], d=None)
        assert result == expected

        assert len(errors) == 3
        error_locs = {e.loc for e in errors}
        assert ("a",) in error_locs
        assert ("c",) in error_locs
        assert ("d",) in error_locs

    def test_repair_deeply_nested_struct(self):
        """An error in a deeply nested struct can be repaired correctly."""
        data = _msgpack({"x": {"y": {"z": "not-an-int"}}})
        result, errors = load_msgpack_with_default(data, DeepStruct1)

        expected = DeepStruct1(x=DeepStruct2(y=DeepStruct3(z=100)))
        assert result == expected
        assert len(errors) == 1
        assert errors[0].loc == ("x", "y", "z")

    def test_repair_deeply_nested_dict_value(self):
        """An error in a deeply nested dictionary value can be repaired."""
        data = _msgpack({
            "data": {
                "level1": {"level2_ok": 123, "level2_bad": "not-an-int-or-null"},
            },
        })
        result, errors = load_msgpack_with_default(data, DeepDict)

        expected = DeepDict(data={"level1": {"level2_ok": 123, "level2_bad": None}})
        assert result == expected
        assert len(errors) == 1
        assert errors[0].loc == ("data", "level1", "level2_bad")

    def test_repair_dict_with_invalid_key_type(self):
        """A dictionary entry with an invalid key type is removed."""
        data = _msgpack({"mapping": {"1": "val1", "not-an-int": "val2", "3": "val3"}})
        result, errors = load_msgpack_with_default(data, IntKeyDictModel)

        expected = IntKeyDictModel(mapping={1: "val1", 3: "val3"})
        assert result == expected
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "not-an-int")

    def test_repair_deeply_nested_list_by_repairing_field(self):
        """Repair by fixing a field inside an object within a nested list."""
        data = _msgpack({
            "data": [
                [{"a": 1, "b": "ok"}],
                [{"a": "bad-int", "b": "also-ok"}],
            ],
        })
        result, errors = load_msgpack_with_default(data, DeepList)

        expected_data = [
            [WithDefaults(a=1, b="ok", c=[], d=None)],
            [WithDefaults(a=42, b="also-ok", c=[], d=None)],
        ]
        assert result.data == expected_data
        assert len(errors) == 1
        assert errors[0].loc == ('data', 1, 0, 'a')

    # --- Edge Cases and Root-Level Errors ---

    def test_malformed_msgpack_with_repairable_model(self):
        """Malformed msgpack with a default-constructible model."""
        data = b'\xc1'  # 0xc1 is never used in msgpack, guaranteed DecodeError
        result, errors = load_msgpack_with_default(data, WithDefaults)

        # Fall back to a default instance
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) > 0
        assert errors[0].loc == ()

    def test_malformed_msgpack_with_unrepairable_model(self):
        """Malformed msgpack when the model cannot be default-constructed."""
        data = b'\xc1'  # 0xc1 is never used in msgpack, guaranteed DecodeError
        result, errors = load_msgpack_with_default(data, Simple)

        assert result is NODEFAULT
        assert len(errors) > 0
        assert errors[0].loc == ()

    def test_wrong_root_type_with_repairable_model(self):
        """Decoding an array into a struct model that is default-constructible."""
        data = _msgpack([])  # array, but model is a struct
        result, errors = load_msgpack_with_default(data, WithDefaults)

        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) > 0
        assert errors[-1].loc == ()

    def test_wrong_root_type_for_list_model(self):
        """Decoding an object into a list model falls back to an empty list."""
        data = _msgpack({})  # object, but model is a list
        result, errors = load_msgpack_with_default(data, List[str])

        assert result == []
        assert len(errors) > 0
        assert errors[-1].loc == ()

    def test_wrong_root_type_for_int_model(self):
        """Decoding a string into an int model."""
        data = _msgpack("not a number")
        result, errors = load_msgpack_with_default(data, int)

        assert result is NODEFAULT
        assert len(errors) > 0
        assert errors[-1].loc == ()

    def test_wrong_root_type_for_str_model(self):
        """Decoding a number into a str model."""
        data = _msgpack(123)
        result, errors = load_msgpack_with_default(data, str)

        assert result is NODEFAULT
        assert len(errors) > 0
        assert errors[-1].loc == ()

    # --- Non-string Dict Keys (Msgpack-specific) ---

    def test_msgpack_int_key_in_struct_with_defaults(self):
        """
        Msgpack data with integer keys validated against a struct.
        The struct has defaults, so repair can fall back to a default instance.
        """
        # {1: 2, 3: 4} as msgpack — valid msgpack but keys are ints, not strings
        data = _msgpack({1: 2, 3: 4})
        result, errors = load_msgpack_with_default(data, WithDefaults)

        # Should fall back to default construction without crashing
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) > 0
        assert errors[0].msg == "Expected `str`, got `int` - at `key` in `$`"

    def test_msgpack_int_key_in_struct_without_defaults(self):
        """
        Msgpack data with integer keys validated against a struct
        that has required fields — repair cannot default-construct.
        """
        data = _msgpack({1: 2})
        result, errors = load_msgpack_with_default(data, Simple)

        # Should return NODEFAULT without crashing
        assert result is NODEFAULT
        assert len(errors) > 0

    # --- Field Alias Scenarios ---

    def test_valid_msgpack_with_aliases(self):
        """Valid msgpack using alias (encode) names decodes correctly."""
        data = _msgpack({"userName": "Alice", "userAge": 25})
        result, errors = load_msgpack_with_default(data, WithAliases)
        assert result == WithAliases(name="Alice", age=25)
        assert errors == []

    def test_repair_aliased_field_with_default(self):
        """
        Repair fills the default for an aliased field when the msgpack
        uses the encode name and the value has a wrong type.
        """
        data = _msgpack({"userName": "hello", "userAge": "not-an-int"})
        result, errors = load_msgpack_with_default(data, WithAliases)

        assert result == WithAliases(name="hello", age=18)
        assert len(errors) == 1
        assert errors[0].loc == ("userAge",)

    def test_repair_aliased_field_without_default(self):
        """
        Repair fails for an aliased field that has no default when the
        msgpack uses the encode name and the value is the wrong type.
        """
        data = _msgpack({"userName": 42, "userAge": 20})
        result, errors = load_msgpack_with_default(data, WithAliases)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("userName",)

    def test_repair_aliased_missing_required_field(self):
        """
        Repair fails when a required aliased field is entirely missing
        from the msgpack (using its encode name).
        """
        data = _msgpack({"userAge": 20})
        result, errors = load_msgpack_with_default(data, WithAliases)

        assert result is NODEFAULT
        assert len(errors) > 0

    def test_repair_nested_aliased_struct(self):
        """
        Repair works for a nested struct where an inner field uses an alias.
        """
        data = _msgpack({
            "inner": {"userName": "ok", "userAge": "bad"},
            "tagField": "hello",
        })
        result, errors = load_msgpack_with_default(data, NestedAlias)

        assert result == NestedAlias(
            inner=WithAliases(name="ok", age=18),
            tag="hello",
        )
        assert len(errors) == 1
        assert errors[0].loc == ("inner", "userAge")

    def test_repair_nested_aliased_struct_invalid_outer(self):
        """
        Repair works for a nested struct where an outer field uses an alias.
        """
        data = _msgpack({
            "inner": {"userName": "ok", "userAge": 20},
            "tagField": 999,
        })
        result, errors = load_msgpack_with_default(data, NestedAlias)

        assert result == NestedAlias(
            inner=WithAliases(name="ok", age=20),
            tag="none",
        )
        assert len(errors) == 1
        assert errors[0].loc == ("tagField",)

    def test_repair_aliased_field_in_list(self):
        """
        Repair works for an aliased field inside an object within a list.
        """
        data = _msgpack({
            "items": [
                {"userName": "a", "userAge": 1},
                {"userName": "b", "userAge": "bad"},
            ],
        })
        result, errors = load_msgpack_with_default(data, DeepAliasList)

        assert result == DeepAliasList(items=[
            WithAliases(name="a", age=1),
            WithAliases(name="b", age=18),
        ])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1, "userAge")

    def test_repair_aliased_field_deep_path_uses_encode_name(self):
        """Error location uses the JSON/msgpack encode name, not the field name."""
        data = _msgpack({"items": [{"userName": "x", "userAge": "bad"}]})
        result, errors = load_msgpack_with_default(data, DeepAliasList)

        assert result == DeepAliasList(items=[
            WithAliases(name="x", age=18),
        ])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 0, "userAge")
        assert "$.items[0].userAge" in errors[0].msg

    # --- Decoder as input ---

    def test_decoder_valid_msgpack_no_errors(self):
        """Passing a MsgpackDecoder with valid data works the same as passing a model."""
        decoder = msgspec.msgpack.Decoder(Simple)
        data = _msgpack({"a": 1, "b": "test"})
        result, errors = load_msgpack_with_default(data, decoder)
        assert result == Simple(a=1, b="test")
        assert errors == []

    def test_decoder_repair_succeeds_with_field_default(self):
        """Passing a MsgpackDecoder, repair uses the decoder's type."""
        decoder = msgspec.msgpack.Decoder(WithDefaults)
        data = _msgpack({"a": "not-an-int", "b": "test"})
        result, errors = load_msgpack_with_default(data, decoder)
        assert result == WithDefaults(a=42, b="test", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_decoder_repair_multiple_errors(self):
        """Passing a MsgpackDecoder with multiple errors."""
        decoder = msgspec.msgpack.Decoder(WithDefaults)
        data = _msgpack({"a": "wrong-type", "b": "good",
                         "c": "not-a-list", "d": "not-an-int"})
        result, errors = load_msgpack_with_default(data, decoder)
        expected = WithDefaults(a=42, b="good", c=[], d=None)
        assert result == expected
        assert len(errors) == 3

    def test_decoder_malformed_msgpack(self):
        """Passing a MsgpackDecoder with malformed msgpack."""
        decoder = msgspec.msgpack.Decoder(WithDefaults)
        data = b'\xc1'  # 0xc1 is never used in msgpack
        result, errors = load_msgpack_with_default(data, decoder)
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ()
