from typing import Tuple, Union

import msgspec
from msgspec import NODEFAULT, Struct, field

from .const import ErrorType
from .parse_ctx import ErrorCtx, KEY_got, get_length_ctx, get_number_ctx, get_pattern_ctx
from .parse_path import KEY_at, KEY_at_check, get_error_path, KEY_at_key_in


class ErrorInfo(Struct, omit_defaults=True):
    # Raw error message
    msg: str
    # Error type
    type: ErrorType
    # Error path
    # ('user', 'profile', 'age')
    # ('...', 'RepairThreshold')
    # ('items', '...key')
    # ('matrix', 0, 1, 'value')
    # note that:
    # - dict values are shown as [...] in msgspec, so they are parsed as "..."
    # - dict keys have specific error format, for compatibility we parsed as "...key"
    # - msgspec doesn't give the specific key/value in dict, path is just "..." and "...key" literally
    # - list index in loc will be int, not str
    loc: Tuple[Union[int, str]] = ()
    # Additional context info
    ctx: ErrorCtx = field(default_factory=ErrorCtx)


def _extract_got(remain):
    """Extract the ``got`` type string from the remainder after ``KEY_got``.

    ``remain`` starts with ``KEY_got`` (``', got '``).
    Returns the type name (text between the first pair of backticks) or ``None``.
    """
    if not remain.startswith(KEY_got):
        return None
    after = remain[len(KEY_got):]
    if after.startswith('`'):
        head, sep, _ = after[1:].partition('`')
        if sep:
            return head
    return None


def _make_error(error, error_type, ctx=NODEFAULT, expected=None):
    """
    Create an ErrorInfo with the given message, type, optional ctx, and expected.

    Args:
        error (str): The raw error message.
        error_type (ErrorType): The classified error type.
        ctx (ErrorCtx | Type[NODEFAULT]): Pre-built context object to attach.
            When NODEFAULT, the default ErrorCtx() from ErrorInfo is used.
        expected (str | None): The expected type name. Only set when non-empty.

    Returns:
        ErrorInfo
    """
    if ctx is not NODEFAULT:
        err = ErrorInfo(msg=error, type=error_type, ctx=ctx)
    else:
        err = ErrorInfo(msg=error, type=error_type)
    if expected:
        err.ctx.expected = expected
    return err


