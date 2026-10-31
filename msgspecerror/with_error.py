"""
The `*_with_error` and `*_with_error_detail` families.

Both families report the first error instead of raising, and neither repairs
anything. They differ in what the error carries:

- `*_with_error` returns the ErrorInfo exactly as msgspec reported it, so a
  dict entry keeps the `...` / `...key` placeholder.
- `*_with_error_detail` walks the raw data to replace the placeholder with the
  real key and collects the offending value, returning an ErrorInfoDetail.

The repair family (`*_with_default`) lives in `repair.py`. The two modules
share only the low level walkers of `repair.py`, which are imported here; this
module is never imported by `repair.py`.
"""
from typing import Any, Tuple, Type, TypeVar, Union, overload

from msgspec import NODEFAULT, UNSET, ValidationError, convert
from msgspec.json import Decoder as JsonDecoder, decode as decode_json
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as decode_msgpack

from .const import DECODE_ERRORS, MSG_RAW_FROM_MEMORY, T_NODEFAULT, ErrorType, T_json_data, T_msgpack_data
from .parse_decoder import _type_error, get_decoder_model
from .parse_error import ErrorInfo, ErrorInfoDetail, _has_placeholder, parse_msgspec_error
from .parse_model import get_field_typehint, is_arraylike
from .parse_msgpack import fixup_msgpack_unicode_slow
from .parse_type import is_struct_like, origin_args, unwrap_typehint
from .repair import (
    _find_failing_dict_key, _find_failing_dict_value, _get_index_model, _resolve_union_member, _validate_entry
)

T = TypeVar('T')

# Return of the *_with_error families: exactly one element is NODEFAULT, the
# object is the model instance on success and the error is set on failure
T_error_result = Tuple[Union[T, T_NODEFAULT], Union[ErrorInfo, T_NODEFAULT]]
T_detail_result = Tuple[Union[T, T_NODEFAULT], Union[ErrorInfoDetail, T_NODEFAULT]]

# Model origins that are decoded from an array, an int path segment addresses
# their items by position. `origin_args()` resolves the abstract sequence
# aliases (`Sequence`, `Collection`, ...) to these concrete types.
_ARRAY_ORIGINS = frozenset({list, tuple, set, frozenset})


def _missing_value(loc, index, keys):
    """
    Result of a path that can not be walked any deeper.

    The remaining segments are kept when they are literal, so the caller still
    gets a complete location. A remaining placeholder can not be resolved.

    Args:
        loc (tuple): Full error location
        index (int): Index of the segment that could not be walked
        keys (list): Segments resolved so far

    Returns:
        tuple[list, Any] | NODEFAULT: (keys, NODEFAULT), or the NODEFAULT sentinel
    """
    tail = loc[index:]
    if _has_placeholder(tail):
        return NODEFAULT
    # A tail that is not made of positions and names is no location either:
    # handing it back would describe a path that never existed
    for part in tail:
        part_type = type(part)
        if part_type is not int and part_type is not str:
            return NODEFAULT
    return keys + list(tail), NODEFAULT


def _unresolved_detail(error):
    """
    Build the detail of an error whose location was not walked.

    Only the failure path calls this, so a call that validates pays nothing for
    it. `ErrorInfoDetail` promises a tuple of int / str segments, and a
    hand-built `ErrorInfo` or a bug in msgspec / in our path parser can hold
    anything: a location that can not be delivered as one is dropped to `()`,
    the only location that can be handed back without inventing a path. The
    text of the original location stays readable in `msg`.

    Args:
        error (ErrorInfo): The parsed error, its `loc` is handed back

    Returns:
        ErrorInfoDetail: The location without a placeholder, and `error_value`
            unset. `loc` is `()` when it is not a usable path
    """
    loc = error.loc
    loc_type = type(loc)
    if loc_type is list:
        # A list is the same path, but the detail promises a tuple
        loc = tuple(loc)
        loc_type = tuple
    if loc_type is not tuple:
        # A `str` would be read character by character, a scalar is no path
        loc = ()
    else:
        for part in loc:
            part_type = type(part)
            # `part_type is int` is False for bool, which is not an index
            if part_type is not int and part_type is not str:
                loc = ()
                break
    return ErrorInfoDetail(
        msg=error.msg, type=error.type, loc=loc, ctx=error.ctx,
        loc_resolved=not _has_placeholder(loc))


