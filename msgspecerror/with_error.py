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

from .const import DECODE_ERRORS, T_json_data, T_msgpack_data, T_NODEFAULT
from .parse_error import ErrorInfo, ErrorInfoDetail, _has_placeholder, parse_msgspec_error
from .parse_struct import get_field_typehint
from .parse_type import is_struct_type, origin_args, unwrap_typehint
from .repair import (
    _find_failing_dict_key, _find_failing_dict_value, _get_item_model, _get_model,
    _resolve_union_member,
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


def _resolve_loc(loc, data, model):
    """
    Resolve the placeholders of an error location against the raw data.

    Args:
        loc (tuple): Error location, may contain `...` / `...key`
        data (Any): Raw data that failed validation
        model: Type that was being converted into

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
            key = _find_failing_dict_value(obj, child_model)
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
            key = _find_failing_dict_key(obj, args[0])
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


def resolve_error(error, data, model_or_decoder):
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
            msgspec.json.Decoder / msgspec.msgpack.Decoder whose `type` is
            used. A decoder is never used to decode anything here.

    Returns:
        ErrorInfoDetail: `loc` with every `...` / `...key` replaced by the real
            key, `msg` / `type` / `ctx` unchanged, `error_value` holding the
            offending value, and the two flags describing what was achieved:
            `loc_resolved` is True when `loc` has no placeholder,
            `error_value_resolved` is True when `error_value` is not UNSET.
            A `loc` that still holds a placeholder or that does not match the
            data is left as it is.

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

    model = _get_model(model_or_decoder)
    try:
        resolved = _resolve_loc(error.loc, data, model)
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
        loc_resolved=not _has_placeholder(keys),
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


def convert_with_error(data, model_or_decoder):
    """
    Convert an in-memory object, returning the first error instead of raising.

    A validation failure comes back as an ErrorInfo, a wrong argument (an
    illegal type hint, a decoder of the other format) still raises TypeError,
    since that is a caller bug rather than bad data.

    Args:
        data (Any): The raw value that failed validation
        model_or_decoder: The target type to convert into, or a msgspec decoder
            whose `type` is used

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    model = _get_model(model_or_decoder)
    try:
        return convert(data, type=model), NODEFAULT
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
    illegal type hint or a str payload given to the msgpack loader, since that
    is a caller bug rather than bad data.

    Args:
        data (bytes | bytearray | memoryview | str): Json payload
        model_or_decoder: The target type to decode into, or a
            msgspec.json.Decoder whose `type` is used

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    if type(model_or_decoder) is JsonDecoder:
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
    an illegal type hint or a str payload, since that is a caller bug rather
    than bad data.

    Args:
        data (bytes | bytearray | memoryview): Msgpack payload
        model_or_decoder: The target type to decode into, or a
            msgspec.msgpack.Decoder whose `type` is used

    Returns:
        tuple[Any, ErrorInfo | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, error) on failure
    """
    if type(model_or_decoder) is MsgpackDecoder:
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


def convert_with_error_detail(data, model_or_decoder):
    """
    Convert an in-memory object, returning the first error with the offending value.

    Nothing is repaired: the data and the error are left untouched.

    Args:
        data (Any): The raw value that failed validation
        model_or_decoder: The target type to convert into, or a msgspec decoder
            whose `type` is used

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    model = _get_model(model_or_decoder)
    try:
        return convert(data, type=model), NODEFAULT
    except ValidationError as e:
        error = parse_msgspec_error(e)
        return NODEFAULT, resolve_error(error, data, model)


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
            msgspec.json.Decoder whose `type` is used

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    if type(model_or_decoder) is JsonDecoder:
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
        error = parse_msgspec_error(e)

    # The typed decode stops at the first value error, so a later malformed
    # byte is never reached: the second decode is allowed to fail
    try:
        raw = decode_json(data)
    except DECODE_ERRORS:
        raw = NODEFAULT
    if raw is NODEFAULT:
        # No raw data to walk, the location keeps msgspec's placeholders
        return NODEFAULT, ErrorInfoDetail(
            msg=error.msg, type=error.type, loc=error.loc, ctx=error.ctx,
            loc_resolved=not _has_placeholder(error.loc),
        )
    return NODEFAULT, resolve_error(error, raw, model)


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
            msgspec.msgpack.Decoder whose `type` is used

    Returns:
        tuple[Any, ErrorInfoDetail | NODEFAULT]: (obj, NODEFAULT) on success,
            (NODEFAULT, detail) on failure
    """
    if type(model_or_decoder) is MsgpackDecoder:
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
        error = parse_msgspec_error(e)

    # The typed decode stops at the first value error, so a later malformed
    # byte is never reached: the second decode is allowed to fail
    try:
        raw = decode_msgpack(data)
    except DECODE_ERRORS:
        raw = NODEFAULT
    if raw is NODEFAULT:
        # No raw data to walk, the location keeps msgspec's placeholders
        return NODEFAULT, ErrorInfoDetail(
            msg=error.msg, type=error.type, loc=error.loc, ctx=error.ctx,
            loc_resolved=not _has_placeholder(error.loc),
        )
    return NODEFAULT, resolve_error(error, raw, model)
