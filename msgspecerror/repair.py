import re
from typing import Any, Dict, List, Tuple, Type, TypeVar, Union, overload

from msgspec import NODEFAULT, DecodeError, EncodeError, UnsetType, ValidationError, convert
from msgspec.json import Decoder as JsonDecoder, decode as decode_json, encode as encode_json
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as decode_msgpack, encode as encode_msgpack

from . import const
from .const import ErrorType, T_json_data, T_msgpack_data, T_utf8_error
from .locate import (
    _DECODE_KWARGS, _find_failing_dict_key, _find_failing_dict_value, _get_index_model, _get_item_model,
    _is_mapping, _is_writable_mapping, _is_writable_sequence, _pin_raw
)
from .parse_decoder import _type_error, get_decoder_model
from .parse_error import ErrorInfo, parse_msgspec_error
from .parse_model import get_field_default, get_field_name, get_field_typehint, is_arraylike
from .parse_msgpack import fixup_msgpack_unicode_fast, fixup_msgpack_unicode_slow
from .parse_type import default_is_accepted, get_default, is_struct_like, origin_args, unwrap_model, unwrap_typehint
from .repair_unicode import _collect_unicode_replace


class _Undo:
    """
    Puts back the containers a repair wrote into, when the repair gives up.

    `convert_with_default()` repairs the object the caller passed in, so a
    repair that ends up with a default result must not leave a half repaired
    object behind. Copying the whole tree up front would cost a full pass on
    every call that has an error, so each container is copied once, right
    before its first write. A repair only replaces or removes an entry, it
    never writes through an entry, so a shallow copy restores the tree.

    `None` stands for "no journal": the object is freshly decoded and nobody
    else holds it, so a give-up can simply drop it.
    """

    __slots__ = ('_saved',)

    def __init__(self):
        # Identity to (container, items), so a container is copied once. The
        # entry holds the container, so its id can not be reused while saved.
        self._saved = {}

    def touch(self, container):
        """
        Copy a container unless it has been copied already.

        Args:
            container (dict | list): The container that is about to be written
        """
        key = id(container)
        if key in self._saved:
            return
        if isinstance(container, dict):
            self._saved[key] = (container, list(container.items()))
        else:
            self._saved[key] = (container, list(container))

    def restore(self):
        """Put every touched container back to its original content and order."""
        for container, items in self._saved.values():
            container.clear()
            if isinstance(container, dict):
                container.update(items)
            else:
                container.extend(items)
        self._saved.clear()