def _resolve_loc(loc, data, model, options):
    """
    Resolve the placeholders of an error location against the raw data.

    Args:
        loc (tuple): Error location, may contain `...` / `...key`
        data (Any): Raw data that failed validation
        model: Type that was being converted into
        options (dict): Extra keyword arguments for `convert`, the same ones the
            call that failed used

    Returns:
        tuple[list, Any] | NODEFAULT: (keys, value) with every placeholder
            replaced by the real key, or the NODEFAULT sentinel when a
            placeholder can not be resolved
    """
    keys = []
    obj = data
    value = data
    for index, part in enumerate(loc):
        is_last = index == len(loc) - 1
        model = unwrap_typehint(model)
        origin, args = origin_args(model)

        if origin is Union:
            # The path segment tells which member of the union msgspec followed,
            # the kind comes back with the member that matched
            member = _resolve_union_member(args, part, obj)
            if member is NODEFAULT:
                return NODEFAULT
            model, model_kind = member
            model = unwrap_typehint(model)
            origin, args = origin_args(model)
        else:
            model_kind = None

        # 1. A dict value, msgspec reports it as `...`
        if part == '...':
            if type(obj) is not dict or not args or len(args) < 2:
                # A placeholder needs a dict model with a key and a value type
                return NODEFAULT
            child_model = args[1]
            key = _find_failing_dict_value(obj, child_model, options)
            if key is NODEFAULT:
                return NODEFAULT
            keys.append(key)
            obj = obj[key]
            value = obj
            model = child_model
            continue

        # 2. A dict key, msgspec reports it as `...key`, always the last segment
        if part == '...key':
            if not is_last or type(obj) is not dict or not args:
                return NODEFAULT
            key = _find_failing_dict_key(obj, args[0], options)
            if key is NODEFAULT:
                return NODEFAULT
            keys.append(key)
            # The offending value is the key itself, not the value under it
            return keys, key

        # The placeholders above return on their own, so only a segment that
        # names a field or a position reaches this point, and that is the one
        # that needs the kind
        if model_kind is None:
            model_kind = is_struct_like(origin, unwrap=False)

        # 3. A list / tuple item
        if type(part) is int:
            if part < 0:
                # msgspec reports a position, never a negative index: a
                # negative segment would silently address the last item
                return NODEFAULT
            if not isinstance(obj, (list, tuple)):
                # msgspec decodes an array to a list, anything else means the
                # location, the data and the model disagree. A dict and a str
                # can be indexed too, and would silently give a wrong item.
                return NODEFAULT
            if origin not in _ARRAY_ORIGINS \
                    and not is_arraylike(origin, known_struct_type=model_kind, unwrap=False):
                # An index only addresses a sequence or an array-decoded model.
                # The data alone is not enough: a dict model with list data
                # would hand back an item the model never looked at.
                return NODEFAULT
            try:
                obj = obj[part]
            except (IndexError, KeyError, TypeError):
                return _missing_value(loc, index, keys)
            keys.append(part)
            value = obj
            model = _get_index_model(origin, args, part, known_struct_type=model_kind)
            continue

        # 4. A field of a model
        if model_kind:
            if type(part) is not str:
                # A field is addressed by its name. A bool would even find the
                # key `1` of a dict, every other type is not a name at all
                return NODEFAULT
            if is_arraylike(origin, known_struct_type=model_kind, unwrap=False):
                # An `array_like` struct and a NamedTuple are decoded from an
                # array, msgspec reports a position for them and never a name.
                # A name here is a mismatch, even if the data happens to be a
                # dict that holds that key.
                return NODEFAULT
            # An object model is read from a dict, or from the attributes of a
            # source object when the failed call used `from_attributes=True`
            if type(obj) is dict:
                try:
                    obj = obj[part]
                except (KeyError, TypeError):
                    # e.g. a field missing from the data
                    return _missing_value(loc, index, keys)
            elif options.get('from_attributes'):
                try:
                    obj = getattr(obj, part)
                except (AttributeError, TypeError):
                    return _missing_value(loc, index, keys)
            else:
                return NODEFAULT
            keys.append(part)
            value = obj
            if not is_last:
                try:
                    model = get_field_typehint(model, part, known_struct_type=model_kind)
                except AttributeError:
                    return NODEFAULT
            continue

        # 5. The path does not match the model
        return NODEFAULT

    return keys, value