def get_error_type(error):
    """
    Parse error message and return ErrorInfo with type and context set.

    Args:
        error (str):

    Returns:
        ErrorInfo: Result with type and ctx set, loc is an empty tuple.
    """
    # Group 2: Structural Errors
    if error.startswith('Object missing required field '):
        return ErrorInfo(msg=error, type=ErrorType.MISSING_FIELD)
    if error.startswith('Object contains unknown field '):
        return ErrorInfo(msg=error, type=ErrorType.UNKNOWN_FIELD)

    # Group 1: Expected ... errors
    if error.startswith('Expected'):
        # parse expected
        _, sep, right = error.partition('`')
        if sep:
            expected, sep, right_right = right.partition('`')
            if sep:
                remain = right_right
            else:
                remain = error
                expected = None
        else:
            remain = error
            expected = None

        # Expected `array` of length
        # Check expect array before KEY_got, because it has KEY_got too
        if error.startswith('Expected `array` '):
            remaining = error[17:]
            ctx = get_length_ctx(remaining)
            return _make_error(error, ErrorType.ARRAY_LENGTH_CONSTRAINT, ctx, expected)

        # Expected `int`, got `str`
        if remain.startswith(KEY_got):
            got_val = _extract_got(remain)
            err = _make_error(error, ErrorType.TYPE_MISMATCH)
            if expected:
                err.ctx.expected = expected
            if got_val:
                err.ctx.got = got_val
            return err

        # Expected `datetime`/`time` with (a|no) timezone component
        if error.startswith('Expected `datetime` with a timezone'):
            return _make_error(error, ErrorType.TIMEZONE_CONSTRAINT, ErrorCtx(tz=True))
        if error.startswith('Expected `datetime` with no timezone'):
            return _make_error(error, ErrorType.TIMEZONE_CONSTRAINT, ErrorCtx(tz=False))
        if error.startswith('Expected `time` with a timezone'):
            return _make_error(error, ErrorType.TIMEZONE_CONSTRAINT, ErrorCtx(tz=True))
        if error.startswith('Expected `time` with no timezone'):
            return _make_error(error, ErrorType.TIMEZONE_CONSTRAINT, ErrorCtx(tz=False))

        # Expected `str` matching regex `<pattern>` - at `<Path>`
        if error.startswith('Expected `str` matching regex '):
            _, _, remaining = error.partition('matching regex ')
            ctx = get_pattern_ctx(remaining)
            return _make_error(error, ErrorType.PATTERN_CONSTRAINT, ctx, expected)

        # Expected `object` of length
        if error.startswith('Expected `object` '):
            remaining = error[18:]
            ctx = get_length_ctx(remaining)
            return _make_error(error, ErrorType.OBJECT_LENGTH_CONSTRAINT, ctx, expected)

        # Expected `str` of length <= 32
        if error.startswith('Expected `str` of length '):
            remaining = error[15:]
            ctx = get_length_ctx(remaining)
            return _make_error(error, ErrorType.LENGTH_CONSTRAINT, ctx, expected)

        # Expected `bytes` of length
        if error.startswith('Expected `bytes` of length '):
            remaining = error[17:]
            ctx = get_length_ctx(remaining)
            return _make_error(error, ErrorType.LENGTH_CONSTRAINT, ctx, expected)

        # Expected `int` >= 0
        if error.startswith('Expected `int` '):
            remaining = error[15:]
            if not remaining.startswith(KEY_at_check):
                ctx = get_number_ctx(remaining, expected=int)
                return _make_error(error, ErrorType.NUMERIC_CONSTRAINT, ctx, expected)

        # Expected `float`
        if error.startswith('Expected `float` '):
            remaining = error[17:]
            if not remaining.startswith(KEY_at_check):
                ctx = get_number_ctx(remaining, expected=float)
                return _make_error(error, ErrorType.NUMERIC_CONSTRAINT, ctx, expected)

        # Expected `decimal`
        if error.startswith('Expected `decimal` '):
            remaining = error[19:]
            if not remaining.startswith(KEY_at_check):
                ctx = get_number_ctx(remaining, expected=float)
                return _make_error(error, ErrorType.NUMERIC_CONSTRAINT, ctx, expected)

        # TOKEN_TYPE_MISMATCH: "Expected `<type>` - at <Path>" without ", got"
        # Must be after all specific Expected patterns to avoid false matches.
        if remain.startswith(KEY_at) or remain.startswith(KEY_at_key_in):
            return _make_error(error, ErrorType.TOKEN_TYPE_MISMATCH, expected=expected)

    # Group 4: Invalid Value Errors
    if error.startswith('Invalid enum value '):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_ENUM_VALUE)
    if error.startswith('Invalid value '):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_TAG_VALUE)

    if error.startswith('Invalid RFC3339 encoded datetime'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_DATETIME)
    if error.startswith('Invalid RFC3339 encoded date'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_DATE)
    if error.startswith('Invalid RFC3339 encoded time'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_TIME)
    if error.startswith('Invalid ISO8601 duration'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_DURATION)
    if error.startswith("Only units"):
        return ErrorInfo(msg=error, type=ErrorType.UNSUPPORTED_DURATION_UNITS)

    if error.startswith('Invalid MessagePack timestamp'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_MSGPACK_TIMESTAMP)
    if error.startswith('Invalid epoch timestamp'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_EPOCH_TIMESTAMP)

    if error.startswith('Invalid UUID'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_UUID)
    if error.startswith('Invalid base64 encoded string'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_BASE64_STRING)
    if error.startswith('Invalid decimal string'):
        return ErrorInfo(msg=error, type=ErrorType.INVALID_DECIMAL_STRING)

    # Group 5: Out of Range Errors
    if error.startswith('Timestamp is out of range'):
        return ErrorInfo(msg=error, type=ErrorType.TIMESTAMP_OUT_OF_RANGE)
    if error.startswith('Duration is out of range'):
        return ErrorInfo(msg=error, type=ErrorType.DURATION_OUT_OF_RANGE)
    if error.startswith('Integer value out of range'):
        return ErrorInfo(msg=error, type=ErrorType.INTEGER_OUT_OF_RANGE)
    if error.startswith('Number out of range'):
        return ErrorInfo(msg=error, type=ErrorType.NUMBER_OUT_OF_RANGE)

    # Group 6: Wrapped Errors
    # JSON_MALFORMED: "JSON is malformed: <reason> (byte <pos>)"
    if error.startswith('JSON is malformed:'):
        return ErrorInfo(msg=error, type=ErrorType.JSON_MALFORMED)

    # MSGPACK_MALFORMED: "MessagePack data is malformed: <reason> (byte <pos>)"
    if error.startswith('MessagePack data is malformed:'):
        return ErrorInfo(msg=error, type=ErrorType.MSGPACK_MALFORMED)

    # Truncated data: same message for both JSON and msgpack DecodeError
    if error == 'Input data was truncated':
        return ErrorInfo(msg=error, type=ErrorType.DATA_TRUNCATED)

    # ENCODE_ERROR: "Can't encode <obj> longer than 2**32 - 1"
    if error.startswith("Can't encode "):
        return ErrorInfo(msg=error, type=ErrorType.ENCODE_ERROR)

    # UNICODE_DECODE_ERROR: "'<codec>' codec can't decode byte <byte>..."
    if error.startswith("'"):
        _, sep, msg = error[1:].partition("'")
        if sep and msg.startswith(" codec can't decode byte "):
            return ErrorInfo(msg=error, type=ErrorType.UNICODE_DECODE_ERROR)

    # WRAPPED_ERROR: catch-all for any remaining unmatched messages,
    # typically user-code errors from dec_hook, __post_init__, etc.
    return ErrorInfo(msg=error, type=ErrorType.WRAPPED_ERROR)


