"""
Comprehensive tests for Group 6 (Wrapped Errors) in get_error_type().

Tests every Group 6 ErrorType member defined in const.py against raw error message
strings, covering all message variants, edge cases, combinatorial variations,
and false-positive prevention.
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
        # === All documented JSON malformed reasons ===
        # See const.py ErrorType.JSON_MALFORMED
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
        # === Edge: byte positions ===
        "JSON is malformed: invalid character (byte 0)",
        "JSON is malformed: invalid character (byte 999999)",
        "JSON is malformed: invalid character (byte 1) at root",
        # === Unicode in reason text ===
        "JSON is malformed: invalid éscape (byte 5)",
        "JSON is malformed: 无效字符 (byte 1)",
        "JSON is malformed: invalid `backtick` char (byte 3)",
        # === Numbers in reason text ===
        "JSON is malformed: expected ',' or ']' (byte 1234567890)",
        "JSON is malformed: expected ',' or ']' (byte 0)",
        # === Zero-length edge: no space after colon ===
        "JSON is malformed:invalid character (byte 1)",
        "JSON is malformed: (byte 0)",
    ])
    def test_json_malformed(self, msg):
        check(msg, ErrorType.JSON_MALFORMED)

    def test_json_malformed_partial_does_not_match(self):
        """Messages containing 'JSON is malformed' without ':' should not match."""
        for msg in [
            "JSON is malformed",
            "JSON is malformed extra text",
        ]:
            result = get_error_type(msg)
            assert result.type != ErrorType.JSON_MALFORMED, f"{msg!r} should not match JSON_MALFORMED"
            assert result.type == ErrorType.WRAPPED_ERROR

    def test_json_malformed_not_case_sensitive(self):
        """Lowercase 'json' should not match (msgspec uses title case)."""
        msg = "json is malformed: invalid character (byte 1)"
        result = get_error_type(msg)
        assert result.type != ErrorType.JSON_MALFORMED
        assert result.type == ErrorType.WRAPPED_ERROR


# ======================================================================
# Group 6: MsgPack Malformed
# ======================================================================

class TestMsgpackMalformed:
    """MSGPACK_MALFORMED — syntactically invalid MessagePack data."""

    @pytest.mark.parametrize("msg", [
        # === Trailing characters ===
        "MessagePack data is malformed: trailing characters (byte 5)",
        "MessagePack data is malformed: trailing characters (byte 0)",
        "MessagePack data is malformed: trailing characters (byte 12345)",
        "MessagePack data is malformed: trailing characters (byte 1) at root",
        "MessagePack data is malformed: trailing characters (byte 4294967295)",
        # === Invalid opcode variants ===
        "MessagePack data is malformed: invalid opcode '\\xc1' (byte 3)",
        "MessagePack data is malformed: invalid opcode '\\xff' (byte 10)",
        "MessagePack data is malformed: invalid opcode '\\x00' (byte 1)",
        "MessagePack data is malformed: invalid opcode '\\xc0' (byte 0)",
        "MessagePack data is malformed: invalid opcode '\\x7f' (byte 2)",
        "MessagePack data is malformed: invalid opcode '\\x81' (byte 100)",
        # === Unicode in opcode? msgspec always uses \\xNN format ===
        "MessagePack data is malformed: trailing characters with unicode ü (byte 3)",
    ])
    def test_msgpack_malformed(self, msg):
        check(msg, ErrorType.MSGPACK_MALFORMED)

    def test_msgpack_malformed_partial_does_not_match(self):
        """Messages without colon after 'is malformed' should not match."""
        for msg in [
            "MessagePack data is malformed",
            "MessagePack data is malformed with extra text",
            "MessagePack data is malformed? (byte 5)",
        ]:
            result = get_error_type(msg)
            assert result.type != ErrorType.MSGPACK_MALFORMED, f"{msg!r} should not match"
            assert result.type == ErrorType.WRAPPED_ERROR

    def test_json_msgpack_no_confusion(self):
        """JSON malformed should not match MsgPack and vice versa."""
        assert get_error_type("JSON is malformed: invalid character (byte 1)").type == ErrorType.JSON_MALFORMED
        assert get_error_type("MessagePack data is malformed: invalid opcode '\\xc1' (byte 3)").type == ErrorType.MSGPACK_MALFORMED


# ======================================================================
# Group 6: Encode Error
# ======================================================================

class TestEncodeError:
    """ENCODE_ERROR — MsgPack encoding size limit violations."""

    @pytest.mark.parametrize("msg", [
        # === All standard MsgPack types ===
        "Can't encode strings longer than 2**32 - 1",
        "Can't encode bytes-like objects longer than 2**32 - 1",
        "Can't encode arrays longer than 2**32 - 1",
        "Can't encode maps longer than 2**32 - 1",
        "Can't encode Ext objects with data longer than 2**32 - 1",
        # === Typename variants (fmt-based) ===
        "Can't encode list longer than 2**32 - 1",
        "Can't encode dict longer than 2**32 - 1",
        "Can't encode str longer than 2**32 - 1",
        "Can't encode bytes longer than 2**32 - 1",
        "Can't encode int longer than 2**32 - 1",
        "Can't encode float longer than 2**32 - 1",
        "Can't encode bool longer than 2**32 - 1",
        "Can't encode tuple longer than 2**32 - 1",
        "Can't encode set longer than 2**32 - 1",
        "Can't encode frozenset longer than 2**32 - 1",
        "Can't encode bytearray longer than 2**32 - 1",
        "Can't encode memoryview longer than 2**32 - 1",
        "Can't encode Decimal longer than 2**32 - 1",
        "Can't encode UUID longer than 2**32 - 1",
        "Can't encode datetime longer than 2**32 - 1",
        # === Edge: custom user types ===
        "Can't encode CustomType longer than 2**32 - 1",
        "Can't encode MyModel longer than 2**32 - 1",
    ])
    def test_encode_error(self, msg):
        check(msg, ErrorType.ENCODE_ERROR)

    def test_encode_error_false_positive(self):
        """'Can't encode ' at start is required; similar prefixes should not match."""
        for msg in [
            "Can't decode this message",
            "Can't parse input",
            "Can't find field",
            "Can't process data",
            # These don't start with exactly 'Can't encode '
            "Can't encode: something went wrong",  # 'encode:' not 'encode '
            "Can't encode-other",  # dash instead of space
        ]:
            result = get_error_type(msg)
            assert result.type != ErrorType.ENCODE_ERROR, f"{msg!r} should not match ENCODE_ERROR"

    def test_encode_error_exact_prefix(self):
        """'Can't encode ' (with trailing space) is required."""
        msg = "Can't encode"  # no trailing space
        result = get_error_type(msg)
        assert result.type != ErrorType.ENCODE_ERROR
        assert result.type == ErrorType.WRAPPED_ERROR


