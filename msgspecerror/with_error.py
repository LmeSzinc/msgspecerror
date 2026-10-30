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

from .const import DECODE_ERRORS, T_NODEFAULT, T_json_data, T_msgpack_data
from .parse_error import ErrorInfo, ErrorInfoDetail, _has_placeholder, parse_msgspec_error
from .parse_msgpack import fixup_msgpack_unicode_slow
from .parse_struct import get_field_typehint
from .parse_type import is_struct_type, origin_args, unwrap_typehint
from .repair import (
    _find_failing_dict_key, _find_failing_dict_value, _get_item_model, _get_model_options, _resolve_union_member
)

T = TypeVar('T')

# Return of the *_with_error families: exactly one element is NODEFAULT, the
# object is the model instance on success and the error is set on failure
T_error_result = Tuple[Union[T, T_NODEFAULT], Union[ErrorInfo, T_NODEFAULT]]
T_detail_result = Tuple[Union[T, T_NODEFAULT], Union[ErrorInfoDetail, T_NODEFAULT]]


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
    return keys + list(tail), NODEFAULT


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
            # The path segment tells which member of the union msgspec followed
            member = _resolve_union_member(args, part, obj)
            if member is NODEFAULT:
                return NODEFAULT
            model = unwrap_typehint(member)
            origin, args = origin_args(model)

        # 1. A dict value, msgspec reports it as `...`
        if part == '...':
            if type(obj) is not dict or not args:
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

        # 3. A list / tuple item
        if type(part) is int:
            try:
                obj = obj[part]
            except (IndexError, KeyError, TypeError):
                return _missing_value(loc, index, keys)
            keys.append(part)
            value = obj
            model = _get_item_model(origin, args, part)
            continue

        # 4. A struct field
        if is_struct_type(origin):
            try:
                obj = obj[part]
            except (KeyError, TypeError):
                # e.g. a field missing from the data, or a scalar on the path
                return _missing_value(loc, index, keys)
            keys.append(part)
            value = obj
            if not is_last:
                try:
                    model = get_field_typehint(model, part)
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

    model, options = _get_model_options(model_or_decoder, kwargs)
    try:
        resolved = _resolve_loc(error.loc, data, model, options)
    except Exception:
        # A loc that doesn't match the data / model must not escape, the same
        # contract as tests/repair/test_malformed_loc.py for the repair engine
        resolved = NODEFAULT

    if resolved is NODEFAULT:
        return ErrorInfoDetail(
            msg=error.msg, type=error.type, loc=error.loc, ctx=error.ctx,
            loc_resolved=not _has_placeholder(error.loc),
        )

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
    model, options = _get_model_options(model_or_decoder, kwargs)
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
            msgspec.json.Decoder whose `type` is used. A msgpack decoder
            raises TypeError.

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    # Decoder classes can't be subclassed reliably, an exact type check is
    # enough and avoids reading a `.type` attribute of an unrelated object
    cls = type(model_or_decoder)
    if cls is MsgpackDecoder:
        raise TypeError(
            'load_json_with_error() requires a msgspec.json.Decoder, '
            'got a msgspec.msgpack.Decoder'
        )
    if cls is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
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
            msgspec.msgpack.Decoder whose `type` is used. A json decoder
            raises TypeError.

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    # Decoder classes can't be subclassed reliably, an exact type check is
    # enough and avoids reading a `.type` attribute of an unrelated object
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        raise TypeError(
            'load_msgpack_with_error() requires a msgspec.msgpack.Decoder, '
            'got a msgspec.json.Decoder'
        )
    if cls is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
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
    model, options = _get_model_options(model_or_decoder, kwargs)
    try:
        return convert(data, type=model, **options), NODEFAULT
    except ValidationError as e:
        error = parse_msgspec_error(e)
        return NODEFAULT, resolve_error(error, data, model, **options)


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
            msgspec.json.Decoder whose `type` is used. A msgpack decoder
            raises TypeError.

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    # Decoder classes can't be subclassed reliably, an exact type check is
    # enough and avoids reading a `.type` attribute of an unrelated object
    cls = type(model_or_decoder)
    if cls is MsgpackDecoder:
        raise TypeError(
            'load_json_with_error_detail() requires a msgspec.json.Decoder, '
            'got a msgspec.msgpack.Decoder'
        )
    if cls is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
    else:
        decoder = None
        model = model_or_decoder
    # The key search must validate with the rules the payload was decoded
    # with, a json payload always carries string keys
    _, options = _get_model_options(model_or_decoder, {'str_keys': True})

    try:
        if decoder is None:
            return decode_json(data, type=model), NODEFAULT
        return decoder.decode(data), NODEFAULT
    except DECODE_ERRORS as e:
        error = parse_msgspec_error(e)

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
        return NODEFAULT, ErrorInfoDetail(
            msg=error.msg, type=error.type, loc=error.loc, ctx=error.ctx,
            loc_resolved=not _has_placeholder(error.loc),
        )
    return NODEFAULT, _drop_rewritten_value(resolve_error(error, raw, model, **options), repaired)


