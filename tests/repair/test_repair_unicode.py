from msgspecerror.const import ErrorType
from msgspecerror.repair_unicode import _collect_unicode_replace


class TestCollectUnicodeReplace:
    """Tests for ``collect_unicode_replace``."""

    def test_root_string_with_replacement(self):
        """Root string containing U+FFFD returns one error."""
        errors = _collect_unicode_replace("bad\ufffdstr")
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == ()

    def test_root_string_clean(self):
        """Root string without U+FFFD returns no errors."""
        errors = _collect_unicode_replace("hello")
        assert errors == []

    def test_non_container_root(self):
        """Root int, float, etc. returns no errors."""
        assert _collect_unicode_replace(42) == []
        assert _collect_unicode_replace(3.14) == []
        assert _collect_unicode_replace(True) == []
        assert _collect_unicode_replace(None) == []

    def test_dict_value_with_replacement(self):
        """Dict string value containing U+FFFD is detected."""
        obj = {"name": "bad\ufffdval"}
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == ("name",)

    def test_dict_key_with_replacement(self):
        """Dict key containing U+FFFD is detected."""
        obj = {"bad\ufffdkey": "value"}
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == ("bad\ufffdkey",)

    def test_dict_clean(self):
        """Dict without U+FFFD returns no errors."""
        obj = {"a": 1, "b": "hello"}
        errors = _collect_unicode_replace(obj)
        assert errors == []

    def test_list_item_with_replacement(self):
        """List string item containing U+FFFD is detected."""
        obj = ["good", "bad\ufffditem"]
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == (1,)

    def test_list_clean(self):
        """List without U+FFFD returns no errors."""
        obj = ["a", "b", 42]
        errors = _collect_unicode_replace(obj)
        assert errors == []

    def test_nested_dict_in_dict(self):
        """Nested dict values are traversed recursively."""
        obj = {"outer": {"inner": "bad\ufffd"}}
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 1
        assert errors[0].type == ErrorType.UNICODE_DECODE_ERROR
        assert errors[0].loc == ("outer", "inner")

    def test_nested_list_in_dict(self):
        """List nested inside a dict is traversed."""
        obj = {"items": ["good", "bad\ufffd"]}
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1)

    def test_nested_dict_in_list(self):
        """Dict nested inside a list is traversed."""
        obj = [{"field": "bad\ufffd"}]
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 1
        assert errors[0].loc == (0, "field")

    def test_deeply_nested_mixed(self):
        """Mixed deep nesting produces correct paths."""
        obj = {
            "level1": [
                {"key\ufffdA": "ok"},
                {"keyB": "bad\ufffdval"},
            ],
        }
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 2
        locs = {e.loc for e in errors}
        assert ("level1", 0, "key\ufffdA") in locs
        assert ("level1", 1, "keyB") in locs

    def test_multiple_errors_in_same_dict(self):
        """Multiple fields with U+FFFD in the same dict are all reported."""
        obj = {"a": "bad\ufffd", "b": "also\ufffdbad"}
        errors = _collect_unicode_replace(obj)
        assert len(errors) == 2
        locs = {e.loc for e in errors}
        assert ("a",) in locs
        assert ("b",) in locs

    def test_empty_dict(self):
        """Empty dict returns no errors."""
        assert _collect_unicode_replace({}) == []

    def test_empty_list(self):
        """Empty list returns no errors."""
        assert _collect_unicode_replace([]) == []
