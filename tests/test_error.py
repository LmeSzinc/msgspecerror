"""
Comprehensive tests for get_error_type().

Tests every ErrorType member defined in const.py against raw error message
strings, covering all message variants and constraints formats.
"""
from typing import Dict

import msgspec
import pytest

from msgspecerror.const import MSGSPEC_0_22_PLUS, ErrorType, _parse_msgspec_version
from msgspecerror.parse_ctx import ErrorCtx
from msgspecerror.parse_error import ErrorInfo, get_error_type, parse_msgspec_error

# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


def check(msg, expected_type, expected_ctx=None):
    """Assert get_error_type returns correct type and ctx. loc is always ()."""
    result = get_error_type(msg)
    assert isinstance(result, ErrorInfo), f"Expected ErrorInfo, got {type(result)}"
    assert result.type == expected_type, (
        f"msg={msg!r}: expected {expected_type}, got {result.type}"
    )
    assert result.ctx == (expected_ctx if expected_ctx is not None else ErrorCtx()), (
        f"msg={msg!r}: ctx mismatch. expected={expected_ctx}, got={result.ctx}"
    )
    assert result.loc == (), "loc should be empty tuple from get_error_type"
    assert result.msg == msg, "msg should preserve the original input"


# ======================================================================
# Group 1: Type Mismatch & Unexpected Token
# ======================================================================

class TestTypeMismatch:
    """TYPE_MISMATCH — messages containing ', got `' inside the Expected block."""

    @pytest.mark.parametrize("msg, ctx", [
        # root-level (no path suffix)
        ("Expected `int`, got `str`", ErrorCtx(expected="int", got="str")),
        ("Expected `str`, got `int`", ErrorCtx(expected="str", got="int")),
        ("Expected `bool`, got `null`", ErrorCtx(expected="bool", got="null")),
        ("Expected `array`, got `str`", ErrorCtx(expected="array", got="str")),
        ("Expected `object`, got `int`", ErrorCtx(expected="object", got="int")),
        # with path suffix
        ("Expected `int`, got `str` - at `$.age`", ErrorCtx(expected="int", got="str")),
        ("Expected `str`, got `float` - at `$.name`", ErrorCtx(expected="str", got="float")),
        ("Expected `MyCustomClass`, got `str` - at `$.custom_field`",
         ErrorCtx(expected="MyCustomClass", got="str")),
        ("Expected `int`, got `str` - at `$.type`", ErrorCtx(expected="int", got="str")),
        # list index in path
        ("Expected `int`, got `str` - at `$.items[0]`", ErrorCtx(expected="int", got="str")),
        # annotated types
        ("Expected `Annotated[int, ...]`, got `str` - at `$.score`",
         ErrorCtx(expected="Annotated[int, ...]", got="str")),
        # msgspec >= 0.22.0 names a `Raw` target `raw` instead of `any`, so the
        # context carries the raw flag on that side only
        ("Expected `raw`, got `int` - at `$.payload`",
         ErrorCtx(expected="raw", got="int", may_raw_rejection=MSGSPEC_0_22_PLUS)),
        ("Expected `raw`, got `str`",
         ErrorCtx(expected="raw", got="str", may_raw_rejection=MSGSPEC_0_22_PLUS)),
        # an empty type name is kept as an empty string, it is not a missing name
        ("Expected ``, got `object` - at `$.payload`",
         ErrorCtx(expected="", got="object", may_raw_rejection=not MSGSPEC_0_22_PLUS)),
        # msgspec >= 0.22.0: a bool literal type that receives a non-bool value
        ("Expected `bool`, got `str` - at `$.enabled`",
         ErrorCtx(expected="bool", got="str")),
    ])
    def test_type_mismatch(self, msg, ctx):
        check(msg, ErrorType.TYPE_MISMATCH, ctx)