def _pop_last_collection(last_collection_data, last_collection_key, undo=None):
    """
    Pop the dict entry / list item that we have stepped into, when the value inside it can't be repaired

    Args:
        last_collection_data (dict | list | None): The innermost list/dict data, None if we never stepped into one
        last_collection_key (int | str): The failing key in `last_collection_data`
        undo (_Undo | None): The journal of the caller's object, see `_Undo`

    Returns:
        bool: True if the entry is popped
    """
    if last_collection_data is None:
        # we never stepped into a list/dict, nothing to pop
        return False
    if undo is not None:
        undo.touch(last_collection_data)
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
    config = getattr(unwrap_model(struct), '__struct_config__', None)
    if config is None or config.tag is None or not _is_mapping(obj):
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
        tuple[Any, str] | NODEFAULT: The matching union member and its kind, as
            `is_struct_like()` names it, or NODEFAULT if no member matches. The
            kind is handed back because naming it is what found the member, and
            the caller would otherwise look it up again.
    """
    if part == '...' or part == '...key':
        # A dict value / key placeholder, msgspec allows at most one dict type in a union
        for arg in model_args:
            if arg is UnsetType:
                continue
            arg_origin, arg_args = origin_args(arg)
            if arg_origin is dict and arg_args:
                return arg, ''
        return NODEFAULT

    if type(part) is int:
        # A list index, msgspec allows at most one array type in a union
        for arg in model_args:
            if arg is UnsetType:
                continue
            arg_origin, _ = origin_args(arg)
            arg_kind = is_struct_like(arg_origin, unwrap=False)
            if arg_origin in (list, tuple, set, frozenset) \
                    or is_arraylike(arg_origin, known_struct_type=arg_kind, unwrap=False):
                return arg, arg_kind
        return NODEFAULT

    # A field name, a tagged union may have several object members
    models = []
    for arg in model_args:
        if arg is UnsetType:
            continue
        arg_origin, _ = origin_args(arg)
        arg_kind = is_struct_like(arg_origin, unwrap=False)
        if arg_kind == 'struct':
            # Only the field name matters here, and the name check does not have
            # to resolve the annotation the way a typehint read does
            try:
                get_field_name(arg, part, known_struct_type=arg_kind)
            except AttributeError:
                continue
        elif arg_kind:
            try:
                get_field_typehint(arg, part, known_struct_type=arg_kind)
            except AttributeError:
                continue
        else:
            continue
        models.append((arg, arg_kind))

    if not models:
        return NODEFAULT
    if len(models) == 1:
        # The only member having this field, but the tag still has to agree
        return models[0] if _struct_tag_matches(models[0][0], obj) else NODEFAULT
    for model, arg_kind in models:
        # Several members have the field, the tag tells which one failed
        if _struct_tag_matches(model, obj):
            return model, arg_kind
    return NODEFAULT


def _walkable_error(error):
    """
    Return an error whose location the repair walk can read.

    A location is a tuple of segments and the walk reads every one of them as a
    key. A `str`, a `dict` or a scalar can not be walked at all, and a `bool`
    would be read as the index `1`: a hand-built `ErrorInfo` or a corrupted
    parser can hand one over, and the error is then reported as the root one
    instead of raising mid-repair. Only the failure path calls this, and the
    caller's error is never rewritten: a fresh copy is returned when the
    location has to change.

    Args:
        error (ErrorInfo): The error to judge

    Returns:
        ErrorInfo: `error` itself when its location is walkable, a copy with an
            empty location otherwise
    """
    loc = error.loc
    loc_type = type(loc)
    if loc_type is list:
        # A list is the same path, only the type does not promise a tuple
        return ErrorInfo(msg=error.msg, type=error.type, loc=tuple(loc), ctx=error.ctx)
    if loc_type is not tuple:
        return ErrorInfo(msg=error.msg, type=error.type, ctx=error.ctx)
    for part in loc:
        part_type = type(part)
        # `part_type is int` is False for bool, which is not an index
        if part_type is not int and part_type is not str:
            return ErrorInfo(msg=error.msg, type=error.type, ctx=error.ctx)
    return error


def _repair_once(
        raw_obj, model, error: ErrorInfo, guess_default=False, kwargs=None, undo=None
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
        undo (_Undo | None): The journal of the caller's object, None when the
            object is freshly decoded and nobody else holds it
    Returns:
        tuple[Any, ErrorInfo | _NoDefault]: (obj, error) the repaired object and fixed error.
            If repair failed, obj will be NODEFAULT.
            If this error is not consider as an error, error will be NODEFAULT and won't be collected.
    """
    # A location is a tuple of segments, and the walk below reads every one of
    # them as a key: one that is not is reported as the root error instead of
    # raising mid-repair
    error = _walkable_error(error)

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
            # The error path points into one member of the union, the kind comes
            # back with it: naming it is what found the member
            member = _resolve_union_member(model_args, part, obj)
            if member is NODEFAULT:
                # The path segment doesn't match any member of the union
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                    return NODEFAULT, error
                return raw_obj, error
            model, model_kind = member
            model = unwrap_typehint(model)
            model_origin, model_args = origin_args(model)
        else:
            model_kind = None

        # 1. Invalid dict value
        # msgspec does not tell which key is invalid, just giving placeholder '...', we need to find the exact key
        if part == '...':
            if not _is_writable_mapping(obj):
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
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                    return NODEFAULT, error
                return raw_obj, error
            if child_model is Ellipsis:
                # `Tuple[int, ...]` and other non-dict models don't have a value type,
                # so the error path doesn't match the model
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                    return NODEFAULT, error
                return raw_obj, error
            failing_key = _find_failing_dict_value(obj, child_model, kwargs)
            if failing_key is NODEFAULT:
                # Could not identify the failing key. Unrecoverable.
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
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
                        if undo is not None:
                            undo.touch(obj)
                        obj[key] = value
                        return raw_obj, error
                # minimal repair, delete the invalid key-value pair
                if undo is not None:
                    undo.touch(obj)
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
            if not _is_writable_mapping(obj):
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
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                    return NODEFAULT, error
                return raw_obj, error
            failing_key = _find_failing_dict_key(obj, key_model, kwargs)
            if failing_key is NODEFAULT:
                # Could not identify the failing key. Unrecoverable.
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
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
                        if undo is not None:
                            undo.touch(obj)
                        obj[temp_key] = value
                        # we don't consider this an error, it's not a user input error
                        return raw_obj, NODEFAULT
                # failed to convert, key is removed as a fix
                return raw_obj, error
            else:
                # this shouldn't happen because '...key' should be the last key,
                # since json/msgpack key can't be objects.
                return NODEFAULT, error

        # The placeholders above leave the loop on their own, so only a segment
        # that names a field or a position reaches this point: it is the one
        # that needs the kind, and it is named once for the branches below
        if model_kind is None:
            model_kind = is_struct_like(model_origin, unwrap=False)

        # 3. Invalid item in list
        if type(part) is int:
            if part < 0:
                # msgspec reports a position, never a negative index: treating
                # it as Python's last item would repair the wrong entry
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                    return NODEFAULT, error
                return raw_obj, error
            if not _is_writable_sequence(obj):
                # Error path like `0` implies a list, but obj doesn't match.
                return NODEFAULT, error
            if is_arraylike(model_origin, known_struct_type=model_kind, unwrap=False):
                # An `array_like` struct and a NamedTuple are decoded from an
                # array, the index is the field order. An item can not be deleted
                # here: every later field would shift into the wrong place. A
                # default keeps the shape, and without one the object that holds
                # it is dropped.
                if is_last:
                    try:
                        value = get_field_default(
                            model, part, validate=True, guess_default=guess_default, known_struct_type=model_kind)
                    except AttributeError:
                        value = NODEFAULT
                    if value is not NODEFAULT:
                        if undo is not None:
                            undo.touch(obj)
                        try:
                            obj[part] = value
                        except IndexError:
                            # The index is past the end of the data: the loc
                            # does not match the array, nothing can be written
                            return NODEFAULT, error
                        return raw_obj, error
                    if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                        return NODEFAULT, error
                    return raw_obj, error

                # The error is inside the field at this position, so the walk
                # descends into the value the index addresses. The field type is
                # looked up by the same position. `last_collection_*` is left
                # alone on purpose: the array itself can not lose an item, so a
                # value that can not be repaired inside it drops the object that
                # holds the array, not one of its fields.
                item_model = _get_index_model(
                    model_origin, model_args, part, known_struct_type=model_kind)
                if item_model is not NODEFAULT:
                    try:
                        obj = obj[part]
                    except IndexError:
                        # this should happen, unless raw_obj and error.loc don't match
                        item_model = NODEFAULT
                if item_model is NODEFAULT:
                    if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                        return NODEFAULT, error
                    return raw_obj, error
                model = item_model
                continue
            item_model = _get_item_model(model_origin, model_args, part)
            if is_last:
                # fix obj
                if guess_default and item_model is not NODEFAULT:
                    value = get_default(item_model, guess_default=True)
                    if value is not NODEFAULT:
                        if undo is not None:
                            undo.touch(obj)
                        try:
                            obj[part] = value
                        except IndexError:
                            # this should happen, unless raw_obj and error.loc don't match
                            return NODEFAULT, error
                        return raw_obj, error
                # minimal repair, delete the invalid item
                if undo is not None:
                    undo.touch(obj)
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
                    if not _pop_last_collection(last_collection_data, last_collection_key, undo):
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

        # 4. Invalid field of a model
        if model_kind:
            if is_arraylike(model_origin, known_struct_type=model_kind, unwrap=False):
                # An `array_like` struct and a NamedTuple are decoded from an
                # array, so a name addresses no field of them: writing one would
                # build a document the model can not read back. Branch 3 above
                # has already handled the position form.
                if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                    return NODEFAULT, error
                return raw_obj, error
            # The data tells how the model is decoded: an object model is
            # addressed by field name, an array model by position, which branch
            # 3 above has already handled
            if not _is_writable_mapping(obj):
                # A field name implies an object model, but obj doesn't match.
                return NODEFAULT, error
            if is_last:
                # fix obj
                # try field default first. The value is validated here instead of
                # calling `get_field_default(..., validate=True)`, the raw default is
                # needed anyway to tell an unusable default from no default at all,
                # so the field is looked up only once.
                try:
                    value = get_field_default(model, part, known_struct_type=model_kind)
                except AttributeError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                        return NODEFAULT, error
                    return raw_obj, error
                if value is not NODEFAULT:
                    # msgspec does not validate struct defaults, a default that
                    # msgspec rejects as an explicit value must not be written into
                    # the input. The entry is deleted instead, msgspec applies the
                    # default itself when the field is absent.
                    if default_is_accepted(
                            get_field_typehint(model, part, known_struct_type=model_kind), value):
                        if undo is not None:
                            undo.touch(obj)
                        obj[part] = value
                        return raw_obj, error
                    if undo is not None:
                        undo.touch(obj)
                    try:
                        del obj[part]
                    except KeyError:
                        # this shouldn't happen, unless raw_obj and error.loc don't match
                        if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                            return NODEFAULT, error
                        return raw_obj, error
                    return raw_obj, error
                # if field doesn't have a default, try to get from type
                child_model = get_field_typehint(model, part, known_struct_type=model_kind)
                # get_default returns NODEFAULT instead of a value that
                # doesn't pass validation, a useless guessed value included
                value = get_default(child_model, guess_default=guess_default)
                # still no default, delete the entry we have stepped into
                if value is NODEFAULT:
                    if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                        return NODEFAULT, error
                    return raw_obj, error
                if undo is not None:
                    undo.touch(obj)
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
                    model = get_field_typehint(model, part, known_struct_type=model_kind)
                except AttributeError:
                    # this shouldn't happen, unless raw_obj and error.loc don't match
                    if not _pop_last_collection(last_collection_data, last_collection_key, undo):
                        return NODEFAULT, error
                    return raw_obj, error
                continue

        # 5. Fallback
        # This shouldn't happen unless raw_obj, model, error don't match
        # If it really happens, we trust `model` and do the best we can
        value = get_default(model, guess_default=guess_default)
        return value, error