def resolve_error(error, data, model_or_decoder, **kwargs):
    """
    Resolve the dict keys that msgspec leaves as `...` in an error location.

    msgspec reports a failed dict entry as `$[...]`, parse_msgspec_error()
    keeps the placeholder in `loc`. This function walks the raw data with the
    same model that was being converted and replaces every placeholder with
    the actual key, collecting the offending value on the way.

    It is read-only: `data` and `error` are never modified, and no exception is
    raised for a location that doesn't match the data / model.

    Args:
        error (ErrorInfo | ValidationError | str): Error to resolve. An
            ErrorInfo is used as it is, anything else is parsed with
            parse_msgspec_error() first. An ErrorInfoDetail that is already
            resolved (`loc_resolved` and `error_value_resolved` both True) is
            returned unchanged.
        data (Any): The raw value that failed validation, e.g. the decoded
            yaml / json / msgpack object. It must be the data before
            validation, not the converted model instance.
        model_or_decoder: The type that was being converted into, or a
            msgspec.json.Decoder / msgspec.msgpack.Decoder to take `.type`,
            `.strict` and `.dec_hook` from. A decoder is never used to decode
            anything here, its options only describe how the data was
            validated, so the key search sees the same rules.
        **kwargs: Options of the equivalent `msgspec.convert` call, they
            override the decoder ones. Pass whatever the failing call passed,
            e.g. `strict=False`: validation rules decide which entry fails, so
            a key searched with other rules can point at the wrong entry.

    Returns:
        ErrorInfoDetail: `loc` with every `...` / `...key` replaced by the real
            key, `msg` / `type` / `ctx` unchanged, `error_value` holding the
            offending value, and the two flags describing what was achieved:
            `loc_resolved` is True when the data was walked all the way, so
            `loc` points at one concrete entry, `error_value_resolved` is True
            when `error_value` is not UNSET. A `loc` that still holds a
            placeholder or that does not match the data is left as it is, and
            `loc_resolved` is then False.

    Examples:
        class ItemInfo(Struct):
            amount: int = 0
        class Inventory(Struct):
            items: Dict[str, ItemInfo] = {}
        data = {'items': {'apple': {'amount': 'ABC'}}}
        try:
            convert(data, type=Inventory)
        except ValidationError as e:
            error = parse_msgspec_error(e)
            print(error.loc)
            # ('items', '...', 'amount')
            detail = resolve_error(error, data, Inventory)
            print(detail.loc)
            # ('items', 'apple', 'amount')
            print(detail.error_value)
            # ABC
    """
    if isinstance(error, ErrorInfoDetail) and error.loc_resolved and error.error_value_resolved:
        # Fully resolved, walking again can not add anything
        return error
    if not isinstance(error, ErrorInfo):
        error = parse_msgspec_error(error)

    _, model, options = get_decoder_model(model_or_decoder, options=kwargs)
    return _resolve_error_with(error, data, model, options)