class TestMayRawRejection:
    """
    `ErrorCtx.may_raw_rejection` marks a message that can be a rejected
    `msgspec.Raw` field.

    `msgspec.Raw` carries no type bit of its own, so the type name in the
    message depends on the msgspec version:

    - 0.22 and later names it `raw`
    - before 0.22 it shares the typecode of `typing.Any`, which names it `any`
    - before 0.22 a required TypedDict field, or a dataclass / attrs field with
      a `default_factory`, leaves the name empty, which parses to
      `expected=''`

    A released msgspec never changes its message, so the version decides which
    names are possible, see `MSGSPEC_0_22_PLUS`. The flag is set while the
    message is classified, it is a candidate rather than a verdict: the
    typehint at `loc` still has to be checked against the model: a `Raw` in a
    union reports the name of the member that rejected the value.
    """

    # The type name a raw target gets on the installed msgspec version
    RAW = 'raw' if MSGSPEC_0_22_PLUS else 'any'

    @pytest.mark.parametrize("msg, expected", [
        # the names a `Raw` target can carry, chosen by the installed version
        ("Expected `raw`, got `int` - at `$.payload`", MSGSPEC_0_22_PLUS),
        ("Expected `raw`, got `object`", MSGSPEC_0_22_PLUS),
        ("Expected `any`, got `str` - at `$.payload`", not MSGSPEC_0_22_PLUS),
        ("Expected `any`, got `object` - at `$.payload`", not MSGSPEC_0_22_PLUS),
        ("Expected ``, got `object` - at `$.payload`", not MSGSPEC_0_22_PLUS),
        ("Expected ``, got `str`", not MSGSPEC_0_22_PLUS),
        # a real type mismatch is never a candidate
        ("Expected `int`, got `str` - at `$.payload`", False),
    ])
    def test_the_shape_of_the_installed_version(self, msg, expected):
        assert get_error_type(msg).ctx.may_raw_rejection is expected

    @pytest.mark.parametrize("msg", [
        # a real type mismatch names a concrete type
        "Expected `int`, got `str` - at `$.age`",
        "Expected `str`, got `int`",
        # `Optional[Raw]` is a null check, the decoder itself rejects the value
        "Expected `null`, got `int` - at `$.payload`",
        "Expected `null`, got `object` - at `$.payload`",
        # a union holding Raw reports the member that rejected the value
        "Expected `int`, got `object` - at `$.v`",
        # a token mismatch has no `got` part, so it never reaches the check
        "Expected `raw` - at `$.payload`",
        "Expected `any` - at `key` in `$`",
        # another error type, even when the text sits in the message
        "Object contains unknown field `Expected `raw`` - at `$`",
        "Object missing required field `Expected `any`` - at `$`",
        "Expected `array` of length 2, got 3 - at `$.pair`",
        # a wrapped user-code error whose text only starts like a raw rejection
        "Expected `raw` is what the hook wanted - at `$.payload`",
    ])
    def test_other_messages_are_not_raw_shaped(self, msg):
        """These are False whatever the msgspec version is."""
        assert get_error_type(msg).ctx.may_raw_rejection is False

    def test_a_bare_string_can_mimic_the_shape(self):
        """
        A message alone cannot tell who wrote it, so a raw-shaped string is a
        candidate. `parse_msgspec_error()` rules out user code by the exception
        cause, see `TestUserCodeErrorsByCause`: only a string is shape-read.
        """
        info = get_error_type(f"Expected `{self.RAW}`, got `int` - at `$.payload`")

        assert info.type is ErrorType.TYPE_MISMATCH
        assert info.ctx.may_raw_rejection is True


class TestRawMessageVersionGate:
    """
    `may_raw_rejection` picks the accepted type names from
    `MSGSPEC_0_22_PLUS`, and the two sets do not overlap.

    The flag is set while the message is classified, so it is forced before
    the message is parsed. `_make_error()` reads the flag from its own module
    namespace, so the fake build patches `msgspecerror.parse_error`.
    """

    @pytest.mark.parametrize("msg, expected", [
        # 0.22 and later only names it `raw`
        ("Expected `raw`, got `int` - at `$.payload`", True),
        ("Expected `any`, got `str` - at `$.payload`", False),
        ("Expected ``, got `object` - at `$.payload`", False),
        ("Expected `int`, got `str` - at `$.payload`", False),
    ])
    def test_a_0_22_or_later_build(self, monkeypatch, msg, expected):
        monkeypatch.setattr('msgspecerror.parse_error.MSGSPEC_0_22_PLUS', True)
        assert get_error_type(msg).ctx.may_raw_rejection is expected

    @pytest.mark.parametrize("msg, expected", [
        # before 0.22 it shares the typecode of `Any`, or has no name at all
        ("Expected `raw`, got `int` - at `$.payload`", False),
        ("Expected `any`, got `str` - at `$.payload`", True),
        ("Expected ``, got `object` - at `$.payload`", True),
        ("Expected `int`, got `str` - at `$.payload`", False),
    ])
    def test_a_build_before_0_22(self, monkeypatch, msg, expected):
        monkeypatch.setattr('msgspecerror.parse_error.MSGSPEC_0_22_PLUS', False)
        assert get_error_type(msg).ctx.may_raw_rejection is expected

    def test_the_flag_follows_the_installed_version(self):
        installed = _parse_msgspec_version(msgspec.__version__)
        assert MSGSPEC_0_22_PLUS == (installed >= (0, 22))