# ======================================================================
# Group 6: Unicode Decode Error
# ======================================================================

class TestUnicodeDecodeError:
    """UNICODE_DECODE_ERROR — invalid unicode in bytes."""

    @pytest.mark.parametrize("msg", [
        # === Various codecs ===
        "'utf-8' codec can't decode byte 0x80 in position 3: invalid start byte",
        "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
        "'utf-8' codec can't decode byte 0xc0 in position 10: invalid start byte",
        "'utf-16' codec can't decode byte 0x00 in position 5: truncated data",
        "'utf-16-le' codec can't decode byte 0xd8 in position 2: unexpected end of data",
        "'shift_jis' codec can't decode byte 0x81 in position 7: illegal multibyte sequence",
        "'gbk' codec can't decode byte 0xfe in position 1: illegal multibyte sequence",
        "'ascii' codec can't decode byte 0x80 in position 0: ordinal not in range(128)",
        "'latin-1' codec can't decode byte 0x81 in position 2: invalid start byte",
        # === More codecs ===
        "'utf-32' codec can't decode byte 0x00 in position 0: invalid codec",
        "'cp1252' codec can't decode byte 0x81 in position 5: invalid start byte",
        "'big5' codec can't decode byte 0x81 in position 1: illegal multibyte sequence",
        "'euc-kr' codec can't decode byte 0x80 in position 3: illegal multibyte sequence",
        "'koi8-r' codec can't decode byte 0x80 in position 2: invalid start byte",
        "'iso8859-1' codec can't decode byte 0x80 in position 0: invalid start byte",
        "'mac_roman' codec can't decode byte 0x80 in position 1: invalid start byte",
        "'utf-7' codec can't decode byte 0x2b in position 0: invalid base64",
        # === Edge: position 0 ===
        "'utf-8' codec can't decode byte 0x80 in position 0: invalid start byte",
        # === Edge: large position ===
        "'utf-8' codec can't decode byte 0x80 in position 999999: invalid start byte",
        # === Edge: different byte formats ===
        "'utf-8' codec can't decode byte 0x00 in position 3: invalid start byte",
        "'utf-8' codec can't decode byte 0xfe in position 0: invalid start byte",
        "'utf-8' codec can't decode byte 0xed in position 1: invalid start byte",
        # === Edge: reason with special chars ===
        "'utf-8' codec can't decode byte 0x80 in position 3: continuation byte with non-continuation prefix",
        "'utf-8' codec can't decode byte 0x80 in position 5: unexpected continuation byte",
        "'utf-8' codec can't decode byte 0xc2 in position 0: overlong encoding",
        # === Without position detail (truncated form) ===
        "'utf-8' codec can't decode byte 0x80",
        "'utf-8' codec can't decode byte 0x80 in position 3",
    ])
    def test_unicode_decode_error(self, msg):
        check(msg, ErrorType.UNICODE_DECODE_ERROR)

    def test_unicode_decode_error_false_positives(self):
        """Similar messages that should NOT match UNICODE_DECODE_ERROR."""
        cases = [
            # "codec" appears but not with the specific pattern
            "expected codec parameter - at $",
            "unknown codec specified",
            "codec not found: utf-8",
            # "decode" and "byte" appear but not in the right pattern
            "Can't decode this message",
            "failed to decode byte 0x80",
            "can't decode byte 0x80",
            # The pattern needs "' codec can't decode byte " (with leading backtick)
            "utf-8 codec can't decode byte 0x80",
            "- codec can't decode byte 0x80",
            # codec with different prefix
            "codec can't decode byte 0x80",
            # Double-quoted instead of single-quoted
            '"utf-8" codec can\'t decode byte 0x80 in position 3: invalid start byte',
        ]
        for msg in cases:
            result = get_error_type(msg)
            assert result.type != ErrorType.UNICODE_DECODE_ERROR, (
                f"{msg!r} should not match UNICODE_DECODE_ERROR"
            )
            assert result.type == ErrorType.WRAPPED_ERROR, (
                f"{msg!r} should fall to WRAPPED_ERROR, got {result.type}"
            )