def _resolve_error_with(error, data, model, options):
    """
    Walk a location against a model whose rules are already resolved.

    Args:
        error (ErrorInfo): The parsed error, its `loc` is walked
        data (Any): The data that was being validated
        model (Any): The target type, a decoder is already resolved to `.type`
        options (dict): Options of the equivalent `msgspec.convert` call

    Returns:
        ErrorInfoDetail: The same result `resolve_error()` reports
    """
    loc = error.loc
    loc_type = type(loc)
    if loc_type is list:
        loc = tuple(loc)
        loc_type = tuple
    if loc_type is not tuple:
        # A location is a tuple of segments. Anything else (a str, a dict, a
        # scalar) would be read as characters or items and walked as a path
        # that never existed, so it is rejected before the walkers see it.
        # One type check, and only on the path that has already failed.
        return _unresolved_detail(error)

    try:
        resolved = _resolve_loc(loc, data, model, options)
    except Exception:
        # A loc that doesn't match the data / model must not escape, the same
        # contract as tests/repair/test_malformed_loc.py for the repair engine
        resolved = NODEFAULT

    if resolved is NODEFAULT:
        return _unresolved_detail(error)

    keys, value = resolved
    return ErrorInfoDetail(
        msg=error.msg, type=error.type, loc=tuple(keys), ctx=error.ctx,
        # NODEFAULT can not be stored in a struct field, it breaks json encoding
        error_value=UNSET if value is NODEFAULT else value,
        # The walk consumed every placeholder, so the location points at one
        # concrete entry. A dict key that happens to be the string `...` is a
        # real key, it is not a placeholder left behind.
        loc_resolved=True,
        error_value_resolved=value is not NODEFAULT,
    )


@overload
def convert_with_error(
        data: Any,
        model_or_decoder: "Type[T]",
) -> T_error_result: ...


@overload
def convert_with_error(
        data: Any,
        model_or_decoder: "Union[JsonDecoder[T], MsgpackDecoder[T]]",
) -> T_error_result: ...


def convert_with_error(data, model_or_decoder, **kwargs):
    """
    Convert an in-memory object, returning the first error instead of raising.

    A validation failure comes back as an ErrorInfo, a wrong argument (an
    illegal type hint) still raises TypeError, since that is a caller bug
    rather than bad data.

    Args:
        data (Any): The raw value that failed validation
        model_or_decoder: The target type to convert into, or a msgspec decoder
            to take `.type` / `.strict` / `.dec_hook` from
        **kwargs: Options of the equivalent `msgspec.convert` call (`strict`,
            `from_attributes`, `dec_hook`, `builtin_types`, `str_keys`), they
            override the decoder ones

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    _, model, options = get_decoder_model(model_or_decoder, options=kwargs)
    try:
        return convert(data, type=model, **options), NODEFAULT
    except ValidationError as e:
        return NODEFAULT, parse_msgspec_error(e)


@overload
def load_json_with_error(
        data: T_json_data,
        model_or_decoder: "Type[T]",
) -> T_error_result: ...


@overload
def load_json_with_error(
        data: T_json_data,
        model_or_decoder: "JsonDecoder[T]",
) -> T_error_result: ...


def load_json_with_error(data, model_or_decoder):
    """
    Decode a json payload, returning the first error instead of raising.

    This function never raises for bad data: a validation failure, a malformed
    or truncated payload and an invalid utf-8 all come back as an ErrorInfo.
    The location is left as msgspec reports it, so dict entries keep the
    `...` / `...key` placeholder. Use load_json_with_error_detail() to get the
    concrete key. A TypeError is still raised for a wrong argument, such as an
    illegal type hint or a msgpack decoder, since that is a caller bug rather
    than bad data.

    Args:
        data (bytes | bytearray | memoryview | str): Json payload
        model_or_decoder: The target type to decode into, or a
            msgspec.json.Decoder, which decodes the payload as it is. A msgpack
            decoder raises TypeError.

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    # `model` is bound for both kinds of argument, so the `type=` call site
    # below never reads an unbound name. A decoder decodes the payload itself,
    # only a typehint reaches that call. The `convert` options are not resolved
    # here, nothing on this path reads them. The class check is the same exact
    # type comparison get_decoder_model() does, and the message comes from the
    # same builder.
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
    elif cls is MsgpackDecoder:
        raise TypeError(_type_error('load_json_with_error', 'json', 'msgpack'))
    else:
        decoder = None
        model = model_or_decoder

    try:
        if decoder is None:
            return decode_json(data, type=model), NODEFAULT
        return decoder.decode(data), NODEFAULT
    except DECODE_ERRORS as e:
        return NODEFAULT, parse_msgspec_error(e)


@overload
def load_msgpack_with_error(
        data: T_msgpack_data,
        model_or_decoder: "Type[T]",
) -> T_error_result: ...


@overload
def load_msgpack_with_error(
        data: T_msgpack_data,
        model_or_decoder: "MsgpackDecoder[T]",
) -> T_error_result: ...


