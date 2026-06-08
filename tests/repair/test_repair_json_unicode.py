from typing import Dict

from msgspec import NODEFAULT

from msgspecerror.const import ErrorType
from msgspecerror.repair import load_json_with_default
from .test_repair_json import Simple, WithDefaults


class TestLoadJsonWithDefaultUTF8:
    """
    Test suite for UTF-8 error handling in `load_json_with_default`.
    """
    # An invalid UTF-8 byte sequence (an isolated continuation byte)
    invalid_utf8_sequence = b'\x80'

    # --- 'strict' mode tests ---

    def test_utf_error_strict_unrepairable_model(self):
        """
        Test 'strict' mode with an unrepairable model.
        It should catch the UnicodeDecodeError, treat it as a root error, and fail
        because the model has no default constructor.
        """
        data = b'{"a": 1, "b": "bad-' + self.invalid_utf8_sequence + b'-string"}'
        result, errors = load_json_with_default(data, Simple, utf8_error='strict')

        # Repair fails because Simple cannot be default-constructed.
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ()  # Root-level error
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert 'utf-8' in errors[0].msg  # Check that the error message contains "utf-8"

    def test_utf_error_strict_repairable_model(self):
        """
        Test 'strict' mode with a repairable model.
        It should catch the UnicodeDecodeError and fall back to the model's default.
        """
        data = b'{"a": 1, "b": "bad-' + self.invalid_utf8_sequence + b'-string"}'
        result, errors = load_json_with_default(data, WithDefaults, utf8_error='strict')

        # Should successfully fall back to a default instance of WithDefaults.
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ()  # Root-level error
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert 'utf-8' in errors[0].msg

    # --- 'replace' mode tests ---

    def test_utf_error_replace_in_value(self):
        """
        Test 'replace' mode. The invalid sequence should be replaced with U+FFFD,
        and an error should be logged.
        """
        data = b'{"a": 1, "b": "value' + self.invalid_utf8_sequence + b'end"}'
        result, errors = load_json_with_default(data, Simple, utf8_error='replace')

        # The result should contain the replacement character.
        replacement_char = '\ufffd'
        assert result == Simple(a=1, b=f"value{replacement_char}end")

        # An error should be reported at the correct location.
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == ('b',)

    def test_utf_error_replace_in_key(self):
        """Test 'replace' mode when the error occurs in a dictionary key."""
        data = b'{"key' + self.invalid_utf8_sequence + b'": "value"}'
        model = Dict[str, str]
        result, errors = load_json_with_default(data, model, utf8_error='replace')

        # The resulting key should contain the replacement character.
        replacement_char = '\ufffd'
        expected_key = f"key{replacement_char}"
        assert result == {expected_key: "value"}

        # An error should be reported for the key.
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == (expected_key,)

    def test_utf_error_replace_with_other_errors(self):
        """
        Test 'replace' mode combined with other validation errors.
        Both types of errors should be handled and reported.
        """
        # 'a' has a type error, 'b' has a unicode error
        data = b'{"a": "not-an-int", "b": "value' + self.invalid_utf8_sequence + b'end"}'
        result, errors = load_json_with_default(data, WithDefaults, utf8_error='replace')

        # 'a' should be repaired to its default, 'b' should contain the replacement char.
        replacement_char = '\ufffd'
        assert result.a == 42
        assert result.b == f"value{replacement_char}end"
        assert result.c == []
        assert result.d is None

        # Both the unicode error and the type error should be reported.
        assert len(errors) == 2
        error_types = {e.type for e in errors}
        error_locs = {e.loc for e in errors}
        assert ErrorType.UNICODE_DECODE_ERROR in error_types
        assert ErrorType.TYPE_MISMATCH in error_types
        assert ('b',) in error_locs
        assert ('a',) in error_locs

    def test_utf_error_replace_multiple_locations(self):
        """
        Test that multiple, distinct unicode errors are all found and reported.
        """
        bad_byte = self.invalid_utf8_sequence
        # JSON with two unicode errors: one in a key, one in a nested value.
        data = (
            b'{"key' + bad_byte + b'": "value", '
            b'"nested": {"data": "info' + bad_byte + b'end"}}'
        )
        model = Dict[str, object]  # Use a general model to avoid type errors.
        result, errors = load_json_with_default(data, model, utf8_error='replace')

        # Check that the result has the replacement characters in the right places.
        replacement_char = '\ufffd'
        expected_key = f"key{replacement_char}"
        assert result[expected_key] == "value"
        assert result["nested"]["data"] == f"info{replacement_char}end"

        # Both errors should be collected.
        assert len(errors) == 2
        error_locs = {e.loc for e in errors}
        assert (expected_key,) in error_locs
        assert ("nested", "data") in error_locs
        for e in errors:
            assert e.type == ErrorType.UNICODE_DECODE_ERROR

    # --- 'ignore' mode tests ---

    def test_utf_error_ignore(self):
        """
        Test 'ignore' mode. The invalid byte sequence should be removed,
        and no error should be reported.
        """
        data = b'{"a": 1, "b": "value' + self.invalid_utf8_sequence + b'end"}'
        result, errors = load_json_with_default(data, Simple, utf8_error='ignore')

        # The invalid sequence should be gone, and no errors reported.
        assert result == Simple(a=1, b="valueend")
        assert errors == []

    def test_utf_error_ignore_in_key(self):
        """Test 'ignore' mode when the error occurs in a dictionary key."""
        data = b'{"key' + self.invalid_utf8_sequence + b'mid": "value"}'
        model = Dict[str, str]
        result, errors = load_json_with_default(data, model, utf8_error='ignore')

        # The invalid sequence in the key should be gone.
        assert result == {"keymid": "value"}
        assert errors == []

    # --- Root-Level Unicode Errors ---

    def test_utf_error_at_root_strict(self):
        """
        Test 'strict' mode when the entire input is an invalid UTF-8 sequence.
        It should be treated as a root-level error.
        """
        # The model is repairable, but the error happens before parsing can even start.
        result, errors = load_json_with_default(
            self.invalid_utf8_sequence, WithDefaults, utf8_error='strict'
        )

        # It should fall back to the model's default.
        assert result == WithDefaults()
        assert len(errors) == 1
        assert errors[0].loc == ()
        assert errors[0].type == ErrorType.JSON_MALFORMED
        assert 'JSON is malformed' in errors[0].msg

    def test_utf_error_at_root_replace(self):
        """
        Test 'replace' mode when the entire input is an invalid UTF-8 sequence.
        The result should be the replacement character, which then fails model validation.
        """
        # The input b'\x80' becomes '�'. This is not a valid JSON for a Struct model.
        result, errors = load_json_with_default(
            self.invalid_utf8_sequence, WithDefaults, utf8_error='replace'
        )

        # The decoded object '�' cannot be converted to `WithDefaults`.
        # This becomes a root-level `DecodeError` or `ValidationError` because
        # '�' is not valid JSON for a struct. The system then tries to repair,
        # succeeding because `WithDefaults` has a default constructor.
        assert result == WithDefaults()

        # The process is:
        # 1. UnicodeDecodeError caught.
        # 2. Data becomes b'\xef\xbf\xbd' ('�').
        # 3. Retry `decode_json(b'\xef\xbf\xbd', type=WithDefaults)` -> fails.
        # 4. _handle_root_error is called, which creates a default WithDefaults.
        # We expect one final root error.
        assert len(errors) == 1
        assert errors[0].loc == ()  # The final error is at the root.

    def test_utf_error_at_root_ignore(self):
        """
        Test 'ignore' mode when the entire input is an invalid UTF-8 sequence.
        The result should be an empty byte string, which is a DecodeError.
        """
        # The input b'\x80' becomes b''. An empty string is not valid JSON.
        result, errors = load_json_with_default(
            self.invalid_utf8_sequence, WithDefaults, utf8_error='ignore'
        )

        # Decoding an empty byte string fails, so it falls back to the model's default.
        assert result == WithDefaults()
        assert len(errors) == 1
        assert errors[0].loc == ()
        assert errors[0].type == ErrorType.JSON_MALFORMED
        assert "JSON is malformed" in errors[0].msg  # The error should mention empty input.


