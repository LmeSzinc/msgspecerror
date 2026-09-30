from enum import Enum
from typing import (AbstractSet, Collection, Dict, FrozenSet, List, Mapping, MutableMapping,
                    MutableSet, Optional, Sequence, Set, Tuple)

import msgspec
import msgspec.msgpack
import pytest
from msgspec import NODEFAULT, Struct, field
from typing_extensions import Annotated

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
    data: Dict[str, Dict[str, Optional[int]]]  # Optional[int] can be repaired to None with guess_default=True


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

        # The failing key-value pair is deleted, the valid pairs are kept.
        expected = DeepDict(data={"level1": {"level2_ok": 123}})
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


# --- Models for Testing guess_default ---

class MyEnum(Enum):
    """An enum type, enum values are never guessed."""
    A = 1
    B = 2


class NoDefaultStruct(Struct):
    """A struct that cannot be default-constructed."""
    x: int


class IntValueMap(Struct):
    """A dict with int values, a failing value can be guessed as 0."""
    mapping: Dict[str, int] = {}


class EnumValueMap(Struct):
    """A dict with enum values, which have no guessable default."""
    mapping: Dict[str, MyEnum] = {}


class ConstrainedValueMap(Struct):
    """A dict with constrained int values, the guessed 0 fails `ge=18`."""
    mapping: Dict[str, Annotated[int, msgspec.Meta(ge=18)]] = {}


class StructValueMap(Struct):
    """A dict with struct values that cannot be default-constructed."""
    mapping: Dict[str, NoDefaultStruct] = {}


class IntItemList(Struct):
    """A list with int items, a failing item can be guessed as 0."""
    items: List[int] = []


class EnumItemList(Struct):
    """A list with enum items, which have no guessable default."""
    items: List[MyEnum] = []


class TupleModel(Struct):
    """A tuple whose item types differ per index."""
    t: Tuple[int, str] = (0, "")


