"""
`Literal[True]` / `Literal[False]` support, added in msgspec 0.22.0

The opposite bool is reported as
"Invalid enum value True/False - at <Path>" (ErrorType.INVALID_ENUM_VALUE),
the repair falls back to the field default.
"""
from typing import Literal

import msgspec
import pytest
from msgspec import NODEFAULT, Struct

from msgspecerror.const import ErrorType
from msgspecerror.repair import load_json_with_default, load_msgpack_with_default


def _supports_literal_bool():
    """
    Whether the installed msgspec supports `Literal[True]` / `Literal[False]`

    The bool literals are added in msgspec 0.22.0, older versions raise a
    `TypeError` at decode time for such a type.

    Returns:
        bool: True if the bool literal types are supported
    """
    try:
        msgspec.json.decode(b'true', type=Literal[True])
    except TypeError:
        return False
    return True


SUPPORTS_LITERAL_BOOL = _supports_literal_bool()

requires_literal_bool = pytest.mark.skipif(
    not SUPPORTS_LITERAL_BOOL,
    reason='Literal[True] / Literal[False] need msgspec 0.22.0+',
)


class Flags(Struct):
    """A struct whose bool literal fields have matching defaults."""
    enabled: Literal[True] = True
    disabled: Literal[False] = False


class NoDefaultFlag(Struct):
    """A struct whose bool literal field has no default, it can't be repaired."""
    enabled: Literal[True]


@requires_literal_bool
class TestRepairLiteralBool:
    """End-to-end tests: a bool literal mismatch is repaired to the field default."""

    def test_json_repaired_to_field_default(self):
        data = b'{"enabled": false}'
        result, errors = load_json_with_default(data, Flags)

        assert result == Flags(enabled=True, disabled=False)
        assert len(errors) == 1
        assert errors[0].type is ErrorType.INVALID_ENUM_VALUE
        assert errors[0].loc == ("enabled",)
        assert errors[0].msg == "Invalid enum value False - at `$.enabled`"

    def test_json_valid_input_is_untouched(self):
        """Only the failing field is repaired, the valid sibling is kept."""
        data = b'{"enabled": true, "disabled": false}'
        result, errors = load_json_with_default(data, Flags)

        assert result == Flags(enabled=True, disabled=False)
        assert errors == []

    def test_msgpack_repaired_to_field_default(self):
        data = msgspec.msgpack.encode({"disabled": True})
        result, errors = load_msgpack_with_default(data, Flags)

        assert result == Flags(enabled=True, disabled=False)
        assert len(errors) == 1
        assert errors[0].type is ErrorType.INVALID_ENUM_VALUE
        assert errors[0].loc == ("disabled",)
        assert errors[0].msg == "Invalid enum value True - at `$.disabled`"

    def test_unrepairable_without_default(self):
        """`Literal` is never guessed, a field without default falls back to the root default."""
        data = b'{"enabled": false}'
        result, errors = load_json_with_default(data, NoDefaultFlag, guess_default=True)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].type is ErrorType.INVALID_ENUM_VALUE
        assert errors[0].loc == ("enabled",)
