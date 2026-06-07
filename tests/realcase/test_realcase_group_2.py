"""
Real-case tests for Group 2: Structural Errors.

Every test triggers a real msgspec error by decoding malformed data,
then validates parse_msgspec_error correctly classifies it.
"""
import msgspec
import pytest

from msgspecerror import parse_msgspec_error
from msgspecerror.const import ErrorType


class TestMissingFieldReal:
    """MISSING_FIELD — triggered by missing required struct fields."""

    def test_single_missing_field(self):
        """Object missing required field `age`"""
        class User(msgspec.Struct):
            name: str
            age: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "alice"}', type=User)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.MISSING_FIELD
        assert err.loc == ("age",)

    def test_missing_field_nested(self):
        """Object missing required field `value` - at `$.inner`"""
        class Inner(msgspec.Struct):
            value: int

        class Outer(msgspec.Struct):
            inner: Inner

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"inner": {}}', type=Outer)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.MISSING_FIELD
        assert err.loc == ("inner", "value")

    def test_missing_multiple_fields_one_provided(self):
        """Object missing required field `b`"""
        class Full(msgspec.Struct):
            a: str
            b: int
            c: float

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"a": "x"}', type=Full)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.MISSING_FIELD
        assert err.loc == ("b",)


class TestUnknownFieldReal:
    """UNKNOWN_FIELD — triggered by extra fields in a struct
    with forbid_unknown_fields=True."""

    def test_simple_unknown_field(self):
        """Object contains unknown field `extra`"""
        class Strict(msgspec.Struct, forbid_unknown_fields=True):
            name: str

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"name": "alice", "extra": 1}', type=Strict
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNKNOWN_FIELD
        assert err.loc == ("extra",)

    def test_unknown_field_nested(self):
        """Object contains unknown field `unknown` - at `$.inner`"""
        class Inner(msgspec.Struct, forbid_unknown_fields=True):
            value: int

        class Outer(msgspec.Struct):
            inner: Inner

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"inner": {"value": 1, "unknown": "bad"}}', type=Outer
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNKNOWN_FIELD
        assert err.loc == ("inner", "unknown")

    def test_multiple_unknown_fields(self):
        """Object contains unknown field `x`"""
        class Strict(msgspec.Struct, forbid_unknown_fields=True):
            a: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"a": 1, "x": 2, "y": 3}', type=Strict
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNKNOWN_FIELD
        assert err.loc == ("x",)
