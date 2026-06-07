"""
Comprehensive tests for path parsing with ambiguous field names (doc Section 5).

These cover field names containing special characters ($, [, ], `, .) and
empty-string field names — cases where the text of the path alone has
multiple possible interpretations, or where the parser must handle unusual
location strings correctly.

Adds combinatorial variations, integration tests, false-positive prevention,
and stress tests beyond the doc examples.
"""
import pytest

from msgspecerror.parse_error import get_error_path, parse_msgspec_error
from msgspecerror.const import ErrorType
from msgspecerror.parse_ctx import ErrorCtx


# ======================================================================
# Helper
# ======================================================================

def check(error, expected_loc, expected_type):
    """Assert parse_msgspec_error returns correct loc and type."""
    result = parse_msgspec_error(error)
    assert result.loc == expected_loc, (
        f"loc mismatch:\n"
        f"  error:    {error!r}\n"
        f"  expected: {expected_loc!r}\n"
        f"  got:      {result.loc!r}"
    )
    assert result.type == expected_type, (
        f"type mismatch for {error!r}: "
        f"expected {expected_type}, got {result.type}"
    )


# ======================================================================
# Section 5.1 — Field names containing `$`
#
# The first `$` after the backtick is always the root marker. Any
# subsequent `$` is part of a field name.
# ======================================================================

class TestDollarInFieldName:
    """Paths where `$` appears inside field names."""

    @pytest.mark.parametrize("error, expected", [
        # Single $ field
        ("Expected `int`, got `str` - at `$.$`", ("$",)),
        # $ at different positions in field name
        ("Expected `int`, got `str` - at `$.a$b`", ("a$b",)),
        ("Expected `int`, got `str` - at `$.$abc`", ("$abc",)),
        ("Expected `int`, got `str` - at `$.abc$`", ("abc$",)),
        # Multiple $ in field name
        ("Expected `int`, got `str` - at `$.a$$b`", ("a$$b",)),
        ("Expected `int`, got `str` - at `$.$$$`", ("$$$",)),
        # $ with numbers
        ("Expected `int`, got `str` - at `$.$1`", ("$1",)),
        ("Expected `int`, got `str` - at `$.$2x`", ("$2x",)),
        # $ with hyphen
        ("Expected `int`, got `str` - at `$.$-`", ("$-",)),
        # $ with underscore
        ("Expected `int`, got `str` - at `$.$_`", ("$_",)),
        # ($ followed by dot — known split limitation, see Section 4.1)
        ("Expected `int`, got `str` - at `$.$.x`", ("$", "x")),
        ("Expected `int`, got `str` - at `$.a.$`", ("a", "$")),
        ("Expected `int`, got `str` - at `$.$.a.b`", ("$", "a", "b")),
        # $ at intermediate position
        ("Expected `int`, got `str` - at `$.x$.y`", ("x$", "y")),
    ])
    def test_dollar_in_field(self, error, expected):
        assert get_error_path(error) == expected

    def test_dollar_field_full_parse(self):
        """Full parse_msgspec_error with $ in field name."""
        check(
            "Expected `int`, got `str` - at `$.$`",
            ("$",),
            ErrorType.TYPE_MISMATCH,
        )

    def test_dollar_field_multiple_error_types(self):
        """$ in field name with different error message types."""
        cases = [
            ("Invalid value 3 - at `$.$`", ("$",), ErrorType.INVALID_TAG_VALUE),
            ("Invalid enum value 'admin' - at `$.$`", ("$",), ErrorType.INVALID_ENUM_VALUE),
            ("Invalid RFC3339 encoded datetime - at `$.$`", ("$",), ErrorType.INVALID_DATETIME),
            ("Expected `object` of length >= 1 - at `$.$`", ("$",), ErrorType.OBJECT_LENGTH_CONSTRAINT, ErrorCtx(min_length=1)),
        ]
        for case in cases:
            error, expected_loc, expected_type = case[:3]
            expected_ctx = case[3] if len(case) > 3 else None
            result = parse_msgspec_error(error)
            assert result.loc == expected_loc, f"{error!r}: loc {result.loc} != {expected_loc}"
            assert result.type == expected_type, f"{error!r}: type {result.type} != {expected_type}"
            if expected_ctx is not None:
                assert result.ctx == expected_ctx, f"{error!r}: ctx {result.ctx} != {expected_ctx}"