def load_msgpack_with_error(data, model_or_decoder):
    """
    Decode a msgpack payload, returning the first error instead of raising.

    This function never raises for bad data: a validation failure, a malformed
    or truncated payload and an invalid utf-8 all come back as an ErrorInfo.
    The location is left as msgspec reports it, so dict entries keep the
    `...` / `...key` placeholder. Use load_msgpack_with_error_detail() to get
    the concrete key. A TypeError is still raised for a wrong argument, such as
    an illegal type hint, a str payload or a json decoder, since that is a
    caller bug rather than bad data.

    Args:
        data (bytes | bytearray | memoryview): Msgpack payload
        model_or_decoder: The target type to decode into, or a
            msgspec.msgpack.Decoder, which decodes the payload as it is. A json
            decoder raises TypeError.

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    # `model` is bound for both kinds of argument, so the `type=` call site
    # below never reads an unbound name. A decoder decodes the payload itself,
    # only a typehint reaches that call. The `convert` options are not resolved
    # here, nothing on this path reads them. The class check is the same exact
    # type comparison get_decoder_model() does, and the message comes from the
    # same builder.
    cls = type(model_or_decoder)
    if cls is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
    elif cls is JsonDecoder:
        raise TypeError(_type_error('load_msgpack_with_error', 'msgpack', 'json'))
    else:
        decoder = None
        model = model_or_decoder

    try:
        if decoder is None:
            return decode_msgpack(data, type=model), NODEFAULT
        return decoder.decode(data), NODEFAULT
    except DECODE_ERRORS as e:
        return NODEFAULT, parse_msgspec_error(e)


@overload
def convert_with_error_detail(
        data: Any,
        model_or_decoder: "Type[T]",
) -> T_detail_result: ...


@overload
def convert_with_error_detail(
        data: Any,
        model_or_decoder: "Union[JsonDecoder[T], MsgpackDecoder[T]]",
) -> T_detail_result: ...


def convert_with_error_detail(data, model_or_decoder, **kwargs):
    """
    Convert an in-memory object, returning the first error with the offending value.

    Nothing is repaired: the data and the error are left untouched. The key
    search uses the same options as the convert call, so a `strict=False` call
    points at the entry that really failed, not at one the parser rejected for
    the wrong rules.

    A `msgspec.Raw` field can not be built from an in-memory object: `convert`
    stops there, so the location it reports names the raw field and not the
    error behind it, if any. A placeholder `Raw` is pinned at that location and
    the value is validated again, the same way a dict entry is judged by the key
    search, so a real error still comes back with its own location and value.
    When only the raw field complains there is no verdict to report, and the
    call is answered with `INPUT_REJECTED` instead of a location that looks
    precise while it only names the raw field.

    Args:
        data (Any): The raw value that failed validation
        model_or_decoder: The target type to convert into, or a msgspec decoder
            to take `.type` / `.strict` / `.dec_hook` from
        **kwargs: Options of the equivalent `msgspec.convert` call (`strict`,
            `from_attributes`, `dec_hook`, `builtin_types`, `str_keys`), they
            override the decoder ones

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    _, model, options = get_decoder_model(model_or_decoder, options=kwargs)
    try:
        return convert(data, type=model, **options), NODEFAULT
    except ValidationError as e:
        error = parse_msgspec_error(e)

    # Only `convert` reports here, and it rejects an already decoded object at a
    # `msgspec.Raw` field, so a raw-shaped error is that artifact and not a data
    # error. The field itself can not be judged: what matters is the error it
    # hides, so the value is validated again with a placeholder raw pinned at
    # the reported location.
    if error.ctx.may_raw_rejection:
        error = _validate_entry(data, model, options)
    if error is None or error is NODEFAULT:
        # Only a raw field complains: every value is the one the caller passed,
        # but an in-memory object can never build a raw field, so there is no
        # verdict to hand back
        return NODEFAULT, ErrorInfoDetail(
            msg=MSG_RAW_FROM_MEMORY, type=ErrorType.INPUT_REJECTED, loc=(),
            # An empty location is the whole document: concrete, but there is
            # no entry to point at and no offending value to hand back
            loc_resolved=True)
    # The model and the options are already resolved, `resolve_error` would
    # resolve them a second time from the same arguments
    return NODEFAULT, _resolve_error_with(error, data, model, options)


