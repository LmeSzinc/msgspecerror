from typing import Any, Dict, Literal, Type, TypeVar, overload, Union, Tuple, List

from msgspec import DecodeError, NODEFAULT, ValidationError, convert
from msgspec.json import Decoder as JsonDecoder, decode as decode_json
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as decode_msgpack

from . import const
from .const import ErrorType
from .parse_error import MsgspecError, parse_msgspec_error
from .parse_msgpack import fixup_msgpack_unicode_fast, fixup_msgpack_unicode_slow
from .parse_struct import get_field_default, get_field_typehint
from .parse_type import get_default, is_struct_type, origin_args
from .repair_unicode import _collect_unicode_replace


def _repair_once(
        raw_obj, model, error: MsgspecError
) -> "tuple[Any, MsgspecError | Type[NODEFAULT]]":
    """
    Args:
        raw_obj:
        model:
        error:

    Returns:
        tuple[Any, MsgspecError | _NoDefault]: (obj, error) the repaired object and fixed error.
            If repair failed, obj will be NODEFAULT.
            If this error is not consider as an error, error will be NODEFAULT and won't be collected.
    """
    # handle root error
    if not error.loc:
        value = get_default(model)
        return value, error

    # handle errors in deep path
    obj = raw_obj
    list_loc = [loc for loc in error.loc]
    last_index = len(list_loc) - 1
    for i, part in enumerate(list_loc):
        is_last = i == last_index

        model_origin, model_args = origin_args(model)

        # 1. Invalid dict value
        # msgspec does not tell which key is invalid, just giving placeholder '...', we need to find the exact key
        if part == '...':
            if type(obj) is not dict:
                # Error path `...` implies a dict, but obj doesn't match.
                return NODEFAULT, error

            # Find the specific key at this level that causes the failure.
            try:
                child_model = model_args[1]
            except IndexError:
                # this shouldn't happen because a dict typehint should have a key and a value
                return NODEFAULT, error
            for key, value in obj.items():
                try:
                    # Attempt to convert just the value to see if it's the source.
                    convert(value, type=child_model)
                except ValidationError:
                    break
            else:
                # Could not identify the failing key. Unrecoverable.
                return NODEFAULT, error

            # fix loc
            loc = error.loc
            error.loc = loc[:i] + (key,) + loc[i + 1:]
            if is_last:
                # fix obj
                value = get_default(child_model)
                if value is NODEFAULT:
                    return NODEFAULT, error
                obj[key] = value
                return raw_obj, error
            else:
                # go deeper
                try:
                    obj = obj[key]
                except KeyError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                model = child_model
                continue

        # 2. Invalid dict key
        # '...key' is our special defined key to distinguish type error on key and on value
        if part == '...key':
            if type(obj) is not dict:
                # Error path `...` implies a dict, but obj doesn't match.
                return NODEFAULT, error

            # Find the specific key that causes the failure
            try:
                key_model = model_args[0]
            except IndexError:
                # this shouldn't happen because a dict typehint should have a key and a value
                return NODEFAULT, error
            except TypeError:
                # Msgpack data with integer keys validated against a struct.
                # model_args is None
                return NODEFAULT, error
            for key in obj.keys():
                try:
                    convert(key, key_model)
                except ValidationError:
                    break
            else:
                # Could not identify the failing key. Unrecoverable.
                return NODEFAULT, error

            # fix loc
            loc = error.loc
            error.loc = loc[:i] + (key,) + loc[i + 1:]

            if is_last:
                # fix obj
                try:
                    value = obj.pop(key)
                except KeyError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                # here comes something tricky
                # In `msgspec.json.decode(..., Dict[int, int])`,
                #   the output key will be int or any other depending on your type
                # If `msgspec.json.decode(...)`, the output key will always be string,
                #   because json key can only be string
                # We create a temp dict and use `str_keys=True`, fooling msgspec to encode str to target type
                temp_model = Dict[key_model, int]
                temp_dict = {key: 1}
                try:
                    temp_dict = convert(temp_dict, temp_model, str_keys=True)
                except ValidationError:
                    pass
                else:
                    # set validated key to object
                    for temp_key in temp_dict:
                        obj[temp_key] = value
                        # we don't consider this an error, it's not a user input error
                        return raw_obj, NODEFAULT
                # failed to convert, key is removed as a fix
                return raw_obj, error
            else:
                # this shouldn't happen because '...key' should be the last key,
                # since json/msgpack key can't be objects.
                return NODEFAULT, error

        # 3. Invalid item in list
        if type(part) is int:
            if type(obj) is not list:
                # Error path like `0` implies a list, but obj doesn't match.
                return NODEFAULT, error
            if is_last:
                # fix obj, just pop the item
                try:
                    obj.pop(part)
                except IndexError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                return raw_obj, error
            else:
                # go deeper
                try:
                    obj = obj[part]
                except IndexError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                model = model_args[0]
                continue

        # 4. Invalid object field
        if is_struct_type(model):
            if type(obj) is not dict:
                # Error path like `0` implies a list, but obj doesn't match.
                return NODEFAULT, error
            if is_last:
                # fix obj
                # try field default first
                try:
                    value = get_field_default(model, part)
                except AttributeError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                # if field doesn't have a default, try to get from type
                if value is NODEFAULT:
                    child_model = get_field_typehint(model, part)
                    value = get_default(child_model)
                    # still no default?
                    if value is NODEFAULT:
                        return NODEFAULT, error
                obj[part] = value
                return raw_obj, error
            else:
                # go deeper
                try:
                    obj = obj[part]
                except KeyError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                try:
                    model = get_field_typehint(model, part)
                except AttributeError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                continue

        # 5. Fallback
        # This shouldn't happen unless raw_obj, model, error don't match
        # If it really happens, we trust `model` and do the best we can
        value = get_default(model)
        return value, error


