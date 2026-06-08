from typing import Dict, List, Optional

import msgspec
import msgspec.json
from msgspec import NODEFAULT, Struct, field

from msgspecerror.repair import load_json_with_default


# --- Test Models ---

class Simple(Struct):
    """A simple struct with no default values for its fields."""
    a: int
    b: str


class WithDefaults(Struct):
    """A struct with explicit defaults and a default_factory."""
    a: int = 42
    b: str = "default"
    c: List[int] = field(default_factory=list)
    d: Optional[int] = None  # Optional[T] is equivalent to Union[T, None], which can be repaired to None


class Nested(Struct):
    """A nested struct where the inner struct has defaults and can be repaired."""
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
    data: Dict[str, Dict[str, Optional[int]]]  # Optional[int] can be repaired to None


class DeepList(Struct):
    """Model for testing deeply nested lists."""
    data: List[List[WithDefaults]]  # WithDefaults can be repaired


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


# --- Test Suite ---

class TestLoadJsonWithDefault:
    """
    Test suite for the `load_json_with_default` function.
    """

    # --- Basic Scenarios: Success, Repairable, and Unrepairable ---

    def test_valid_json_no_errors(self):
        """Test decoding a valid JSON that perfectly matches the model."""
        data = b'{"a": 1, "b": "test"}'
        result, errors = load_json_with_default(data, Simple)
        assert result == Simple(a=1, b="test")
        assert errors == []

    def test_repair_fails_for_simple_type_without_default(self):
        """
        Test that repair fails for a simple type field that has no default value.
        `Simple.a` is an `int` with no explicit default, so repair should fail.
        """
        data = b'{"a": "not-an-int", "b": "test"}'
        result, errors = load_json_with_default(data, Simple)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_repair_fails_for_missing_required_field_without_default(self):
        """
        Test that repair fails when a required field without a default is missing.
        """
        data = b'{"b": "test"}'  # Field 'a' is missing
        result, errors = load_json_with_default(data, Simple)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_repair_succeeds_with_field_default(self):
        """Test that a field is repaired using its explicit default value from the model."""
        data = b'{"a": "not-an-int", "b": "test"}'
        result, errors = load_json_with_default(data, WithDefaults)

        # 'a' should be repaired to its default value, 42
        assert result == WithDefaults(a=42, b="test", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_repair_succeeds_for_optional_field(self):
        """Test that an Optional[T] field with a type mismatch is repaired to None."""
        data = b'{"a": 1, "d": "not-an-int"}'
        result, errors = load_json_with_default(data, WithDefaults)

        # Optional[int] field 'd' should be repaired to None
        assert result == WithDefaults(a=1, b="default", c=[], d=None)
        assert len(errors) == 1
        assert errors[0].loc == ("d",)

    # --- Complex Scenarios: Multiple Errors & Deeply Nested Data ---

    def test_repair_multiple_errors_in_one_object(self):
        """Test that multiple errors in a single JSON object are all repaired correctly."""
        # 'a', 'c', and 'd' all have incorrect types
        data = b'{"a": "wrong-type", "b": "good", "c": "not-a-list", "d": "not-an-int"}'
        result, errors = load_json_with_default(data, WithDefaults)

        # a -> 42 (default), c -> [] (default_factory), d -> None (Optional)
        expected = WithDefaults(a=42, b="good", c=[], d=None)
        assert result == expected

        # The function repairs errors one by one, so all should be collected.
        assert len(errors) == 3
        error_locs = {e.loc for e in errors}
        assert ("a",) in error_locs
        assert ("c",) in error_locs
        assert ("d",) in error_locs

    def test_repair_deeply_nested_struct(self):
        """Test that an error in a deeply nested struct can be repaired correctly."""
        # The error is at s2.y.z
        data = b'{"x": {"y": {"z": "not-an-int"}}}'
        result, errors = load_json_with_default(data, DeepStruct1)

        # DeepStruct3.z has a default value of 100, so it can be repaired.
        expected = DeepStruct1(x=DeepStruct2(y=DeepStruct3(z=100)))
        assert result == expected
        assert len(errors) == 1
        assert errors[0].loc == ("x", "y", "z")

    def test_repair_deeply_nested_dict_value(self):
        """Test that an error in a deeply nested dictionary value can be repaired."""
        # The error is at data['level1']['level2_bad']
        data = b'{"data": {"level1": {"level2_ok": 123, "level2_bad": "not-an-int-or-null"}}}'
        result, errors = load_json_with_default(data, DeepDict)

        # The value's type is Optional[int], which can be repaired to None.
        expected = DeepDict(data={"level1": {"level2_ok": 123, "level2_bad": None}})
        assert result == expected
        assert len(errors) == 1
        assert errors[0].loc == ("data", "level1", "level2_bad")

    def test_repair_dict_with_invalid_key_type(self):
        """Test that a dictionary entry with an invalid key type is removed."""
        # The key "not-an-int" cannot be converted to the expected `int` type.
        data = b'{"mapping": {"1": "val1", "not-an-int": "val2", "3": "val3"}}'
        result, errors = load_json_with_default(data, IntKeyDictModel)

        # The key-value pair with the invalid key should be removed.
        expected = IntKeyDictModel(mapping={1: "val1", 3: "val3"})
        assert result == expected
        assert len(errors) == 1
        # The error location should point to the specific key that failed conversion.
        assert errors[0].loc == ("mapping", "not-an-int")

    def test_repair_deeply_nested_list_by_repairing_field(self):
        """Test repair by fixing a field inside an object within a nested list."""
        # The error is in the type of data[1][0].a
        data = b'{"data": [[{"a": 1, "b": "ok"}], [{"a": "bad-int", "b": "also-ok"}]]}'
        result, errors = load_json_with_default(data, DeepList)

        # WithDefaults.a has a default of 42, so the field can be repaired.
        expected_data = [
            [WithDefaults(a=1, b="ok", c=[], d=None)],
            [WithDefaults(a=42, b="also-ok", c=[], d=None)]  # 'a' is repaired
        ]
        assert result.data == expected_data
        assert len(errors) == 1
        assert errors[0].loc == ('data', 1, 0, 'a')

    # --- Edge Cases and Root-Level Errors ---

    def test_malformed_json_with_repairable_model(self):
        """Test handling of malformed JSON when the model has defaults for all fields."""
        data = b'{"a": 1, "b": "test'  # Incomplete JSON
        result, errors = load_json_with_default(data, WithDefaults)

        # The function should fall back to creating a default instance of the model.
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ()  # Root-level error
        assert "truncated" in errors[0].msg

    def test_malformed_json_with_unrepairable_model(self):
        """Test handling of malformed JSON when the model cannot be default-constructed."""
        data = b'{"a": 1, '  # Incomplete JSON
        result, errors = load_json_with_default(data, Simple)

        # Fallback to default construction will fail because `Simple` has required fields.
        assert result is NODEFAULT
        assert len(errors) > 0
        assert errors[0].loc == ()

    def test_wrong_root_type_with_repairable_model(self):
        """Test decoding a JSON array into a struct model that is default-constructible."""
        data = b'[]'  # JSON root is an array, but model is a struct
        result, errors = load_json_with_default(data, WithDefaults)

        # A root-level type error occurs, but WithDefaults can be default constructed.
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) > 0
        assert errors[-1].loc == ()  # The final error is at the root level.

    def test_wrong_root_type_for_list_model(self):
        """Test decoding a JSON object into a list model, which should fall back to an empty list."""
        data = b'{}'  # JSON root is an object, but model is a list
        result, errors = load_json_with_default(data, List[str])

        # A root-level type error occurs, but `List[str]` can be default-constructed to `[]`.
        assert result == []
        assert len(errors) > 0
        assert errors[-1].loc == ()
        assert "Expected `array`" in errors[-1].msg

    def test_wrong_root_type_for_int_model(self):
        """Test decoding a JSON string into an int model, which should fall back to 0."""
        data = b'"not a number"'  # JSON root is a string, but model is an int
        result, errors = load_json_with_default(data, int)

        assert result == NODEFAULT
        assert len(errors) > 0
        assert errors[-1].loc == ()
        assert "Expected `int`" in errors[-1].msg

    def test_wrong_root_type_for_str_model(self):
        """Test decoding a JSON number into a str model, which should fall back to an empty string."""
        data = b'123'  # JSON root is a number, but model is a string
        result, errors = load_json_with_default(data, str)

        assert result == NODEFAULT
        assert len(errors) > 0
        assert errors[-1].loc == ()

    # --- Field Alias Scenarios ---

    def test_valid_json_with_aliases(self):
        """Valid JSON using alias (encode) names decodes correctly."""
        data = b'{"userName": "Alice", "userAge": 25}'
        result, errors = load_json_with_default(data, WithAliases)
        assert result == WithAliases(name="Alice", age=25)
        assert errors == []

    def test_repair_aliased_field_with_default(self):
        """
        Repair fills in the default for an aliased field when the JSON
        uses the encode name and the value has a wrong type.
        """
        data = b'{"userName": "hello", "userAge": "not-an-int"}'
        result, errors = load_json_with_default(data, WithAliases)

        # 'age' is an aliased field (encode_name='userAge') with default 18
        assert result == WithAliases(name="hello", age=18)
        assert len(errors) == 1
        # The error location uses the JSON key (encode name), not the field name
        assert errors[0].loc == ("userAge",)

    def test_repair_aliased_field_without_default(self):
        """
        Repair fails for an aliased field that has no default when the
        JSON uses the encode name and the value is the wrong type.
        """
        data = b'{"userName": 42, "userAge": 20}'
        result, errors = load_json_with_default(data, WithAliases)

        # 'name' is aliased (encode_name='userName') and has no default
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("userName",)

    def test_repair_aliased_missing_required_field(self):
        """
        Repair fails when a required aliased field is entirely missing
        from the JSON (using its encode name).
        """
        data = b'{"userAge": 20}'
        result, errors = load_json_with_default(data, WithAliases)

        # 'name' (encode_name='userName') is required and missing.
        # Since 'name' has no default and is not Optional, repair cannot
        # construct a value.
        assert result is NODEFAULT
        assert len(errors) > 0

    def test_repair_nested_aliased_struct(self):
        """
        Repair works for a nested struct where an inner field uses an alias.
        """
        data = b'{"inner": {"userName": "ok", "userAge": "bad"}, "tagField": "hello"}'
        result, errors = load_json_with_default(data, NestedAlias)

        # 'userAge' should be repaired to its default 18
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
        data = b'{"inner": {"userName": "ok", "userAge": 20}, "tagField": 999}'
        result, errors = load_json_with_default(data, NestedAlias)

        # 'tag' (encode_name='tagField') has default "none"
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
        data = b'{"items": [{"userName": "a", "userAge": 1}, {"userName": "b", "userAge": "bad"}]}'
        result, errors = load_json_with_default(data, DeepAliasList)

        assert result == DeepAliasList(items=[
            WithAliases(name="a", age=1),
            WithAliases(name="b", age=18),  # age repaired to default
        ])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1, "userAge")

    def test_repair_aliased_field_deep_path_uses_encode_name(self):
        """Error location in a deeply nested aliased field uses the JSON encode name."""
        data = b'{"items": [{"userName": "x", "userAge": "bad"}]}'
        result, errors = load_json_with_default(data, DeepAliasList)

        assert result == DeepAliasList(items=[
            WithAliases(name="x", age=18),
        ])
        assert len(errors) == 1
        # loc segment is the encode name "userAge", not the field name "age"
        assert errors[0].loc == ("items", 0, "userAge")
        # The error message also uses the encode name in the JSON path
        assert "$.items[0].userAge" in errors[0].msg

    # --- Decoder as input ---

    def test_decoder_valid_json_no_errors(self):
        """Passing a JsonDecoder with valid data works the same as passing a model."""
        decoder = msgspec.json.Decoder(Simple)
        data = b'{"a": 1, "b": "test"}'
        result, errors = load_json_with_default(data, decoder)
        assert result == Simple(a=1, b="test")
        assert errors == []

    def test_decoder_repair_succeeds_with_field_default(self):
        """Passing a JsonDecoder, repair uses the decoder's type."""
        decoder = msgspec.json.Decoder(WithDefaults)
        data = b'{"a": "not-an-int", "b": "test"}'
        result, errors = load_json_with_default(data, decoder)
        assert result == WithDefaults(a=42, b="test", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_decoder_repair_multiple_errors(self):
        """Passing a JsonDecoder with multiple errors."""
        decoder = msgspec.json.Decoder(WithDefaults)
        data = b'{"a": "wrong-type", "b": "good", "c": "not-a-list", "d": "not-an-int"}'
        result, errors = load_json_with_default(data, decoder)
        expected = WithDefaults(a=42, b="good", c=[], d=None)
        assert result == expected
        assert len(errors) == 3

    def test_decoder_malformed_json(self):
        """Passing a JsonDecoder with malformed JSON."""
        decoder = msgspec.json.Decoder(WithDefaults)
        data = b'{"a": 1, "b": "test'  # Incomplete JSON
        result, errors = load_json_with_default(data, decoder)
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ()
