"""
Real-case tests for Group 6: Wrapped and Malformed Errors.

Every test triggers a real msgspec error by decoding malformed data,
then validates parse_msgspec_error correctly classifies it.

Covers:
- WRAPPED_ERROR (dec_hook, __post_init__)
- UNICODE_DECODE_ERROR
- JSON_MALFORMED
- MSGPACK_MALFORMED
- ENCODE_ERROR
"""
from typing import Dict

import msgspec
import msgspec.msgpack
import pytest

from msgspecerror import parse_msgspec_error, ErrorCtx
from msgspecerror.const import ErrorType


class TestWrappedErrorReal:
    """WRAPPED_ERROR — triggered by user code errors during decoding."""

    def test_dec_hook_error(self):
        """custom construction failed

        ValueError raised in dec_hook is wrapped as ValidationError
        with the original message preserved."""
        class Custom:
            def __init__(self, val):
                if val == "bad":
                    raise ValueError("custom construction failed")
                self.val = val

        def dec_hook(typ, val):
            if typ is Custom:
                return Custom(val)

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"bad"', type=Custom, dec_hook=dec_hook)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.WRAPPED_ERROR
        assert err.ctx == ErrorCtx()

    def test_post_init_error(self):
        """name cannot be empty

        ValueError raised in __post_init__ is wrapped."""
        class Validating(msgspec.Struct):
            name: str

            def __post_init__(self):
                if self.name == "":
                    raise ValueError("name cannot be empty")

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": ""}', type=Validating)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.WRAPPED_ERROR
        assert err.ctx == ErrorCtx()

    def test_dec_hook_type_error(self):
        """bad type for custom

        TypeError raised in dec_hook is wrapped."""
        class Custom2:
            def __init__(self, val):
                raise TypeError("bad type for custom")

        def dec_hook2(typ, val):
            if typ is Custom2:
                return Custom2(val)

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"x"', type=Custom2, dec_hook=dec_hook2)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.WRAPPED_ERROR
        assert err.ctx == ErrorCtx()

    def test_post_init_type_error(self):
        """type validation failed

        TypeError raised in __post_init__ is wrapped."""
        class ValidateType(msgspec.Struct):
            val: int

            def __post_init__(self):
                raise TypeError("type validation failed")

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"val": 42}', type=ValidateType)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.WRAPPED_ERROR
        assert err.ctx == ErrorCtx()

    def test_wrapped_error_nested(self):
        """nested construction error - at `$[...]`

        ValueError from dec_hook inside a dict field."""
        class Custom3:
            def __init__(self, val):
                raise ValueError("nested construction error")

        def dec_hook3(typ, val):
            if typ is Custom3:
                return Custom3(val)

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"field": "bad"}',
                type=Dict[str, Custom3],
                dec_hook=dec_hook3,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.WRAPPED_ERROR
        assert err.loc == ("...",)
        assert err.ctx == ErrorCtx()


class TestUnicodeDecodeErrorReal:
    """UNICODE_DECODE_ERROR — triggered by invalid UTF-8 bytes
    being decoded as str via msgpack."""

    def test_msgpack_invalid_utf8(self):
        """'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"""
        with pytest.raises(UnicodeDecodeError) as exc_info:
            msgspec.msgpack.decode(b'\xa2\xff\xfe', type=str)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNICODE_DECODE_ERROR
        assert err.ctx == ErrorCtx()

    def test_msgpack_invalid_utf8_nested(self):
        """'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"""
        with pytest.raises(UnicodeDecodeError) as exc_info:
            msgspec.msgpack.decode(
                b'\x81\xa1n\xa2\xff\xfe',
                type=Dict[str, str],
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNICODE_DECODE_ERROR
        assert err.ctx == ErrorCtx()


class TestJsonMalformedReal:
    """JSON_MALFORMED — triggered by syntactically invalid JSON."""

    def test_trailing_comma(self):
        """JSON is malformed: trailing comma in array (byte 5)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.json.decode(b'[1,2,]', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.JSON_MALFORMED
        assert err.ctx == ErrorCtx()

    def test_trailing_characters(self):
        """JSON is malformed: trailing characters (byte 4)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.json.decode(b'{} extra', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.JSON_MALFORMED
        assert err.ctx == ErrorCtx()

    def test_invalid_character(self):
        """JSON is malformed: object keys must be strings (byte 1)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.json.decode(b'{invalid}', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.JSON_MALFORMED
        assert err.ctx == ErrorCtx()

    def test_unclosed_brace_truncated(self):
        """Input data was truncated

        Truncated JSON is classified as DATA_TRUNCATED."""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.json.decode(b'{"a": 1', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.DATA_TRUNCATED
        assert err.ctx == ErrorCtx()

    def test_single_quote(self):
        """JSON is malformed: object keys must be strings (byte 1)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.json.decode(b"{'a': 1}", type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.JSON_MALFORMED
        assert err.ctx == ErrorCtx()

    def test_invalid_escape(self):
        """JSON is malformed: invalid escape character in string (byte 3)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.json.decode(b'"\\x"', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.JSON_MALFORMED
        assert err.ctx == ErrorCtx()


class TestMsgpackMalformedReal:
    """MSGPACK_MALFORMED — triggered by syntactically invalid msgpack data."""

    def test_invalid_opcode(self):
        """MessagePack data is malformed: invalid opcode '\\xc1' (byte 0)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.msgpack.decode(b'\xc1', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.MSGPACK_MALFORMED
        assert err.ctx == ErrorCtx()

    def test_trailing_characters(self):
        """MessagePack data is malformed: trailing characters (byte 1)"""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.msgpack.decode(b'\x00\xc1', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.MSGPACK_MALFORMED
        assert err.ctx == ErrorCtx()

    def test_truncated_data(self):
        """Input data was truncated

        Truncated msgpack data is also classified as DATA_TRUNCATED."""
        with pytest.raises(msgspec.DecodeError) as exc_info:
            msgspec.msgpack.decode(b'\xda\x00\x05', type=object)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.DATA_TRUNCATED
        assert err.ctx == ErrorCtx()


class TestEncodeErrorReal:
    """ENCODE_ERROR — triggered by encoding objects too large for msgpack.

    Note: This may raise OverflowError/MemoryError if the system can't
    allocate the large buffer, before msgspec can check the size.
    """

    @pytest.mark.skip
    def test_string_overflow(self):
        """Can't encode strings longer than 2**32 - 1

        Creating a string longer than 2**32 - 1 causes an error.
        On most systems creating a 4GB string causes MemoryError first,
        so this test documents behavior rather than asserting EncodeError."""
        import sys

        if sys.maxsize > 2**32:
            length = 2**32
            try:
                msgspec.msgpack.encode("x" * length)
            except msgspec.EncodeError:
                pass
            except (OverflowError, MemoryError):
                pass