def _handle_obj_repair(
        raw_obj, model, error: ValidationError
) -> "tuple[Any, list[MsgspecError]]":
    """
    Args:
        raw_obj (Any):
        model (type): The target type to decode into.
        error (ValidationError):

    Returns:
        tuple[any, list[MsgspecError]]:
    """
    error = parse_msgspec_error(error)
    collected_errors = []
    for _ in range(const.MAXIMUM_REPAIR):
        # repair once
        raw_obj, error = _repair_once(raw_obj, model, error)
        if error is not NODEFAULT:
            collected_errors.append(error)

        # repair failed
        if raw_obj is NODEFAULT:
            return get_default(model, return_obj=True), collected_errors

        # try if all repaired
        try:
            return convert(raw_obj, model), collected_errors
        except ValidationError as e:
            error = parse_msgspec_error(e)
            # User-code errors (__post_init__, dec_hook, etc.) are wrapped as
            # WRAPPED_ERROR. Retrying the same fix won't help — bail immediately.
            if error.type is ErrorType.WRAPPED_ERROR:
                rejected = MsgspecError(
                    'Input rejected: validation failed on struct defaults',
                    type=ErrorType.INPUT_REJECTED, loc=())
                collected_errors.append(rejected)
                return get_default(model, return_obj=True), collected_errors

    # Too many iterations — possible malicious input
    rejected = MsgspecError(
        'Input rejected: too many repair cycles',
        type=ErrorType.INPUT_REJECTED, loc=())
    collected_errors.append(rejected)
    return get_default(model, return_obj=True), collected_errors


def _handle_root_error(
        model: Any, error
) -> "tuple[Any, list[MsgspecError]]":
    """
    Args:
        model (type): The target type to decode into.
        error (str | Exception):

    Returns:
        tuple[any, list[MsgspecError]]:
    """
    collected_errors = [parse_msgspec_error(error)]
    # try if model can be default constructed
    return get_default(model, return_obj=True), collected_errors


def _handle_json_unicode_repair(
        data: bytes,
        *,
        utf8_error: "Literal['replace', 'ignore']"
) -> "tuple[bytes | str, list[MsgspecError]]":
    """
    Helper function to repair json data that has unicode error
    """
    if utf8_error == 'replace':
        data = data.decode('utf-8', errors=utf8_error)
        # msgspec allow decoding json in str, so we just keep in str
        # data = data_str.encode('utf-8')
        try:
            raw_obj = decode_json(data)
        except DecodeError:
            return data, []
        return data, _collect_unicode_replace(raw_obj)
    elif utf8_error == 'ignore':
        data = data.decode('utf-8', errors=utf8_error)
        return data, []
    else:
        # this shouldn't happen
        return data, []


