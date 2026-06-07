"""
Full parse_msgspec_error integration tests with ambiguous field names.

Verifies that the final parser output (type, loc, ctx) is correct when
paths contain special characters ($, [, ], `, .) or empty field names.
This ensures path-parsing changes don't break the full parse chain.
"""
import pytest

from msgspecerror.parse_error import parse_msgspec_error
from msgspecerror.const import ErrorType
from msgspecerror.parse_ctx import ErrorCtx


# ======================================================================
# $ in field names (Section 5.1)
# ======================================================================

class TestDollarInPath:
    """Full parse with $ in field names."""

    @pytest.mark.parametrize("error, expected_loc, expected_type, expected_ctx", [
        ("Expected `int`, got `str` - at `$.$`",
         ("$",), ErrorType.TYPE_MISMATCH, None),
        ("Invalid value 3 - at `$.$`",
         ("$",), ErrorType.INVALID_TAG_VALUE, None),
        ("Invalid enum value 'admin' - at `$.$`",
         ("$",), ErrorType.INVALID_ENUM_VALUE, None),
        ("Invalid RFC3339 encoded datetime - at `$.$`",
         ("$",), ErrorType.INVALID_DATETIME, None),
        ("Expected `int` >= 0 - at `$.$`",
         ("$",), ErrorType.NUMERIC_CONSTRAINT, ErrorCtx(ge=0, expected="int")),
        ("Expected `str` of length <= 32 - at `$.$`",
         ("$",), ErrorType.LENGTH_CONSTRAINT, ErrorCtx(max_length=32, expected="str")),
        ("Object missing required field `id` - at `$.$`",
         ("$", "id"), ErrorType.MISSING_FIELD, None),
        ("Object contains unknown field `extra` - at `$.$`",
         ("$", "extra"), ErrorType.UNKNOWN_FIELD, None),
        ("Expected `int`, got `str` - at `$.a$b`",
         ("a$b",), ErrorType.TYPE_MISMATCH, None),
        ("Expected `int`, got `str` - at `$.$$$`",
         ("$$$",), ErrorType.TYPE_MISMATCH, None),
    ])
    def test_dollar_in_path(self, error, expected_loc, expected_type, expected_ctx):
        result = parse_msgspec_error(error)
        assert result.loc == expected_loc, f"loc: {result.loc!r} != {expected_loc!r} for {error!r}"
        assert result.type == expected_type, f"type: {result.type} != {expected_type} for {error!r}"
        if expected_ctx is not None:
            assert result.ctx == expected_ctx, f"ctx: {result.ctx!r} != {expected_ctx!r}"


# ======================================================================
# Non-numeric bracket content (Section 5.2)
# ======================================================================

