"""
Real-case tests for Group 3: Constraint and Length Errors.
"""
from typing import Dict, List, Tuple
from typing_extensions import Annotated

import msgspec
import datetime
import pytest

from msgspecerror import parse_msgspec_error, ErrorCtx
from msgspecerror.const import ErrorType


class TestArrayLengthConstraintReal:
    """ARRAY_LENGTH_CONSTRAINT — triggered by tuple/NamedTuple length mismatches."""

    def test_fixtuple_too_few(self):
        """Expected `array` of length 2"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[1]', type=Tuple[int, int])
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.ARRAY_LENGTH_CONSTRAINT
        assert err.ctx.expected == "array"
        assert err.ctx.min_length == 2
        assert err.ctx.max_length == 2

    def test_fixtuple_too_many(self):
        """Expected `array` of length 2"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[1,2,3]', type=Tuple[int, int])
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.ARRAY_LENGTH_CONSTRAINT
        assert err.ctx.expected == "array"
        assert err.ctx.min_length == 2
        assert err.ctx.max_length == 2

    def test_fixtuple_nested(self):
        """Expected `array` of length 2 - at `$.coord`"""
        class HasCoord(msgspec.Struct):
            coord: Tuple[int, int]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"coord": [1,2,3]}',
                type=HasCoord,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.ARRAY_LENGTH_CONSTRAINT
        assert err.loc == ("coord",)
        assert err.ctx.expected == "array"
        assert err.ctx.min_length == 2
        assert err.ctx.max_length == 2

    def test_namedtuple_length(self):
        """Expected `array` of length 2"""
        from typing import NamedTuple

        class Point(NamedTuple):
            x: int
            y: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[1,2,3]', type=Point)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.ARRAY_LENGTH_CONSTRAINT
        assert err.ctx.expected == "array"
        assert err.ctx.min_length == 2
        assert err.ctx.max_length == 2


class TestObjectLengthConstraintReal:
    """OBJECT_LENGTH_CONSTRAINT — dict/metadata length violations."""

    def test_object_too_small(self):
        """Expected `object` of length >= 1"""
        T = Annotated[Dict[str, int], msgspec.Meta(min_length=1)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{}', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.OBJECT_LENGTH_CONSTRAINT
        assert err.ctx.expected == "object"
        assert err.ctx.min_length == 1

    def test_object_too_large(self):
        """Expected `object` of length <= 2"""
        T = Annotated[Dict[str, int], msgspec.Meta(max_length=2)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"a": 1, "b": 2, "c": 3}', type=T
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.OBJECT_LENGTH_CONSTRAINT
        assert err.ctx.expected == "object"
        assert err.ctx.max_length == 2

    def test_object_nested(self):
        """Expected `object` of length >= 1 - at `$.meta`"""
        class HasMeta(msgspec.Struct):
            meta: Annotated[Dict[str, int], msgspec.Meta(min_length=1)]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"meta": {}}',
                type=HasMeta,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.OBJECT_LENGTH_CONSTRAINT
        assert err.loc == ("meta",)
        assert err.ctx.expected == "object"
        assert err.ctx.min_length == 1