class TestUserCodeErrorsByCause:
    """
    `parse_msgspec_error()` reads the exception cause before the message.

    msgspec wraps a `ValueError` / `TypeError` from user code in a
    `ValidationError` and sets the original as `__cause__`, see
    `ms_maybe_wrap_validation_error()`. The wrapped message is the user's own
    text, so it is reported as `WRAPPED_ERROR` whatever it looks like. Only a
    message without a cause is classified by its shape.
    """

    # Messages that carry another type when they are read by shape
    SHAPED = [
        ('Expected `int`, got `str` - at `$.age`', ErrorType.TYPE_MISMATCH),
        ('Expected `str` - at `$.kind`', ErrorType.TOKEN_TYPE_MISMATCH),
        ('Invalid enum value 3 - at `$.type`', ErrorType.INVALID_ENUM_VALUE),
        ('Invalid value 3 - at `$.type`', ErrorType.INVALID_TAG_VALUE),
        ('Object missing required field `id`', ErrorType.MISSING_FIELD),
        ('JSON is malformed: invalid character (byte 0)', ErrorType.JSON_MALFORMED),
    ]

    @staticmethod
    def wrapped(message, cause):
        """Build the exception msgspec builds for a user-code error."""
        try:
            raise msgspec.ValidationError(message) from cause
        except msgspec.ValidationError as e:
            return e

    @pytest.mark.parametrize('message, shape_type', SHAPED)
    def test_a_caused_message_is_wrapped(self, message, shape_type):
        error = self.wrapped(message, ValueError('user code'))

        info = parse_msgspec_error(error)

        assert info.type is ErrorType.WRAPPED_ERROR
        assert info.msg == message
        assert info.ctx == ErrorCtx()
        # the same text without a cause keeps its shape type
        assert get_error_type(message).type is shape_type

    @pytest.mark.parametrize('cause', [ValueError('bad value'), TypeError('bad type')])
    def test_both_wrapped_exception_types(self, cause):
        """ms_maybe_wrap_validation_error() wraps ValueError and TypeError."""
        error = self.wrapped('Expected `int`, got `str`', cause)

        assert parse_msgspec_error(error).type is ErrorType.WRAPPED_ERROR

    def test_a_caused_message_is_not_raw_shaped(self):
        """A hook that writes a raw-shaped message is not a raw field rejection."""
        message = "Expected `raw`, got `int` - at `$.payload`"
        info = parse_msgspec_error(self.wrapped(message, ValueError('user code')))

        assert info.type is ErrorType.WRAPPED_ERROR
        assert info.ctx.expected is None
        assert info.ctx.may_raw_rejection is False

    def test_the_location_is_still_parsed(self):
        """A wrapped message keeps the path msgspec appended to it."""
        message = 'Expected `int`, got `str` - at `$.user.profile.age`'
        info = parse_msgspec_error(self.wrapped(message, ValueError('user code')))

        assert info.type is ErrorType.WRAPPED_ERROR
        assert info.loc == ('user', 'profile', 'age')

    def test_a_message_without_a_cause_keeps_its_shape(self):
        """msgspec's own errors carry no cause."""
        try:
            msgspec.json.decode(b'{"age": "x"}', type=Dict[str, int])
        except msgspec.ValidationError as e:
            info = parse_msgspec_error(e)
        else:  # pragma: no cover - the decode has to fail
            raise AssertionError('the decode should have failed')

        assert info.type is ErrorType.TYPE_MISMATCH
        assert info.ctx.expected == 'int'

    def test_a_plain_string_is_read_by_shape(self):
        """A string has no cause to read."""
        info = parse_msgspec_error('Expected `int`, got `str` - at `$.age`')

        assert info.type is ErrorType.TYPE_MISMATCH
        assert info.ctx.expected == 'int'

    def test_a_suppressed_cause_is_not_a_wrapped_error(self):
        """`raise ... from None` leaves no cause, so the shape is used."""
        try:
            raise msgspec.ValidationError('Expected `int`, got `str`') from None
        except msgspec.ValidationError as e:
            info = parse_msgspec_error(e)

        assert info.type is ErrorType.TYPE_MISMATCH


class TestTokenTypeMismatch:
    """TOKEN_TYPE_MISMATCH — messages like "Expected `<type>` - at <Path>"
    without ", got `<B>`"."""

    @pytest.mark.parametrize("msg, ctx", [
        ("Expected `str` - at `$.kind`", ErrorCtx(expected="str")),
        ("Expected `int` - at `$.kind`", ErrorCtx(expected="int")),
        ("Expected `str` - at `key` in `$`", ErrorCtx(expected="str")),
        ("Expected `float` - at `$.value`", ErrorCtx(expected="float")),
        ("Expected `decimal` - at `$.price`", ErrorCtx(expected="decimal")),
    ])
    def test_token_type_mismatch(self, msg, ctx):
        """All TOKEN_TYPE_MISMATCH messages now correctly classified."""
        check(msg, ErrorType.TOKEN_TYPE_MISMATCH, ctx)


