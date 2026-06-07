"""
Comprehensive tests for Group 6 (Wrapped Errors) in get_error_type().

Tests every Group 6 ErrorType member defined in const.py against raw error message
strings, covering all message variants and edge cases.
"""
import pytest

from msgspecerror.const import ErrorType
from msgspecerror.parse_ctx import ErrorCtx
from msgspecerror.parse_error import MsgspecError, get_error_type


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def check(msg, expected_type, expected_ctx=None):
    """Assert get_error_type returns correct type and ctx. loc is always ()."""
    result = get_error_type(msg)
    assert isinstance(result, MsgspecError), f"Expected MsgspecError, got {type(result)}"
    assert result.type == expected_type, (
        f"msg={msg!r}: expected {expected_type}, got {result.type}"
    )
    assert result.ctx == (expected_ctx if expected_ctx is not None else ErrorCtx()), (
        f"msg={msg!r}: ctx mismatch. expected={expected_ctx}, got={result.ctx}"
    )
    assert result.loc == (), "loc should be empty tuple from get_error_type"
    assert result.msg == msg, "msg should preserve the original input"


# ======================================================================
# Group 6: JSON Malformed
# ======================================================================

class TestJsonMalformed:
    """JSON_MALFORMED — syntactically invalid JSON."""

    @pytest.mark.parametrize("msg", [
        # Various JSON malformed reasons
        "JSON is malformed: invalid character (byte 1)",
        "JSON is malformed: trailing characters (byte 42)",
        "JSON is malformed: expected ',' or ']' (byte 10)",
        "JSON is malformed: expected ',' or '}' (byte 15)",
        "JSON is malformed: expected ':' (byte 20)",
        "JSON is malformed: expected '\"' (byte 5)",
        "JSON is malformed: trailing comma in array (byte 30)",
        "JSON is malformed: trailing comma in object (byte 25)",
        "JSON is malformed: object keys must be strings (byte 8)",
        "JSON is malformed: invalid number (byte 12)",
        "JSON is malformed: invalid escape character in string (byte 100)",
        "JSON is malformed: invalid character in unicode escape (byte 50)",
        "JSON is malformed: invalid utf-16 surrogate pair (byte 60)",
        "JSON is malformed: unexpected end of hex escape (byte 35)",
        "JSON is malformed: unexpected end of escaped utf-16 surrogate pair (byte 70)",
        "JSON is malformed: invalid escaped character (byte 80)",
        # Edge: byte 0 position
        "JSON is malformed: invalid character (byte 0)",
        # Edge: large byte position
        "JSON is malformed: invalid character (byte 999999)",
    ])
    def test_json_malformed(self, msg):
        check(msg, ErrorType.JSON_MALFORMED)


# ======================================================================
# Group 6: MsgPack Malformed
# ======================================================================

class TestMsgpackMalformed:
    """MSGPACK_MALFORMED — syntactically invalid MessagePack data."""

    @pytest.mark.parametrize("msg", [
        # Trailing characters variant
        "MessagePack data is malformed: trailing characters (byte 5)",
        "MessagePack data is malformed: trailing characters (byte 0)",
        "MessagePack data is malformed: trailing characters (byte 12345)",
        # Invalid opcode variant
        "MessagePack data is malformed: invalid opcode '\\xc1' (byte 3)",
        "MessagePack data is malformed: invalid opcode '\\xff' (byte 10)",
        "MessagePack data is malformed: invalid opcode '\\x00' (byte 1)",
    ])
    def test_msgpack_malformed(self, msg):
        check(msg, ErrorType.MSGPACK_MALFORMED)


# ======================================================================
# Group 6: Encode Error
# ======================================================================

class TestEncodeError:
    """ENCODE_ERROR — MsgPack encoding size limit violations."""

    @pytest.mark.parametrize("msg", [
        "Can't encode strings longer than 2**32 - 1",
        "Can't encode bytes-like objects longer than 2**32 - 1",
        "Can't encode arrays longer than 2**32 - 1",
        "Can't encode maps longer than 2**32 - 1",
        "Can't encode Ext objects with data longer than 2**32 - 1",
        # Typename variants (fmt-based: "Can't encode %s longer than 2**32 - 1")
        "Can't encode list longer than 2**32 - 1",
        "Can't encode dict longer than 2**32 - 1",
        "Can't encode str longer than 2**32 - 1",
    ])
    def test_encode_error(self, msg):
        check(msg, ErrorType.ENCODE_ERROR)


# ======================================================================
# Group 6: Unicode Decode Error
# ======================================================================