# Codec of a repair family, the repair engine needs it to put payload bytes back
# into a `msgspec.Raw` field. The module already imports both codecs, so the
# loaders only pass a name instead of a closure.
ENCODERS = {'json': encode_json, 'msgpack': encode_msgpack}
DECODERS = {'json': decode_json, 'msgpack': decode_msgpack}


def _rebuild(raw_obj, model, codec, decoder, kwargs):
    """
    Decode the repaired object again, so a `msgspec.Raw` field gets its bytes.

    Only used on msgspec before 0.19.0, whose `convert` refuses a `Raw`
    instance, so the object alone can never become a model with a raw field.
    The `convert` family has no payload either, so it names the codec the value
    is encoded in.

    Args:
        raw_obj (Any): The repaired object
        model (Any): The target type
        codec (str): Codec the object is encoded in, 'json' or 'msgpack'
        decoder (Decoder | None): The caller's decoder, its options are kept
        kwargs (dict): Options of the equivalent `convert` call

    Returns:
        Any: The model instance built from `raw_obj`
    """
    packed = ENCODERS[codec](raw_obj)
    if decoder is not None:
        return decoder.decode(packed)
    # Only the options a decode takes are forwarded, `convert` has more
    options = {key: kwargs[key] for key in _DECODE_KWARGS if key in kwargs}
    return DECODERS[codec](packed, type=model, **options)


