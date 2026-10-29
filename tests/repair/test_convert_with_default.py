"""Tests for `convert_with_default`, the in-memory entry of the `_with_default` family.

The repair engine itself is covered by the other modules under `tests/repair/`.
What matters here is the boundary of the new entry point:

1. it agrees with `load_json_with_default` on the same payload;
2. it repairs the caller's object in place, which `load_*` does to its own
   freshly decoded object;
3. it must not touch the input when the repair fails.
"""
import copy
from typing import Dict, List, Tuple

import msgspec
import pytest
from msgspec import NODEFAULT, Struct, ValidationError, convert

from msgspecerror import convert_with_default
from msgspecerror.const import ErrorType
from msgspecerror.repair import load_json_with_default

# --- Test Models ---


class Req(Struct):
    """`a` has no default, `b` does, so a minimal repair of `a` fails."""
    a: int
    b: str = ''


class Item(Struct):
    amount: int = 0


class Inventory(Struct):
    items: Dict[str, Item] = {}


class Pair(Struct):
    name: str = 'x'
    values: Tuple[int, str] = (0, '')


# `(model, data)` pairs that fail validation. The same data goes through both
# `convert_with_default` and `load_json_with_default`.
FAILING_CASES = [
    ('required-field', Req, {'a': 'bad'}),
    ('unknown-field-dict', Dict[str, int], {'k': 'x'}),
    ('dict-of-struct', Inventory, {'items': {'apple': {'amount': 'ABC'}}}),
    ('nested-dict', Dict[str, Dict[str, int]], {'a': {'b': 'x'}}),
    ('list-item', List[int], [1, 'x']),
    ('tuple-item', Tuple[int, str], [1, 2]),
    ('fixed-tuple-field', Pair, {'values': [1, 2]}),
]

ROOT_CASES = [
    ('str-to-int', int, 'abc'),
    ('list-to-dict', Dict[str, int], ['x']),
]


def assert_same_as_loader(model, data, guess_default):
    """`convert_with_default` and `load_json_with_default` must agree."""
    result, errors = convert_with_default(
        copy.deepcopy(data), model, guess_default=guess_default)
    expected_result, expected_errors = load_json_with_default(
        msgspec.json.encode(data), model, guess_default=guess_default)

    assert (result is NODEFAULT) == (expected_result is NODEFAULT)
    if result is not NODEFAULT:
        assert result == expected_result
    assert [e.loc for e in errors] == [e.loc for e in expected_errors]
    assert [e.type for e in errors] == [e.type for e in expected_errors]


class TestSuccess:
    def test_returns_the_model_and_no_error(self):
        result, errors = convert_with_default({'a': 1}, Req)
        assert result == Req(a=1)
        assert errors == []

    def test_does_not_modify_the_input(self):
        data = {'a': 1, 'b': 'x'}
        convert_with_default(data, Req)
        assert data == {'a': 1, 'b': 'x'}

    def test_guess_default_is_irrelevant_on_success(self):
        result, errors = convert_with_default({'a': 1}, Req, guess_default=True)
        assert result == Req(a=1)
        assert errors == []

    def test_decoder_is_accepted(self):
        result, errors = convert_with_default({'a': 1}, msgspec.json.Decoder(Req))
        assert result == Req(a=1)
        assert errors == []

    def test_msgpack_decoder_is_accepted(self):
        result, errors = convert_with_default({'a': 1}, msgspec.msgpack.Decoder(Req))
        assert result == Req(a=1)
        assert errors == []


class TestRepairInPlace:
    """A successful repair writes into the object the caller passed in."""

    def test_field_default_is_written_into_the_input(self):
        data = {'items': {'apple': {'amount': 'ABC'}}}
        result, errors = convert_with_default(data, Inventory)

        assert result == Inventory(items={'apple': Item(amount=0)})
        assert [e.loc for e in errors] == [('items', 'apple', 'amount')]
        # The caller's dict is the one that got repaired
        assert data == {'items': {'apple': {'amount': 0}}}

    def test_guessed_default_is_written_into_the_input(self):
        data = {'a': 'bad'}
        result, errors = convert_with_default(data, Req, guess_default=True)

        assert result == Req(a=0, b='')
        assert [e.loc for e in errors] == [('a',)]
        assert data == {'a': 0}

    def test_dict_entry_is_deleted_from_the_input(self):
        data = {'k': 'x', 'ok': 1}
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {'ok': 1}
        assert [e.loc for e in errors] == [('k',)]
        assert data == {'ok': 1}

    def test_list_item_is_deleted_from_the_input(self):
        data = [1, 'x']
        result, errors = convert_with_default(data, List[int])

        assert result == [1]
        assert [e.loc for e in errors] == [(1,)]
        assert data == [1]

    def test_nested_object_identity_is_kept(self):
        """The repair mutates the caller's nested object, it does not copy it."""
        inner = {'amount': 'ABC'}
        data = {'items': {'apple': inner}}
        convert_with_default(data, Inventory)

        assert data['items']['apple'] is inner
        assert inner == {'amount': 0}