class TestUnicodeDecodeError:
    """UNICODE_DECODE_ERROR — invalid unicode in bytes."""

    @pytest.mark.parametrize("msg", [
        # Various codecs and byte positions
        "'utf-8' codec can't decode byte 0x80 in position 3: invalid start byte",
        "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
        "'utf-8' codec can't decode byte 0xc0 in position 10: invalid start byte",
        "'utf-16' codec can't decode byte 0x00 in position 5: truncated data",
        "'utf-16-le' codec can't decode byte 0xd8 in position 2: unexpected end of data",
        "'shift_jis' codec can't decode byte 0x81 in position 7: illegal multibyte sequence",
        "'gbk' codec can't decode byte 0xfe in position 1: illegal multibyte sequence",
        "'ascii' codec can't decode byte 0x80 in position 0: ordinal not in range(128)",
        "'latin-1' codec can't decode byte 0x81 in position 2: invalid start byte",
        # Edge: position 0
        "'utf-8' codec can't decode byte 0x80 in position 0: invalid start byte",
        # Edge: large position
        "'utf-8' codec can't decode byte 0x80 in position 999999: invalid start byte",
    ])
    def test_unicode_decode_error(self, msg):
        check(msg, ErrorType.UNICODE_DECODE_ERROR)

    def test_unicode_decode_error_no_false_positive(self):
        """Verify UNICODE_DECODE_ERROR pattern does not match unrelated messages."""
        msg = "expected codec parameter - at $"
        result = get_error_type(msg)
        assert result.type != ErrorType.UNICODE_DECODE_ERROR
        assert result.type == ErrorType.WRAPPED_ERROR

        msg = "Can't decode this message"
        result = get_error_type(msg)
        assert result.type != ErrorType.UNICODE_DECODE_ERROR


# ======================================================================
# Group 6: Wrapped Error (Catch-all)
# ======================================================================

class TestWrappedErrorGroup6:
    """WRAPPED_ERROR is still the catch-all for messages that don't match
    any specific Group 6 pattern (or other groups).
    These are typically user-code errors from dec_hook, __post_init__, etc."""

    @pytest.mark.parametrize("msg", [
        # User-generated errors from __post_init__, dec_hook, etc.
        "passwords cannot be the same - at $",
        "Custom validation failed",
        "ValueError: invalid literal for int()",
        # Random strings that don't match any known pattern
        "Some completely unexpected error",
        "",
        # Expected patterns without specifics (fall-through cases)
        "Expected `str`" + " " * 100,
        # Definition-time error messages should fall through (they don't
        # happen during decode/encode)
        "All base classes must be types",
        "__annotations__ must be a dict",
        "Cannot set eq=False and order=True",
        "default_factory must be callable",
        "dec_hook must be callable",
        "`tag` must be a `str` or an `int`",
        "`rename` must be a str, callable, or mapping",
        "Must be called with a struct type or instance",
        "NoDefaultType takes no arguments",
        "enum.EnumMeta should be a type",
        "dataclasses with `InitVar` fields are not supported",
        # Generic user-code messages
        "the password must be at least 8 characters",
        "user must be an admin",
        "cannot set field",
    ])
    def test_wrapped_error(self, msg):
        check(msg, ErrorType.WRAPPED_ERROR)

    def test_expected_int_no_context(self):
        result = get_error_type("Expected `int`")
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_expected_float_no_context(self):
        result = get_error_type("Expected `float`")
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_expected_decimal_no_context(self):
        result = get_error_type("Expected `decimal`")
        assert result.type == ErrorType.WRAPPED_ERROR


# ======================================================================
# Edge Cases — Ambiguous or Malformed Input for Group 6
# ======================================================================

class TestGroup6EdgeCases:
    """Resilience against edge cases in Group 6 parsing."""

    def test_json_malformed_without_colon(self):
        msg = "JSON is malformed"
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_msgpack_malformed_without_colon(self):
        msg = "MessagePack data is malformed"
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_encode_error_no_match_for_cant_encode_other_context(self):
        msg = "Can't decode this message"
        result = get_error_type(msg)
        assert result.type != ErrorType.ENCODE_ERROR

    def test_unicode_decode_error_similar_but_wrong(self):
        msg = "utf-8 codec error: can't decode some bytes"
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_unicode_decode_without_position(self):
        msg = "'utf-8' codec can't decode byte 0x80"
        result = get_error_type(msg)
        assert result.type == ErrorType.UNICODE_DECODE_ERROR

    def test_json_malformed_missing_byte(self):
        msg = "JSON is malformed: unexpected token"
        result = get_error_type(msg)
        assert result.type == ErrorType.JSON_MALFORMED

    def test_msgpack_missing_opcode_detail(self):
        msg = "MessagePack data is malformed: invalid opcode (byte 5)"
        result = get_error_type(msg)
        assert result.type == ErrorType.MSGPACK_MALFORMED

    def test_backtick_message_no_must_be(self):
        """Backtick-quoted messages without 'must be' should not match anything special."""
        msg = "`value` is not valid"
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR


# ======================================================================
# Edge Cases — Priority and Overlap Prevention
# ======================================================================

class TestPriorityOverlap:
    """Ensure specific Group 6 patterns don't interfere with Groups 1-5."""

    def test_group4_invalid_uuid_precedes_group6(self):
        check("Invalid UUID - at `$.id`", ErrorType.INVALID_UUID)

    def test_group5_out_of_range_precedes_group6(self):
        check("Timestamp is out of range - at `$.ts`", ErrorType.TIMESTAMP_OUT_OF_RANGE)

    def test_group1_type_mismatch_precedes_group6(self):
        check("Expected `int`, got `str`", ErrorType.TYPE_MISMATCH)

    def test_group2_missing_field_precedes_group6(self):
        check("Object missing required field `id`", ErrorType.MISSING_FIELD)

    def test_group3_numeric_constraint_precedes_group6(self):
        check("Expected `int` >= 0", ErrorType.NUMERIC_CONSTRAINT, ErrorCtx(ge=0))
