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

# A map used as a map key decodes to an unhashable object, the map itself is
# unable to be such a key. Reported as MSGPACK_MALFORMED since msgspec 0.22.0,
# older versions leak a `TypeError: unhashable type: 'dict'` instead.
UNHASHABLE_KEY_DATA = b'\x81\x81\x01\x01\x01'


def _supports_hashable_key_error():
    """
    Whether the installed msgspec reports an unhashable map key as a DecodeError

    Returns:
        bool: True if the map key check is added in the decoder
    """
    try:
        msgspec.msgpack.decode(UNHASHABLE_KEY_DATA, type=dict)
    except msgspec.DecodeError:
        return True
    except TypeError:
        return False
    return False


SUPPORTS_HASHABLE_KEY_ERROR = _supports_hashable_key_error()

requires_hashable_key_error = pytest.mark.skipif(
    not SUPPORTS_HASHABLE_KEY_ERROR,
    reason='the unhashable map key message needs msgspec 0.22.0+',
)


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
        assert errors[0].type is ErrorType.DATA_TRUNCATED

    def test_truncated_unrepairable(self):
        data = b'\xa5abc'
        result, errors = load_msgpack_with_default(data, UnrepairableModel)
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].type is ErrorType.DATA_TRUNCATED

    # -- Unhashable map key (msgspec 0.22.0+) --

    @requires_hashable_key_error
    def test_unhashable_key_repairable(self):
        result, errors = load_msgpack_with_default(UNHASHABLE_KEY_DATA, RepairableModel)
        assert result == RepairableModel(a=42, b="default")
        assert len(errors) == 1
        assert errors[0].type is ErrorType.MSGPACK_MALFORMED
        assert 'map keys must be hashable' in errors[0].msg

    @requires_hashable_key_error
    def test_unhashable_key_unrepairable(self):
        result, errors = load_msgpack_with_default(UNHASHABLE_KEY_DATA, UnrepairableModel)
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].type is ErrorType.MSGPACK_MALFORMED
        assert 'map keys must be hashable' in errors[0].msg
