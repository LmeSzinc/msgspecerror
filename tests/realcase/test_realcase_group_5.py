"""
Real-case tests for Group 5: Out of Range Errors.

Every test triggers a real msgspec error by decoding data with out-of-range
values, then validates parse_msgspec_error correctly classifies it.
"""
import msgspec
import msgspec.msgpack
import datetime
import pytest

from msgspecerror import parse_msgspec_error, ErrorCtx
from msgspecerror.const import ErrorType


class TestTimestampOutOfRangeReal:
    """TIMESTAMP_OUT_OF_RANGE — triggered by epoch timestamps outside
    Python's datetime range."""

    def test_negative_timestamp_too_large(self):
        """Timestamp is out of range"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'-999999999999999999',
                type=datetime.datetime,
                strict=False,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMESTAMP_OUT_OF_RANGE
        assert err.ctx == ErrorCtx()

    def test_positive_timestamp_too_large(self):
        """Timestamp is out of range"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'999999999999999999',
                type=datetime.datetime,
                strict=False,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMESTAMP_OUT_OF_RANGE
        assert err.ctx == ErrorCtx()

    def test_timestamp_out_of_range_nested(self):
        """Timestamp is out of range  - at `$.ts`"""
        class HasTs(msgspec.Struct):
            ts: datetime.datetime

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"ts": -999999999999999999}',
                type=HasTs,
                strict=False,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TIMESTAMP_OUT_OF_RANGE
        assert err.loc == ("ts",)
        assert err.ctx == ErrorCtx()


class TestDurationOutOfRangeReal:
    """DURATION_OUT_OF_RANGE — triggered by durations outside
    Python's timedelta range."""

    def test_large_int_duration(self):
        """Duration is out of range"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'999999999999999999999999999999999999',
                type=datetime.timedelta,
                strict=False,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.DURATION_OUT_OF_RANGE
        assert err.ctx == ErrorCtx()

    def test_duration_out_of_range_nested(self):
        """Duration is out of range - at `$.dur`"""
        class HasDur(msgspec.Struct):
            dur: datetime.timedelta

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"dur": 999999999999999999999999}',
                type=HasDur,
                strict=False,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.DURATION_OUT_OF_RANGE
        assert err.loc == ("dur",)
        assert err.ctx == ErrorCtx()


class TestIntegerOutOfRangeReal:
    """INTEGER_OUT_OF_RANGE — triggered by JSON numbers with >4300 digits."""

    def test_integer_too_long(self):
        """Integer value out of range"""
        data = b"-" + b"9" * 4301
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(data, type=int)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INTEGER_OUT_OF_RANGE
        assert err.ctx == ErrorCtx()

    def test_integer_too_long_nested(self):
        """Integer value out of range - at `$.val`"""
        class HasVal(msgspec.Struct):
            val: int

        data = b'{"val": -' + b"9" * 4301 + b"}"
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(data, type=HasVal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INTEGER_OUT_OF_RANGE
        assert err.loc == ("val",)
        assert err.ctx == ErrorCtx()


class TestNumberOutOfRangeReal:
    """NUMBER_OUT_OF_RANGE — triggered by numbers outside double range."""

    def test_float_exponent_too_large(self):
        """Number out of range"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'1e999', type=float)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMBER_OUT_OF_RANGE
        assert err.ctx == ErrorCtx()

    def test_number_out_of_range_nested(self):
        """Number out of range - at `$.big`"""
        class HasBig(msgspec.Struct):
            big: float

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"big": 1e999}',
                type=HasBig,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.NUMBER_OUT_OF_RANGE
        assert err.loc == ("big",)
        assert err.ctx == ErrorCtx()