class TestFailedRepair:
    """A repair that finds no default leaves the input alone."""

    def test_required_field_without_default(self):
        data = {'a': 'bad'}
        result, errors = convert_with_default(data, Req)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [('a',)]
        assert data == {'a': 'bad'}

    def test_root_error_does_not_modify_the_input(self):
        data = 'abc'
        result, errors = convert_with_default(data, int)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [()]
        assert data == 'abc'

    def test_root_container_error_does_not_modify_the_input(self):
        data = ['x']
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {}
        assert [e.loc for e in errors] == [()]
        assert data == ['x']


class TestMatchesLoadJsonWithDefault:
    """The two entries of the `_with_default` family share one repair engine."""

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('name, model, data', FAILING_CASES)
    def test_failing_cases(self, name, model, data, guess_default):
        assert_same_as_loader(model, data, guess_default)

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('name, model, data', ROOT_CASES)
    def test_root_cases(self, name, model, data, guess_default):
        assert_same_as_loader(model, data, guess_default)

    @pytest.mark.parametrize('name, model, data', [
        ('ok', Req, {'a': 1}),
        ('ok-dict', Dict[str, int], {'k': 1}),
    ])
    def test_success_cases(self, name, model, data):
        assert_same_as_loader(model, data, False)


class TestErrorDetails:
    def test_error_type_is_preserved(self):
        _, errors = convert_with_default({'k': 'x'}, Dict[str, int])
        assert errors[0].type is ErrorType.TYPE_MISMATCH
        assert errors[0].ctx.expected == 'int'
        assert errors[0].ctx.got == 'str'

    def test_constraint_error_is_preserved(self):
        from typing_extensions import Annotated

        model = Dict[str, Annotated[int, msgspec.Meta(ge=18)]]
        result, errors = convert_with_default({'k': 10}, model)

        assert result is NODEFAULT or result == {}
        assert errors[0].type is ErrorType.NUMERIC_CONSTRAINT
        assert errors[0].ctx.ge == 18

    def test_multiple_errors_are_all_reported(self):
        data = {'a': 'x', 'b': 'y'}
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {}
        assert [e.loc for e in errors] == [('a',), ('b',)]
        assert data == {}

    def test_invalid_type_hint_raises(self):
        """A wrong argument is a caller bug, not bad data."""
        with pytest.raises(TypeError):
            convert_with_default({}, 12345)

    def test_valid_data_is_not_reported_as_an_error(self):
        result, errors = convert_with_default({'a': 1}, Req)
        assert errors == []
        # The result is accepted by the model it claims to be
        assert convert(result, Req) == result

    def test_failed_result_is_never_a_partial_model(self):
        """A failed repair returns NODEFAULT, not a mix of repaired and raw data."""
        data = {'items': {'apple': {'amount': 'ABC'}}}
        result, _ = convert_with_default(data, Req)
        assert result is NODEFAULT
        with pytest.raises(ValidationError):
            convert(data, Req)


class TestDecoderOptions:
    """The repair does not fight the rules the caller validated with."""

    DATA = {"items": {"good": {"amount": "1"}, "bad": {"amount": "xx"}}}

    def test_strict_false_option(self):
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), Inventory, guess_default=True, strict=False)

        assert result == Inventory(items={"good": Item(amount=1), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [("items", "bad", "amount")]

    def test_without_the_option_both_entries_fail(self):
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), Inventory, guess_default=True)

        assert result == Inventory(items={"good": Item(amount=0), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [
            ("items", "good", "amount"), ("items", "bad", "amount")]

    def test_decoder_options_are_used(self):
        decoder = msgspec.json.Decoder(Inventory, strict=False)
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), decoder, guess_default=True)

        assert result == Inventory(items={"good": Item(amount=1), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [("items", "bad", "amount")]

    def test_explicit_option_overrides_the_decoder(self):
        decoder = msgspec.json.Decoder(Inventory, strict=False)
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), decoder, guess_default=True, strict=True)

        assert result == Inventory(items={"good": Item(amount=0), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [
            ("items", "good", "amount"), ("items", "bad", "amount")]

    def test_dec_hook_option(self):
        class Point:
            def __init__(self, value):
                self.value = value

        def dec_hook(typ, obj):
            if typ is Point:
                return Point(obj)
            raise NotImplementedError

        class Layer(Struct):
            p: Point
            n: int = 0

        class Doc(Struct):
            layers: Dict[str, Layer] = {}

        data = {"layers": {"one": {"p": [1, 2], "n": 1}, "bad": {"p": [3, 4], "n": "xx"}}}
        result, errors = convert_with_default(data, Doc, guess_default=True, dec_hook=dec_hook)

        assert len(result.layers) == 2
        assert result.layers["one"].p.value == [1, 2]
        assert result.layers["bad"].n == 0
        assert [e.loc for e in errors] == [("layers", "bad", "n")]