# ======================================================================
# Group 2: Structural Errors
# ======================================================================

class TestMissingField:
    """MISSING_FIELD — 'Object missing required field ...'."""

    @pytest.mark.parametrize("msg", [
        "Object missing required field `id`",
        "Object missing required field `name` - at `$.user`",
        "Object missing required field `age` - at `$.person`",
        "Object missing required field `email` - at `$.contact`",
    ])
    def test_missing_field(self, msg):
        check(msg, ErrorType.MISSING_FIELD)


class TestUnknownField:
    """UNKNOWN_FIELD — 'Object contains unknown field ...'."""

    @pytest.mark.parametrize("msg", [
        "Object contains unknown field `favorite_color`",
        "Object contains unknown field `extra` - at `$.config`",
        "Object contains unknown field `unknown_flag` - at `$`",
    ])
    def test_unknown_field(self, msg):
        check(msg, ErrorType.UNKNOWN_FIELD)


# ======================================================================
# Group 3: Constraint and Length Errors
# ======================================================================

class TestArrayLengthConstraint:
    """ARRAY_LENGTH_CONSTRAINT — array length violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # exact length (tuple size mismatch) — min=max
        ("Expected `array` of length 2, got 3 - at `$.coords`",
         ErrorCtx(min_length=2, max_length=2, expected="array")),
        ("Expected `array` of length 5, got 1 - at `$.data`",
         ErrorCtx(min_length=5, max_length=5, expected="array")),
        # range via "of length <min> to <max>"
        ("Expected `array` of length 1 to 5, got 6 - at `$.items`",
         ErrorCtx(min_length=1, max_length=5, expected="array")),
        # >=
        ("Expected `array` of length >= 3",
         ErrorCtx(min_length=3, expected="array")),
        ("Expected `array` of length >= 1 - at `$.tags`",
         ErrorCtx(min_length=1, expected="array")),
        # <=
        ("Expected `array` of length <= 10",
         ErrorCtx(max_length=10, expected="array")),
        ("Expected `array` of length <= 0 - at `$.empty`",
         ErrorCtx(max_length=0, expected="array")),
        # "at least" / "at most" (msgspec 0.19+)
        ("Expected `array` of at least length 2",
         ErrorCtx(min_length=2, expected="array")),
        ("Expected `array` of at most length 10 - at `$.buffer`",
         ErrorCtx(max_length=10, expected="array")),
    ])
    def test_array_length_constraint(self, msg, ctx):
        check(msg, ErrorType.ARRAY_LENGTH_CONSTRAINT, ctx)


class TestObjectLengthConstraint:
    """OBJECT_LENGTH_CONSTRAINT — dict/object length violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # >=
        ("Expected `object` of length >= 1 - at `$.metadata`",
         ErrorCtx(min_length=1, expected="object")),
        ("Expected `object` of length >= 1",
         ErrorCtx(min_length=1, expected="object")),
        # <=
        ("Expected `object` of length <= 5",
         ErrorCtx(max_length=5, expected="object")),
        # >= and <= are both present when constraint is set
        ("Expected `object` of length >= 2",
         ErrorCtx(min_length=2, expected="object")),
    ])
    def test_object_length_constraint(self, msg, ctx):
        check(msg, ErrorType.OBJECT_LENGTH_CONSTRAINT, ctx)


class TestLengthConstraint:
    """LENGTH_CONSTRAINT — str/bytes length violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # str variants — le
        ("Expected `str` of length <= 32",
         ErrorCtx(max_length=32, expected="str")),
        ("Expected `str` of length <= 0",
         ErrorCtx(max_length=0, expected="str")),
        # str variants — ge
        ("Expected `str` of length >= 1 - at `$.name`",
         ErrorCtx(min_length=1, expected="str")),
        ("Expected `str` of length >= 8",
         ErrorCtx(min_length=8, expected="str")),
        # str variants — range
        ("Expected `str` of length 5 to 10 - at `$.code`",
         ErrorCtx(min_length=5, max_length=10, expected="str")),
        # bytes variants — exact
        ("Expected `bytes` of length 16",
         ErrorCtx(min_length=16, max_length=16, expected="bytes")),
        # bytes variants — ge
        ("Expected `bytes` of length >= 8 - at `$.key`",
         ErrorCtx(min_length=8, expected="bytes")),
        ("Expected `bytes` of length >= 1",
         ErrorCtx(min_length=1, expected="bytes")),
        # bytes variants — le
        ("Expected `bytes` of length <= 1024",
         ErrorCtx(max_length=1024, expected="bytes")),
    ])
    def test_length_constraint(self, msg, ctx):
        check(msg, ErrorType.LENGTH_CONSTRAINT, ctx)


class TestPatternConstraint:
    """PATTERN_CONSTRAINT — regex pattern violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # simple patterns
        ("Expected `str` matching regex '\\d{4}-\\d{2}-\\d{2}' - at `$.date`",
         ErrorCtx(pattern='\\d{4}-\\d{2}-\\d{2}', expected="str")),
        ("Expected `str` matching regex '^[a-z]+$'",
         ErrorCtx(pattern='^[a-z]+$', expected="str")),
        # special characters
        ("Expected `str` matching regex 'https?://.*' - at `$.url`",
         ErrorCtx(pattern='https?://.*', expected="str")),
        # pattern with backticks (the pattern itself is in quotes)
        ("Expected `str` matching regex '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}'",
         ErrorCtx(pattern='[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}', expected="str")),
    ])
    def test_pattern_constraint(self, msg, ctx):
        check(msg, ErrorType.PATTERN_CONSTRAINT, ctx)


