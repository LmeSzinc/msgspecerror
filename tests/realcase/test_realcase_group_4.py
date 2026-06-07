"""
Real-case tests for Group 4: Invalid Value Errors.

Every test triggers a real msgspec error by decoding data with invalid
values (enum, tag, datetime, uuid, base64, decimal, etc.),
then validates parse_msgspec_error correctly classifies it.
"""
import msgspec
import msgspec.msgpack
import datetime
import uuid
import decimal
import pytest
from enum import Enum

from msgspecerror import parse_msgspec_error
from msgspecerror.const import ErrorType


class TestInvalidEnumValueReal:
    """INVALID_ENUM_VALUE — triggered by invalid enum values."""

    def test_str_enum_invalid(self):
        """Invalid enum value 'blue'"""
        class Color(str, Enum):
            RED = "red"
            GREEN = "green"

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"blue"', type=Color)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_ENUM_VALUE

    def test_int_enum_invalid(self):
        """Invalid enum value 99"""
        class Status(int, Enum):
            ACTIVE = 1
            INACTIVE = 2

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'99', type=Status)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_ENUM_VALUE

    def test_str_enum_nested(self):
        """Invalid enum value 'superuser' - at `$.role`"""
        class Role(str, Enum):
            ADMIN = "admin"
            USER = "user"

        class HasRole(msgspec.Struct):
            role: Role

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"role": "superuser"}',
                type=HasRole,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_ENUM_VALUE
        assert err.loc == ("role",)


class TestInvalidTagValueReal:
    """INVALID_TAG_VALUE — triggered by tagged union tag mismatches."""

    def test_tag_field_str_value_unknown(self):
        """Invalid value 'Fish' - at `$.type`"""
        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Cat | Dog

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"name": "fluffy", "type": "Fish"}', type=Animal
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_TAG_VALUE
        assert err.loc == ("type",)


class TestInvalidDateTimeReal:
    """INVALID_DATETIME — triggered by malformed RFC3339 datetime strings."""

    def test_bad_datetime_string(self):
        """Invalid RFC3339 encoded datetime"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-date"', type=datetime.datetime)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DATETIME

    def test_bad_datetime_nested(self):
        """Invalid RFC3339 encoded datetime - at `$.timestamp`"""
        class HasTimestamp(msgspec.Struct):
            timestamp: datetime.datetime

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"timestamp": "bad-datetime"}',
                type=HasTimestamp,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DATETIME
        assert err.loc == ("timestamp",)


class TestInvalidDateReal:
    """INVALID_DATE — triggered by malformed RFC3339 date strings."""

    def test_bad_date_string(self):
        """Invalid RFC3339 encoded date"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-date"', type=datetime.date)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DATE

    def test_bad_date_nested(self):
        """Invalid RFC3339 encoded date - at `$.birth`"""
        class HasBirth(msgspec.Struct):
            birth: datetime.date

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"birth": "bad-date"}',
                type=HasBirth,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DATE
        assert err.loc == ("birth",)


class TestInvalidTimeReal:
    """INVALID_TIME — triggered by malformed RFC3339 time strings."""

    def test_bad_time_string(self):
        """Invalid RFC3339 encoded time"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-time"', type=datetime.time)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_TIME

    def test_bad_time_nested(self):
        """Invalid RFC3339 encoded time - at `$.event`"""
        class HasEvent(msgspec.Struct):
            event: datetime.time

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"event": "bad-time"}',
                type=HasEvent,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_TIME
        assert err.loc == ("event",)


class TestInvalidDurationReal:
    """INVALID_DURATION — triggered by malformed ISO8601 duration strings."""

    def test_bad_duration_string(self):
        """Invalid ISO8601 duration"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-duration"', type=datetime.timedelta)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DURATION

    def test_bad_duration_nested(self):
        """Invalid ISO8601 duration - at `$.period`"""
        class HasPeriod(msgspec.Struct):
            period: datetime.timedelta

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"period": "bad-duration"}',
                type=HasPeriod,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DURATION
        assert err.loc == ("period",)