class TestBracketInPath:
    """Full parse with non-numeric bracket content."""

    @pytest.mark.parametrize("error, expected_loc, expected_type", [
        ("Expected `int`, got `str` - at `$.[x]`",
         ("[x]",), ErrorType.TYPE_MISMATCH),
        ("Invalid value 3 - at `$.[x]`",
         ("[x]",), ErrorType.INVALID_TAG_VALUE),
        ("Expected `int`, got `str` - at `$.user[]`",
         ("user[]",), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$[0][x]`",
         (0, "[x]"), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$[x][0]`",
         ("[x]", 0), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$[x][0][y]`",
         ("[x]", 0, "[y]"), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$.data[x].value`",
         ("data[x]", "value"), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$.[0]`",
         ("", 0), ErrorType.TYPE_MISMATCH),
    ])
    def test_bracket_in_path(self, error, expected_loc, expected_type):
        result = parse_msgspec_error(error)
        assert result.loc == expected_loc, f"loc: {result.loc!r} != {expected_loc!r}"
        assert result.type == expected_type, f"type: {result.type} != {expected_type}"


# ======================================================================
# Empty field names (Section 5.3)
# ======================================================================

class TestEmptyFieldInPath:
    """Full parse with empty field names (rename=\"\")."""

    @pytest.mark.parametrize("error, expected_loc, expected_type", [
        ("Expected `int`, got `str` - at `$.`",
         ("",), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$..`",
         ("", ""), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$.[0]`",
         ("", 0), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$[0].`",
         (0, ""), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$..[0]`",
         ("", "", 0), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$[0]..`",
         (0, "", ""), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$..name`",
         ("", "name"), ErrorType.TYPE_MISMATCH),
        ("Expected `int`, got `str` - at `$.name.`",
         ("name", ""), ErrorType.TYPE_MISMATCH),
        ("Object missing required field `id` - at `$.`",
         ("", "id"), ErrorType.MISSING_FIELD),
        ("Object contains unknown field `extra` - at `$.`",
         ("", "extra"), ErrorType.UNKNOWN_FIELD),
    ])
    def test_empty_field_in_path(self, error, expected_loc, expected_type):
        result = parse_msgspec_error(error)
        assert result.loc == expected_loc, (
            f"loc: {result.loc!r} != {expected_loc!r}\n  for {error!r}"
        )
        assert result.type == expected_type, (
            f"type: {result.type} != {expected_type}\n  for {error!r}"
        )

    def test_empty_field_with_ctx(self):
        """Empty field names should preserve ctx information."""
        result = parse_msgspec_error("Expected `int` >= 0 - at `$.`")
        assert result.loc == ("",)
        assert result.type == ErrorType.NUMERIC_CONSTRAINT
        assert result.ctx == ErrorCtx(ge=0, expected="int")

        result = parse_msgspec_error("Expected `int` >= 0 - at `$.a.`")
        assert result.loc == ("a", "")
        assert result.type == ErrorType.NUMERIC_CONSTRAINT
        assert result.ctx == ErrorCtx(ge=0, expected="int")


# ======================================================================
# Backticks in field names (Section 5.4)
# ======================================================================

class TestBacktickInPath:
    """Full parse with backticks in field names."""

    @pytest.mark.parametrize("error, expected_loc, expected_type", [
        # TYPE_MISMATCH
        ('Expected `int`, got `str` - at `$.custom`field`',
         ("custom`field",), ErrorType.TYPE_MISMATCH),
        ('Expected `int`, got `str` - at `$.`items[0]`',
         ('`items', 0), ErrorType.TYPE_MISMATCH),
        # INVALID_TAG_VALUE
        ("Invalid value 3 - at `$.type`name`",
         ("type`name",), ErrorType.INVALID_TAG_VALUE),
        # INVALID_DATETIME
        ("Invalid RFC3339 encoded datetime - at `$.ts`stamp`",
         ("ts`stamp",), ErrorType.INVALID_DATETIME),
        # MISSING_FIELD
        ("Object missing required field `req`name` - at `$.path`",
         ("path", "req`name"), ErrorType.MISSING_FIELD),
        # UNKNOWN_FIELD
        ("Object contains unknown field `unk`own` - at `$.path`",
         ("path", "unk`own"), ErrorType.UNKNOWN_FIELD),
        # MISSING_FIELD with backtick in both
        ("Object missing required field `req`field` - at `$.p`th`",
         ("p`th", "req`field"), ErrorType.MISSING_FIELD),
        # UNKNOWN_FIELD with backtick in both
        ("Object contains unknown field `RepairThresho` ld1` - at `$.op`si`",
         ("op`si", "RepairThresho` ld1"), ErrorType.UNKNOWN_FIELD),
    ])
    def test_backtick_in_path(self, error, expected_loc, expected_type):
        result = parse_msgspec_error(error)
        assert result.loc == expected_loc, (
            f"loc: {result.loc!r} != {expected_loc!r}\n  for {error!r}"
        )
        assert result.type == expected_type, (
            f"type: {result.type} != {expected_type}\n  for {error!r}"
        )

    def test_backtick_with_constraint(self):
        """Backtick in path with constraint errors should preserve ctx."""
        result = parse_msgspec_error("Expected `int` >= 0 - at `$.ag`e`")
        assert result.loc == ("ag`e",)
        assert result.type == ErrorType.NUMERIC_CONSTRAINT
        assert result.ctx == ErrorCtx(ge=0, expected="int")

        result = parse_msgspec_error("Expected `str` of length <= 32 - at `$.nam`e`")
        assert result.loc == ("nam`e",)
        assert result.type == ErrorType.LENGTH_CONSTRAINT
        assert result.ctx == ErrorCtx(max_length=32, expected="str")


# ======================================================================
# Mixed special characters
# ======================================================================

