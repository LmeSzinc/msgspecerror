import re
from typing import Any, Dict, List, Tuple, Type, TypeVar, Union, overload

from msgspec import NODEFAULT, DecodeError, EncodeError, Raw, UnsetType, ValidationError, convert
from msgspec.json import Decoder as JsonDecoder, decode as decode_json, encode as encode_json
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as decode_msgpack, encode as encode_msgpack

from . import const
from .const import ErrorType, T_json_data, T_msgpack_data, T_utf8_error
from .parse_error import ErrorInfo, parse_msgspec_error
from .parse_msgpack import fixup_msgpack_unicode_fast, fixup_msgpack_unicode_slow
from .parse_struct import (
    _default_is_accepted, _unwrap_struct_model, get_field_default, get_field_name, get_field_typehint
)
from .parse_type import get_default, is_struct_type, origin_args, unwrap_typehint
from .repair_unicode import _collect_unicode_replace


def _get_item_model(model_origin, model_args, index):
    """
    Get the typehint of one item in a sequence typehint

    Args:
        model_origin (type): Origin of the sequence typehint, e.g. list, tuple, set
        model_args (tuple | None): Args of the sequence typehint, None for bare typehints
        index (int): Index of the item

    Returns:
        Any: The item typehint, or NODEFAULT if it can't be determined
    """
    if not model_args:
        # Bare `list` / `tuple` / `set` typehint without an item type
        return NODEFAULT
    if model_origin is tuple and Ellipsis not in model_args:
        # Fixed-length tuple, the item type is per index
        try:
            return model_args[index]
        except IndexError:
            return NODEFAULT
    # list / set / frozenset / variable-length tuple, all items share one type
    return model_args[0]


def _pop_last_collection(last_collection_data, last_collection_key):
    """
    Pop the dict entry / list item that we have stepped into, when the value inside it can't be repaired

    Args:
        last_collection_data (dict | list | None): The innermost list/dict data, None if we never stepped into one
        last_collection_key (int | str): The failing key in `last_collection_data`

    Returns:
        bool: True if the entry is popped
    """
    if last_collection_data is None:
        # we never stepped into a list/dict, nothing to pop
        return False
    try:
        # the key is found in `last_collection_data` during the walk, so this should not fail
        last_collection_data.pop(last_collection_key)
    except (KeyError, IndexError):
        # the key is stale, the repair failed
        return False
    return True


def _struct_tag_matches(struct, obj):
    """
    Check whether the tag in the raw obj points to the given struct

    Args:
        struct (type): A Struct type
        obj (Any): The raw data at the current path level

    Returns:
        bool: True if the obj has no tag info or the tag points to the struct
    """
    config = _unwrap_struct_model(struct).__struct_config__
    if config.tag is None or type(obj) is not dict:
        # Nothing to check the struct against
        return True
    tag = obj.get(config.tag_field, NODEFAULT)
    return tag is NODEFAULT or tag == config.tag


def _resolve_union_member(model_args, part, obj):
    """
    Pick the union member that the error path segment refers to

    msgspec reports the error path relative to one member of a union, so the
    repair walk must use that member instead of the union itself. The segment
    kind tells which member it was: `...` / `...key` come from a dict value or
    key, an int comes from a list item, a str must be a field of a struct.

    Args:
        model_args (tuple): Args of the union typehint
        part (int | str): The current error path segment
        obj (Any): The raw data at the current path level

    Returns:
        Any: The matching union member, or NODEFAULT if no member matches
    """
    if part == '...' or part == '...key':
        # A dict value / key placeholder, msgspec allows at most one dict type in a union
        for arg in model_args:
            if arg is UnsetType:
                continue
            arg_origin, arg_args = origin_args(arg)
            if arg_origin is dict and arg_args:
                return arg
        return NODEFAULT

    if type(part) is int:
        # A list index, msgspec allows at most one array-like type in a union
        for arg in model_args:
            if arg is UnsetType:
                continue
            arg_origin, _ = origin_args(arg)
            if arg_origin in (list, tuple, set, frozenset):
                return arg
        return NODEFAULT

    # A struct field name, a tagged union may have several struct members
    structs = []
    for arg in model_args:
        if arg is UnsetType:
            continue
        arg_origin, _ = origin_args(arg)
        if not is_struct_type(arg_origin):
            continue
        try:
            get_field_name(arg, part)
        except AttributeError:
            continue
        structs.append(arg)

    if not structs:
        return NODEFAULT
    if len(structs) == 1:
        # The only member having this field, but the tag still has to agree
        return structs[0] if _struct_tag_matches(structs[0], obj) else NODEFAULT
    for struct in structs:
        # Several members have the field, the tag tells which one failed
        if _struct_tag_matches(struct, obj):
            return struct
    return NODEFAULT


