import pytest
from msgspec import NODEFAULT, Struct

import msgspec.msgpack
from msgspecerror.const import ErrorType
from msgspecerror.repair import load_msgpack_with_default


class RepairableModel(Struct):
    a: int = 42
    b: str = "default"


class UnrepairableModel(Struct):
    a: int
    b: str


# Each entry: (name, malformed_bytes, expected_reason_substring)
MSGPACK_MALFORMED_CASES = [
    ("invalid opcode",  b'\xc1',  "invalid opcode"),
    ("trailing bytes",  msgspec.msgpack.encode({"a": 1}) + b'\xc1',  "trailing characters"),
]


class TestMsgpackMalformedRepair:
    """End-to-end tests: malformed msgpack through the full repair pipeline."""

    @pytest.mark.parametrize("name,data,expected_reason", MSGPACK_MALFORMED_CASES)
    def test_all_malformed_reasons_repairable(self, name, data, expected_reason):
        """Every documented MSGPACK_MALFORMED reason for a repairable model."""
        result, errors = load_msgpack_with_default(data, RepairableModel)
        assert result == RepairableModel(a=42, b="default"), f"failed for {name}"
        assert len(errors) == 1, f"expected 1 error for {name}"
        assert errors[0].type is ErrorType.MSGPACK_MALFORMED, f"wrong type for {name}"
        assert expected_reason in errors[0].msg, f"wrong reason for {name}"

    @pytest.mark.parametrize("name,data,expected_reason", MSGPACK_MALFORMED_CASES)
    def test_all_malformed_reasons_unrepairable(self, name, data, expected_reason):
        """Every documented MSGPACK_MALFORMED reason for an unrepairable model."""
        result, errors = load_msgpack_with_default(data, UnrepairableModel)
        assert result is NODEFAULT, f"should be NODEFAULT for {name}"
        assert len(errors) == 1, f"expected 1 error for {name}"
        assert errors[0].type is ErrorType.MSGPACK_MALFORMED, f"wrong type for {name}"

    # -- Truncated data (special message format) --

    def test_truncated_repairable(self):
        data = b'\xa5abc'
        result, errors = load_msgpack_with_default(data, RepairableModel)
        assert result == RepairableModel(a=42, b="default")
        assert len(errors) == 1
        assert errors[0].msg == "Input data was truncated"
        # Format-ambiguous: parse_msgspec_error returns WRAPPED_ERROR
        assert errors[0].type is ErrorType.WRAPPED_ERROR

    def test_truncated_unrepairable(self):
        data = b'\xa5abc'
        result, errors = load_msgpack_with_default(data, UnrepairableModel)
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].type is ErrorType.WRAPPED_ERROR