# ======================================================================
# Section 5.2 — Field names with bracket content (not `...`)
#
# Non-numeric bracket content is preserved as a field name.
# Numeric content is parsed as array index.
# `[...]` (three dots) is parsed as dict key marker.
# ======================================================================

class TestBracketNonNumeric:
    """Paths where `[...]` contains non-numeric content (not `...`)."""

    @pytest.mark.parametrize("error, expected", [
        # Basic non-numeric brackets
        ("Expected `int`, got `str` - at `$.[x]`",  ("[x]",)),
        ("Expected `int`, got `str` - at `$.user[]`",  ("user[]",)),
        ("Expected `int`, got `str` - at `$.[[]`",  ("[[]",)),
        ("Expected `int`, got `str` - at `$.[]]`",  ("[]]",)),
        # Alphanumeric content
        ("Expected `int`, got `str` - at `$.[a1]`",  ("[a1]",)),
        ("Expected `int`, got `str` - at `$.[1a]`",  ("[1a]",)),
        ("Expected `int`, got `str` - at `$.[_test]`",  ("[_test]",)),
        # Special chars in bracket content
        ("Expected `int`, got `str` - at `$.[$]`",  ("[$]",)),
        ("Expected `int`, got `str` - at `$.[-]`",  ("[-]",)),
        # Empty brackets
        ("Expected `int`, got `str` - at `$.field[]`",  ("field[]",)),
        # Single char bracket content
        ("Expected `int`, got `str` - at `$.[a]`",  ("[a]",)),
        ("Expected `int`, got `str` - at `$.[0]`",  (0,)),
    ])
    def test_non_numeric_bracket(self, error, expected):
        """Non-numeric bracket content is preserved as field name."""
        result = get_error_path(error)
        assert result == expected, (
            f"  error:    {error!r}\n"
            f"  expected: {expected!r}\n"
            f"  got:      {result!r}"
        )

    def test_non_numeric_bracket_false_positive(self):
        """'[0]' (purely numeric) should remain array index, not field name."""
        error = "Expected `int`, got `str` - at `$.data[0]`"
        assert get_error_path(error) == ('data', 0)

    def test_mixed_numeric_and_non_numeric(self):
        """Mix of valid numeric index and non-numeric bracket."""
        cases = [
            # Non-numeric then numeric
            ("Expected `int`, got `str` - at `$.[x][0]`", ("[x]", 0)),
            ("Expected `int`, got `str` - at `$.[abc][1]`", ("[abc]", 1)),
            # Numeric then non-numeric
            ("Expected `int`, got `str` - at `$.[0][x]`", (0, "[x]")),
            ("Expected `int`, got `str` - at `$.[1][abc]`", (1, "[abc]")),
            # Cascade: non-numeric, numeric, non-numeric
            ("Expected `int`, got `str` - at `$.[x][0][y]`", ("[x]", 0, "[y]")),
            # Numeric, numeric, non-numeric
            ("Expected `int`, got `str` - at `$.[0][1][x]`", (0, 1, "[x]")),
            # Non-numeric in nested position — data[x] is one combined field
            ("Expected `int`, got `str` - at `$.data[x].value`",
             ("data[x]", "value")),
        ]
        for error, expected in cases:
            result = get_error_path(error)
            assert result == expected, (
                f"  error:    {error!r}\n"
                f"  expected: {expected!r}\n"
                f"  got:      {result!r}"
            )

    def test_dict_key_three_dots_unaffected(self):
        """ `[...]` (three dots) must remain dict key marker."""
        cases = [
            ("Expected `int`, got `str` - at `$[...]`", ('...',)),
            ("Expected `int`, got `str` - at `$.data[...]`", ('data', '...')),
            ("Expected `int`, got `str` - at `$.users[...].name`", ('users', '...', 'name')),
            ("Expected `int`, got `str` - at `$.data[0][...]`", ('data', 0, '...')),
            ("Expected `int`, got `str` - at `$[...][0]`", ('...', 0)),
            ("Expected `int`, got `str` - at `$[...][...].x`", ('...', '...', 'x')),
        ]
        for error, expected in cases:
            result = get_error_path(error)
            assert result == expected, (
                f"  error:    {error!r}\n"
                f"  expected: {expected!r}\n"
                f"  got:      {result!r}"
            )

    def test_full_parse_bracket(self):
        """Full parse_msgspec_error with non-numeric bracket content."""
        cases = [
            ("Expected `int`, got `str` - at `$.[x]`",
             ("[x]",), ErrorType.TYPE_MISMATCH),
            ("Invalid enum value 'admin' - at `$.[x]`",
             ("[x]",), ErrorType.INVALID_ENUM_VALUE),
            ("Object missing required field `id` - at `$.[x]`",
             ("[x]", "id"), ErrorType.MISSING_FIELD),
            ("Object contains unknown field `extra` - at `$.[x]`",
             ("[x]", "extra"), ErrorType.UNKNOWN_FIELD),
        ]
        for error, expected_loc, expected_type in cases:
            result = parse_msgspec_error(error)
            assert result.loc == expected_loc, f"{error!r}: loc {result.loc}"
            assert result.type == expected_type, f"{error!r}: type {result.type}"