def _get_model_options(model_or_decoder, kwargs):
    """
    Get the target type and the options of the equivalent convert call.

    A decoder carries the rules it validates with, they become the defaults of
    the convert call so every re-validation sees the same rules. `str_keys`
    follows the format: json keys are always strings, msgpack keys may be
    anything. An explicit keyword argument always wins over a decoder.

    Args:
        model_or_decoder (type | Decoder): Target type, or a decoder to take
            `.type` / `.strict` / `.dec_hook` / format from
        kwargs (dict): Explicit options, they override the decoder ones

    Returns:
        tuple[Any, dict]: The target type and the convert keyword arguments
    """
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        options = {
            'strict': model_or_decoder.strict,
            'dec_hook': model_or_decoder.dec_hook,
            'str_keys': True,
        }
        model = model_or_decoder.type
    elif cls is MsgpackDecoder:
        options = {
            'strict': model_or_decoder.strict,
            'dec_hook': model_or_decoder.dec_hook,
        }
        model = model_or_decoder.type
    else:
        options = {}
        model = model_or_decoder

    options.update(kwargs)
    return model, options


def _loc_leads_to_raw(model, loc):
    """
    Check whether the typehint at an error location is a `msgspec.Raw`.

    A `msgspec.Raw` field keeps the raw bytes of the payload, so a model with
    one can not be validated from an already decoded object: the decoder
    accepts any raw bytes while `convert` only accepts a `Raw` instance. An
    error reported at such a field is a false positive of the re-validation,
    it says nothing about the entry that holds the data.

    `loc` comes from an error message, so it is the short path to the failure
    rather than the full model, walking it costs a few dictionary lookups.

    Args:
        model (Any): Typehint the value was validated against
        loc (tuple): Location of the error inside that value

    Returns:
        bool: True if the typehint at `loc` is a `msgspec.Raw`
    """
    model = unwrap_typehint(model)
    origin, args = origin_args(model)
    if origin is Union:
        # Any member leading to Raw makes the failure untrustworthy
        return any(
            _loc_leads_to_raw(arg, loc)
            for arg in args
            if arg is not UnsetType
        )
    if not loc:
        return model is Raw

    part = loc[0]
    rest = loc[1:]
    if part == '...':
        if origin is not dict or not args:
            return False
        return _loc_leads_to_raw(args[1], rest)
    if part == '...key':
        # A dict key is a scalar, it can not be a Raw
        return False
    if type(part) is int:
        child_model = _get_item_model(origin, args, part)
        if child_model is NODEFAULT:
            return False
        return _loc_leads_to_raw(child_model, rest)
    if is_struct_type(origin):
        try:
            child_model = get_field_typehint(model, part)
        except AttributeError:
            # An unknown field is not a Raw
            return False
        return _loc_leads_to_raw(child_model, rest)
    return False


_MAXIMUM_RAW_PATCH = 8


def _is_raw_false_positive(error, model):
    """
    Check whether an error is the re-validation rejecting a `msgspec.Raw` field.

    A `msgspec.Raw` field takes the payload bytes, so the decoder never fails
    on it. `convert` only accepts a `Raw` instance and rejects an already
    decoded object, which is an artifact of validating twice.

    Args:
        error (ErrorInfo): Parsed error
        model (Any): Typehint the value was validated against

    Returns:
        bool: True if the error is an artifact of the re-validation
    """
    if error.type is not ErrorType.TYPE_MISMATCH:
        # A missing or unknown field at a raw field is a real error
        return False
    return _loc_leads_to_raw(model, error.loc)


def _pin_raw(value, loc, model):
    """
    Put a `msgspec.Raw` where an error location points, on a copy of the path.

    Pinning makes the raw field acceptable, so the rest of the entry can be
    judged: an entry that only looked invalid because of a raw field passes,
    and one with a real error reports it. Only the containers on the way to
    `loc` are copied, the caller's object is never modified.

    A container whose items all share one typehint is pinned in one go, so an
    entry holding a long list of raw values is settled by a single round
    instead of one round per item.

    Args:
        value (Any): The value to copy
        loc (tuple): Location to pin, taken from an error message
        model (Any): Typehint `value` was validated against

    Returns:
        Any | NODEFAULT: The pinned copy, or NODEFAULT if `loc` can't be walked
    """
    if not loc:
        return Raw(b'')

    part = loc[0]
    rest = loc[1:]
    model = unwrap_typehint(model)
    origin, args = origin_args(model)

    if part == '...':
        # A dict value failure, the failing key is unknown, pin every entry
        if type(value) is not dict:
            return NODEFAULT
        child_model = args[1] if args else NODEFAULT
        pinned = {}
        for key, item in value.items():
            item = _pin_raw(item, rest, child_model)
            if item is NODEFAULT:
                return NODEFAULT
            pinned[key] = item
        return pinned

    if type(part) is int:
        if type(value) is not list or not 0 <= part < len(value):
            return NODEFAULT
        item_model = _get_item_model(origin, args, part)
        if item_model is NODEFAULT:
            return NODEFAULT
        if origin in (list, set, frozenset) or (origin is tuple and Ellipsis in (args or ())):
            # Every item shares one typehint, pin the whole container at once
            pinned = []
            for item in value:
                item = _pin_raw(item, rest, item_model)
                if item is NODEFAULT:
                    return NODEFAULT
                pinned.append(item)
            return pinned
        item = _pin_raw(value[part], rest, item_model)
        if item is NODEFAULT:
            return NODEFAULT
        pinned = list(value)
        pinned[part] = item
        return pinned

    if type(value) is not dict:
        return NODEFAULT
    try:
        child = value[part]
    except (KeyError, TypeError):
        # The key is missing, or the segment is not usable as a key at all
        return NODEFAULT
    try:
        child_model = get_field_typehint(model, part)
    except AttributeError:
        child_model = NODEFAULT
    item = _pin_raw(child, rest, child_model)
    if item is NODEFAULT:
        return NODEFAULT
    pinned = dict(value)
    pinned[part] = item
    return pinned


