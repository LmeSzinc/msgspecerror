import pytest
from msgspec import NODEFAULT, Struct

from msgspecerror.const import ErrorType
from msgspecerror.repair import load_json_with_default


class RepairableModel(Struct):
    a: int = 42
    b: str = "default"


class UnrepairableModel(Struct):
    a: int
    b: str


# Each entry: (name, malformed_bytes, expected_reason_substring)
# Data verified against msgspec 0.21.1
JSON_MALFORMED_CASES = [
    ("invalid character",              b'{"a": }',               "invalid character"),
    ("trailing characters",            b'{"a": 1}extra',         "trailing characters"),
    ("expected ',' or ']'",            b'[1 2]',                 "expected ',' or ']'"),
    ("expected ',' or '}'",            b'{"a": 1 "b"}',          "expected ',' or '}'"),
    ("expected ':'",                   b'{"a" 1}',               "expected ':'"),
    ("trailing comma in array",        b'[1,]',                  "trailing comma in array"),
    ("trailing comma in object",       b'{"a": 1,}',             "trailing comma in object"),
    ("object keys must be strings",    b'{1: 2}',                "object keys must be strings"),
    ("invalid number",                 b'[1.]',                  "invalid number"),
    ("invalid escape in string",       b'["\\q"]',               "invalid escape character in string"),
]


class TestJsonMalformedRepair:
    """End-to-end tests: malformed JSON through the full repair pipeline."""

    @pytest.mark.parametrize("name,data,expected_reason", JSON_MALFORMED_CASES)
    def test_all_malformed_reasons_repairable(self, name, data, expected_reason):
        """Every documented JSON_MALFORMED reason for a repairable model."""
        result, errors = load_json_with_default(data, RepairableModel)
        assert result == RepairableModel(a=42, b="default"), f"failed for {name}"
        assert len(errors) == 1, f"expected 1 error for {name}"
        assert errors[0].type is ErrorType.JSON_MALFORMED, f"wrong type for {name}"
        assert expected_reason in errors[0].msg, f"wrong reason for {name}"

    @pytest.mark.parametrize("name,data,expected_reason", JSON_MALFORMED_CASES)
    def test_all_malformed_reasons_unrepairable(self, name, data, expected_reason):
        """Every documented JSON_MALFORMED reason for an unrepairable model."""
        result, errors = load_json_with_default(data, UnrepairableModel)
        assert result is NODEFAULT, f"should be NODEFAULT for {name}"
        assert len(errors) == 1, f"expected 1 error for {name}"
        assert errors[0].type is ErrorType.JSON_MALFORMED, f"wrong type for {name}"

    # -- Truncated data (special message format) --

    def test_truncated_repairable(self):
        data = b'{"a": 1, "b": "test'
        result, errors = load_json_with_default(data, RepairableModel)
        assert result == RepairableModel(a=42, b="default")
        assert len(errors) == 1
        assert errors[0].msg == "Input data was truncated"
        # Format-ambiguous: parse_msgspec_error returns WRAPPED_ERROR
        assert errors[0].type is ErrorType.WRAPPED_ERROR

    def test_truncated_unrepairable(self):
        data = b'{"a": 1, "b": "test'
        result, errors = load_json_with_default(data, UnrepairableModel)
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].type is ErrorType.WRAPPED_ERROR
