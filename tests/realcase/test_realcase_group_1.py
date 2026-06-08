"""
Real-case tests for Group 1: Type Mismatch & Unexpected Token.

Every test triggers a real msgspec error by decoding malformed data,
then validates that parse_msgspec_error correctly classifies it.
"""
from typing import List, Union

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
        assert err.ctx.expected == "int"
        assert err.ctx.got == "str"

    def test_str_got_int(self):
        """Expected `str`, got `int` - at `$.name`"""

        class Model(msgspec.Struct):
            name: str

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": 42}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("name",)
        assert err.ctx.expected == "str"
        assert err.ctx.got == "int"

    def test_bool_got_str(self):
        """Expected `bool`, got `str`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"hello"', type=bool)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "bool"
        assert err.ctx.got == "str"

    def test_int_got_null(self):
        """Expected `int`, got `null`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'null', type=int)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "int"
        assert err.ctx.got == "null"

    def test_array_got_str(self):
        """Expected `array`, got `str`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not_array"', type=List[int])
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "array"
        assert err.ctx.got == "str"

    def test_object_got_int(self):
        """Expected `object`, got `int`"""

        class Model(msgspec.Struct):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'42', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "object"
        assert err.ctx.got == "int"

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
        assert err.ctx.expected == "float"
        assert err.ctx.got == "str"

    def test_list_index_path(self):
        """Expected `int`, got `str` - at `$.items[1]`"""

        class Model(msgspec.Struct):
            items: List[int]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"items": [1, "bad", 3]}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("items", 1)
        assert err.ctx.expected == "int"
        assert err.ctx.got == "str"

    def test_bytes_got_int(self):
        """Expected `bytes`, got `int`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'42', type=bytes)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "bytes"
        assert err.ctx.got == "int"


class TestTokenTypeMismatchReal:
    """TOKEN_TYPE_MISMATCH — triggered by tag value / token type mismatches."""

    # ==================================================================
    # Trigger 1: Tag Value Mismatch (JSON)
    # ==================================================================

    def test_tag_field_str_got_int(self):
        """Expected `str` - at `$.type`"""

        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Union[Cat, Dog]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "fluffy", "type": 1}', type=Animal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == ("type",)
        assert err.ctx.expected == "str"

    def test_tag_field_int_got_str(self):
        """Invalid value 'xyz' - at `$.type`

        Tag value 'xyz' doesn't match any known type but is a string,
        so it goes through tag lookup first and produces INVALID_TAG_VALUE.
        """

        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Union[Cat, Dog]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "fluffy", "type": "xyz"}', type=Animal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_TAG_VALUE

    def test_tag_field_str_in_int_union(self):
        """Expected `str` - at `$.type`

        tag=True -> tag value is str (class name). Pass a number to
        trigger TOKEN_TYPE_MISMATCH.
        """

        class A(msgspec.Struct, tag=True):
            pass

        class B(msgspec.Struct, tag=True):
            pass

        AB = Union[A, B]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"type": 999}', type=AB)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == ("type",)
        assert err.ctx.expected == "str"

    # ==================================================================
    # Trigger 2: Map Key Mismatch (convert)
    #   convert_is_str_key: non-string dict key in msgspec.convert()
    #   when target type is Struct / TypedDict / dataclass
    # ==================================================================

    def test_convert_non_str_key_struct(self):
        """Expected `str` - at `key` in `$`

        msgspec.convert() with a Struct target rejects non-string keys
        because struct field names are always strings.
        """

        class Model(msgspec.Struct):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.convert({1: 10}, Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == ("...key",)
        assert err.ctx.expected == "str"

    # ==================================================================
    # Trigger 3: Token Type Mismatch (JSON - array-like tag)
    #   json_decode_cstr: array first element expected str tag, got int/bool/null
    #   Error message: "Expected `str` - at `$[0]`"
    # ==================================================================

    def test_array_like_str_tag_got_int(self):
        """Expected `str` - at `$[0]`

        Array-like tagged struct expects a str tag (class name) as the
        first element, but finds an int instead.
        """

        class Tagged(msgspec.Struct, tag=True, array_like=True):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[123, 42]', type=Tagged)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == (0,)
        assert err.ctx.expected == "str"

    def test_array_like_str_tag_got_bool(self):
        """Expected `str` - at `$[0]`

        Array-like tagged struct expects a str tag as the first element,
        but finds a JSON boolean instead.
        """

        class Tagged(msgspec.Struct, tag=True, array_like=True):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[true, 42]', type=Tagged)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == (0,)
        assert err.ctx.expected == "str"

    def test_array_like_str_tag_got_null(self):
        """Expected `str` - at `$[0]`

        Array-like tagged struct expects a str tag as the first element,
        but finds a JSON null instead.
        """

        class Tagged(msgspec.Struct, tag=True, array_like=True):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[null, 42]', type=Tagged)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == (0,)
        assert err.ctx.expected == "str"