# ======================================================================
# Section 5.3 — Empty-string field names (known limitation)
#
# rename="" inserts an empty path component (`$.`). Multiple
# consecutive empty renames produce multiple empty components (`$..`).
# The parser should preserve empty field names, but doesn't — empty
# segments are filtered out by `_path_split_part`'s `if part:` guard.
# ======================================================================

class TestEmptyFieldName:
    """Paths with empty-string field names (rename=\"\")."""

    @pytest.mark.parametrize("error, actual, correct", [
        # rename="" → path `$.`
        ("Expected `int`, got `str` - at `$.`",
         (),                   # current: dropped
         ("",)),               # correct: one empty field name

        # rename=("", "") → path `$..`
        ("Expected `int`, got `str` - at `$..`",
         (),                   # current: dropped
         ("", "")),            # correct: two empty names

        # rename=("", 1, "") → path `$[1].`
        ("Expected `int`, got `str` - at `$[1].`",
         (1,),                 # current: empty tail dropped
         (1, "")),             # correct: (1, "")

        # Empty after dict key → path `$[...].`
        ("Expected `int`, got `str` - at `$[...].`",
         ("...",),             # current: empty tail dropped
         ("...", "")),         # correct: ("...", "")
    ])
    def test_empty_field_name(self, error, actual, correct):
        result = get_error_path(error)
        assert result == actual, (
            f"Current parser produces {result!r}, "
            f"expected current behavior {actual!r}.\n"
            f"  Correct behaviour would preserve empty components: {correct!r}\n"
            f"  See _path_split_part — 'if part:' filters out ''."
        )

    def test_normal_paths_still_work(self):
        """Normal paths without empty fields should work fine."""
        cases = [
            ("Invalid enum value 'admin' - at `$[1]`", (1,)),
            ("Expected `int`, got `str` - at `$[0].value`", (0, 'value')),
            ("Expected `int`, got `str` - at `$.data[0]`", ('data', 0)),
            ("Expected `int`, got `str` - at `$[...]`", ('...',)),
            ("Expected `int`, got `str` - at `$.a.b.c`", ('a', 'b', 'c')),
            ("Expected `int`, got `str` - at `$.data.items[0].name`", ('data', 'items', 0, 'name')),
        ]
        for error, expected_path in cases:
            result = get_error_path(error)
            assert result == expected_path, f"{error!r}: {result} != {expected_path}"


# ======================================================================
# Section 5.4 — Field names containing backticks
#
# msgspec uses backticks to delimit field names and path components
# in error messages. When a field name itself contains a backtick,
# the parser must correctly identify the boundaries by finding the
# correct KEY_at / KEY_at_key_in separator.
# ======================================================================