class TestNumericConstraint:
    """NUMERIC_CONSTRAINT — int/float/decimal constraint violations."""

    @pytest.mark.parametrize("msg, ctx", [
        # int: ge
        ("Expected `int` >= 0",
         ErrorCtx(ge=0, expected="int")),
        ("Expected `int` >= 18 - at `$.age`",
         ErrorCtx(ge=18, expected="int")),
        # int: le
        ("Expected `int` <= 100",
         ErrorCtx(le=100, expected="int")),
        ("Expected `int` <= 10 - at `$.count`",
         ErrorCtx(le=10, expected="int")),
        # int: gt
        ("Expected `int` > 0",
         ErrorCtx(gt=0, expected="int")),
        # int: lt
        ("Expected `int` < 10 - at `$.rank`",
         ErrorCtx(lt=10, expected="int")),
        # int: multiple_of
        ("Expected `int` that's a multiple of 6",
         ErrorCtx(multiple_of=6, expected="int")),
        ("Expected `int` that's a multiple of 2 - at `$.even`",
         ErrorCtx(multiple_of=2, expected="int")),
        # float
        ("Expected `float` >= 0.0",
         ErrorCtx(ge=0.0, expected="float")),
        ("Expected `float` <= 100.5 - at `$.price`",
         ErrorCtx(le=100.5, expected="float")),
        ("Expected `float` > 0.0",
         ErrorCtx(gt=0.0, expected="float")),
        ("Expected `float` < 1.0 - at `$.ratio`",
         ErrorCtx(lt=1.0, expected="float")),
        ("Expected `float` that's a multiple of 0.5",
         ErrorCtx(multiple_of=0.5, expected="float")),
        # decimal (stored as float in ctx)
        ("Expected `decimal` >= 0.0",
         ErrorCtx(ge=0.0, expected="decimal")),
        ("Expected `decimal` <= 999999.99 - at `$.amount`",
         ErrorCtx(le=999999.99, expected="decimal")),
        ("Expected `decimal` that's a multiple of 0.01",
         ErrorCtx(multiple_of=0.01, expected="decimal")),
    ])
    def test_numeric_constraint(self, msg, ctx):
        check(msg, ErrorType.NUMERIC_CONSTRAINT, ctx)


class TestTimezoneConstraint:
    """TIMEZONE_CONSTRAINT — datetime/time timezone awareness."""

    @pytest.mark.parametrize("msg, ctx", [
        ("Expected `datetime` with a timezone component",
         ErrorCtx(tz=True)),
        ("Expected `datetime` with a timezone component - at `$.event_at`",
         ErrorCtx(tz=True)),
        ("Expected `datetime` with no timezone component",
         ErrorCtx(tz=False)),
        ("Expected `datetime` with no timezone component - at `$.naive_dt`",
         ErrorCtx(tz=False)),
        ("Expected `time` with a timezone component",
         ErrorCtx(tz=True)),
        ("Expected `time` with a timezone component - at `$.start`",
         ErrorCtx(tz=True)),
        ("Expected `time` with no timezone component",
         ErrorCtx(tz=False)),
        ("Expected `time` with no timezone component - at `$.end`",
         ErrorCtx(tz=False)),
    ])
    def test_timezone_constraint(self, msg, ctx):
        check(msg, ErrorType.TIMEZONE_CONSTRAINT, ctx)


# ======================================================================
# Group 4: Invalid Value Errors
# ======================================================================