def _validate_entry(value, model, kwargs):
    """
    Validate one dict entry the way the failed call did.

    A raw field is pinned and the entry is validated again, so a raw field
    ahead of the real error does not hide it.

    Args:
        value (Any): The decoded entry value
        model (Any): Typehint of the dict values
        kwargs (dict): Options of the equivalent `msgspec.convert` call

    Returns:
        tuple | None | NODEFAULT: The location of the failure inside the entry,
            None when the entry passes, NODEFAULT when the entry can't be judged
    """
    previous_loc = None
    for _ in range(_MAXIMUM_RAW_PATCH):
        try:
            convert(value, type=model, **kwargs)
        except ValidationError as e:
            error = parse_msgspec_error(e)
            if not _is_raw_false_positive(error, model):
                return error.loc
            if error.loc == previous_loc:
                # Pinning made no difference, this msgspec refuses a `Raw`
                # instance here (fixed in 0.19.0), so the entry can't be judged
                return NODEFAULT
            previous_loc = error.loc
            value = _pin_raw(value, error.loc, model)
            if value is NODEFAULT:
                return NODEFAULT
            continue
        except TypeError:
            # `model` is not a type msgspec supports
            return NODEFAULT
        return None

    # Still complaining about raw fields, the entry can't be judged
    return NODEFAULT


def _find_failing_dict_value(obj, value_model, kwargs=None, tail=None):
    """
    Find the dict entry whose value fails validation.

    Args:
        obj (dict): Raw dict to search
        value_model: Typehint of the dict values
        kwargs (dict): Extra keyword arguments for `convert`, the same ones the
            failed call used. Validation rules may change which entry fails
            (`strict=False` accepts a numeric string), so the search has to see
            exactly the rules the caller's call saw.
        tail (tuple): The rest of the caller's error location, after the
            `...` this search replaces. An entry failing elsewhere is a
            weaker match, it is only reported when nothing matches exactly.

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
    kwargs = kwargs or {}
    weaker_match = NODEFAULT
    for key, value in obj.items():
        loc = _validate_entry(value, value_model, kwargs)
        if loc is None:
            # The entry passes, a raw field was the only complaint
            continue
        if loc is NODEFAULT:
            return NODEFAULT
        if tail is None or loc == tail:
            return key
        if weaker_match is NODEFAULT:
            weaker_match = key
    return weaker_match


def _find_failing_dict_key(obj, key_model, kwargs=None):
    """
    Find the dict key that fails validation.

    Args:
        obj (dict): Raw dict to search
        key_model: Typehint of the dict keys
        kwargs (dict): Extra keyword arguments for `convert`, see
            `_find_failing_dict_value`

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
    kwargs = kwargs or {}
    # A key alone can't be validated: `str_keys` only applies to the keys of a
    # dict, so the key is wrapped back into one. `Dict[key_model, Any]` keeps
    # the key coercion rules of the model the failed call was using.
    try:
        key_dict_model = Dict[key_model, Any]
    except TypeError:
        # `key_model` is not a typehint that can be used as a dict key type
        return NODEFAULT
    for key in obj.keys():
        try:
            convert({key: None}, type=key_dict_model, **kwargs)
        except ValidationError:
            return key
        except TypeError:
            # `key_model` is not a type msgspec supports
            break
    return NODEFAULT