class TestLengthConstraintReal:
    """LENGTH_CONSTRAINT — str/bytes length violations."""

    def test_str_too_long(self):
        """Expected `str` of length <= 5"""
        T = Annotated[str, msgspec.Meta(max_length=5)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"hello world"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.LENGTH_CONSTRAINT
        assert err.ctx.expected == "str"
        assert err.ctx.max_length == 5

    def test_str_too_short(self):
        """Expected `str` of length >= 3"""
        T = Annotated[str, msgspec.Meta(min_length=3)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"ab"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.LENGTH_CONSTRAINT
        assert err.ctx.expected == "str"
        assert err.ctx.min_length == 3

    def test_str_length_range(self):
        """Expected `str` of length <= 4"""
        T = Annotated[str, msgspec.Meta(min_length=2, max_length=4)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"toolongstr"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.LENGTH_CONSTRAINT
        assert err.ctx.expected == "str"
        assert err.ctx.max_length == 4

    def test_str_length_nested(self):
        """Expected `str` of length <= 3 - at `$.name`"""
        class HasName(msgspec.Struct):
            name: Annotated[str, msgspec.Meta(max_length=3)]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"name": "toolong"}',
                type=HasName,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.LENGTH_CONSTRAINT
        assert err.loc == ("name",)
        assert err.ctx.expected == "str"
        assert err.ctx.max_length == 3

    def test_bytes_too_long(self):
        """Expected `bytes` of length <= 2"""
        T = Annotated[bytes, msgspec.Meta(max_length=2)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"AAAA"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.LENGTH_CONSTRAINT
        assert err.ctx.expected == "bytes"
        assert err.ctx.max_length == 2

    def test_bytes_too_short(self):
        """Expected `bytes` of length >= 8"""
        T = Annotated[bytes, msgspec.Meta(min_length=8)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"AA=="', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.LENGTH_CONSTRAINT
        assert err.ctx.expected == "bytes"
        assert err.ctx.min_length == 8


class TestPatternConstraintReal:
    """PATTERN_CONSTRAINT — regex pattern violations."""

    def test_simple_pattern(self):
        r"""Expected `str` matching regex '^\d+$'"""
        T = Annotated[str, msgspec.Meta(pattern=r"^\d+$")]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"abc"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.PATTERN_CONSTRAINT
        assert err.ctx.expected == "str"
        assert err.ctx.pattern == r"^\\d+$"

    def test_pattern_date_format(self):
        r"""Expected `str` matching regex '\d{4}-\d{2}-\d{2}'"""
        T = Annotated[str, msgspec.Meta(pattern=r"\d{4}-\d{2}-\d{2}")]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-date"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.PATTERN_CONSTRAINT
        assert err.ctx.expected == "str"
        assert err.ctx.pattern == r"\\d{4}-\\d{2}-\\d{2}"

    def test_pattern_nested(self):
        r"""Expected `str` matching regex '^[A-Z]+$' - at `$.code`"""
        class HasCode(msgspec.Struct):
            code: Annotated[str, msgspec.Meta(pattern=r"^[A-Z]+$")]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"code": "abc123"}',
                type=HasCode,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.PATTERN_CONSTRAINT
        assert err.loc == ("code",)
        assert err.ctx.expected == "str"
        assert err.ctx.pattern == r"^[A-Z]+$"


class TestNumericConstraintReal:
    """NUMERIC_CONSTRAINT — numeric range/multiple violations."""

    def test_int_ge(self):
        """Expected `int` >= 18"""
        T = Annotated[int, msgspec.Meta(ge=18)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'15', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.ctx.expected == "int"
        assert err.ctx.ge == 18

    def test_int_le(self):
        """Expected `int` <= 10"""
        T = Annotated[int, msgspec.Meta(le=10)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'15', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.ctx.expected == "int"
        assert err.ctx.le == 10

    def test_int_gt(self):
        """Expected `int` >= 1  (gt(0) normalized to ge(1))"""
        T = Annotated[int, msgspec.Meta(gt=0)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'-1', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.ctx.expected == "int"
        assert err.ctx.ge == 1

    def test_int_lt(self):
        """Expected `int` <= 99  (lt(100) normalized to le(99))"""
        T = Annotated[int, msgspec.Meta(lt=100)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'200', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.ctx.expected == "int"
        assert err.ctx.le == 99

    def test_int_multiple_of(self):
        """Expected `int` that's a multiple of 5"""
        T = Annotated[int, msgspec.Meta(multiple_of=5)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'12', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.ctx.expected == "int"
        assert err.ctx.multiple_of == 5

    def test_float_ge(self):
        """Expected `float` >= 1.0"""
        T = Annotated[float, msgspec.Meta(ge=1.0)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'0.5', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.ctx.expected == "float"
        assert err.ctx.ge == 1.0

    def test_int_ge_nested(self):
        """Expected `int` >= 18 - at `$.age`"""
        class HasAge(msgspec.Struct):
            age: Annotated[int, msgspec.Meta(ge=18)]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"age": 15}',
                type=HasAge,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.loc == ("age",)
        assert err.ctx.expected == "int"
        assert err.ctx.ge == 18

    def test_int_multiple_of_nested(self):
        """Expected `int` that's a multiple of 6 - at `$.count`"""
        class HasCount(msgspec.Struct):
            count: Annotated[int, msgspec.Meta(multiple_of=6)]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"count": 15}',
                type=HasCount,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMERIC_CONSTRAINT
        assert err.loc == ("count",)
        assert err.ctx.expected == "int"
        assert err.ctx.multiple_of == 6


class TestTimezoneConstraintReal:
    """TIMEZONE_CONSTRAINT — datetime/time timezone constraints."""

    def test_datetime_needs_tz(self):
        """Expected `datetime` with a timezone component"""
        T = Annotated[datetime.datetime, msgspec.Meta(tz=True)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"2024-01-01T00:00:00"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMEZONE_CONSTRAINT
        assert err.ctx.tz is True

    def test_datetime_no_tz(self):
        """Expected `datetime` with no timezone component"""
        T = Annotated[datetime.datetime, msgspec.Meta(tz=False)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"2024-01-01T00:00:00+00:00"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMEZONE_CONSTRAINT
        assert err.ctx.tz is False

    def test_time_needs_tz(self):
        """Expected `time` with a timezone component"""
        T = Annotated[datetime.time, msgspec.Meta(tz=True)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"12:00:00"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMEZONE_CONSTRAINT
        assert err.ctx.tz is True

    def test_time_no_tz(self):
        """Expected `time` with no timezone component"""
        T = Annotated[datetime.time, msgspec.Meta(tz=False)]
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"12:00:00+01:00"', type=T)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMEZONE_CONSTRAINT
        assert err.ctx.tz is False