def _contains_replacement(value):
    """
    Check whether a value holds the character the byte repair writes.

    The repair rewrites an invalid utf-8 sequence to U+FFFD, so a value that
    holds one may not be the value the payload had. Only the value itself is
    walked, the rest of the document is not touched.

    Args:
        value (Any): Value to check, a container is walked

    Returns:
        bool: True if a U+FFFD character is reachable
    """
    stack = [value]
    while stack:
        item = stack.pop()
        item_type = type(item)
        if item_type is str:
            if '\ufffd' in item:
                return True
        elif item_type is dict:
            stack.extend(item.keys())
            stack.extend(item.values())
        elif item_type in (list, tuple, set, frozenset):
            stack.extend(item)
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


def _drop_rewritten_value(detail, repaired):
    """
    Withdraw the value a byte repair wrote, keeping the location.

    Args:
        detail (ErrorInfoDetail): Detail resolved against the untyped decode
        repaired (bool): True if the payload had to be rewritten before that
            decode, so a located value may hold a character the payload did
            not have

    Returns:
        ErrorInfoDetail: `detail` when the value is trustworthy, a copy without
            `error_value` when it holds a written character
    """
    if not repaired:
        return detail
    if not _contains_replacement(detail.error_value):
        return detail
    # The located value holds a character the repair wrote, it is not the real
    # one. The location is still useful, the value is not handed back.
    return ErrorInfoDetail(
        msg=detail.msg, type=detail.type, loc=detail.loc, ctx=detail.ctx,
        loc_resolved=detail.loc_resolved,
    )


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
            msgspec.msgpack.Decoder whose `type` is used. A json decoder
            raises TypeError.

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    # Decoder classes can't be subclassed reliably, an exact type check is
    # enough and avoids reading a `.type` attribute of an unrelated object
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        raise TypeError(
            'load_msgpack_with_error_detail() requires a msgspec.msgpack.Decoder, '
            'got a msgspec.json.Decoder'
        )
    if cls is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
    else:
        decoder = None
        model = model_or_decoder
    # The key search must validate with the rules the payload was decoded with,
    # msgpack keys are not restricted to strings, so `str_keys` stays False
    _, options = _get_model_options(model_or_decoder, {})

    try:
        if decoder is None:
            return decode_msgpack(data, type=model), NODEFAULT
        return decoder.decode(data), NODEFAULT
    except DECODE_ERRORS as e:
        error = parse_msgspec_error(e)

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
        return NODEFAULT, ErrorInfoDetail(
            msg=error.msg, type=error.type, loc=error.loc, ctx=error.ctx,
            loc_resolved=not _has_placeholder(error.loc),
        )
    detail = resolve_error(error, raw, model, **options)
    return NODEFAULT, _drop_rewritten_value(detail, repaired)