def _repair_once(
        raw_obj, model, error: ErrorInfo, guess_default=False, kwargs=None
) -> "tuple[Any, ErrorInfo | Type[NODEFAULT]]":
    """
    Args:
        raw_obj:
        model:
        error:
        guess_default (bool): Defaults to False.
            If False, a dict entry or list item that fails validation is deleted
                as the minimal repair.
            If True, a failing dict value or list item is replaced by a guessed default
                (e.g. 0 for int, "" for str), falling back to deletion if the type has no
                guessable default or the guessed value fails validation.
        kwargs (dict): Options of the equivalent `msgspec.convert` call, the
            same ones the failed call used, so an entry is judged with the same
            rules that rejected the input

    Returns:
        tuple[Any, ErrorInfo | _NoDefault]: (obj, error) the repaired object and fixed error.
            If repair failed, obj will be NODEFAULT.
            If this error is not consider as an error, error will be NODEFAULT and won't be collected.
    """
    # handle root error
    if not error.loc:
        value = get_default(model, guess_default=guess_default)
        return value, error

    # handle errors in deep path
    obj = raw_obj
    list_loc = [loc for loc in error.loc]
    last_index = len(list_loc) - 1
    # The data and key of the innermost list/dict level we have stepped into, the entry is
    # popped when the value inside it can't be repaired
    last_collection_data = None
    last_collection_key = None
    for i, part in enumerate(list_loc):
        is_last = i == last_index

        # A field typehint may be wrapped (Annotated, NewType, Final), msgspec decodes
        # the field as the unwrapped typehint, so the walk should use it too. A
        # parametrized generic alias (`Box[int]`) keeps its parameters, the field
        # typehints inside it depend on them.
        model = unwrap_typehint(model)

        model_origin, model_args = origin_args(model)
        if model_origin is Union:
            # The error path points into one member of the union
            model = _resolve_union_member(model_args, part, obj)
            if model is NODEFAULT:
                # The path segment doesn't match any member of the union
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
            model = unwrap_typehint(model)
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
            except TypeError:
                # model_args is None: the model isn't a parameterized dict,
                # so the error path doesn't match the model
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
            if child_model is Ellipsis:
                # `Tuple[int, ...]` and other non-dict models don't have a value type,
                # so the error path doesn't match the model
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
            failing_key = _find_failing_dict_value(
                obj, child_model, kwargs, tail=tuple(list_loc[i + 1:]))
            if failing_key is NODEFAULT:
                # Could not identify the failing key. Unrecoverable.
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
            key = failing_key

            # fix loc
            loc = error.loc
            error.loc = loc[:i] + (key,) + loc[i + 1:]
            if is_last:
                # fix obj
                if guess_default:
                    value = get_default(child_model, guess_default=True)
                    if value is not NODEFAULT:
                        obj[key] = value
                        return raw_obj, error
                # minimal repair, delete the invalid key-value pair
                try:
                    del obj[key]
                except KeyError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                return raw_obj, error
            else:
                # go deeper
                last_collection_data = obj
                last_collection_key = key
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
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
            failing_key = _find_failing_dict_key(obj, key_model, kwargs)
            if failing_key is NODEFAULT:
                # Could not identify the failing key. Unrecoverable.
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
            key = failing_key

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
            item_model = _get_item_model(model_origin, model_args, part)
            if is_last:
                # fix obj
                if guess_default and item_model is not NODEFAULT:
                    value = get_default(item_model, guess_default=True)
                    if value is not NODEFAULT:
                        try:
                            obj[part] = value
                        except IndexError:
                            # this should happen, unless raw_obj and error.loc don't match
                            return NODEFAULT, error
                        return raw_obj, error
                # minimal repair, delete the invalid item
                try:
                    obj.pop(part)
                except IndexError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                return raw_obj, error
            else:
                # go deeper
                if item_model is NODEFAULT:
                    # The item type is unknown, we don't know how to repair the deeper path
                    if not _pop_last_collection(last_collection_data, last_collection_key):
                        return NODEFAULT, error
                    return raw_obj, error
                last_collection_data = obj
                last_collection_key = part
                try:
                    obj = obj[part]
                except IndexError:
                    # this should happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                model = item_model
                continue

        # 4. Invalid object field
        if is_struct_type(model):
            if type(obj) is not dict:
                # Error path like `0` implies a list, but obj doesn't match.
                return NODEFAULT, error
            if is_last:
                # fix obj
                # try field default first. The value is validated here instead of
                # calling `get_field_default(..., validate=True)`, the raw default is
                # needed anyway to tell an unusable default from no default at all,
                # so the field is looked up only once.
                try:
                    value = get_field_default(model, part)
                except AttributeError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    if not _pop_last_collection(last_collection_data, last_collection_key):
                        return NODEFAULT, error
                    return raw_obj, error
                if value is not NODEFAULT:
                    # msgspec does not validate struct defaults, a default that
                    # msgspec rejects as an explicit value must not be written into
                    # the input. The entry is deleted instead, msgspec applies the
                    # default itself when the field is absent.
                    if _default_is_accepted(model, part, value):
                        obj[part] = value
                        return raw_obj, error
                    try:
                        del obj[part]
                    except KeyError:
                        # this shouldn't happen, unless raw_obj and error.loc don't match
                        if not _pop_last_collection(last_collection_data, last_collection_key):
                            return NODEFAULT, error
                        return raw_obj, error
                    return raw_obj, error
                # if field doesn't have a default, try to get from type
                child_model = get_field_typehint(model, part)
                # get_default returns NODEFAULT instead of a value that
                # doesn't pass validation, a useless guessed value included
                value = get_default(child_model, guess_default=guess_default)
                # still no default, delete the entry we have stepped into
                if value is NODEFAULT:
                    if not _pop_last_collection(last_collection_data, last_collection_key):
                        return NODEFAULT, error
                    return raw_obj, error
                obj[part] = value
                return raw_obj, error
            else:
                # go deeper
                try:
                    obj = obj[part]
                except (KeyError, TypeError):
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    return NODEFAULT, error
                try:
                    model = get_field_typehint(model, part)
                except AttributeError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    if not _pop_last_collection(last_collection_data, last_collection_key):
                        return NODEFAULT, error
                    return raw_obj, error
                continue

        # 5. Fallback
        # This shouldn't happen unless raw_obj, model, error don't match
        # If it really happens, we trust `model` and do the best we can
        value = get_default(model, guess_default=guess_default)
        return value, error