T = TypeVar("T")


@overload
def load_json_with_default(
        data: Union[bytes, str],
        model_or_decoder: Type[T],
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = ...,
) -> Tuple[T, List[MsgspecError]]: ...


@overload
def load_json_with_default(
        data: Union[bytes, str],
        model_or_decoder: "JsonDecoder[T]",
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = ...,
) -> Tuple[T, List[MsgspecError]]: ...


def load_json_with_default(
        data: Union[bytes, str],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
) -> Tuple[Any, List[MsgspecError]]:
    """
    Decodes bytes, substituting defaults for fields that fail validation or have invalid unicode.

    Args:
        data (bytes | str): The input bytes to decode.
        model_or_decoder: The target type to decode into, or a ``msgspec.json.Decoder`` instance.
            When a decoder is passed, the model is extracted from ``decoder.type``.
        utf8_error: The error handling scheme to use for the handling of decoding errors.
            - "strict", any UnicodeDecodeError will be treated as root level error and generate a root level default.
                You may lose tons of useful data because of one unicode error.
            - "replace", replace error bytes with � (U+FFFD, '\uFFFD', \xef\xbf\xbd).
                Most data will be preserved, but you may have a U+FFFD in string.
            - "ignore", remove error bytes.
                Most data will be preserved, but you may lose the error data

    Returns:
        tuple[any, list[MsgspecError]]: (result, errors) validated result and a list of collected errors
            If validate failed and model (or any deeply nested field) can't be default constructed,
            (e.g. model has a required field) result would be NODEFAULT
            Note that it's function caller's duty to check if result is NODEFAULT,
                and decide whether to generate a default or raise error.

    Examples:
        class SimpleStruct(Struct):
            a: int
            b: str
        class NestedStruct(Struct):
            s: SimpleStruct
            c: int
        data = b'{"s": {"a": "bad-int", "b": "good"}, "c": 123}'
        result, errors = load_json_with_default(data, NestedStruct, guess_default=True)
        print(result)
        # NestedStruct(s=SimpleStruct(a=0, b='good'), c=123)
        print(errors)
        # [MsgspecError(msg='Expected `int`, got `str` - at `$.s.a`',
        # type=<ErrorType.TYPE_MISMATCH>, loc=('s', 'a'), ctx=ErrorCtx())]
    """
    # since there's no BASETYPE flag on decoder classes, subclassing is impossible,
    # so `type(obj) is Decoder` is equivalent to `isinstance(obj, Decoder)` and we can reduce some time cost
    if type(model_or_decoder) is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
    else:
        decoder = None
        model = model_or_decoder

    collected_errors = []
    for _ in range(2):
        try:
            # happy path, return directly
            if decoder is None:
                return decode_json(data, type=model), collected_errors
            else:
                return decoder.decode(data), collected_errors
        except ValidationError as e:
            try:
                raw_obj = decode_json(data)
            except DecodeError as error:
                return _handle_root_error(model, error)
            except UnicodeDecodeError as error:
                if utf8_error in ['replace', 'ignore']:
                    data, new_errors = _handle_json_unicode_repair(data, utf8_error=utf8_error)
                    collected_errors.extend(new_errors)
                    continue
                else:
                    return _handle_root_error(model, error)
            # Most errors will enter here
            raw_obj, new_errors = _handle_obj_repair(raw_obj, model, e)
            collected_errors.extend(new_errors)
            return raw_obj, collected_errors
        except DecodeError as error:
            return _handle_root_error(model, error)
        except UnicodeDecodeError as error:
            if utf8_error in ['replace', 'ignore']:
                data, new_errors = _handle_json_unicode_repair(data, utf8_error=utf8_error)
                collected_errors.extend(new_errors)
                continue
            else:
                return _handle_root_error(model, error)

    # this shouldn't happen
    result = get_default(model, return_obj=True)
    rejected = MsgspecError(
        'Input rejected: failed to solve UnicodeDecodeError',
        type=ErrorType.INPUT_REJECTED, loc=())
    collected_errors.append(rejected)
    return result, collected_errors


