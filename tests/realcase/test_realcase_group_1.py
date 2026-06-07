"""
Real-case tests for Group 1: Type Mismatch & Unexpected Token.

Every test triggers a real msgspec error by decoding malformed data,
then validates that parse_msgspec_error correctly classifies it.
"""
import msgspec
import pytest

from msgspecerror import parse_msgspec_error
from msgspecerror.const import ErrorType


class TestTypeMismatchReal:
    """TYPE_MISMATCH — triggered by real type mismatches."""

    def test_int_got_str(self):
        """Expected `int`, got `str` - at `$.age`"""
        class Model(msgspec.Struct):
            age: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"age": "not_an_int"}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("age",)

    def test_str_got_int(self):
        """Expected `str`, got `int` - at `$.name`"""
        class Model(msgspec.Struct):
            name: str

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": 42}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("name",)

    def test_bool_got_str(self):
        """Expected `bool`, got `str`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"hello"', type=bool)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH

    def test_int_got_null(self):
        """Expected `int`, got `null`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'null', type=int)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH

    def test_array_got_str(self):
        """Expected `array`, got `str`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not_array"', type=list[int])
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH

    def test_object_got_int(self):
        """Expected `object`, got `int`"""
        class Model(msgspec.Struct):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'42', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH

    def test_float_got_str_deep(self):
        """Expected `float`, got `str` - at `$.inner.value`"""
        class Inner(msgspec.Struct):
            value: float

        class Outer(msgspec.Struct):
            inner: Inner

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"inner": {"value": "not_float"}}', type=Outer)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("inner", "value")

    def test_list_index_path(self):
        """Expected `int`, got `str` - at `$.items[1]`"""
        class Model(msgspec.Struct):
            items: list[int]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"items": [1, "bad", 3]}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("items", 1)

    def test_bytes_got_int(self):
        """Expected `bytes`, got `int`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'42', type=bytes)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH


class TestUnexpectedTokenReal:
    """UNEXPECTED_TOKEN — triggered by tag value type mismatches."""

    def test_tag_field_str_got_int(self):
        """Expected `str` - at `$.type`"""
        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Cat | Dog

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "fluffy", "type": 1}', type=Animal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNEXPECTED_TOKEN
        assert err.loc == ("type",)

    def test_tag_field_int_got_str(self):
        """Invalid value 'xyz' - at `$.type`

        Tag value 'xyz' doesn't match any known type but is a string,
        so it goes through tag lookup first and produces INVALID_TAG_VALUE.
        """
        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Cat | Dog

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "fluffy", "type": "xyz"}', type=Animal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_TAG_VALUE

    def test_tag_field_str_in_int_union(self):
        """Expected `str` - at `$.type`

        tag=True -> tag value is str (class name). Pass a number to
        trigger UNEXPECTED_TOKEN.
        """
        class A(msgspec.Struct, tag=True):
            pass

        class B(msgspec.Struct, tag=True):
            pass

        AB = A | B

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"type": 999}', type=AB)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNEXPECTED_TOKEN
        assert err.loc == ("type",)