class TestBacktickInFieldName:
    """Paths where field names contain backtick characters."""

    # --- "Object contains unknown field" ---

    def test_object_unknown_backtick_in_both(self):
        """Backtick in both field name and path."""
        error = ("Object contains unknown field "
                 "`field`with`ticks` - at `$.path`with`it`")
        result = get_error_path(error)
        assert result == ("path`with`it", "field`with`ticks"), f"Got {result!r}"

    def test_object_unknown_backtick_in_field_only(self):
        """Backtick only in field name, not path."""
        error = ("Object contains unknown field "
                 "`field`name` - at `$.clean_path`")
        result = get_error_path(error)
        assert result == ("clean_path", "field`name"), f"Got {result!r}"

    def test_object_unknown_backtick_in_path_only(self):
        """Backtick only in path, not field name."""
        error = ("Object contains unknown field "
                 "`normal_name` - at `$.p`th`")
        result = get_error_path(error)
        assert result == ("p`th", "normal_name"), f"Got {result!r}"

    # --- "Object missing required field" ---

    def test_object_missing_backtick_in_both(self):
        """Object missing with backtick in both."""
        error = ("Object missing required field "
                 "`req`field` - at `$.p`th`")
        result = get_error_path(error)
        assert result == ("p`th", "req`field"), f"Got {result!r}"

    def test_object_missing_backtick_in_field_only(self):
        error = ("Object missing required field "
                 "`req`name` - at `$.path`")
        result = get_error_path(error)
        assert result == ("path", "req`name"), f"Got {result!r}"

    # --- TYPE_MISMATCH with backtick ---

    @pytest.mark.parametrize("error, expected", [
        # Standard examples from doc
        ('Expected `MyCustomClass`, got `str` - at `$.custom`field`',
         ("custom`field",)),
        ('Expected `int`, got `str` - at `$.`items[0]`',
         ('`items', 0)),
        # Additional variants
        ('Expected `int`, got `str` - at `$.back`tick`',
         ("back`tick",)),
        # $` at end of path — trailing backtick is the delimiter
        ('Expected `int`, got `str` - at `$.start`',
         ("start",)),
        ('Expected `int`, got `str` - at `$.`end`',
         ("`end",)),
        ('Expected `int`, got `str` - at `$.multi`ple`ticks`',
         ("multi`ple`ticks",)),
    ])
    def test_type_mismatch_with_backtick(self, error, expected):
        result = get_error_path(error)
        assert result == expected, f"Expected {expected!r}, got {result!r}"

    # --- Other error types with backtick in path ---

    @pytest.mark.parametrize("error, expected_loc, expected_type, expected_ctx", [
        ("Invalid value 3 - at `$.type`name`",
         ("type`name",), ErrorType.INVALID_TAG_VALUE, None),
        ("Invalid enum value 'admin' - at `$.role`name`",
         ("role`name",), ErrorType.INVALID_ENUM_VALUE, None),
        ("Invalid RFC3339 encoded datetime - at `$.ts`stamp`",
         ("ts`stamp",), ErrorType.INVALID_DATETIME, None),
        ("Invalid UUID - at `$.id`entifier`",
         ("id`entifier",), ErrorType.INVALID_UUID, None),
        ("Expected `int` >= 0 - at `$.ag`e`",
         ("ag`e",), ErrorType.NUMERIC_CONSTRAINT, ErrorCtx(ge=0)),
        ("Expected `str` of length <= 32 - at `$.nam`e`",
         ("nam`e",), ErrorType.LENGTH_CONSTRAINT, ErrorCtx(max_length=32)),
    ])
    def test_other_errors_with_backtick(self, error, expected_loc, expected_type, expected_ctx):
        result = parse_msgspec_error(error)
        assert result.loc == expected_loc, f"Expected loc {expected_loc}, got {result.loc}"
        assert result.type == expected_type, f"Expected type {expected_type}, got {result.type}"
        if expected_ctx is not None:
            assert result.ctx == expected_ctx, f"Expected ctx {expected_ctx}, got {result.ctx}"

    # --- Full parse integration ---

    def test_full_parse_backtick_type_mismatch(self):
        check(
            "Expected `int`, got `str` - at `$.`items[0]`",
            ('`items', 0),
            ErrorType.TYPE_MISMATCH,
        )

    def test_full_parse_backtick_missing_field(self):
        check(
            "Object missing required field `req`name` - at `$.path`",
            ("path", "req`name"),
            ErrorType.MISSING_FIELD,
        )

    def test_full_parse_backtick_unknown_field(self):
        check(
            "Object contains unknown field `unk`own` - at `$.p`th`",
            ("p`th", "unk`own"),
            ErrorType.UNKNOWN_FIELD,
        )