@overload
def load_msgpack_with_default(
        data: bytes,
        model_or_decoder: Type[T],
) -> Tuple[Any, List[MsgspecError]]: ...


@overload
def load_msgpack_with_default(
        data: bytes,
        model_or_decoder: "MsgpackDecoder[T]",
) -> Tuple[Any, List[MsgspecError]]: ...


def load_msgpack_with_default(
        data: bytes,
        model_or_decoder: Any,
) -> Tuple[Any, List[MsgspecError]]:
    """
    Decodes bytes, substituting defaults for fields that fail validation.

    Unlike the JSON variant, msgpack does not have a ``utf8_error`` parameter
    at the Python API level.  Instead, ``UnicodeDecodeError`` is handled
    transparently by:

    1. **Fast fix**: locate the exact string that failed (via
       ``fixup_msgpack_unicode_fast``) and fix it with UTF-8 ``'replace'``
       semantics.
    2. **Slow fallback**: if the fast method is ambiguous or the data still
       contains bad strings, the entire msgpack structure is walked once
       (O(N)) and every invalid string is repaired in a single pass.

    This two-level strategy prevents attackers from forcing O(N*M) behaviour
    by injecting many bad strings.

    Args:
        data (bytes): The input bytes to decode.
        model_or_decoder: The target type to decode into, or a ``msgspec.msgpack.Decoder`` instance.
            When a decoder is passed, the model is extracted from ``decoder.type``.

    Returns:
        tuple[any, list[MsgspecError]]: (result, errors) validated result and a list of collected errors

    See load_json_with_default for more info.
    """
    # since there's no BASETYPE flag on decoder classes, subclassing is impossible,
    # so `type(obj) is Decoder` is equivalent to `isinstance(obj, Decoder)` and we can reduce some time cost
    if type(model_or_decoder) is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
    else:
        decoder = None
        model = model_or_decoder

    unicode_errors: List[MsgspecError] = []

    for attempt in range(2):
        try:
            # happy path, return directly
            if decoder is None:
                return decode_msgpack(data, type=model), unicode_errors
            else:
                return decoder.decode(data), unicode_errors
        except ValidationError as e:
            try:
                raw_obj = decode_msgpack(data)
            except UnicodeDecodeError as error:
                # Fix unicode and retry on the next loop iteration
                data = _repair_msgpack_unicode(data, error, attempt)
                unicode_errors.append(parse_msgspec_error(error))
                continue
            except DecodeError as error:
                result, errors = _handle_root_error(model, error)
                errors.extend(unicode_errors)
                return result, errors
            # Most errors will enter here
            return _handle_obj_repair(raw_obj, model, e)
        except DecodeError as error:
            result, errors = _handle_root_error(model, error)
            errors.extend(unicode_errors)
            return result, errors
        except UnicodeDecodeError as error:
            data = _repair_msgpack_unicode(data, error, attempt)
            unicode_errors.append(parse_msgspec_error(error))
            continue

    # Exhausted retries – shouldn't happen after the slow walker
    result = get_default(model, return_obj=True)
    rejected = MsgspecError(
        'Input rejected: failed to solve UnicodeDecodeError',
        type=ErrorType.INPUT_REJECTED, loc=())
    return result, unicode_errors + [rejected]


def _repair_msgpack_unicode(
        data: bytes,
        error: UnicodeDecodeError,
        attempt: int,
        utf8_error: str = 'replace',
) -> bytes:
    """
    Repair msgpack bytes with invalid UTF-8.

    *Attempt 0*: use ``fixup_msgpack_unicode_fast`` to pinpoint the exact
    failing string.  If the search is ambiguous, fall back to the slow
    one-pass walker.

    *Attempt 1*: use ``fixup_msgpack_unicode_slow`` directly – this
    guarantees a single O(N) pass regardless of how many strings need
    repair, preventing attackers from causing O(N*M) re-decode cycles.
    """
    if attempt == 0:
        fixed = fixup_msgpack_unicode_fast(data, error, utf8_error=utf8_error)
        if fixed is not None:
            return fixed
    return fixup_msgpack_unicode_slow(data, utf8_error=utf8_error)
