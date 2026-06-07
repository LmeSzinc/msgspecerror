"""
Comprehensive tests for get_error_path() path parsing logic.

Covers all error message types, structural variants, index forms,
dict-key markers, string-digit field names, unicode, edge cases,
and integration with parse_msgspec_error.
"""
import pytest

from msgspecerror.parse_error import get_error_path


# ======================================================================
# Core: Basic path forms across all error types
# ======================================================================

class TestBasicPaths:
    """Basic dotted and bracketed paths with various error message prefixes."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # === TYPE_MISMATCH ===
        ("Expected `int`, got `str` - at `$.user.profile.age`",
         ('user', 'profile', 'age')),
        # === INVALID_TAG_VALUE ===
        ("Invalid value 3 - at `$.kind`",
         ('kind',)),
        # === INVALID_ENUM_VALUE ===
        ("Invalid enum value 'admin' - at `$.role`",
         ('role',)),
        # === INVALID_DATETIME ===
        ("Invalid RFC3339 encoded datetime - at `$.timestamp`",
         ('timestamp',)),
        # === INVALID_DATE ===
        ("Invalid RFC3339 encoded date - at `$.birth_date`",
         ('birth_date',)),
        # === INVALID_TIME ===
        ("Invalid RFC3339 encoded time - at `$.event_at`",
         ('event_at',)),
        # === INVALID_DURATION ===
        ("Invalid ISO8601 duration - at `$.period`",
         ('period',)),
        # === INVALID_UUID ===
        ("Invalid UUID - at `$.id`",
         ('id',)),
        # === INVALID_BASE64_STRING ===
        ("Invalid base64 encoded string - at `$.data`",
         ('data',)),
        # === INVALID_DECIMAL_STRING ===
        ("Invalid decimal string - at `$.price`",
         ('price',)),
        # === INVALID_MSGPACK_TIMESTAMP ===
        ("Invalid MessagePack timestamp - at `$.ts`",
         ('ts',)),
        # === INVALID_EPOCH_TIMESTAMP ===
        ("Invalid epoch timestamp - at `$.epoch`",
         ('epoch',)),
        # === UNSUPPORTED_DURATION_UNITS ===
        ("Only units 'D', 'H', 'M', and 'S' are supported "
         "when parsing ISO8601 durations - at `$.period`",
         ('period',)),
        # === MISSING_FIELD ===
        ("Object missing required field `name` - at `$.user.profile`",
         ('user', 'profile', 'name')),
        # === UNKNOWN_FIELD ===
        ("Object contains unknown field `extra` - at `$.config`",
         ('config', 'extra')),
        # === TIMESTAMP_OUT_OF_RANGE ===
        ("Timestamp is out of range - at `$.ts`",
         ('ts',)),
        # === DURATION_OUT_OF_RANGE ===
        ("Duration is out of range - at `$.duration`",
         ('duration',)),
        # === INTEGER_OUT_OF_RANGE ===
        ("Integer value out of range - at `$.big_int`",
         ('big_int',)),
        # === NUMBER_OUT_OF_RANGE ===
        ("Number out of range - at `$.big_num`",
         ('big_num',)),
        # === ARRAY_LENGTH_CONSTRAINT ===
        ("Expected `array` of length 2, got 3 - at `$.coords`",
         ('coords',)),
        # === OBJECT_LENGTH_CONSTRAINT ===
        ("Expected `object` of length >= 1 - at `$.metadata`",
         ('metadata',)),
        # === LENGTH_CONSTRAINT (str) ===
        ("Expected `str` of length <= 32 - at `$.name`",
         ('name',)),
        # === PATTERN_CONSTRAINT ===
        ("Expected `str` matching regex '\\d+' - at `$.code`",
         ('code',)),
        # === NUMERIC_CONSTRAINT (int) ===
        ("Expected `int` >= 0 - at `$.age`",
         ('age',)),
        # === TIMEZONE_CONSTRAINT ===
        ("Expected datetime with a timezone component - at `$.event_at`",
         ('event_at',)),
        # === ENCODE_ERROR (no path suffix expected) ===
        ("Can't encode strings longer than 2**32 - 1",
         ()),
        # === JSON_MALFORMED ===
        ("JSON is malformed: invalid character (byte 1)",
         ()),
        # === MSGPACK_MALFORMED ===
        ("MessagePack data is malformed: invalid opcode '\\xc1' (byte 3)",
         ()),
        # === WRAPPED_ERROR ===
        ("passwords cannot be the same - at $",
         ()),
    ])
    def test_paths_across_error_types(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path


# ======================================================================
# Path start and end forms
# ======================================================================

class TestPathStartEnd:
    """Paths that start or end with specific component types."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # === Start forms ===
        ("Expected `int`, got `str` - at `$`",
         ()),                             # just root, no components
        ("Expected `int`, got `str` - at `$[0]`",
         (0,)),                           # root → index
        ("Expected `int`, got `str` - at `$[...]`",
         ('...',)),                       # root → dict key
        ("Expected `int`, got `str` - at `$.name`",
         ('name',)),                      # root → field

        # === End forms ===
        ("Expected `int`, got `str` - at `$.data.items[0]`",
         ('data', 'items', 0)),           # ends with index
        ("Expected `int`, got `str` - at `$.data.users[...]`",
         ('data', 'users', '...')),       # ends with dict key
        ("Expected `int`, got `str` - at `$.data`",
         ('data',)),                      # ends with field

        # === Single component variant ===
        ("Expected `int`, got `str` - at `$[0]`",
         (0,)),
        ("Expected `int`, got `str` - at `$.0`",
         ('0',)),
    ])
    def test_path_start_end(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path


# ======================================================================
# Indices
# ======================================================================

class TestIndices:
    """Numeric index [N] forms."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # Single index, no field
        ("Expected `int`, got `str` - at `$[0]`", (0,)),
        ("Expected `int`, got `str` - at `$[1]`", (1,)),
        ("Expected `int`, got `str` - at `$[9]`", (9,)),
        ("Expected `int`, got `str` - at `$[10]`", (10,)),
        ("Expected `int`, got `str` - at `$[99]`", (99,)),
        ("Expected `int`, got `str` - at `$[100]`", (100,)),
        ("Expected `int`, got `str` - at `$[999999]`", (999999,)),
        ("Expected `int`, got `str` - at `$[2147483647]`", (2147483647,)),

        # Field then index
        ("Expected `int`, got `str` - at `$.data[0]`", ('data', 0)),
        ("Expected `int`, got `str` - at `$.items[5]`", ('items', 5)),

        # Consecutive indices
        ("Expected `int`, got `str` - at `$[0][1]`", (0, 1)),
        ("Expected `int`, got `str` - at `$[0][1][2]`", (0, 1, 2)),
        ("Expected `int`, got `str` - at `$[0][1][2][3]`", (0, 1, 2, 3)),

        # Field with consecutive indices
        ("Expected `int`, got `str` - at `$.matrix[0][1]`", ('matrix', 0, 1)),
        ("Expected `int`, got `str` - at `$.cube[0][1][2]`", ('cube', 0, 1, 2)),

        # Dict key then index
        ("Expected `int`, got `str` - at `$[...][0]`", ('...', 0)),
        ("Expected `int`, got `str` - at `$.items[...][1]`", ('items', '...', 1)),

        # Index then dict key
        ("Expected `int`, got `str` - at `$[0][...]`", (0, '...')),

        # Negative index (edge case — msgspec doesn't produce these)
        # but parser should handle
        ("Expected `int`, got `str` - at `$[-1]`", (-1,)),
    ])
    def test_indices(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path

    def test_large_index(self):
        """Very large index value."""
        error = "Expected `int`, got `str` - at `$.data[1000000]`"
        assert get_error_path(error) == ('data', 1000000)

    def test_many_indices(self):
        """Many consecutive indices."""
        indices = ''.join(f'[{i}]' for i in range(50))
        error = f"Expected `int`, got `str` - at `$.data{indices}.end`"
        result = get_error_path(error)
        expected = ('data',) + tuple(range(50)) + ('end',)
        assert result == expected
        assert len(result) == 52


# ======================================================================
# Dict keys
# ======================================================================

class TestDictKeys:
    """Dict key [...] marker forms."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # Standalone dict key
        ("Expected `int`, got `str` - at `$[...]`", ('...',)),

        # Dict key after field
        ("Expected `int`, got `str` - at `$.users[...]`", ('users', '...')),
        ("Expected `int`, got `str` - at `$.data[...]`", ('data', '...')),

        # Dict key then field
        ("Expected `int`, got `str` - at `$[...].name`", ('...', 'name')),
        ("Expected `int`, got `str` - at `$.items[...].val`", ('items', '...', 'val')),

        # Multiple dict keys
        ("Expected `int`, got `str` - at `$.data[...][...]`", ('data', '...', '...')),
        ("Expected `int`, got `str` - at `$[...][...].name`", ('...', '...', 'name')),

        # Dict key at intermediate position
        ("Expected `int`, got `str` - at `$.data[0][...].value`", ('data', 0, '...', 'value')),
        ("Expected `int`, got `str` - at `$.users[...][0].name`", ('users', '...', 0, 'name')),
    ])
    def test_dict_keys(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path

    def test_dict_key_key_path(self):
        """Invalid dict key path via '...key' marker."""
        error = "Expected `str`, got `int` - at `key` in `$.member_map`"
        assert get_error_path(error) == ('member_map', '...key')

    def test_many_dict_keys(self):
        """Many consecutive dict keys."""
        keys = '[...]' * 10
        error = f"Expected `int`, got `str` - at `$.data{keys}.end`"
        result = get_error_path(error)
        assert result[0] == 'data'
        assert result[1:-1] == ('...',) * 10
        assert result[-1] == 'end'
        assert len(result) == 12


# ======================================================================
# String-digit field names
# ======================================================================

class TestStringDigitFields:
    """Dot-separated string digits vs. bracket int indices."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # Dot-separated digits are strings
        ("Expected `int`, got `str` - at `$.123`", ('123',)),
        ("Expected `int`, got `str` - at `$.0`", ('0',)),
        ("Expected `int`, got `str` - at `$.00`", ('00',)),
        ("Expected `int`, got `str` - at `$.01`", ('01',)),
        ("Expected `int`, got `str` - at `$.999999`", ('999999',)),

        # Bracket-enclosed digits are integers
        ("Expected `int`, got `str` - at `$[0]`", (0,)),
        ("Expected `int`, got `str` - at `$[00]`", (0,)),  # int('00') = 0

        # Mixed: dot digits + bracket int
        ("Expected `int`, got `str` - at `$.data.456[0].name`",
         ('data', '456', 0, 'name')),

        # All dot-digit levels
        ("Expected `int`, got `str` - at `$.0.1.2`", ('0', '1', '2')),
        ("Expected `int`, got `str` - at `$.00.01.02`", ('00', '01', '02')),
    ])
    def test_string_digit_fields(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path


# ======================================================================
# Object missing / unknown field
# ======================================================================

class TestObjectFieldCases:
    """Object missing/unknown field name extraction."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # Missing at root (no path suffix)
        ("Object missing required field `id`", ('id',)),
        ("Object missing required field `name`", ('name',)),
        ("Object missing required field `user.email`",
         ('user.email',)),  # field name literally 'user.email'

        # Missing with sub-path
        ("Object missing required field `role` - at `$.users[...].profile`",
         ('users', '...', 'profile', 'role')),
        ("Object missing required field `age` - at `$.person`",
         ('person', 'age')),

        # Unknown at root (no path suffix)
        ("Object contains unknown field `extra_field`", ('extra_field',)),
        ("Object contains unknown field `unknown`", ('unknown',)),

        # Unknown with sub-path
        ("Object contains unknown field `color` - at `$.items[1]`",
         ('items', 1, 'color')),
        ("Object contains unknown field `tag` - at `$.config`",
         ('config', 'tag')),
    ])
    def test_object_field_cases(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path

    def test_missing_no_path_returns_field(self):
        """Missing field without path suffix returns just the field name."""
        assert get_error_path("Object missing required field `id`") == ('id',)

    def test_missing_with_empty_field_name(self):
        """Missing field with a backtick-like field — not a real backtick case."""
        error = "Object missing required field ` ` - at `$.outer`"
        assert get_error_path(error) == ('outer', ' ')

    def test_unknown_no_path_returns_field(self):
        assert get_error_path("Object contains unknown field `extra`") == ('extra',)


# ======================================================================
# Complex nested combinations
# ======================================================================

class TestComplexNested:
    """Deeply nested paths mixing fields, indices, and dict keys."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # field → index → field
        ("Expected `int`, got `str` - at `$.items[0].details.name`",
         ('items', 0, 'details', 'name')),

        # field → dict → field
        ("Expected `int`, got `str` - at `$.users[...].profile`",
         ('users', '...', 'profile')),

        # field → dict → index
        ("Expected `int`, got `str` - at `$.users[...].roles[0]`",
         ('users', '...', 'roles', 0)),

        # field → index → index → field
        ("Expected `int`, got `str` - at `$.matrix[0][1].value`",
         ('matrix', 0, 1, 'value')),

        # field → dict → dict → field
        ("Expected `int`, got `str` - at `$.users[...][...].name`",
         ('users', '...', '...', 'name')),

        # field → dict → index → field
        ("Expected `int`, got `str` - at `$.data[...][0].value`",
         ('data', '...', 0, 'value')),

        # field → index → dict → field
        ("Expected `int`, got `str` - at `$.data[0][...].value`",
         ('data', 0, '...', 'value')),

        # 7-component deep mix
        ("Expected `int`, got `str` - at "
         "`$.data[0].users[...].permissions[1].action`",
         ('data', 0, 'users', '...', 'permissions', 1, 'action')),

        # Dict key at start
        ("Expected `int`, got `str` - at `$[...].name`",
         ('...', 'name')),

        # Index at start, field at end
        ("Expected `int`, got `str` - at `$[0].value`",
         (0, 'value')),

        # All three types
        ("Expected `int`, got `str` - at `$.a[0][...].b[1]`",
         ('a', 0, '...', 'b', 1)),
    ])
    def test_complex_nested(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path

    def test_deeply_nested(self):
        """Very deeply nested path with many components."""
        error = ("Expected `int`, got `str` - at "
                 "`$.a.b[0].c.d[1].e[...].f.g[2].h`")
        assert get_error_path(error) == (
            'a', 'b', 0, 'c', 'd', 1, 'e', '...', 'f', 'g', 2, 'h'
        )


# ======================================================================
# Unicode in field names
# ======================================================================

class TestUnicodeFields:
    """Unicode characters in field names."""

    @pytest.mark.parametrize("error_string, expected_path", [
        ("Expected `int`, got `str` - at `$.用户`", ('用户',)),
        ("Expected `int`, got `str` - at `$.名前`", ('名前',)),
        ("Expected `int`, got `str` - at `$.имя`", ('имя',)),
        ("Expected `int`, got `str` - at `$.اسم`", ('اسم',)),
        ("Expected `int`, got `str` - at `$.使用者.name`",
         ('使用者', 'name')),
        ("Expected `int`, got `str` - at `$.emoji😊`", ('emoji😊',)),
        ("Expected `int`, got `str` - at `$.café`", ('café',)),
        ("Expected `int`, got `str` - at `$.über.name`",
         ('über', 'name')),
        ("Expected `int`, got `str` - at `$.数据[0]`",
         ('数据', 0)),
        ("Expected `int`, got `str` - at `$.users[...].名前`",
         ('users', '...', '名前')),
    ])
    def test_unicode_fields(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path

    def test_unicode_in_object_field(self):
        """Unicode in missing/unknown field name."""
        cases = [
            ("Object missing required field `ユーザーID`",
             ('ユーザーID',)),
            ("Object contains unknown field `未知` - at `$.data`",
             ('data', '未知')),
            ("Object missing required field `名前` - at `$.profile`",
             ('profile', '名前')),
        ]
        for error, expected in cases:
            assert get_error_path(error) == expected


# ======================================================================
# Edge cases — no path or malformed
# ======================================================================

class TestEdgeCases:
    """Messages with no path or malformed inputs."""

    @pytest.mark.parametrize("error_string, expected_path", [
        # No path components
        ("Expected `int`, got `str`", ()),
        ("Expected `int`, got `str` - at $", ()),
        ("", ()),
        ("A completely unrelated error message", ()),

        # Messages that look like they have paths but don't
        ("Expected `int`, got `str` at $.foo", ()),
        ("Invalid value at $.path", ()),

        # Only whitespace
        ("   ", ()),

        # Numbers and symbols
        ("12345", ()),
        ("!@#$%", ()),
    ])
    def test_no_path(self, error_string, expected_path):
        assert get_error_path(error_string) == expected_path

    def test_path_with_special_field_name(self):
        """Field names with special but allowed characters."""
        cases = [
            ("Expected `int`, got `str` - at `$.field_name`", ('field_name',)),
            ("Expected `int`, got `str` - at `$.field-name`", ('field-name',)),
            ("Expected `int`, got `str` - at `$.field.name`", ('field', 'name')),
            ("Expected `int`, got `str` - at `$.field123`", ('field123',)),
            ("Expected `int`, got `str` - at `$.FIELD`", ('FIELD',)),
            ("Expected `int`, got `str` - at `$.Mixed_Case_Name`",
             ('Mixed_Case_Name',)),
        ]
        for error, expected in cases:
            assert get_error_path(error) == expected


# ======================================================================
# Stress tests
# ======================================================================

class TestStress:
    """Stress tests for path parsing robustness."""

    def test_very_long_field_name(self):
        """A single very long field name should not crash."""
        field = 'a' * 10000
        error = f"Expected `int`, got `str` - at `$.{field}`"
        result = get_error_path(error)
        assert result == (field,)

    def test_very_long_path(self):
        """A long dotted path should not crash."""
        components = [f'field{i}' for i in range(100)]
        path = '.'.join(components)
        error = f"Expected `int`, got `str` - at `$.{path}`"
        result = get_error_path(error)
        assert result == tuple(components)

    def test_path_with_null_byte(self):
        """Field names with null bytes should not crash."""
        error = "Expected `int`, got `str` - at `$.field\x00name`"
        result = get_error_path(error)
        assert result == ("field\x00name",)

    def test_path_with_newline(self):
        """Field names with newlines should not crash."""
        error = "Expected `int`, got `str` - at `$.field\nname`"
        result = get_error_path(error)
        assert result == ("field\nname",)

    def test_path_with_tab(self):
        """Field names with tabs should not crash."""
        error = "Expected `int`, got `str` - at `$.field\tname`"
        result = get_error_path(error)
        assert result == ("field\tname",)