class TestGuessDefaultRepair:
    """Tests for the `guess_default` option, which controls how failing values are repaired."""

    def test_dict_value_failure_deletes_pair_by_default(self):
        """By default, a failing dict value is deleted, the valid pairs are kept."""
        data = _msgpack({"mapping": {"ok": 1, "bad": "not-an-int", "ok2": 3}})
        result, errors = load_msgpack_with_default(data, IntValueMap)

        assert result == IntValueMap(mapping={"ok": 1, "ok2": 3})
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "bad")

    def test_dict_value_failure_becomes_guessed_default(self):
        """With guess_default=True, a failing dict value becomes the guessed default."""
        data = _msgpack({"mapping": {"ok": 1, "bad": "not-an-int", "ok2": 3}})
        result, errors = load_msgpack_with_default(data, IntValueMap, guess_default=True)

        assert result == IntValueMap(mapping={"ok": 1, "bad": 0, "ok2": 3})
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "bad")

    def test_dict_value_unguessable_type_is_deleted(self):
        """An enum value has no safe default, so the pair is deleted even when guessing."""
        data = _msgpack({"mapping": {"ok": 1, "bad": 3}})
        result, errors = load_msgpack_with_default(data, EnumValueMap, guess_default=True)

        assert result == EnumValueMap(mapping={"ok": MyEnum.A})
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "bad")

    def test_dict_value_invalid_guess_is_deleted(self):
        """The guessed 0 is rejected by `ge=18`, so the pair is deleted."""
        data = _msgpack({"mapping": {"ok": 20, "bad": "not-an-int"}})
        result, errors = load_msgpack_with_default(data, ConstrainedValueMap, guess_default=True)

        assert result == ConstrainedValueMap(mapping={"ok": 20})
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "bad")

    def test_dict_value_struct_without_defaults_is_deleted(self):
        """A struct value that can't be default-constructed is deleted instead of being `{}`."""
        data = _msgpack({"mapping": {"ok": {"x": 1}, "bad": "not-a-struct"}})
        result, errors = load_msgpack_with_default(data, StructValueMap, guess_default=True)

        assert result == StructValueMap(mapping={"ok": NoDefaultStruct(x=1)})
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "bad")

    def test_dict_key_failure_deletes_pair_when_guessing(self):
        """Dict keys are never guessed, the failing pair is deleted in both modes."""
        data = _msgpack({"mapping": {"1": "val1", "not-an-int": "val2", "3": "val3"}})
        result, errors = load_msgpack_with_default(data, IntKeyDictModel, guess_default=True)

        assert result == IntKeyDictModel(mapping={1: "val1", 3: "val3"})
        assert len(errors) == 1
        assert errors[0].loc == ("mapping", "not-an-int")

    def test_list_item_failure_deletes_item_by_default(self):
        """By default, a failing list item is deleted, the valid items are kept."""
        data = _msgpack({"items": [1, "not-an-int", 3]})
        result, errors = load_msgpack_with_default(data, IntItemList)

        assert result == IntItemList(items=[1, 3])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)

    def test_list_item_failure_becomes_guessed_default(self):
        """With guess_default=True, a failing list item becomes the guessed default."""
        data = _msgpack({"items": [1, "not-an-int", 3]})
        result, errors = load_msgpack_with_default(data, IntItemList, guess_default=True)

        assert result == IntItemList(items=[1, 0, 3])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)

    def test_list_item_unguessable_type_is_deleted(self):
        """An enum item has no safe default, so it is deleted even when guessing."""
        data = _msgpack({"items": [1, 3]})
        result, errors = load_msgpack_with_default(data, EnumItemList, guess_default=True)

        assert result == EnumItemList(items=[MyEnum.A])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)

    def test_tuple_item_is_guessed_by_index(self):
        """A tuple item is guessed with the type at the failing index."""
        data = _msgpack({"t": ["not-an-int", 2]})
        result, errors = load_msgpack_with_default(data, TupleModel, guess_default=True)

        assert result == TupleModel(t=(0, ""))
        assert len(errors) == 2
        assert errors[0].loc == ("t", 0)
        assert errors[1].loc == ("t", 1)

    def test_struct_field_without_default_is_guessed(self):
        """With guess_default=True, a field with no default is repaired by a guessed value."""
        data = _msgpack({"a": "bad-int", "b": "good"})
        result, errors = load_msgpack_with_default(data, Simple, guess_default=True)

        assert result == Simple(a=0, b="good")
        assert len(errors) == 1
        assert errors[0].loc == ("a",)


# --- Models for Testing Container Types ---

class ListModel(Struct):
    """A list field."""
    v: List[int]


class SequenceModel(Struct):
    """An abstract Sequence field, msgspec decodes it as a list."""
    v: Sequence[int]


class CollectionModel(Struct):
    """An abstract Collection field, msgspec decodes it as a list."""
    v: Collection[int]


class SetModel(Struct):
    """A set field, msgspec decodes it as a list."""
    v: Set[int]


class MutableSetModel(Struct):
    """A mutable set field."""
    v: MutableSet[int]


class AbstractSetModel(Struct):
    """An abstract set field."""
    v: AbstractSet[int]


class FrozenSetModel(Struct):
    """A frozenset field."""
    v: FrozenSet[int]


class VariadicTupleModel(Struct):
    """A variable-length tuple field."""
    v: Tuple[int, ...]


class DictOfSetModel(Struct):
    """A dict whose values are sets."""
    v: Dict[str, Set[int]]


class DictModel(Struct):
    """A dict field."""
    v: Dict[str, int]


class MappingModel(Struct):
    """An abstract Mapping field, msgspec decodes it as a dict."""
    v: Mapping[str, int]


class MutableMappingModel(Struct):
    """An abstract MutableMapping field."""
    v: MutableMapping[str, int]


class IntKeyMappingModel(Struct):
    """A Mapping with int keys."""
    v: Mapping[int, str]