# ======================================================================
# Mixed — combining multiple special characters
# ======================================================================

class TestMixedEdgeCases:
    """Combinations of multiple special characters."""

    @pytest.mark.parametrize("error, expected", [
        # $ + bracket
        ("Expected `int`, got `str` - at `$.$[x]`", ("$[x]",)),
        # $ + backtick
        ("Expected `int`, got `str` - at `$.$`tick`", ("$`tick",)),
        # bracket + backtick
        ("Expected `int`, got `str` - at `$.[a`b]`", ("[a`b]",)),
        # $ + bracket + backtick
        ("Expected `int`, got `str` - at `$.$`name[0]`", ("$`name", 0)),
        # Multiple special chars in one field
        ("Expected `int`, got `str` - at `$.a$b[c]d`e`", ("a$b[c]d`e",)),
    ])
    def test_special_chars_combination(self, error, expected):
        """Field names combining $, brackets, and backticks."""
        result = get_error_path(error)
        assert result == expected, f"Expected {expected!r}, got {result!r}"

    def test_invalid_tag_value_with_path(self):
        check("Invalid value 3 - at `$.type`", ('type',), ErrorType.INVALID_TAG_VALUE)

    def test_convert_dict_key_path(self):
        check("Expected `str`, got `int` - at `key` in `$.member_map`",
              ('member_map', '...key'),
              ErrorType.TYPE_MISMATCH)

    def test_wrapped_error_with_path(self):
        """WRAPPED_ERROR with a user-error path — KEY_at works."""
        error = "passwords cannot be the same - at $"
        result = parse_msgspec_error(error)
        assert result.loc == ()
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_missing_field_at_root_no_path(self):
        error = "Object missing required field `id`"
        result = parse_msgspec_error(error)
        assert result.loc == ('id',)
        assert result.type == ErrorType.MISSING_FIELD


# ======================================================================
# Stress tests — long paths, unicode, deep nesting
# ======================================================================