def _handle_obj_repair(
        raw_obj, model, error: ValidationError, guess_default=False, kwargs=None, rebuild=None
) -> "tuple[Any, list[ErrorInfo]]":
    """
    Args:
        raw_obj (Any):
        model (type): The target type to decode into.
        error (ValidationError):
        guess_default (bool): Defaults to False, see `_repair_once`
        kwargs (dict): Options of the equivalent `msgspec.convert` call, used to
            re-validate the repaired object. A decoder that coerces
            (`strict=False`) or needs a `dec_hook` would otherwise fight the
            repair: a value the decoder accepted looks invalid to `convert`.
        rebuild (callable): Builds the model instance from the repaired object
            when `convert` can not. A `msgspec.Raw` field keeps the payload
            bytes, which a decoded object no longer has, so only a typed decode
            of the re-encoded object can fill it. Defaults to None, the repair
            then reports that it can not build the instance.

    Returns:
        tuple[any, list[ErrorInfo]]:
    """
    error = parse_msgspec_error(error)
    if rebuild is None and _is_raw_false_positive(error, model):
        # The input can not be validated at all: a `msgspec.Raw` field needs the
        # payload bytes, which an in-memory object does not have. Repairing
        # would write a placeholder into the caller's object and still fail, so
        # stop before touching it.
        rejected = ErrorInfo(
            'Input rejected: a msgspec.Raw field can not be built from an in-memory object',
            type=ErrorType.INPUT_REJECTED, loc=())
        return get_default(model, guess_default=guess_default, return_obj=True), [rejected]

    collected_errors = []
    for _ in range(const.MAXIMUM_REPAIR):
        # repair once
        raw_obj, error = _repair_once(
            raw_obj, model, error, guess_default=guess_default, kwargs=kwargs)
        if error is not NODEFAULT:
            collected_errors.append(error)

        # repair failed
        if raw_obj is NODEFAULT:
            return get_default(model, guess_default=guess_default, return_obj=True), collected_errors

        # try if all repaired
        try:
            return convert(raw_obj, model, **(kwargs or {})), collected_errors
        except ValidationError as e:
            error = parse_msgspec_error(e)
            if _is_raw_false_positive(error, model):
                # The only complaint left is a `msgspec.Raw` field, which
                # `convert` never accepts from a decoded object. A typed decode
                # of the re-encoded object does fill it, when the caller can
                # provide one.
                if rebuild is not None:
                    try:
                        return rebuild(raw_obj), collected_errors
                    except (ValidationError, DecodeError, TypeError, EncodeError):
                        pass
                # Everything else is repaired, but the model instance can not be
                # built here, so the root default is the best this family can do.
                rejected = ErrorInfo(
                    'Input rejected: a msgspec.Raw field can not be built from a decoded object',
                    type=ErrorType.INPUT_REJECTED, loc=())
                collected_errors.append(rejected)
                return get_default(model, guess_default=guess_default, return_obj=True), collected_errors
            # User-code errors (__post_init__, dec_hook, etc.) are wrapped as
            # WRAPPED_ERROR. Retrying the same fix won't help — bail immediately.
            if error.type is ErrorType.WRAPPED_ERROR:
                rejected = ErrorInfo(
                    'Input rejected: validation failed on struct defaults',
                    type=ErrorType.INPUT_REJECTED, loc=())
                collected_errors.append(rejected)
                return get_default(model, guess_default=guess_default, return_obj=True), collected_errors

    # Too many iterations — possible malicious input
    rejected = ErrorInfo(
        'Input rejected: too many repair cycles',
        type=ErrorType.INPUT_REJECTED, loc=())
    collected_errors.append(rejected)
    return get_default(model, guess_default=guess_default, return_obj=True), collected_errors