# ======================================================================
# Group 6: Cross-type False Positive Prevention
# ======================================================================

class TestGroup6CrossTypeFalsePositives:
    """Ensure Group 6 patterns don't accidentally match other Group 6 types."""

    def test_json_malformed_not_msgpack(self):
        """JSON_MALFORMED prefix should not trigger MSGPACK_MALFORMED."""
        msg = "JSON is malformed: invalid character (byte 1)"
        assert get_error_type(msg).type == ErrorType.JSON_MALFORMED

    def test_msgpack_malformed_not_json(self):
        """MSGPACK_MALFORMED prefix should not trigger JSON_MALFORMED."""
        msg = "MessagePack data is malformed: invalid opcode '\\xc1' (byte 3)"
        assert get_error_type(msg).type == ErrorType.MSGPACK_MALFORMED

    def test_encode_error_not_unicode_decode(self):
        """'Can't encode' should not be confused with any unicode decode pattern."""
        msg = "Can't encode strings longer than 2**32 - 1"
        assert get_error_type(msg).type == ErrorType.ENCODE_ERROR

    def test_unicode_decode_not_encode_error(self):
        """Unicode decode error should not match ENCODE_ERROR."""
        msg = "'utf-8' codec can't decode byte 0x80 in position 3: invalid start byte"
        assert get_error_type(msg).type == ErrorType.UNICODE_DECODE_ERROR

    def test_malformed_overlap_keywords(self):
        """Messages containing keywords from multiple Group 6 types."""
        cases = [
            # "malformed" appears in both JSON and MsgPack, but only with correct prefix
            ("Data is malformed: something broke", ErrorType.WRAPPED_ERROR),
            ("stream is malformed: invalid character (byte 1)", ErrorType.WRAPPED_ERROR),
            # "encode" appears in ENCODE_ERROR but not alone
            ("encode error: something went wrong", ErrorType.WRAPPED_ERROR),
            ("Can't encode: no encoder available", ErrorType.WRAPPED_ERROR),
        ]
        for msg, expected_type in cases:
            result = get_error_type(msg)
            assert result.type == expected_type, (
                f"{msg!r}: expected {expected_type}, got {result.type}"
            )