def _give_up(model, guess_default, errors, undo):
    """
    Answer with a default object, after putting back what a repair has written.

    Args:
        model (type): The target type to decode into.
        guess_default (bool): Defaults to False, see `_repair_once`
        errors (list[ErrorInfo]): The errors collected so far
        undo (_Undo | None): The journal of the caller's object, None when the
            object is freshly decoded and nobody else holds it

    Returns:
        tuple[Any, list[ErrorInfo]]: The default object, or NODEFAULT, and the errors
    """
    if undo is not None:
        # Half repaired input is worse than the input the caller passed: a
        # retry would start from a different object than the first call did.
        undo.restore()
    return get_default(model, guess_default=guess_default, return_obj=True), errors


def _handle_obj_repair(
        raw_obj, model, error: ValidationError, guess_default=False, kwargs=None, codec=None, decoder=None,
        raw_spans=None, undo=None
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
        codec (str): 'json' or 'msgpack' for a repair family that holds a
            payload. Defaults to None, an in-memory object has no payload. A
            `msgspec.Raw` field still needs bytes there, so the value is encoded
            as msgpack to fill it: that is the one builtin format which carries
            every type a decoded payload can hold.
        decoder (Decoder): The caller's decoder, used to keep its options when
            the object has to be decoded again. Defaults to None.
        raw_spans (dict | None): Map of repaired item bytes to the original
            ones, from the unicode repair of the payload. Defaults to None,
            nothing is restored.
        undo (_Undo | None): The journal of the object the caller passed in, so
            a repair that gives up leaves that object as it was. Defaults to
            None, the object belongs to the library and is discarded anyway.

    Returns:
        tuple[any, list[ErrorInfo]]:
    """
    error = parse_msgspec_error(error)
    # `_pin_raw()` below indexes the segments and `_repair_once()` iterates them,
    # so only a walkable location may go further
    error = _walkable_error(error)
    # `codec is None` means the failed call was `convert`, the only direction
    # that rejects an already decoded object at a `msgspec.Raw` field. A load
    # family fails inside the typed decoder instead, which takes the payload
    # bytes at such a field, so an error it reports there is a real one.
    from_convert = codec is None
    collected_errors = []
    rejected_msg = 'Input rejected: too many repair cycles'
    for _ in range(const.MAXIMUM_REPAIR):
        if not const.MSGSPEC_0_19_PLUS and from_convert and error.ctx.may_raw_rejection:
            # msgspec before 0.19.0 refuses a `Raw` instance in `convert`, so the
            # field can not be filled in place. The re-encoded object is decoded
            # by the typed decoder instead, which does accept raw bytes. No
            # payload means no bytes for a raw field, the object is re-encoded as
            # msgpack, which is what a caller would do by hand.
            refill_codec = codec if codec is not None else 'msgpack'
            try:
                return _rebuild(raw_obj, model, refill_codec, decoder, kwargs), collected_errors
            except ValidationError as rebuild_error:
                # The raw field was ahead of another error. The re-encoded object
                # has the same shape, so its location is usable and the repair
                # goes on instead of giving up on the entry.
                error = parse_msgspec_error(rebuild_error)
                from_convert = False
                # The decoder rejects a raw field as well, so this msgspec can
                # not build the model from the object either
                if error.type is ErrorType.WRAPPED_ERROR or error.ctx.may_raw_rejection:
                    rejected_msg = const.MSG_RAW_FROM_DECODED
                    break
            except (DecodeError, TypeError, EncodeError, RecursionError):
                rejected_msg = const.MSG_RAW_FROM_DECODED
                break
            continue

        if from_convert and error.ctx.may_raw_rejection:
            # The typehint at the error is a `msgspec.Raw`, and `convert` only
            # takes a `Raw` instance there. The value is encoded back into that
            # spot, so the model can be built from this object instead of a
            # re-decode. This is not a data error: the field is restored, so
            # nothing is collected. The encoder is taken here, no other branch
            # of the loop encodes anything.
            refill_codec = codec if codec is not None else 'msgpack'
            filled = _pin_raw(raw_obj, error.loc, model, ENCODERS[refill_codec], raw_spans)
            if filled is NODEFAULT:
                # The path can not be walked, or the value there is one msgpack
                # can not carry, so the field can not be restored
                if codec is None:
                    rejected = ErrorInfo(const.MSG_RAW_FROM_MEMORY, type=ErrorType.INPUT_REJECTED, loc=())
                    return _give_up(model, guess_default, [rejected], undo)
                collected_errors.append(error)
                return _give_up(model, guess_default, collected_errors, undo)
            raw_obj = filled
        else:
            # repair once
            raw_obj, error = _repair_once(
                raw_obj, model, error, guess_default=guess_default, kwargs=kwargs, undo=undo)
            if error is not NODEFAULT:
                collected_errors.append(error)

            # repair failed
            if raw_obj is NODEFAULT:
                return _give_up(model, guess_default, collected_errors, undo)

        # try if all repaired
        try:
            return convert(raw_obj, model, **(kwargs or {})), collected_errors
        except ValidationError as e:
            error = parse_msgspec_error(e)
            from_convert = True
            if error.ctx.may_raw_rejection:
                # Another raw field, the loop fills it in the next round
                continue
            # User-code errors (__post_init__, dec_hook, etc.) are wrapped as
            # WRAPPED_ERROR. Retrying the same fix won't help — bail immediately.
            if error.type is ErrorType.WRAPPED_ERROR:
                rejected = ErrorInfo(
                    'Input rejected: validation failed on struct defaults',
                    type=ErrorType.INPUT_REJECTED, loc=())
                collected_errors.append(rejected)
                return _give_up(model, guess_default, collected_errors, undo)

    rejected = ErrorInfo(rejected_msg, type=ErrorType.INPUT_REJECTED, loc=())
    collected_errors.append(rejected)
    return _give_up(model, guess_default, collected_errors, undo)


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
        data,
        *,
        utf8_error: T_utf8_error,
) -> "tuple[bytes | str, list[ErrorInfo]]":
    """
    Helper function to repair json data that has unicode error

    Args:
        data (bytes | bytearray | memoryview): The payload that failed to decode
        utf8_error (str): 'replace' to write U+FFFD, 'ignore' to drop it

    Returns:
        tuple[bytes | str, list[ErrorInfo]]: The repaired payload and the errors
    """
    if type(data) is not bytes:
        # A `memoryview` has no `.decode()`. The payload only reaches here on the
        # repair path, so one copy is cheap and every branch below then works on
        # the same type.
        data = bytes(data)
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
        model_or_decoder: The target type to decode into, or a ``msgspec.json.Decoder`` instance,
            which decodes the payload as it is. A ``msgspec.msgpack.Decoder`` is rejected with TypeError.
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
    # The class check only picks the decoder and the model: a payload that
    # decodes is never repaired, so the `convert` options are resolved in the
    # one branch that reads them. The repair does have to judge an entry with
    # the rules the decoding used, or a value the decoder accepted
    # (`strict=False`, a `dec_hook`) looks invalid to `convert`.
    cls = type(model_or_decoder)
    if cls is JsonDecoder:
        decoder = model_or_decoder
        model = decoder.type
    elif cls is MsgpackDecoder:
        raise TypeError(_type_error('load_json_with_default', 'json', 'msgpack'))
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
            except ValidationError:
                # The payload parses: an untyped decode only fails here on a
                # value check, and the typed decode never looked at that value
                # because it sits in a `msgspec.Raw` field. That error is
                # collateral, the typed one is the real one, so report it.
                return _handle_root_error(model, e, guess_default=guess_default)
            except DecodeError as error:
                # Malformed json, which is the real error even when the typed
                # decode stopped earlier on a type mismatch
                return _handle_root_error(model, error, guess_default=guess_default)
            except UnicodeDecodeError as error:
                if utf8_error in ['replace', 'ignore']:
                    data, new_errors = _handle_json_unicode_repair(data, utf8_error=utf8_error)
                    collected_errors.extend(new_errors)
                    continue
                else:
                    return _handle_root_error(model, error, guess_default=guess_default)
            # Most errors will enter here, and only this path judges the object:
            # the repaired one is validated with the rules the decode used, or a
            # value the decoder accepted (`strict=False`, a `dec_hook`) looks
            # invalid. The paths above return or retry without reading them.
            _, _, options = get_decoder_model(
                model_or_decoder, JsonDecoder, {'str_keys': True}, 'load_json_with_default')
            raw_obj, new_errors = _handle_obj_repair(
                raw_obj, model, e, guess_default=guess_default, kwargs=options, codec='json', decoder=decoder)
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
        model_or_decoder: The target type to decode into, or a ``msgspec.msgpack.Decoder`` instance,
            which decodes the payload as it is. A ``msgspec.json.Decoder`` is rejected with TypeError.
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
    # The class check only picks the decoder: a payload that decodes is never
    # repaired, and the repair below works from the failure it gets. `model` is
    # bound for both kinds of argument, a decoder decodes the payload itself and
    # only a typehint reaches the `type=` call.
    cls = type(model_or_decoder)
    if cls is MsgpackDecoder:
        decoder = model_or_decoder
        model = decoder.type
    elif cls is JsonDecoder:
        raise TypeError(_type_error('load_msgpack_with_default', 'msgpack', 'json'))
    else:
        decoder = None
        model = model_or_decoder

    try:
        if decoder is None:
            return decode_msgpack(data, type=model), []
        return decoder.decode(data), []
    except (ValidationError, DecodeError, UnicodeDecodeError) as failure:
        # Handed to the repair below, which works from this failure: a payload
        # that decodes as it is never allocates any of it, and the bytes are not
        # decoded once more just to raise the same error again
        error = failure

    unicode_errors: List[ErrorInfo] = []
    # The bytes every unicode rewrite replaced, so a `msgspec.Raw` field can be
    # filled with what the payload really had instead of the repaired string
    raw_spans = {}

    # One round per repair, and one more for what the repaired payload fails on:
    # round 0 tries the fast fixup (one string), round 1 runs the slow walk (every
    # remaining string), and the `ValidationError` that the repaired payload then
    # raises still needs a round of its own. With two rounds that last one ran out
    # of budget and a payload with two different invalid strings was answered
    # with a default object instead of the repaired one.
    for attempt in range(_MAX_UNICODE_ROUNDS):
        if isinstance(error, UnicodeDecodeError):
            # The payload holds bytes that are not utf-8
            if utf8_error not in ('replace', 'ignore'):
                # strict or unknown → root error
                result, errors = _handle_root_error(model, error, guess_default=guess_default)
                errors.extend(unicode_errors)
                return result, errors
            data = _repair_msgpack_unicode(
                data, error, attempt, utf8_error=utf8_error, spans=raw_spans)
            unicode_errors.append(parse_msgspec_error(error))
        elif isinstance(error, ValidationError):
            try:
                raw_obj = decode_msgpack(data)
            except UnicodeDecodeError as nested:
                if utf8_error not in ('replace', 'ignore'):
                    # strict or unknown → root error
                    result, errors = _handle_root_error(model, nested, guess_default=guess_default)
                    errors.extend(unicode_errors)
                    return result, errors
                data = _repair_msgpack_unicode(
                    data, nested, attempt, utf8_error=utf8_error, spans=raw_spans)
                unicode_errors.append(parse_msgspec_error(nested))
            except DecodeError as nested:
                result, errors = _handle_root_error(model, nested, guess_default=guess_default)
                errors.extend(unicode_errors)
                return result, errors
            else:
                # Most errors enter here: the payload reads as it is, the object
                # is repaired in place. Only this path judges the object, and it
                # has to use the rules the decoding did, or a value the decoder
                # accepted (`strict=False`, a `dec_hook`) looks invalid. Msgpack
                # keys are not restricted to strings, so `str_keys` stays off.
                _, _, options = get_decoder_model(
                    model_or_decoder, MsgpackDecoder, None, 'load_msgpack_with_default')
                return _handle_obj_repair(
                    raw_obj, model, error, guess_default=guess_default, kwargs=options,
                    codec='msgpack', decoder=decoder, raw_spans=raw_spans)
        else:
            # Malformed msgpack, there is no value to repair
            result, errors = _handle_root_error(model, error, guess_default=guess_default)
            errors.extend(unicode_errors)
            return result, errors

        # The bytes changed, decode them again; a repair that did not work is
        # handled by the next attempt
        try:
            if decoder is None:
                return decode_msgpack(data, type=model), unicode_errors
            return decoder.decode(data), unicode_errors
        except ValidationError as e:
            error = e
        except UnicodeDecodeError as e:
            error = e
        except DecodeError as e:
            result, errors = _handle_root_error(model, e, guess_default=guess_default)
            errors.extend(unicode_errors)
            return result, errors

    # Exhausted retries – shouldn't happen after the slow walker
    result = get_default(model, guess_default=guess_default, return_obj=True)
    rejected = ErrorInfo(
        'Input rejected: failed to solve UnicodeDecodeError',
        type=ErrorType.INPUT_REJECTED, loc=())
    return result, unicode_errors + [rejected]


# Rounds the msgpack unicode repair may take. The slow walk repairs every
# remaining string in one pass, so two rounds always finish the repair, and the
# `ValidationError` the repaired payload raises needs one more.
_MAX_UNICODE_ROUNDS = 4


def _repair_msgpack_unicode(
        data: bytes,
        error: UnicodeDecodeError,
        attempt: int,
        utf8_error: T_utf8_error = 'replace',
        spans=None,
) -> bytes:
    """
    Repair msgpack bytes with invalid UTF-8.

    *Attempt 0*: use ``fixup_msgpack_unicode_fast`` to pinpoint the exact
    failing string.  If the search is ambiguous, fall back to the slow
    one-pass walker.

    *Attempt 1*: use ``fixup_msgpack_unicode_slow`` directly – this
    guarantees a single O(N) pass regardless of how many strings need
    repair, preventing attackers from causing O(N*M) re-decode cycles.

    Args:
        data (bytes | bytearray | memoryview): The payload to repair. The fixups
            copy it to `bytes`, which is what their `.find()` / `.decode()`
            calls and the span map need
        error (UnicodeDecodeError): The failure that stopped the untyped decode
        attempt (int): 0 for the fast fix, anything else for the slow walk
        utf8_error (str): Error handler for the invalid bytes
        spans (dict | None): Map of repaired item bytes to the original ones,
            filled in for every rewrite. Defaults to None.

    Returns:
        bytes: The repaired payload
    """
    if attempt == 0:
        fixed = fixup_msgpack_unicode_fast(data, error, utf8_error=utf8_error, spans=spans)
        if fixed is not None:
            return fixed
    return fixup_msgpack_unicode_slow(data, utf8_error=utf8_error, spans=spans)


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

    A repair that gives up does not leave a half repaired object behind: every
    container it has written into is put back as it was, so a caller can retry
    the same call on the same object. The containers are copied one at a time,
    right before the first write, so a call that validates on the first try
    pays nothing for this.

    A `msgspec.Raw` field can not be built from an in-memory object: it needs
    bytes, and there is no payload to take them from. The value at that field is
    encoded as msgpack and filled in, so the model is built and the field holds
    the sub-document (`msgpack.decode(payload)` gives it back).

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
    _, model, options = get_decoder_model(model_or_decoder, options=kwargs)
    try:
        return convert(data, type=model, **options), []
    except ValidationError as e:
        return _handle_obj_repair(
            data, model, e, guess_default=guess_default, kwargs=options, undo=_Undo())