class TestMixedCharsInPath:
    """Full parse with combinations of $, brackets, and backticks."""

    @pytest.mark.parametrize("error, expected_loc, expected_type", [
        # $ + bracket
        ("Expected `int`, got `str` - at `$.$[x]`",
         ("$[x]",), ErrorType.TYPE_MISMATCH),
        # bracket + backtick
        ("Expected `int`, got `str` - at `$.[a`b]`",
         ("[a`b]",), ErrorType.TYPE_MISMATCH),
        # empty + index + backtick
        ("Expected `int`, got `str` - at `$.[0].a`field`",
         ("", 0, "a`field"), ErrorType.TYPE_MISMATCH),
        # All three: empty field, backtick, bracket
        ("Expected `int`, got `str` - at `$.[a`b]`",
         ("[a`b]",), ErrorType.TYPE_MISMATCH),
    ])
    def test_mixed_chars_in_path(self, error, expected_loc, expected_type):
        result = parse_msgspec_error(error)
        assert result.loc == expected_loc, (
            f"loc: {result.loc!r} != {expected_loc!r}\n  for {error!r}"
        )
        assert result.type == expected_type, (
            f"type: {result.type} != {expected_type}\n  for {error!r}"
        )


# ======================================================================
# Stress tests — full parse with large/ambiguous paths
# ======================================================================

class TestStressFullParse:
    """Stress tests for full parse_msgspec_error with complex paths."""

    def test_long_field_name(self):
        """Very long field name with $."""
        name = "$" + "a" * 10000
        error = f"Expected `int`, got `str` - at `$.{name}`"
        result = parse_msgspec_error(error)
        assert result.loc == (name,)
        assert result.type == ErrorType.TYPE_MISMATCH

    def test_deeply_nested_empty_fields(self):
        """Many empty field names combined with indices."""
        error = "Expected `int`, got `str` - at `$.a..[0]...[1]..b`"
        result = parse_msgspec_error(error)
        # a.. yields a, '', ''; [0] yields 0;
        # ... yields '', '', ''; [1] yields 1;
        # ..b yields '', 'b'
        assert result.loc == ("a", "", "", 0, "", "", "", 1, "", "b")
        assert result.type == ErrorType.TYPE_MISMATCH

    def test_many_alternating_empty_and_indices(self):
        """Alternating empty fields and indices."""
        error = "Expected `int`, got `str` - at `$[0]..[1]..[2]..`"
        result = parse_msgspec_error(error)
        # [0] → 0, .. → '', '', [1] → 1, .. → '', '', [2] → 2, .. → '', ''
        assert result.loc == (0, "", "", 1, "", "", 2, "", "")
        assert result.type == ErrorType.TYPE_MISMATCH

    def test_object_missing_empty_field(self):
        """Object missing on an empty field name with sub-path."""
        error = "Object missing required field `id` - at `$..`"
        result = parse_msgspec_error(error)
        assert result.loc == ("", "", "id")
        assert result.type == ErrorType.MISSING_FIELD

    def test_object_unknown_empty_field(self):
        """Object unknown on an empty field name."""
        error = "Object contains unknown field `extra` - at `$.[0]`"
        result = parse_msgspec_error(error)
        assert result.loc == ("", 0, "extra")
        assert result.type == ErrorType.UNKNOWN_FIELD

    def test_backtick_with_object_missing(self):
        """Object missing with backtick in both field and path."""
        error = "Object missing required field `req`name` - at `$.p`th`"
        result = parse_msgspec_error(error)
        assert result.loc == ("p`th", "req`name")
        assert result.type == ErrorType.MISSING_FIELD

    def test_dict_key_path(self):
        """Dict key path is correctly identified."""
        error = "Expected `int`, got `str` - at `key` in `$.member_map`"
        result = parse_msgspec_error(error)
        assert result.loc == ('member_map', '...key')
        assert result.type == ErrorType.TYPE_MISMATCH

    def test_wrapped_error_empty_field(self):
        """WRAPPED_ERROR with empty field in path."""
        error = "custom error message - at `$.[0]`"
        result = parse_msgspec_error(error)
        assert result.loc == ("", 0)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_all_special_chars(self):
        """Combination of $, bracket, and backtick."""
        # Path: $.a$b[c]d`e[0] — a$b[c]d`e accumulates as one field, [0] is index
        error = "Expected `int`, got `str` - at `$.a$b[c]d`e[0]`"
        result = parse_msgspec_error(error)
        assert result.loc == ("a$b[c]d`e", 0)
        assert result.type == ErrorType.TYPE_MISMATCH