class TestInvalidEnumValue:
    """INVALID_ENUM_VALUE — value not in enum/literal."""

    @pytest.mark.parametrize("msg", [
        "Invalid enum value 'admin'",
        "Invalid enum value 'admin' - at `$.role`",
        "Invalid enum value 'RED' - at `$.color`",
        "Invalid enum value 3 - at `$.status`",
        "Invalid enum value 'user' - at `$.permissions[0]`",
        # msgspec >= 0.22.0: `Literal[True]` / `Literal[False]` mismatch,
        # the bool is formatted with repr(), so it is not quoted
        "Invalid enum value True",
        "Invalid enum value False",
        "Invalid enum value True - at `$.enabled`",
        "Invalid enum value False - at `$.enabled`",
        "Invalid enum value False - at `$.flags[0]`",
    ])
    def test_invalid_enum(self, msg):
        check(msg, ErrorType.INVALID_ENUM_VALUE)


class TestInvalidTagValue:
    """INVALID_TAG_VALUE — unrecognized tag in tagged union."""

    @pytest.mark.parametrize("msg", [
        "Invalid value 3",
        "Invalid value 'unknown' - at `$.type`",
        "Invalid value `Bird` - at `$.kind`",
        "Invalid value 404 - at `$.tag`",
    ])
    def test_invalid_tag(self, msg):
        check(msg, ErrorType.INVALID_TAG_VALUE)


class TestInvalidDateTime:
    """INVALID_DATETIME — malformed RFC3339 datetime string."""

    @pytest.mark.parametrize("msg", [
        "Invalid RFC3339 encoded datetime - at `$.timestamp`",
        "Invalid RFC3339 encoded datetime - at `$`",
        "Invalid RFC3339 encoded datetime",
    ])
    def test_invalid_datetime(self, msg):
        check(msg, ErrorType.INVALID_DATETIME)


class TestInvalidDate:
    """INVALID_DATE — malformed RFC3339 date string."""

    @pytest.mark.parametrize("msg", [
        "Invalid RFC3339 encoded date - at `$.birth_date`",
        "Invalid RFC3339 encoded date - at `$`",
        "Invalid RFC3339 encoded date",
    ])
    def test_invalid_date(self, msg):
        check(msg, ErrorType.INVALID_DATE)


class TestInvalidTime:
    """INVALID_TIME — malformed RFC3339 time string."""

    @pytest.mark.parametrize("msg", [
        "Invalid RFC3339 encoded time - at `$.event_time`",
        "Invalid RFC3339 encoded time - at `$`",
        "Invalid RFC3339 encoded time",
    ])
    def test_invalid_time(self, msg):
        check(msg, ErrorType.INVALID_TIME)


class TestInvalidDuration:
    """INVALID_DURATION — malformed ISO8601 duration string."""

    @pytest.mark.parametrize("msg", [
        "Invalid ISO8601 duration - at `$.period`",
        "Invalid ISO8601 duration - at `$`",
        "Invalid ISO8601 duration",
    ])
    def test_invalid_duration(self, msg):
        check(msg, ErrorType.INVALID_DURATION)


class TestUnsupportedDurationUnits:
    """UNSUPPORTED_DURATION_UNITS — unsupported ISO8601 units."""

    @pytest.mark.parametrize("msg", [
        "Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations",
        "Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations - at `$.period`",
    ])
    def test_unsupported_duration_units(self, msg):
        check(msg, ErrorType.UNSUPPORTED_DURATION_UNITS)


class TestInvalidMsgpackTimestamp:
    """INVALID_MSGPACK_TIMESTAMP — malformed msgpack timestamp."""

    @pytest.mark.parametrize("msg", [
        "Invalid MessagePack timestamp - at `$`",
        "Invalid MessagePack timestamp: nanoseconds out of range",
        "Invalid MessagePack timestamp: nanoseconds out of range - at `$`",
    ])
    def test_invalid_msgpack_ts(self, msg):
        check(msg, ErrorType.INVALID_MSGPACK_TIMESTAMP)


class TestInvalidEpochTimestamp:
    """INVALID_EPOCH_TIMESTAMP — non-finite float for datetime."""

    @pytest.mark.parametrize("msg", [
        "Invalid epoch timestamp - at `$`",
        "Invalid epoch timestamp",
    ])
    def test_invalid_epoch(self, msg):
        check(msg, ErrorType.INVALID_EPOCH_TIMESTAMP)


class TestInvalidUUID:
    """INVALID_UUID — malformed UUID string or bytes."""

    @pytest.mark.parametrize("msg", [
        "Invalid UUID - at `$.id`",
        "Invalid UUID",
        "Invalid UUID bytes",
        "Invalid UUID bytes - at `$.binary_id`",
    ])
    def test_invalid_uuid(self, msg):
        check(msg, ErrorType.INVALID_UUID)