def _handle_root_error(
        model: Any, error, guess_default=False
) -> "tuple[Any, list[ErrorInfo]]":
    """
    Args:
        model (type): The target type to decode into.
        error (str | Exception):
        guess_default (bool): Defaults to False, see `_repair_once`

    Returns:
        tuple[any, list[ErrorInfo]]:
    """
    collected_errors = [parse_msgspec_error(error)]
    # try if model can be default constructed
    return get_default(model, guess_default=guess_default, return_obj=True), collected_errors


# A lone surrogate is the only character that can not be encoded to utf-8.
# A compiled character class scans the text in one C level pass and allocates
# nothing per character. A str.translate() table over the surrogate range holds
# 2048 int keys, about 130KB of resident memory, and boxes every code point of
# the payload to look it up, which makes it several times slower as well.
_LONE_SURROGATE = re.compile('[\ud800-\udfff]')


def _handle_json_encode_repair(
        data,
        error,
        *,
        utf8_error: T_utf8_error,
) -> "tuple[str, list[ErrorInfo]]":
    """
    Repair a str payload that can not be encoded to utf-8.

    msgspec encodes a str payload to utf-8 before parsing it, a lone surrogate
    fails there, so the payload never reaches the parser. The surrogate is
    replaced by U+FFFD or removed per `utf8_error`, the same two characters the
    bytes repair produces, and the rest of the payload is kept.

    Args:
        data (str): The str payload that failed to encode
        error (UnicodeEncodeError): The error to report
        utf8_error (str): 'replace' to write U+FFFD, 'ignore' to drop it

    Returns:
        tuple[str, list[ErrorInfo]]: (repaired payload, the original error)
    """
    if utf8_error == 'ignore':
        data = _LONE_SURROGATE.sub('', data)
    else:
        data = _LONE_SURROGATE.sub('\ufffd', data)
    return data, [parse_msgspec_error(error)]


def _handle_json_unicode_repair(
        data: bytes,
        *,
        utf8_error: T_utf8_error,
) -> "tuple[bytes | str, list[ErrorInfo]]":
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
        data: T_json_data,
        model_or_decoder: Type[T],
        *,
        utf8_error: T_utf8_error = ...,
        guess_default: bool = ...,
) -> Tuple[T, List[ErrorInfo]]: ...


@overload
def load_json_with_default(
        data: T_json_data,
        model_or_decoder: "JsonDecoder[T]",
        *,
        utf8_error: T_utf8_error = ...,
        guess_default: bool = ...,
) -> Tuple[T, List[ErrorInfo]]: ...