def parse_msgspec_error(error: Union[str, Exception]) -> ErrorInfo:
    """
    Parse plain text error message like "Expected `int`, got `str` - at `$.user.profile.age`"
    into structured ErrorInfo object.
    This makes msgspec more pydantic.

    Args:
        error (str | Exception): The raw error message or a
            msgspec.ValidationError instance.

    Returns:
        ErrorInfo: Structured error with type, location, and context.

    Examples:
        Basic usage with a raw error string::

            >>> err = parse_msgspec_error(
            ...     "Expected `int`, got `str` - at `$.user.age`")
            >>> print(err)
            ErrorInfo(msg='Expected `int`, got `str` - at `$.user.age`',
                      type=<ErrorType.TYPE_MISMATCH>,
                      loc=('user', 'age'))

        With a caught msgspec.ValidationError::

            >>> try:
            ...     msgspec.json.Decoder(int).decode(b'"not_an_int"')
            ... except msgspec.ValidationError as e:
            ...     err = parse_msgspec_error(e)
            ...     print(err.type)
            <ErrorType.TYPE_MISMATCH>
            ...     print(err.loc)
            ()

        Constraint errors carry context::

            >>> from typing_extensions import Annotated
            >>> T = Annotated[int, msgspec.Meta(ge=18)]
            >>> try:
            ...     msgspec.json.Decoder(T).decode(b'15')
            ... except msgspec.ValidationError as e:
            ...     err = parse_msgspec_error(e)
            ...     print(f"type={err.type}, ge={err.ctx.ge}")
            type=<ErrorType.NUMERIC_CONSTRAINT>, ge=18
    """
    msg = str(error)
    result = get_error_type(msg)
    result.loc = get_error_path(msg)
    return result