class TestInvalidBase64:
    """INVALID_BASE64_STRING — invalid base64 content."""

    @pytest.mark.parametrize("msg", [
        "Invalid base64 encoded string - at `$.data`",
        "Invalid base64 encoded string",
        "Invalid base64 encoded string - at `$.payload`",
    ])
    def test_invalid_base64(self, msg):
        check(msg, ErrorType.INVALID_BASE64_STRING)


class TestInvalidDecimal:
    """INVALID_DECIMAL_STRING — unparseable decimal string."""

    @pytest.mark.parametrize("msg", [
        "Invalid decimal string - at `$.price`",
        "Invalid decimal string",
        "Invalid decimal string - at `$.amount`",
    ])
    def test_invalid_decimal(self, msg):
        check(msg, ErrorType.INVALID_DECIMAL_STRING)


# ======================================================================
# Group 5: Out of Range Errors
# ======================================================================

class TestTimestampOutOfRange:
    """TIMESTAMP_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Timestamp is out of range",
        "Timestamp is out of range - at `$.ts`",
    ])
    def test_timestamp_range(self, msg):
        check(msg, ErrorType.TIMESTAMP_OUT_OF_RANGE)


class TestDurationOutOfRange:
    """DURATION_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Duration is out of range",
        "Duration is out of range - at `$.period`",
    ])
    def test_duration_range(self, msg):
        check(msg, ErrorType.DURATION_OUT_OF_RANGE)