def load_json_with_default(
        data: T_json_data,
        model_or_decoder: Any,
        *,
        utf8_error: T_utf8_error = 'replace',
        guess_default: bool = False,
) -> Tuple[Any, List[ErrorInfo]]:
    """
    Decodes bytes, substituting defaults for fields that fail validation or have invalid unicode.

    Args:
        data (bytes | str): The input bytes to decode.
        model_or_decoder: The target type to decode into, or a ``msgspec.json.Decoder`` instance.
            When a decoder is passed, the model is extracted from ``decoder.type``.
            A ``msgspec.msgpack.Decoder`` is rejected with TypeError.
        utf8_error: The error handling scheme to use for the handling of decoding errors.
            - "strict", any UnicodeDecodeError will be treated as root level error and generate a root level default.
                You may lose tons of useful data because of one unicode error.
            - "replace", replace error bytes with � (U+FFFD, '\uFFFD', \xef\xbf\xbd).
                Most data will be preserved, but you may have a U+FFFD in string.
            - "ignore", remove error bytes.
                Most data will be preserved, but you may lose the error data
        guess_default (bool): Defaults to False.
            False by default for safety. If True, a failing value is replaced by a guessed
            default (e.g. 0 for int, "" for str) when no explicit default is available.
            - A struct field with no default is replaced by the guessed default.
            - A dict value or list item is replaced by the guessed default, or deleted if
                the type has no guessable default or the guessed value fails validation.
            If False, a failing dict entry or list item is deleted as the minimal repair,
            and a repair that can't find a default fails, falling back to the root default.

    Returns:
        tuple[any, list[ErrorInfo]]: (result, errors) validated result and a list of collected errors
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
        # [ErrorInfo(msg='Expected `int`, got `str` - at `$.s.a`',
        # type=<ErrorType.TYPE_MISMATCH>, loc=('s', 'a'), ctx=ErrorCtx())]
    """
    # Decoder classes can't be subclassed reliably, an exact type check is
    # enough and avoids reading a `.type` attribute of an unrelated object
    cls = type(model_or_decoder)
    if cls is MsgpackDecoder:
        raise TypeError(
            'load_json_with_default() requires a msgspec.json.Decoder, '
            'got a msgspec.msgpack.Decoder'
        )
    if cls is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
    else:
        decoder = None
        model = model_or_decoder
    # A json payload always carries string keys, and the repair has to judge an
    # entry with the same rules the decoding used, or a value the decoder
    # accepted (`strict=False`, a `dec_hook`) looks invalid to `convert`
    _, options = _get_model_options(model_or_decoder, {'str_keys': True})
    # `str_keys` is a `convert` option, a decode takes only the codec ones
    decode_options = {key: value for key, value in options.items() if key != 'str_keys'}

    def rebuild(raw_obj):
        """Decode the repaired object again, so a `msgspec.Raw` field gets bytes."""
        packed = encode_json(raw_obj)
        if decoder is None:
            return decode_json(packed, type=model, **decode_options)
        return decoder.decode(packed)

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
                return _handle_root_error(model, error, guess_default=guess_default)
            except UnicodeDecodeError as error:
                if utf8_error in ['replace', 'ignore']:
                    data, new_errors = _handle_json_unicode_repair(data, utf8_error=utf8_error)
                    collected_errors.extend(new_errors)
                    continue
                else:
                    return _handle_root_error(model, error, guess_default=guess_default)
            # Most errors will enter here
            raw_obj, new_errors = _handle_obj_repair(
                raw_obj, model, e, guess_default=guess_default, kwargs=options, rebuild=rebuild)
            collected_errors.extend(new_errors)
            return raw_obj, collected_errors
        except DecodeError as error:
            return _handle_root_error(model, error, guess_default=guess_default)
        except UnicodeDecodeError as error:
            if utf8_error in ['replace', 'ignore']:
                data, new_errors = _handle_json_unicode_repair(data, utf8_error=utf8_error)
                collected_errors.extend(new_errors)
                continue
            else:
                return _handle_root_error(model, error, guess_default=guess_default)
        except UnicodeEncodeError as error:
            # Only a str payload reaches here: msgspec encodes it to utf-8 first,
            # a lone surrogate fails before the parser runs
            if utf8_error in ['replace', 'ignore']:
                data, new_errors = _handle_json_encode_repair(data, error, utf8_error=utf8_error)
                collected_errors.extend(new_errors)
                continue
            else:
                return _handle_root_error(model, error, guess_default=guess_default)

    # this shouldn't happen
    result = get_default(model, guess_default=guess_default, return_obj=True)
    rejected = ErrorInfo(
        'Input rejected: failed to solve UnicodeEncodeError',
        type=ErrorType.INPUT_REJECTED, loc=())
    collected_errors.append(rejected)
    return result, collected_errors


@overload
def load_msgpack_with_default(
        data: T_msgpack_data,
        model_or_decoder: Type[T],
        *,
        utf8_error: T_utf8_error = ...,
        guess_default: bool = ...,
) -> Tuple[T, List[ErrorInfo]]: ...


@overload
def load_msgpack_with_default(
        data: T_msgpack_data,
        model_or_decoder: "MsgpackDecoder[T]",
        *,
        utf8_error: T_utf8_error = ...,
        guess_default: bool = ...,
) -> Tuple[T, List[ErrorInfo]]: ...


