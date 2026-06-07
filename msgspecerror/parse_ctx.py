from msgspec import NODEFAULT, Struct

from .parse_path import KEY_at


class ErrorCtx(Struct, omit_defaults=True):
    """
    An optional object which contains extra info
    """
    expected: "str | None" = None
    got: "str | None" = None
    gt: "int | float | None" = None
    ge: "int | float | None" = None
    lt: "int | float | None" = None
    le: "int | float | None" = None
    multiple_of: "int | float | None" = None
    pattern: "str | None" = None
    min_length: "int | None" = None
    max_length: "int | None" = None
    tz: "bool | None" = None

    def _iter_repr_fields(self):
        # show non-None fields only
        field_names = ['expected', 'got', 'gt', 'ge', 'lt', 'le', 'multiple_of',
                       'pattern', 'min_length', 'max_length', 'tz', ]
        for key in field_names:
            value = getattr(self, key, None)
            if value is not None:
                yield f'{key}={value!r}'

    def __repr__(self):
        attrs_str = ', '.join(self._iter_repr_fields())
        return f'{self.__class__.__name__}({attrs_str})'


KEY_to = ' to '
KEY_gt = '> '
KEY_ge = '>= '
KEY_lt = '< '
KEY_le = '<= '
KEY_got = ', got '
KEY_multiple_of = 'multiple of '


def revert_regex(msg):
    """
    Unescape a regex pattern string that was processed by Python's ``repr()``
    via msgspec's ``%R`` format specifier.

    ``repr()`` wraps the pattern in quotes (``'...'`` or ``"..."``) and
    doubles every backslash (``\\``  becomes  ``\\\\``).  This function
    reverses both transformations so the pattern can be used directly.

    Args:
        msg (str): A pattern string extracted from a msgspec error message,
            **including** the surrounding quotes added by ``repr()``.

    Returns:
        str: The unescaped pattern.
    """
    # Strip the outer quotes that repr() added, and remember which
    # quote character was used so we can undo internal escaping.
    if len(msg) >= 2:
        head = msg[0]
        tail = msg[-1]
        if head == tail and (head == "'" or head == '"'):
            # repr() escapes internal quotes that conflict with the outer
            # wrapper: ``\\'`` inside ``'...'``, or ``\\"`` inside ``"..."``.
            # Must run before the \\\\→\\ replacement, otherwise a literal
            # ``\\'`` (backslash + escaped quote) would be collapsed to
            # ``'`` after both passes.
            if head == "'":
                msg = msg[1:-1]
                msg = msg.replace("\\'", "'")
            elif head == '"':
                msg = msg[1:-1]
                msg = msg.replace('\\"', '"')

    msg = msg.replace('\\\\', '\\')
    return msg


def get_pattern_ctx(msg):
    """
    Args:
        msg (str): Error message with reason removed
            "`<pattern>` - at `<Path>`"

    Returns:
        ErrorCtx | Type[NODEFAULT]:
    """
    # remove path and leave regex
    left, sep, _ = msg.rpartition(KEY_at)
    if sep:
        msg = left

    if msg:
        msg = revert_regex(msg)
        if msg:
            return ErrorCtx(pattern=msg)
    # Empty regex
    return NODEFAULT


def get_length_ctx(msg):
    """
    Args:
        msg (str): Error message with reason removed
            "of at (least|most) length 3"
            "of length <expected>, got <actual> - at <Path>"
            "of length <min> to <max>"
            "of length >= 3"
            "of length 5"

    Returns:
        ErrorCtx | Type[NODEFAULT]:
    """
    msg, _, _ = msg.partition(KEY_at)
    msg, _, _ = msg.partition(KEY_got)
    if msg.startswith('of length '):
        msg = msg[10:]
        # of length <min> to <max>
        if KEY_to in msg:
            min_length, _, max_length = msg.partition(KEY_to)
            try:
                return ErrorCtx(min_length=int(min_length), max_length=int(max_length))
            except ValueError:
                return NODEFAULT
        # of length >= 3
        if msg.startswith(KEY_ge):
            msg = msg[3:]
            try:
                return ErrorCtx(min_length=int(msg))
            except ValueError:
                return NODEFAULT
        if msg.startswith(KEY_le):
            msg = msg[3:]
            try:
                return ErrorCtx(max_length=int(msg))
            except ValueError:
                return NODEFAULT
        # 3 (bare number)
        try:
            return ErrorCtx(min_length=int(msg), max_length=int(msg))
        except ValueError:
            return NODEFAULT

    # of at (least|most) length 3
    elif msg.startswith('of at least length '):
        msg = msg[19:]
        try:
            return ErrorCtx(min_length=int(msg))
        except ValueError:
            return NODEFAULT
    elif msg.startswith('of at most length '):
        msg = msg[18:]
        try:
            return ErrorCtx(max_length=int(msg))
        except ValueError:
            return NODEFAULT
    else:
        # No match
        return NODEFAULT


def get_number_ctx(msg, expected=int):
    """
    Args:
        msg (str): Error message with reason removed
            ">= 3"
            "<= 32"
            "that's a multiple of 6"
        expected (callable): Parser for constraint values.
            int (default), float, etc. Used to convert the extracted
            value string into the appropriate type.

    Returns:
        ErrorCtx | Type[NODEFAULT]:
    """
    msg, _, _ = msg.partition(KEY_at)
    # Remove trailing ", got <actual>" if present
    msg, _, _ = msg.partition(KEY_got)
    if msg.startswith(KEY_ge):
        msg = msg[3:]
        try:
            return ErrorCtx(ge=expected(msg))
        except ValueError:
            return NODEFAULT
    if msg.startswith(KEY_le):
        msg = msg[3:]
        try:
            return ErrorCtx(le=expected(msg))
        except ValueError:
            return NODEFAULT
    if msg.startswith(KEY_gt):
        msg = msg[2:]
        try:
            return ErrorCtx(gt=expected(msg))
        except ValueError:
            return NODEFAULT
    if msg.startswith(KEY_lt):
        msg = msg[2:]
        try:
            return ErrorCtx(lt=expected(msg))
        except ValueError:
            return NODEFAULT
    if KEY_multiple_of in msg:
        _, _, msg = msg.rpartition(KEY_multiple_of)
        try:
            return ErrorCtx(multiple_of=expected(msg))
        except ValueError:
            return NODEFAULT
    # unknown
    return NODEFAULT