class TestIntegerOutOfRange:
    """INTEGER_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Integer value out of range",
        "Integer value out of range - at `$.big_int`",
    ])
    def test_integer_range(self, msg):
        check(msg, ErrorType.INTEGER_OUT_OF_RANGE)


class TestNumberOutOfRange:
    """NUMBER_OUT_OF_RANGE."""

    @pytest.mark.parametrize("msg", [
        "Number out of range",
        "Number out of range - at `$.big_num`",
    ])
    def test_number_range(self, msg):
        check(msg, ErrorType.NUMBER_OUT_OF_RANGE)


# ======================================================================
# Group 6: Wrapped & Fallback Errors
# ======================================================================

class TestWrappedError:
    """WRAPPED_ERROR — fallback for unmatched messages."""

    @pytest.mark.parametrize("msg", [
        # User-generated errors from __post_init__, dec_hook, etc.
        "passwords cannot be the same - at $",
        "Custom validation failed",
        "ValueError: invalid literal for int()",
        # Random strings that don't match any known pattern
        "Some completely unexpected error",
        "",
        "Expected `str`" + " " * 100,
        # "Expected `int`" (no " - at", no constraint) — not matched by any
        # specific Expected pattern and has no " - at " — falls to WRAPPED_ERROR
        "Expected `int`",
        "Expected `float`",
        "Expected `decimal`",
    ])
    def test_wrapped_error(self, msg):
        check(msg, ErrorType.WRAPPED_ERROR)


# ======================================================================
# Edge Cases — Constraint Helpers Returning NODEFAULT
# ======================================================================

class TestHelperNODEFAULTFallback:
    """
    When get_length_ctx / get_number_ctx / get_pattern_ctx return NODEFAULT
    (e.g. unparseable constraints), get_error_type should still return the
    correct ErrorInfo with default empty ErrorCtx.
    """

    @pytest.mark.parametrize("msg, expected_type, expected_ctx", [
        ("Expected `array` of length ???",
         ErrorType.ARRAY_LENGTH_CONSTRAINT, ErrorCtx(expected="array")),
        ("Expected `bytes` of length ???",
         ErrorType.LENGTH_CONSTRAINT, ErrorCtx(expected="bytes")),
        ("Expected `str` of length ???",
         ErrorType.LENGTH_CONSTRAINT, ErrorCtx(expected="str")),
        ("Expected `object` of length ???",
         ErrorType.OBJECT_LENGTH_CONSTRAINT, ErrorCtx(expected="object")),
        ("Expected `int` >= ???",
         ErrorType.NUMERIC_CONSTRAINT, ErrorCtx(expected="int")),
        ("Expected `float` ???",
         ErrorType.NUMERIC_CONSTRAINT, ErrorCtx(expected="float")),
    ])
    def test_unparseable_ctx_fallback(self, msg, expected_type, expected_ctx):
        result = get_error_type(msg)
        assert result.type == expected_type, (
            f"msg={msg!r}: expected {expected_type}, got {result.type}"
        )
        assert result.ctx == expected_ctx, (
            f"msg={msg!r}: expected {expected_ctx}, got {result.ctx}"
        )


# ======================================================================
# Edge Cases — Arbitrary Input Resilience
# ======================================================================

class TestEdgeCases:
    """Resilience against malformed or unexpected input."""

    def test_empty_string(self):
        """Empty string should fall through to WRAPPED_ERROR."""
        result = get_error_type("")
        assert result.type == ErrorType.WRAPPED_ERROR
        assert result.ctx == ErrorCtx()

    def test_single_word(self):
        """A single unexpected word should be WRAPPED_ERROR."""
        result = get_error_type("Something")
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_very_long_message(self):
        """A very long message that doesn't match should be WRAPPED_ERROR."""
        msg = "A" * 10000
        result = get_error_type(msg)
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_prefix_match_with_suffix_garbage(self):
        """
        Patterns match on startswith; garbage after the matched prefix
        may cause ctx helpers to return NODEFAULT, but type should still
        be correct.
        """
        result = get_error_type("Expected `int` >= 0 garbage_trailing_text")
        assert result.type == ErrorType.NUMERIC_CONSTRAINT
        # "garbage_trailing_text" after "0" causes int("0 garbage_trailing_text")
        # to raise ValueError, so get_number_ctx returns NODEFAULT
        # ctx still gets expected type from message
        assert result.ctx == ErrorCtx(expected="int")

    def test_partial_match_does_not_leak(self):
        """'Expected `int`' alone (no constraint suffix) should not match NUMERIC_CONSTRAINT."""
        # This message starts with "Expected" but doesn't match any specific pattern
        # It would go to the 'Expected `int` ' check... wait, "Expected `int`" (without trailing space)...
        # Actually, "Expected `int` " needs the trailing space for startswith.
        # "Expected `int`" (no trailing space) does NOT start with "Expected `int` ".
        # So it doesn't match NUMERIC_CONSTRAINT.
        result = get_error_type("Expected `int`")
        assert result.type == ErrorType.WRAPPED_ERROR

    def test_at_keyword_in_middle_of_message(self):
        """VERBATIM 'at' in field names shouldn't confuse path parsing in get_error_type."""
        # get_error_type doesn't do path parsing itself, it delegates to helpers.
        # This tests that the type classification is not affected by 'at' in the message.
        msg = "Object missing required field `data` - at `$.meta.at`"
        check(msg, ErrorType.MISSING_FIELD)

    def test_type_mismatch_with_backtick_types(self):
        """Types with backticks in their msgspec representation."""
        cases = [
            ("Expected `Union[int, str]`, got `int`",
             ErrorCtx(expected="Union[int, str]", got="int")),
            ("Expected `Optional[str]`, got `null`",
             ErrorCtx(expected="Optional[str]", got="null")),
            ("Expected `list[int]`, got `str`",
             ErrorCtx(expected="list[int]", got="str")),
        ]
        for msg, ctx in cases:
            check(msg, ErrorType.TYPE_MISMATCH, ctx)

    def test_numeric_constraint_with_all_operators(self):
        """All five numeric constraint operators for int type."""
        cases = [
            ("Expected `int` >= 0", ErrorCtx(ge=0, expected="int")),
            ("Expected `int` <= 100", ErrorCtx(le=100, expected="int")),
            ("Expected `int` > 0", ErrorCtx(gt=0, expected="int")),
            ("Expected `int` < 50", ErrorCtx(lt=50, expected="int")),
            ("Expected `int` that's a multiple of 10",
             ErrorCtx(multiple_of=10, expected="int")),
        ]
        for msg, ctx in cases:
            check(msg, ErrorType.NUMERIC_CONSTRAINT, ctx)

    def test_all_float_constraint_operators(self):
        """All five numeric constraint operators for float type."""
        cases = [
            ("Expected `float` >= 0.5", ErrorCtx(ge=0.5, expected="float")),
            ("Expected `float` <= 1.5", ErrorCtx(le=1.5, expected="float")),
            ("Expected `float` > 0.0", ErrorCtx(gt=0.0, expected="float")),
            ("Expected `float` < 100.0", ErrorCtx(lt=100.0, expected="float")),
            ("Expected `float` that's a multiple of 0.25",
             ErrorCtx(multiple_of=0.25, expected="float")),
        ]
        for msg, ctx in cases:
            check(msg, ErrorType.NUMERIC_CONSTRAINT, ctx)


class TestReturnType:
    """Verify get_error_type always returns an ErrorInfo."""

    @pytest.mark.parametrize("msg", [
        "Expected `int`, got `str`",
        "Object missing required field `id`",
        "Expected `array` of length >= 3",
        "Invalid enum value 'admin'",
        "Some random error",
        "",
    ])
    def test_always_returns_msgspec_error(self, msg):
        result = get_error_type(msg)
        assert isinstance(result, ErrorInfo)
        assert isinstance(result.msg, str)
        assert isinstance(result.type, ErrorType)
        assert isinstance(result.ctx, ErrorCtx)
        assert result.loc == ()
