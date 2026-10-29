from typing import Any, Tuple, Union

from msgspec import NODEFAULT, UNSET, Struct, field

from .const import MSGSPEC_0_22_PLUS, ErrorType
from .parse_ctx import ErrorCtx, KEY_got, get_length_ctx, get_number_ctx, get_pattern_ctx
from .parse_path import KEY_at, KEY_at_check, KEY_at_key_in, get_error_path

# Placeholders msgspec leaves in an error path for a dict entry
PLACEHOLDER_VALUE = '...'
PLACEHOLDER_KEY = '...key'


def _has_placeholder(loc):
    """
    Check whether an error location still holds a dict placeholder.

    Both placeholders have to be checked separately: `'...' in loc` is a tuple
    membership test, it does not match `'...key'`.

    Args:
        loc (tuple): Error location

    Returns:
        bool: True if the location still holds a placeholder
    """
    return PLACEHOLDER_VALUE in loc or PLACEHOLDER_KEY in loc


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


class ErrorInfoDetail(ErrorInfo):
    """
    An ErrorInfo that carries the offending value.

    Attributes:
        error_value (Any): The offending value, msgspec.UNSET when there is no
            value to report. For a `...key` error this is the key itself
        loc_resolved (bool): True when `loc` points at one concrete entry, i.e.
            the placeholders were resolved or there was none to resolve. It is
            not a check for the placeholder text: a dict key that is literally
            the string `...` is a real key, and such a location is resolved
        error_value_resolved (bool): True when `error_value` holds a value, i.e.
            it is not msgspec.UNSET
    """
    error_value: Any = UNSET
    loc_resolved: bool = False
    error_value_resolved: bool = False


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


def _make_error(error, error_type, ctx=NODEFAULT, expected=None, loc=None):
    """
    Create an ErrorInfo with the given message, type, optional ctx, and expected.

    A type mismatch whose expected type name can name a `msgspec.Raw` is marked
    with `ctx.may_raw_rejection`, and the flag is the whole message-level answer:

    - `Raw` has no type bit of its own, so only a raw target puts `raw`, `any` or
      an empty name in a message. A `Raw` in a union reports the name of the
      member that rejected the value, which is never one of those, so a union
      does not need to be looked at.
    - A dict key is the `str` taken from the payload and can never hold raw
      bytes, so a location that ends at a key is excluded.

    What the message cannot say is where it came from: `convert` rejects an
    already decoded object at a raw field, while the typed decoder takes the
    payload bytes there. The caller supplies that direction.

    Args:
        error (str): The raw error message.
        error_type (ErrorType): The classified error type.
        ctx (ErrorCtx | Type[NODEFAULT]): Pre-built context object to attach.
            When NODEFAULT, the default ErrorCtx() from ErrorInfo is used.
        expected (str | None): The expected type name, None when the message has
            no name to extract. An empty name is kept as an empty string, because
            that is what the message actually holds between the backticks.
        loc (tuple | None): Parsed error location, from `get_error_path()`. Only
            a type mismatch looks at it. Defaults to None, no location is known and
            the key form is then not excluded.

    Returns:
        ErrorInfo
    """
    if ctx is not NODEFAULT:
        err = ErrorInfo(msg=error, type=error_type, ctx=ctx)
    else:
        err = ErrorInfo(msg=error, type=error_type)
    if expected is not None:
        err.ctx.expected = expected
    # A raw field always reports through `Expected ..., got ...`, so only a type
    # mismatch can be a raw rejection. `Raw` has no type bit of its own: 0.22 and
    # later names it `raw`, before 0.22 it shares the typecode of `typing.Any`
    # and is named `any`, and a field carrying metadata has no name at all. The
    # sets do not overlap, an empty name is only accepted from the versions that
    # can produce it.
    # `dict[Raw, int]` reports its key, and a key is the `str` the decoder took
    # from the payload, never a raw field: the decoder rejects that key by
    # itself, so the message is a real error.
    # A miss leaves the flag alone: a Struct default is materialized on access,
    # so writing False here would create an `ErrorCtx` that was never there.
    if error_type is not ErrorType.TYPE_MISMATCH or expected is None:
        return err
    if loc is not None and PLACEHOLDER_KEY in loc:
        return err
    if MSGSPEC_0_22_PLUS:
        hit = expected == 'raw'
    else:
        hit = expected == 'any' or expected == ''
    if hit:
        err.ctx.may_raw_rejection = True
    return err


def get_error_type(error, loc=None):
    """
    Parse error message and return ErrorInfo with type and context set.

    Args:
        error (str): The error message
        loc (tuple | None): Parsed error location, from `get_error_path()`. Used
            to tell a dict key from a field when a type mismatch is classified,
            which is what `ErrorCtx.may_raw_rejection` needs. When None it is
            parsed here, and only for a type mismatch. The returned `ErrorInfo`
            keeps `loc` empty, the caller sets it.

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
            if loc is None:
                # The raw flag needs the path: a dict key can name a raw type
                # and still be a real error
                loc = get_error_path(error)
            # `expected` goes into `_make_error`, it decides the raw flag
            err = _make_error(error, ErrorType.TYPE_MISMATCH, expected=expected, loc=loc)
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

    # UNICODE_DECODE_ERROR: a codec rejected the unicode of the input.
    # - "'<codec>' codec can't decode byte <byte>..."      invalid start / continuation byte
    # - "'<codec>' codec can't decode bytes in position ..." truncated sequence
    # - "'<codec>' codec can't encode character <char>..." a str payload holding a
    #   lone surrogate, msgspec encodes the payload to utf-8 before parsing it
    if error.startswith("'"):
        _, sep, msg = error[1:].partition("'")
        if sep:
            if msg.startswith(" codec can't decode byte") or msg.startswith(" codec can't encode char"):
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
            msgspec.ValidationError instance. An exception carrying a `__cause__`
            is a message from user code wrapped by msgspec, so it is reported as
            `WRAPPED_ERROR` rather than classified by its text.

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
    # The path is parsed once and handed over, so the raw flag is decided on the
    # same location the caller sees
    loc = get_error_path(msg)
    # msgspec wraps a ValueError / TypeError raised by user code in a
    # `ValidationError` and sets the original exception as the cause, see
    # `ms_maybe_wrap_validation_error()`. Such a message is the user's own text,
    # not one of msgspec's, so classifying it by its shape would be wrong: a
    # `dec_hook` that raises `ValueError('Expected `raw`, got `int`')` must not
    # be read as a raw field rejection.
    if getattr(error, '__cause__', None) is not None:
        result = ErrorInfo(msg=msg, type=ErrorType.WRAPPED_ERROR)
    else:
        result = get_error_type(msg, loc)
    result.loc = loc
    return result