class TestMixedUnicodeAndValidation:
    """
    Tests where both UnicodeDecodeError and ValidationError occur in sequence.

    Two code paths exist:
    - Path A: ``decode_json(data, type=model)`` raises ``UnicodeDecodeError``
      first (outer handler), then after repair the retry raises a
      ``ValidationError``.
    - Path B: ``decode_json(data, type=model)`` raises ``ValidationError``
      first, but ``decode_json(data)`` (raw) raises ``UnicodeDecodeError``.
      After unicode repair the retry hits a ``ValidationError``.
    """
    invalid = b'\x80'

    # --- Path A: UnicodeDecodeError first (outer handler) ---

    def test_path_a_unicode_then_field_validation(self):
        """
        UnicodeDecodeError is raised by the typed decode first (a ``str`` field
        contains invalid UTF-8). After repair, a ValidationError occurs on
        retry for a different field with a type mismatch.
        """
        # Field 'b' (str) has invalid UTF-8 → UnicodeDecodeError
        # Field 'a' (int) has "not-int" → ValidationError on retry
        data = b'{"b": "val' + self.invalid + b'", "a": "not-int"}'
        result, errors = load_json_with_default(data, WithDefaults, utf8_error='replace')

        replacement_char = '\ufffd'
        assert result == WithDefaults(a=42, b=f"val{replacement_char}", c=[])
        assert len(errors) == 2

        error_types = {e.type for e in errors}
        assert ErrorType.UNICODE_DECODE_ERROR in error_types
        assert ErrorType.TYPE_MISMATCH in error_types

        error_locs = {e.loc for e in errors}
        assert ('b',) in error_locs
        assert ('a',) in error_locs

    def test_path_a_dict_str_int(self):
        """
        ``Dict[str, int]`` model: the typed decode encounters invalid UTF-8
        (since all dict values are *str-typed* from JSON), triggering
        UnicodeDecodeError. After repair, the value is still a string and
        can't be converted to int → TYPE_MISMATCH.
        """
        data = b'{"x": "val' + self.invalid + b'", "y": "not-int"}'
        result, errors = load_json_with_default(data, Dict[str, int], utf8_error='replace')

        # Both 'x' and 'y' are strings that can't become int → dict is cleared
        assert result == {}
        assert len(errors) >= 2

        error_types = {e.type for e in errors}
        assert ErrorType.UNICODE_DECODE_ERROR in error_types
        assert ErrorType.TYPE_MISMATCH in error_types

    # --- Path B: ValidationError first, then inner UnicodeDecodeError ---

    def test_path_b_validation_then_unicode(self):
        """
        The typed decode raises ``ValidationError`` on a field with a type
        mismatch first, before encountering a second field's invalid UTF-8.
        The raw decode then hits ``UnicodeDecodeError``, triggering repair.
        """
        # Field 'a' (int) has "not-int" → ValidationError first
        # Field 'b' (str) has invalid UTF-8 → UnicodeDecodeError in raw decode
        data = b'{"a": "not-int", "b": "val' + self.invalid + b'"}'
        result, errors = load_json_with_default(data, WithDefaults, utf8_error='replace')

        replacement_char = '\ufffd'
        assert result == WithDefaults(a=42, b=f"val{replacement_char}", c=[])
        assert len(errors) == 2

        error_types = {e.type for e in errors}
        assert ErrorType.UNICODE_DECODE_ERROR in error_types
        assert ErrorType.TYPE_MISMATCH in error_types

        error_locs = {e.loc for e in errors}
        assert ('b',) in error_locs
        assert ('a',) in error_locs

    # --- Mixed: multiple unicode points + multiple validation errors ---

    def test_mixed_multiple_invalid_list_items(self):
        """
        A ``List[int]`` field with all items invalid after unicode repair.
        The repair loop pops each item one by one, eventually clearing
        the list entirely, while other valid fields are preserved.
        """
        # All items in `c` are strings that can't become int → each pop
        # exposes the same error at index 0 because the list shifts left.
        data = (
            b'{"b": "bad' + self.invalid
            + b'", "c": ["x' + self.invalid
            + b'", "y"], "a": "not-int"}'
        )
        result, errors = load_json_with_default(data, WithDefaults, utf8_error='replace')

        replacement_char = '\ufffd'
        # Field 'b' preserved with repaired value; 'c' cleared; 'a' repaired to 42
        assert result == WithDefaults(a=42, b=f"bad{replacement_char}", c=[])
        # Unicode errors on b and c[0], plus type mismatches for list items and a
        assert len(errors) >= 3

        error_types = {e.type for e in errors}
        assert ErrorType.UNICODE_DECODE_ERROR in error_types
        assert ErrorType.TYPE_MISMATCH in error_types

    # --- Strict mode: mixed errors return root default ---

    def test_mixed_strict_mode(self):
        """
        In strict mode, the first UnicodeDecodeError triggers a root fallback
        without retrying. The model's default constructor is used instead.
        """
        data = b'{"a": "not-int", "b": "val' + self.invalid + b'"}'
        result, errors = load_json_with_default(data, WithDefaults, utf8_error='strict')

        # Falls back to the model's default construction
        assert result == WithDefaults()
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == ()

    def test_mixed_strict_mode_root_type_mismatch(self):
        """
        In strict mode with a root type mismatch that exposes invalid UTF-8
        during raw decode: the inner UnicodeDecodeError is not handled in
        strict mode, so a root error is returned.
        """
        # Root type mismatch (int) + invalid UTF-8 in value
        data = b'{"a": "val' + self.invalid + b'"}'
        result, errors = load_json_with_default(data, int, utf8_error='strict')

        assert result is NODEFAULT
        assert len(errors) >= 1
        assert errors[-1].loc == ()