# ======================================================================
# Group 6: Wrapped Error (Catch-all)
# ======================================================================

class TestWrappedErrorGroup6:
    """WRAPPED_ERROR is still the catch-all for messages that don't match
    any specific Group 6 pattern (or other groups)."""

    @pytest.mark.parametrize("msg", [
        # === User-generated errors from __post_init__, dec_hook ===
        "passwords cannot be the same - at $",
        "Custom validation failed",
        "ValueError: invalid literal for int()",
        # === Random strings ===
        "Some completely unexpected error",
        "",
        "   ",
        "!@#$%^&*()_+",
        # === Expected patterns without specifics (fall-through) ===
        "Expected `str`" + " " * 100,
        # === Definition-time errors (don't happen during decode/encode) ===
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
        # === Generic user-code messages ===
        "the password must be at least 8 characters",
        "user must be an admin",
        "cannot set field",
        # === Messages that partially match Group 6 prefixes ===
        "JSON is malformed",  # missing ':'
        "MessagePack data is malformed",  # missing ':'
        "MessagePack data is malformed?",  # '?' instead of ':'
        "Can't encode",  # no trailing space
        # codec-related but not matching the pattern
        "codec can't decode byte 0x80",
        "- codec can't decode byte 0x80",
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
# Group 6: Edge Cases — Ambiguous or Malformed Input
# ======================================================================

class TestGroup6EdgeCases:
    """Resilience against edge cases in Group 6 parsing."""

    def test_very_long_message(self):
        """Very long messages should not crash."""
        msg = "JSON is malformed: invalid character (byte 1)" + "x" * 10000
        result = get_error_type(msg)
        assert result.type == ErrorType.JSON_MALFORMED

    def test_very_long_msgpack_message(self):
        msg = "MessagePack data is malformed: trailing characters (byte " + "9" * 100 + ")"
        result = get_error_type(msg)
        assert result.type == ErrorType.MSGPACK_MALFORMED

    def test_very_long_encode_message(self):
        msg = "Can't encode " + "x" * 10000 + " longer than 2**32 - 1"
        result = get_error_type(msg)
        assert result.type == ErrorType.ENCODE_ERROR

    def test_unicode_in_message(self):
        """Unicode in error messages should not crash the parser."""
        for msg in [
            "JSON is malformed: 汉字 (byte 1)",
            "JSON is malformed: カラム (byte 5)",
            "JSON is malformed: привет (byte 10)",
            "JSON is malformed: مرحبا (byte 3)",
        ]:
            result = get_error_type(msg)
            assert result.type == ErrorType.JSON_MALFORMED

    def test_newlines_in_message(self):
        """Leading whitespace prevents startswith matching — correct behavior."""
        msg = "\n\n\nJSON is malformed: invalid character (byte 1)"
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR, (
            "Leading whitespace prevents startswith matching; "
            "msgspec never produces such messages"
        )

    def test_tabs_in_message(self):
        """Leading tabs prevent startswith matching — correct behavior."""
        msg = "\t\tJSON is malformed: invalid character (byte 1)"
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_numeric_edge_bytes(self):
        """Extremely large byte positions should not overflow."""
        msg = "JSON is malformed: invalid character (byte 18446744073709551615)"
        result = get_error_type(msg)
        assert result.type == ErrorType.JSON_MALFORMED

    def test_no_overlap_with_groups_1_to_5(self):
        """Messages matching Groups 1-5 should never reach Group 6."""
        groups_1_5_cases = [
            "Expected `int`, got `str`",
            "Object missing required field `id`",
            "Object contains unknown field `extra`",
            "Expected `array` of length 2, got 3",
            "Expected `int` >= 0",
            "Expected `str` matching regex '\\d+'",
            "Invalid enum value 'admin'",
            "Invalid value 3",
            "Invalid RFC3339 encoded datetime",
            "Timestamp is out of range",
            "Duration is out of range",
            "Invalid UUID",
        ]
        for msg in groups_1_5_cases:
            result = get_error_type(msg)
            assert result.type != ErrorType.WRAPPED_ERROR, (
                f"{msg!r} should match a specific error type, not WRAPPED_ERROR"
            )