@overload
def load_json_with_error_detail(
        data: T_json_data,
        model_or_decoder: "Type[T]",
) -> T_detail_result: ...


@overload
def load_json_with_error_detail(
        data: T_json_data,
        model_or_decoder: "JsonDecoder[T]",
) -> T_detail_result: ...


def load_json_with_error_detail(data, model_or_decoder):
    """
    Decode a json payload, returning the first error with the offending value.

    On failure the raw payload is decoded a second time without a target type so
    the error location can be walked. The typed decode stops at the first value
    error, so a later malformed byte never raises, and the second decode is
    allowed to fail: the location then keeps msgspec's placeholders and
    `error_value` stays UNSET, instead of a guess.

    Nothing is repaired: the payload, the data and the error are left untouched.

    Args:
        data (bytes | bytearray | memoryview | str): Json payload
        model_or_decoder: The target type to decode into, or a
            msgspec.json.Decoder, which decodes the payload as it is. A msgpack
            decoder raises TypeError.

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    # The class check is the same exact type comparison get_decoder_model()
    # does, and the message comes from the same builder. `model` is bound for
    # both kinds of argument, a decoder decodes the payload itself and only a
    # typehint reaches the `type=` call.
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
    elif cls is MsgpackDecoder:
        raise TypeError(_type_error('load_json_with_error_detail', 'json', 'msgpack'))
    else:
        decoder = None
        model = model_or_decoder

    try:
        if decoder is None:
            return decode_json(data, type=model), NODEFAULT
        return decoder.decode(data), NODEFAULT
    except DECODE_ERRORS as e:
        error = parse_msgspec_error(e)

    # The key search must validate with the rules the payload was decoded with,
    # and nothing but that search reads them: the happy path above is a plain
    # decode, so the `convert` options are only resolved here
    _, model, options = get_decoder_model(
        model_or_decoder, JsonDecoder, {'str_keys': True}, 'load_json_with_error_detail')

    # The typed decode stops at the first value error, so a later malformed
    # byte is never reached: the second decode is allowed to fail
    repaired = False
    try:
        raw = decode_json(data)
    except UnicodeDecodeError:
        # A `msgspec.Raw` field keeps the payload bytes, so the typed decode
        # reads a string that an untyped decode rejects. Rewriting the invalid
        # bytes is enough to find the failing key, the same way the msgpack
        # family does it.
        try:
            raw = decode_json(_replace_invalid_utf8(data))
        except DECODE_ERRORS:
            raw = NODEFAULT
        else:
            repaired = True
    except DECODE_ERRORS:
        raw = NODEFAULT
    if raw is NODEFAULT:
        # No raw data to walk, the location keeps msgspec's placeholders
        return NODEFAULT, _unresolved_detail(error)
    detail = _resolve_error_with(error, raw, model, options)
    if repaired and _contains_replacement(detail.error_value):
        # The located value holds a character the repair wrote, it is not the
        # real one. The location is still useful, the value is not handed back.
        detail = ErrorInfoDetail(
            msg=detail.msg, type=detail.type, loc=detail.loc, ctx=detail.ctx,
            loc_resolved=detail.loc_resolved,
        )
    return NODEFAULT, detail


# The container kinds the replacement walk descends into. A frozenset lookup is
# one hash probe, a tuple would compare every entry, and a scalar is the common
# case in a decoded payload.
_CONTAINER_TYPES = frozenset({list, tuple, set, frozenset})


def _contains_replacement(value):
    """
    Check whether a value holds the character the byte repair writes.

    The repair rewrites an invalid utf-8 sequence to U+FFFD, so a value that
    holds one may not be the value the payload had. Only the value itself is
    walked, the rest of the document is not touched.

    The walk collects the next level into a fresh list and iterates the current
    one with `for`: a `pop()` per item costs more than the two loop conditions,
    and the level order does not matter for a yes / no answer.

    Args:
        value (Any): Value to check, a container is walked

    Returns:
        bool: True if a U+FFFD character is reachable
    """
    level = [value]
    while level:
        current, level = level, []
        for item in current:
            item_type = type(item)
            if item_type is str:
                if '\ufffd' in item:
                    return True
            elif item_type is dict:
                level.extend(item.keys())
                level.extend(item.values())
            elif item_type in _CONTAINER_TYPES:
                level.extend(item)
    return False


def _replace_invalid_utf8(data):
    """
    Rewrite the invalid utf-8 bytes of a json payload to U+FFFD.

    Args:
        data (bytes | bytearray | memoryview | str): Payload to rewrite, a str
            payload is already text and is returned as it is

    Returns:
        str: The payload as text, every invalid sequence replaced
    """
    if isinstance(data, str):
        return data
    return bytes(data).decode('utf-8', errors='replace')


@overload
def load_msgpack_with_error_detail(
        data: T_msgpack_data,
        model_or_decoder: "Type[T]",
) -> T_detail_result: ...


@overload
def load_msgpack_with_error_detail(
        data: T_msgpack_data,
        model_or_decoder: "MsgpackDecoder[T]",
) -> T_detail_result: ...


def load_msgpack_with_error_detail(data, model_or_decoder):
    """
    Decode a msgpack payload, returning the first error with the offending value.

    On failure the raw payload is decoded a second time without a target type so
    the error location can be walked. The typed decode stops at the first value
    error, so a later malformed byte never raises, and the second decode is
    allowed to fail: the location then keeps msgspec's placeholders and
    `error_value` stays UNSET, instead of a guess.

    Nothing is repaired: the payload, the data and the error are left untouched.

    Args:
        data (bytes | bytearray | memoryview): Msgpack payload
        model_or_decoder: The target type to decode into, or a
            msgspec.msgpack.Decoder, which decodes the payload as it is. A json
            decoder raises TypeError.

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    # The class check is the same exact type comparison get_decoder_model()
    # does, and the message comes from the same builder. `model` is bound for
    # both kinds of argument, a decoder decodes the payload itself and only a
    # typehint reaches the `type=` call.
    cls = type(model_or_decoder)
    if cls is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
    elif cls is JsonDecoder:
        raise TypeError(_type_error('load_msgpack_with_error_detail', 'msgpack', 'json'))
    else:
        decoder = None
        model = model_or_decoder

    try:
        if decoder is None:
            return decode_msgpack(data, type=model), NODEFAULT
        return decoder.decode(data), NODEFAULT
    except DECODE_ERRORS as e:
        error = parse_msgspec_error(e)

    # The key search must validate with the rules the payload was decoded with,
    # and nothing but that search reads them: the happy path above is a plain
    # decode. Msgpack keys are not restricted to strings, so `str_keys` stays off
    _, model, options = get_decoder_model(
        model_or_decoder, MsgpackDecoder, None, 'load_msgpack_with_error_detail')

    # The typed decode stops at the first value error, so a later malformed
    # byte is never reached: the second decode is allowed to fail
    repaired = False
    try:
        raw = decode_msgpack(data)
    except UnicodeDecodeError:
        # A `msgspec.Raw` field keeps the payload bytes, so the typed decode
        # reads a string with invalid utf-8 that an untyped decode rejects.
        # Repairing the bytes is enough to find the failing key, but the
        # repaired value is not the real one, it is never handed back.
        try:
            raw = decode_msgpack(fixup_msgpack_unicode_slow(data, utf8_error='replace'))
        except DECODE_ERRORS:
            raw = NODEFAULT
        else:
            repaired = True
    except DECODE_ERRORS:
        raw = NODEFAULT
    if raw is NODEFAULT:
        # No raw data to walk, the location keeps msgspec's placeholders
        return NODEFAULT, _unresolved_detail(error)
    detail = _resolve_error_with(error, raw, model, options)
    if repaired and _contains_replacement(detail.error_value):
        # The located value holds a character the repair wrote, it is not the
        # real one. The location is still useful, the value is not handed back.
        detail = ErrorInfoDetail(
            msg=detail.msg, type=detail.type, loc=detail.loc, ctx=detail.ctx,
            loc_resolved=detail.loc_resolved,
        )
    return NODEFAULT, detail
