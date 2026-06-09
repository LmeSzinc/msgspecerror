"""
Benchmark for parse_msgspec_error with representative classic error messages
and real msgspec ValidationError / DecodeError exceptions.

Covers every ErrorType family with one or two canonical messages per type.
"""
from enum import Enum
from typing import List

import msgspec

from msgspecerror import parse_msgspec_error
from tests.benchmark.perf import PerformanceTest

# ------------------------------------------------------------------
# Curated classic error messages — one per ErrorType family
# ------------------------------------------------------------------

STRING_ERRORS = [
    # TYPE_MISMATCH
    "Expected `int`, got `str` - at `$.user.age`",
    "Expected `int`, got `str` - at `$.items[1]`",
    "Expected `bytes`, got `int`",
    # TOKEN_TYPE_MISMATCH
    "Expected `str` - at `$.type`",
    "Expected `str` - at `key` in `$`",
    # MISSING_FIELD
    "Object missing required field `age` - at `$.user`",
    # UNKNOWN_FIELD
    "Object contains unknown field `extra` - at `$`",
    # ARRAY_LENGTH_CONSTRAINT
    "Expected `array` of length 2, got 3 - at `$.coords`",
    "Expected `array` of length >= 3",
    # OBJECT_LENGTH_CONSTRAINT
    "Expected `object` of length >= 1 - at `$.metadata`",
    # LENGTH_CONSTRAINT (str)
    "Expected `str` of length <= 32 - at `$.name`",
    "Expected `bytes` of length >= 8 - at `$.key`",
    # PATTERN_CONSTRAINT
    "Expected `str` matching regex '^\\d+$' - at `$.code`",
    # NUMERIC_CONSTRAINT
    "Expected `int` >= 0 - at `$.age`",
    "Expected `float` that's a multiple of 0.5",
    "Expected `decimal` <= 999999.99 - at `$.amount`",
    # TIMEZONE_CONSTRAINT
    "Expected `datetime` with a timezone component - at `$.event_at`",
    # INVALID_ENUM_VALUE
    "Invalid enum value 'blue' - at `$.color`",
    # INVALID_TAG_VALUE
    "Invalid value 'Fish' - at `$.type`",
    # Date/time/duration
    "Invalid RFC3339 encoded datetime - at `$.timestamp`",
    "Invalid ISO8601 duration - at `$.period`",
    # Out of range
    "Timestamp is out of range",
    "Integer value out of range - at `$.val`",
    "Number out of range - at `$.big`",
    # Other invalid values
    "Invalid UUID - at `$.id`",
    "Invalid base64 encoded string - at `$.data`",
    "Invalid decimal string - at `$.price`",
    "Invalid MessagePack timestamp: nanoseconds out of range",
    "Invalid epoch timestamp",
    # Wrapped / malformed / encode errors
    "custom construction error - at $",
    "JSON is malformed: trailing comma in array (byte 5)",
    "Input data was truncated",
    "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
]


def build_real_exceptions():
    """
    Build a list of real msgspec exception objects.

    Returns:
        list of (name, exception): for benchmark registration.
    """
    cases = []

    # 1. TYPE_MISMATCH — simple struct field
    class User(msgspec.Struct):
        age: int

    try:
        msgspec.json.decode(b'{"age": "bad"}', type=User)
    except msgspec.ValidationError as e:
        cases.append(("type_mismatch_simple", e))

    # 2. TYPE_MISMATCH — nested path
    class Inner(msgspec.Struct):
        value: float

    class Outer(msgspec.Struct):
        inner: Inner

    try:
        msgspec.json.decode(b'{"inner": {"value": "bad"}}', type=Outer)
    except msgspec.ValidationError as e:
        cases.append(("type_mismatch_nested", e))

    # 3. TYPE_MISMATCH — list index path
    class HasList(msgspec.Struct):
        items: List[int]

    try:
        msgspec.json.decode(b'{"items": [1, "bad", 3]}', type=HasList)
    except msgspec.ValidationError as e:
        cases.append(("type_mismatch_list", e))

    # 4. MISSING_FIELD
    try:
        msgspec.json.decode(b'{"name": "alice"}', type=User)
    except msgspec.ValidationError as e:
        cases.append(("missing_field", e))

    # 5. UNKNOWN_FIELD
    class Strict(msgspec.Struct, forbid_unknown_fields=True):
        name: str

    try:
        msgspec.json.decode(b'{"name": "alice", "extra": 1}', type=Strict)
    except msgspec.ValidationError as e:
        cases.append(("unknown_field", e))

    # 6. INVALID_ENUM_VALUE
    class Color(str, Enum):
        RED = "red"

    try:
        msgspec.json.decode(b'"blue"', type=Color)
    except msgspec.ValidationError as e:
        cases.append(("invalid_enum", e))

    # 7. JSON_MALFORMED (DecodeError)
    try:
        msgspec.json.decode(b'[1,2,]', type=object)
    except msgspec.DecodeError as e:
        cases.append(("json_malformed", e))

    # 8. WRAPPED_ERROR — __post_init__
    class Validating(msgspec.Struct):
        name: str

        def __post_init__(self):
            if self.name == "":
                raise ValueError("name cannot be empty")

    try:
        msgspec.json.decode(b'{"name": ""}', type=Validating)
    except msgspec.ValidationError as e:
        cases.append(("wrapped_error", e))

    # 9. DATA_TRUNCATED
    try:
        msgspec.json.decode(b'{"a": 1', type=object)
    except msgspec.DecodeError as e:
        cases.append(("data_truncated", e))

    return cases


def register_string_benchmarks(pref):
    """Register parse_msgspec_error with each classic error message string."""
    for msg in STRING_ERRORS:
        pref.register(parse_msgspec_error, msg)


def register_exception_benchmarks(pref):
    """Register parse_msgspec_error with real msgspec exception objects."""
    for name, exc in build_real_exceptions():
        pref.register(parse_msgspec_error, exc)


if __name__ == "__main__":
    with PerformanceTest() as pref:
        register_string_benchmarks(pref)
        register_exception_benchmarks(pref)