class TestStressCases:
    """Stress tests for path parsing robustness."""

    def test_very_long_path(self):
        """A very long dotted path should not crash."""
        path = '.'.join([f'field{i}' for i in range(100)])
        error = f"Expected `int`, got `str` - at `$.{path}`"
        expected = tuple(f'field{i}' for i in range(100))
        result = get_error_path(error)
        assert result == expected
        assert len(result) == 100

    def test_very_long_field_name(self):
        """A single very long field name should not crash."""
        long_name = 'a' * 10000
        error = f"Expected `int`, got `str` - at `$.{long_name}`"
        result = get_error_path(error)
        assert result == (long_name,)

    def test_unicode_field_names(self):
        """Unicode characters in field names."""
        cases = [
            ("Expected `int`, got `str` - at `$.用户`", ("用户",)),
            ("Expected `int`, got `str` - at `$.名前`", ("名前",)),
            ("Expected `int`, got `str` - at `$.имя`", ("имя",)),
            ("Expected `int`, got `str` - at `$.اسم`", ("اسم",)),
            ("Expected `int`, got `str` - at `$.emoji😊`", ("emoji😊",)),
            ("Expected `int`, got `str` - at `$.混合.name`", ("混合", "name")),
        ]
        for error, expected in cases:
            result = get_error_path(error)
            assert result == expected, f"Expected {expected!r}, got {result!r}"

    def test_deeply_nested_mixed(self):
        """Deeply nested path with all component types."""
        error = ("Expected `int`, got `str` - at "
                 "`$.a[0].b[1][2].c[...].d[3].e`")
        result = get_error_path(error)
        assert result == ('a', 0, 'b', 1, 2, 'c', '...', 'd', 3, 'e')

    def test_many_numeric_indices(self):
        """Path with many consecutive numeric indices."""
        indices = ''.join(f'[{i}]' for i in range(50))
        error = f"Expected `int`, got `str` - at `$.data{indices}.end`"
        expected = ('data',) + tuple(range(50)) + ('end',)
        result = get_error_path(error)
        assert result == expected
        assert len(result) == 52

    def test_many_dict_keys(self):
        """Path with many consecutive dict keys."""
        keys = '[...]' * 10
        error = f"Expected `int`, got `str` - at `$.data{keys}.end`"
        result = get_error_path(error)
        assert result[0] == 'data'
        assert result[1:-1] == ('...',) * 10
        assert result[-1] == 'end'
        assert len(result) == 12

    def test_non_numeric_bracket_stress(self):
        """Many consecutive non-numeric brackets accumulate into one field."""
        brackets = ''.join(f'[x{i}]' for i in range(20))
        error = f"Expected `int`, got `str` - at `$.{brackets}`"
        result = get_error_path(error)
        # All 20 brackets accumulate into a single field name
        expected_field = ''.join(f'[x{i}]' for i in range(20))
        assert result == (expected_field,)
        assert len(result) == 1
        # Each [xN] is 4 chars for N=0-9, 5 for N=10-19
        assert result[0].startswith('[x0]')
        assert result[0].endswith('[x19]')
        assert result[0].count('[') == 20

    def test_mixed_bracket_types_stress(self):
        """Mix of numeric, non-numeric, and dict-key brackets."""
        error = ("Expected `int`, got `str` - at "
                 "`$.data[0][x][...][1][y][...][2]`")
        result = get_error_path(error)
        assert result == ('data', 0, '[x]', '...', 1, '[y]', '...', 2)

    def test_null_bytes_in_path(self):
        """Field names with null bytes should not crash (unlikely but defensive)."""
        error = "Expected `int`, got `str` - at `$.field\x00name`"
        result = get_error_path(error)
        # The null byte is just part of the field name
        assert result == ("field\x00name",)

    def test_control_chars_in_path(self):
        """Field names with control characters should not crash."""
        error = "Expected `int`, got `str` - at `$.field\tname`"
        result = get_error_path(error)
        assert result == ("field\tname",)


# ======================================================================
# Edge Cases — Empty / missing paths, documented limitations
# ======================================================================

class TestEmptyPathCases:
    """Messages with no path component at all."""

    @pytest.mark.parametrize("error", [
        "Expected `int`, got `str`",
        "",
        "Some random error",
    ])
    def test_no_path_returns_empty_tuple(self, error):
        result = get_error_path(error)
        assert result == ()

    def test_missing_field_root_returns_field(self):
        result = get_error_path("Object missing required field `id`")
        assert result == ('id',)


# ======================================================================
# Known limitations — documented in doc Section 4
# ======================================================================

class TestKnownLimitations:
    """Verifies that Section 4 documented limitations still hold."""

    def test_dot_in_field_name_section_41(self):
        """Section 4.1: Field names with '.' are always split on '.'."""
        cases = [
            # a.b is always split as two fields
            ("Expected `int`, got `str` - at `$.a.b`", ("a", "b")),
            # rename="a.b" → split
            ("Expected `int`, got `str` - at `$.data.a.b`", ("data", "a", "b")),
        ]
        for error, expected in cases:
            result = get_error_path(error)
            assert result == expected

    def test_bracket_index_conflict_section_42(self):
        """Section 4.2: [0] is always parsed as array index, not field name part."""
        # rename="user[0]" → path `$.user[0]` — parsed as field "user" then index 0
        error = "Expected `int`, got `str` - at `$.user[0]`"
        result = get_error_path(error)
        assert result == ("user", 0), f"Got {result!r}"

    def test_empty_field_name_section_53(self):
        """Section 5.3: Empty field names are dropped (known limitation)."""
        cases = [
            ("Expected `int`, got `str` - at `$.`", ()),
            ("Expected `int`, got `str` - at `$..`", ()),
        ]
        for error, expected in cases:
            result = get_error_path(error)
            assert result == expected, f"Expected {expected!r}, got {result!r} for {error!r}"