class TestUnsupportedDurationUnitsReal:
    """UNSUPPORTED_DURATION_UNITS — triggered by ISO8601 durations
    with unsupported units (Y, W)."""

    def test_year_unit(self):
        """Only units 'D', 'H', 'M', and 'S' are supported when
        parsing ISO8601 durations"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"P1Y"', type=datetime.timedelta)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNSUPPORTED_DURATION_UNITS

    def test_week_unit(self):
        """Only units 'D', 'H', 'M', and 'S' are supported when
        parsing ISO8601 durations"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"P1W"', type=datetime.timedelta)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNSUPPORTED_DURATION_UNITS

    def test_unsupported_nested(self):
        """Only units 'D', 'H', 'M', and 'S' are supported when
        parsing ISO8601 durations - at `$.wait`"""
        class HasWait(msgspec.Struct):
            wait: datetime.timedelta

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"wait": "P1W"}',
                type=HasWait,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.UNSUPPORTED_DURATION_UNITS
        assert err.loc == ("wait",)


class TestInvalidMsgpackTimestampReal:
    """INVALID_MSGPACK_TIMESTAMP — triggered by malformed msgpack
    timestamp extensions."""

    def test_short_timestamp_ext(self):
        """Invalid MessagePack timestamp"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.msgpack.decode(
                b'\xc7\x01\xff\x00', type=datetime.datetime
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_MSGPACK_TIMESTAMP

    def test_nanoseconds_out_of_range(self):
        """Invalid MessagePack timestamp: nanoseconds out of range"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.msgpack.decode(
                b'\xd7\xff\xff\xff\xff\xff\xff\xff\xff\xff',
                type=datetime.datetime,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_MSGPACK_TIMESTAMP


class TestInvalidUuidReal:
    """INVALID_UUID — triggered by malformed UUID strings."""

    def test_bad_uuid_string(self):
        """Invalid UUID"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-uuid"', type=uuid.UUID)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_UUID

    def test_bad_uuid_nested(self):
        """Invalid UUID - at `$.id`"""
        class HasId(msgspec.Struct):
            id: uuid.UUID

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"id": "bad-uuid"}',
                type=HasId,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_UUID
        assert err.loc == ("id",)


class TestInvalidBase64StringReal:
    """INVALID_BASE64_STRING — triggered by invalid base64 strings."""

    def test_invalid_base64(self):
        """Invalid base64 encoded string"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"!!!invalid-base64!!!"', type=bytes)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_BASE64_STRING

    def test_invalid_base64_nested(self):
        """Invalid base64 encoded string - at `$.data`"""
        class HasData(msgspec.Struct):
            data: bytes

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"data": "!!!bad!!!"}',
                type=HasData,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_BASE64_STRING
        assert err.loc == ("data",)


class TestInvalidDecimalStringReal:
    """INVALID_DECIMAL_STRING — triggered by invalid decimal.Decimal strings."""

    def test_invalid_decimal_str(self):
        """Invalid decimal string"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not-a-decimal"', type=decimal.Decimal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DECIMAL_STRING

    def test_invalid_decimal_nested(self):
        """Invalid decimal string - at `$.price`"""
        class HasPrice(msgspec.Struct):
            price: decimal.Decimal

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(
                b'{"price": "bad"}',
                type=HasPrice,
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_DECIMAL_STRING
        assert err.loc == ("price",)


class TestInvalidEpochTimestampReal:
    """INVALID_EPOCH_TIMESTAMP — triggered by non-finite float
    decoded as datetime."""

    def test_nan_as_datetime(self):
        """Invalid epoch timestamp"""
        nan_bytes = b'\xcb\x7f\xf8\x00\x00\x00\x00\x00\x00'
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.msgpack.decode(
                nan_bytes, type=datetime.datetime, strict=False
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_EPOCH_TIMESTAMP

    def test_inf_as_datetime(self):
        """Invalid epoch timestamp"""
        inf_bytes = b'\xcb\x7f\xf0\x00\x00\x00\x00\x00\x00'
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.msgpack.decode(
                inf_bytes, type=datetime.datetime, strict=False
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_EPOCH_TIMESTAMP

    def test_neg_inf_as_datetime(self):
        """Invalid epoch timestamp"""
        ninf_bytes = b'\xcb\xff\xf0\x00\x00\x00\x00\x00\x00'
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.msgpack.decode(
                ninf_bytes, type=datetime.datetime, strict=False
            )
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_EPOCH_TIMESTAMP