class FixedTupleModel(Struct):
    """A fixed-length tuple whose item types differ per index."""
    v: Tuple[str, WithDefaults]


class TestContainerRepair:
    """Failing items of list-like containers and values of dict-like containers."""

    @pytest.mark.parametrize("model, deleted, guessed", [
        (ListModel, [1, 3], [1, 0, 3]),
        (SequenceModel, [1, 3], [1, 0, 3]),
        (CollectionModel, [1, 3], [1, 0, 3]),
        (SetModel, {1, 3}, {0, 1, 3}),
        (MutableSetModel, {1, 3}, {0, 1, 3}),
        (AbstractSetModel, {1, 3}, {0, 1, 3}),
        (FrozenSetModel, frozenset({1, 3}), frozenset({0, 1, 3})),
        (VariadicTupleModel, (1, 3), (1, 0, 3)),
    ])
    def test_item_failure(self, model, deleted, guessed):
        """A failing item is deleted by default, or replaced by the guessed default."""
        data = _msgpack({"v": [1, "bad", 3]})

        result, errors = load_msgpack_with_default(data, model)
        assert result == model(v=deleted)
        assert len(errors) == 1
        assert errors[0].loc == ("v", 1)

        result, errors = load_msgpack_with_default(data, model, guess_default=True)
        assert result == model(v=guessed)
        assert len(errors) == 1
        assert errors[0].loc == ("v", 1)

    @pytest.mark.parametrize("model, deleted, guessed", [
        (DictModel, {"ok": 1}, {"ok": 1, "bad": 0}),
        (MappingModel, {"ok": 1}, {"ok": 1, "bad": 0}),
        (MutableMappingModel, {"ok": 1}, {"ok": 1, "bad": 0}),
    ])
    def test_dict_value_failure(self, model, deleted, guessed):
        """A failing value of a dict-like container is deleted, or replaced by the guessed default."""
        data = _msgpack({"v": {"ok": 1, "bad": "x"}})

        result, errors = load_msgpack_with_default(data, model)
        assert result == model(v=deleted)
        assert len(errors) == 1
        assert errors[0].loc == ("v", "bad")

        result, errors = load_msgpack_with_default(data, model, guess_default=True)
        assert result == model(v=guessed)
        assert len(errors) == 1
        assert errors[0].loc == ("v", "bad")

    def test_dict_key_failure_in_mapping(self):
        """A failing key of a Mapping is deleted, keys are never guessed."""
        data = _msgpack({"v": {"1": "a", "bad": "b"}})

        result, errors = load_msgpack_with_default(data, IntKeyMappingModel, guess_default=True)
        assert result == IntKeyMappingModel(v={1: "a"})
        assert len(errors) == 1
        assert errors[0].loc == ("v", "bad")

    def test_item_failure_in_nested_set(self):
        """A failing item inside a nested set keeps the rest of the data untouched."""
        data = _msgpack({"v": {"ok": [1], "bad": [1, "x"]}})

        result, errors = load_msgpack_with_default(data, DictOfSetModel)
        assert result == DictOfSetModel(v={"ok": {1}, "bad": {1}})
        assert len(errors) == 1
        assert errors[0].loc == ("v", "bad", 1)

        result, errors = load_msgpack_with_default(data, DictOfSetModel, guess_default=True)
        assert result == DictOfSetModel(v={"ok": {1}, "bad": {0, 1}})
        assert len(errors) == 1
        assert errors[0].loc == ("v", "bad", 1)

    def test_fixed_tuple_uses_item_type_at_index(self):
        """A struct inside a fixed-length tuple is repaired with the item type at that index."""
        data = _msgpack({"v": ["ok", {"a": "bad", "b": "good"}]})

        result, errors = load_msgpack_with_default(data, FixedTupleModel)
        assert result == FixedTupleModel(v=("ok", WithDefaults(a=42, b="good")))
        assert len(errors) == 1
        assert errors[0].loc == ("v", 1, "a")