def load_msgpack_with_default(
        data: T_msgpack_data,
        model_or_decoder: Any,
        *,
        utf8_error: T_utf8_error = 'replace',
        guess_default: bool = False,
) -> Tuple[Any, List[ErrorInfo]]:
    """
    Decodes bytes, substituting defaults for fields that fail validation or have invalid unicode.

    When a ``UnicodeDecodeError`` is encountered, a two-level repair strategy
    is used:

    1. **Fast fix** — locate the exact string that failed (via
       ``fixup_msgpack_unicode_fast``) and fix it with the requested
       *utf8_error* handler.
    2. **Slow fallback** — if the fast method is ambiguous or the data still
       contains bad strings, the entire msgpack structure is walked once
       (O(N)) and every invalid string is repaired in a single pass.

    This two-level strategy prevents attackers from forcing O(N*M) behaviour
    by injecting many bad strings.

    Args:
        data (bytes): The input bytes to decode.
        model_or_decoder: The target type to decode into, or a ``msgspec.msgpack.Decoder`` instance.
            When a decoder is passed, the model is extracted from ``decoder.type``.
            A ``msgspec.json.Decoder`` is rejected with TypeError.
        utf8_error: The error handling scheme for invalid UTF-8 in string payloads.
            - ``"strict"`` — any ``UnicodeDecodeError`` is treated as a root-level
              error and produces a root-level default.
            - ``"replace"`` (default) — replace error bytes with U+FFFD.
              Most data is preserved, but strings may contain the replacement
              character.
            - ``"ignore"`` — remove error bytes entirely.
        guess_default (bool): Defaults to False, see load_json_with_default for more info.

    Returns:
        tuple[any, list[ErrorInfo]]: (result, errors) validated result and a list of collected errors
            If validate failed and model (or any deeply nested field) can't be default constructed,
            (e.g. model has a required field) result would be NODEFAULT
            Note that it's function caller's duty to check if result is NODEFAULT,
                and decide whether to generate a default or raise error.

    See load_json_with_default for more info.
    """
    # Decoder classes can't be subclassed reliably, an exact type check is
    # enough and avoids reading a `.type` attribute of an unrelated object
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        raise TypeError(
            'load_msgpack_with_default() requires a msgspec.msgpack.Decoder, '
            'got a msgspec.json.Decoder'
        )
    if cls is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
    else:
        decoder = None
        model = model_or_decoder
    # Msgpack keys are not restricted to strings, so `str_keys` stays False
    _, options = _get_model_options(model_or_decoder, {})

    def rebuild(raw_obj):
        """Decode the repaired object again, so a `msgspec.Raw` field gets bytes."""
        packed = encode_msgpack(raw_obj)
        if decoder is None:
            return decode_msgpack(packed, type=model, **options)
        return decoder.decode(packed)

    unicode_errors: List[ErrorInfo] = []

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
                if utf8_error in ('replace', 'ignore'):
                    data = _repair_msgpack_unicode(data, error, attempt, utf8_error=utf8_error)
                    unicode_errors.append(parse_msgspec_error(error))
                    continue
                # strict or unknown → root error
                result, errors = _handle_root_error(model, error, guess_default=guess_default)
                errors.extend(unicode_errors)
                return result, errors
            except DecodeError as error:
                result, errors = _handle_root_error(model, error, guess_default=guess_default)
                errors.extend(unicode_errors)
                return result, errors
            # Most errors will enter here
            return _handle_obj_repair(
                raw_obj, model, e, guess_default=guess_default, kwargs=options, rebuild=rebuild)
        except DecodeError as error:
            result, errors = _handle_root_error(model, error, guess_default=guess_default)
            errors.extend(unicode_errors)
            return result, errors
        except UnicodeDecodeError as error:
            if utf8_error in ('replace', 'ignore'):
                data = _repair_msgpack_unicode(data, error, attempt, utf8_error=utf8_error)
                unicode_errors.append(parse_msgspec_error(error))
                continue
            # strict or unknown → root error
            result, errors = _handle_root_error(model, error, guess_default=guess_default)
            errors.extend(unicode_errors)
            return result, errors

    # Exhausted retries – shouldn't happen after the slow walker
    result = get_default(model, guess_default=guess_default, return_obj=True)
    rejected = ErrorInfo(
        'Input rejected: failed to solve UnicodeDecodeError',
        type=ErrorType.INPUT_REJECTED, loc=())
    return result, unicode_errors + [rejected]


def _repair_msgpack_unicode(
        data: bytes,
        error: UnicodeDecodeError,
        attempt: int,
        utf8_error: T_utf8_error = 'replace',
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


@overload
def convert_with_default(
        data: Any,
        model_or_decoder: Type[T],
        *,
        guess_default: bool = ...,
        **kwargs,
) -> Tuple[T, List[ErrorInfo]]: ...


@overload
def convert_with_default(
        data: Any,
        model_or_decoder: "Union[JsonDecoder[T], MsgpackDecoder[T]]",
        *,
        guess_default: bool = ...,
        **kwargs,
) -> Tuple[T, List[ErrorInfo]]: ...


def convert_with_default(data, model_or_decoder, *, guess_default=False, **kwargs):
    """
    Convert an in-memory object, substituting defaults for fields that fail validation.

    The repair is applied to `data` in place, the same way
    load_json_with_default() repairs the object it just decoded.
    A caller that still needs the original value must copy it first.

    Args:
        data (Any): The raw value to convert. It is modified in place.
        model_or_decoder: The target type to convert into, or a msgspec decoder
            whose `type` is used
        guess_default (bool): Defaults to False, see load_json_with_default
        **kwargs: Options of the equivalent `msgspec.convert` call (`strict`,
            `from_attributes`, `dec_hook`, `builtin_types`, `str_keys`), they
            override the decoder ones. The repair re-validates with the same
            rules, so a value the caller's call accepted is not repaired away.

    Returns:
        tuple[Any, List[ErrorInfo]]: (result, errors) validated result and a list
            of collected errors. See load_json_with_default for the details.
    """
    model, options = _get_model_options(model_or_decoder, kwargs)
    try:
        return convert(data, type=model, **options), []
    except ValidationError as e:
        return _handle_obj_repair(
            data, model, e, guess_default=guess_default, kwargs=options)
