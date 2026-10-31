import re
from typing import Any, Dict, List, Tuple, Type, TypeVar, Union, overload

from msgspec import NODEFAULT, DecodeError, EncodeError, Raw, UnsetType, ValidationError, convert
from msgspec.json import Decoder as JsonDecoder, decode as decode_json, encode as encode_json
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as decode_msgpack, encode as encode_msgpack

from . import const
from .const import ErrorType, T_json_data, T_msgpack_data, T_utf8_error
from .parse_decoder import _type_error, get_decoder_model
from .parse_error import ErrorInfo, parse_msgspec_error
from .parse_model import get_field_default, get_field_name, get_field_typehint, is_arraylike
from .parse_msgpack import AMBIGUOUS_SPAN, MISSING, fixup_msgpack_unicode_fast, fixup_msgpack_unicode_slow
from .parse_type import default_is_accepted, get_default, is_struct_like, origin_args, unwrap_model, unwrap_typehint
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


def _get_index_model(model_origin, model_args, index, known_struct_type=None):
    """
    Get the typehint an error location addresses by position.

    A sequence shares one item typehint, an `array_like` struct and a NamedTuple
    address their fields by order instead. All are looked up here, so a caller
    that walks an index does not have to ask which kind it is holding.

    Args:
        model_origin (type): Origin of the typehint, e.g. list, tuple, a struct
        model_args (tuple | None): Args of the sequence typehint, None for bare typehints
        index (int): Position in the encoded value
        known_struct_type (str): Defaults to None. The kind as `is_struct_like()`
            names it, when the caller already knows it

    Returns:
        Any: The typehint at `index`, or NODEFAULT if it can't be determined
    """
    if model_origin in (list, set, frozenset, tuple):
        # A sequence shares one item typehint, no kind has to be named for it
        return _get_item_model(model_origin, model_args, index)
    if known_struct_type is None:
        known_struct_type = is_struct_like(model_origin, unwrap=False)
    if is_arraylike(model_origin, known_struct_type=known_struct_type, unwrap=False):
        # The index is the field order of an `array_like` struct or a NamedTuple
        try:
            return get_field_typehint(
                model_origin, index, known_struct_type=known_struct_type, unwrap=False)
        except AttributeError:
            return NODEFAULT
    return _get_item_model(model_origin, model_args, index)


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
    config = getattr(unwrap_model(struct), '__struct_config__', None)
    if config is None or config.tag is None or type(obj) is not dict:
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


_MAXIMUM_RAW_PATCH = 8


def _rebuild_value(node, raw_spans, counts, slots, fixed):
    """
    Copy a value, putting the payload bytes back where a string was rewritten

    One traversal: a rewritten string becomes a `msgspec.Raw` right away and the
    place it landed is remembered, so the decision can be finished afterwards
    without walking the value again.

    Args:
        node (Any): The value to copy
        raw_spans (dict): Map of repaired text to the original item bytes
        counts (dict): Hits per text, updated in place
        slots (list): `(container, key, text)` of every hit, appended in place
        fixed (bool): True inside a tuple, which can not be patched afterwards

    Returns:
        tuple[Any, str | None]: The copy, and the text when `node` itself was a
            rewrite
    """
    node_type = type(node)
    if node_type is str:
        if fixed or raw_spans.get(node, MISSING) is MISSING:
            return node, None
        original = raw_spans[node]
        if original is AMBIGUOUS_SPAN:
            # Two different originals repaired to this text, selecting one would
            # be a guess
            return node, None
        counts[node] = counts.get(node, 0) + 1
        return Raw(original), node
    if node_type is dict:
        out = {}
        for key, item in node.items():
            child, text = _rebuild_value(item, raw_spans, counts, slots, fixed)
            out[key] = child
            if text is not None:
                slots.append((out, key, text, child))
        return out, None
    if node_type is list:
        out = []
        for item in node:
            child, text = _rebuild_value(item, raw_spans, counts, slots, fixed)
            out.append(child)
            if text is not None:
                slots.append((out, len(out) - 1, text, child))
        return out, None
    if node_type is tuple:
        # A tuple can not be patched in place, so its items are left as decoded
        return tuple(_rebuild_value(item, raw_spans, counts, slots, True)[0] for item in node), None
    return node, None


def _restore_rewrites(value, raw_spans):
    """
    Give a raw field the bytes the payload had, not the repaired ones

    The untyped decode needs every string to be valid utf-8, so the payload is
    rewritten before it can be read. A `msgspec.Raw` field is not read by that
    decode at all, so a rewrite inside it is collateral: the field is filled from
    the repaired object, and every rewritten text still in it is swapped back for
    the bytes it replaced.

    The walk is over the decoded value, not over msgpack bytes: a `str` and a
    `bytes` are already told apart there, so a binary blob that happens to hold
    the same text is never touched.

    A text that occurs more than once is left alone, since which occurrence came
    from the repair is unknown. That decision needs the whole value, so the walk
    replaces optimistically and the places it replaced are put back afterwards,
    which keeps the value walked exactly once.

    Args:
        value (Any): The value the raw field is filled from
        raw_spans (dict | None): Map of repaired text to the original payload

    Returns:
        Any: `value` with every unambiguous rewrite restored
    """
    if not raw_spans:
        return value
    counts = {}
    slots = []
    restored, _ = _rebuild_value(value, raw_spans, counts, slots, False)
    for container, key, text, child in slots:
        if counts[text] != 1:
            # The same text is in the value somewhere else as well, which one
            # was rewritten is unknown
            container[key] = text
    return restored


def _pin_raw(value, loc, model, encoder=None, raw_spans=None):
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
        encoder (callable): Encodes the value at `loc` into the bytes the raw
            field should hold. Defaults to None, an empty `Raw` is written, which
            is enough to probe an entry but is not the payload. The repair
            families pass the codec encoder, so the filled field is the real
            content and the model can be built from it.
        raw_spans (dict | None): Map of repaired text to the original payload,
            from the unicode repair of the payload bytes. A string the repair
            touched goes back to what the payload held, wherever it sits in a
            sub-document. Defaults to None, nothing is restored.

    Returns:
        Any | NODEFAULT: The pinned copy, or NODEFAULT if `loc` can't be walked
    """
    if not loc:
        if encoder is None:
            return Raw(b'')
        try:
            return Raw(encoder(_restore_rewrites(value, raw_spans)))
        except (EncodeError, TypeError):
            # The value can not be encoded by this codec
            return NODEFAULT

    part = loc[0]
    rest = loc[1:]
    model = unwrap_typehint(model)
    origin, args = origin_args(model)

    if part == '...':
        # A dict value failure, the failing key is unknown, pin every entry
        if type(value) is not dict:
            return NODEFAULT
        child_model = args[1] if args else NODEFAULT
        pinned = dict(value)
        for key, item in value.items():
            patched = _pin_raw(item, rest, child_model, encoder, raw_spans)
            if patched is NODEFAULT:
                if encoder is None:
                    return NODEFAULT
                # Filling mode: an entry that can not be walked is not the one
                # this error is about, keep it as it is
                continue
            pinned[key] = patched
        return pinned

    if type(part) is int:
        if type(value) is not list or not 0 <= part < len(value):
            return NODEFAULT
        item_model = _get_index_model(origin, args, part)
        if item_model is NODEFAULT:
            return NODEFAULT
        if origin in (list, set, frozenset) or (origin is tuple and Ellipsis in (args or ())):
            # Every item shares one typehint, pin the whole container at once
            pinned = list(value)
            for index, item in enumerate(value):
                patched = _pin_raw(item, rest, item_model, encoder, raw_spans)
                if patched is NODEFAULT:
                    if encoder is None:
                        return NODEFAULT
                    continue
                pinned[index] = patched
            return pinned
        item = _pin_raw(value[part], rest, item_model, encoder, raw_spans)
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
        if encoder is None:
            # The key is missing, or the segment is not usable as a key at all
            return NODEFAULT
        # Filling mode: an absent field is a different error, not this one
        return value
    try:
        child_model = get_field_typehint(model, part)
    except AttributeError:
        child_model = NODEFAULT
    item = _pin_raw(child, rest, child_model, encoder, raw_spans)
    if item is NODEFAULT:
        return NODEFAULT
    pinned = dict(value)
    pinned[part] = item
    return pinned


# Options `msgspec.decode` takes over from a `convert` call. `convert` accepts
# more of them (`str_keys`, `from_attributes`, `builtin_types`), which have no
# meaning for a value decoded from its own bytes.
_DECODE_KWARGS = ('strict', 'dec_hook')


def _validate_by_decode(value, model, kwargs):
    """
    Validate a value by decoding it again, so a `Raw` field gets its bytes.

    `convert` can only take a `Raw` instance at a raw field, and a msgspec before
    0.19.0 refuses that instance. The typed decoder takes the payload bytes
    there, so decoding the value is another way to validate it. msgpack carries
    every type a decoded payload can hold, whatever codec the payload came from.

    Args:
        value (Any): The value to validate, it is never modified
        model (Any): Typehint the value is validated against
        kwargs (dict): Options of the equivalent `msgspec.convert` call

    Returns:
        ErrorInfo | None | NODEFAULT: The error of the decode, with a location
            relative to `value`, None when the value passes, NODEFAULT when it
            can not be carried by msgpack and so can not be judged this way
    """
    try:
        packed = encode_msgpack(value)
    except (EncodeError, TypeError):
        # A value msgpack can not carry, e.g. an object built by a `dec_hook`
        return NODEFAULT
    options = {key: kwargs[key] for key in _DECODE_KWARGS if key in kwargs}
    try:
        decode_msgpack(packed, type=model, **options)
    except ValidationError as e:
        return parse_msgspec_error(e)
    except (DecodeError, TypeError):
        # The model is not a type msgspec can decode into
        return NODEFAULT
    return None


def _validate_entry(value, model, kwargs):
    """
    Validate a decoded value the way the failed call did.

    An already decoded object can not satisfy a `msgspec.Raw` field, so `convert`
    stops at that field and a real error behind it is never reached. Writing a
    placeholder `Raw` at the reported location removes the complaint, so the next
    validation either passes or reports the real error. This is what makes both a
    dict entry and a whole object judgeable.

    When even the placeholder is refused, the msgspec is one whose `convert`
    never takes a `Raw` instance (before 0.19.0): the value is decoded again
    instead, see `_validate_by_decode()`.

    Args:
        value (Any): The value to validate, it is never modified
        model (Any): Typehint the value is validated against
        kwargs (dict): Options of the equivalent `msgspec.convert` call

    Returns:
        ErrorInfo | None | NODEFAULT: The first error that is not the artifact of
            validating a decoded object, None when the raw fields are the only
            complaint, NODEFAULT when the value can't be judged
    """
    original = value
    previous_loc = None
    for _ in range(_MAXIMUM_RAW_PATCH):
        try:
            convert(value, type=model, **kwargs)
        except ValidationError as e:
            error = parse_msgspec_error(e)
            # `convert` only, so a raw-shaped error is the artifact of validating
            # an already decoded object
            if not error.ctx.may_raw_rejection:
                return error
            if error.loc == previous_loc:
                # Pinning made no difference: this msgspec refuses a `Raw`
                # instance, so `convert` can never accept a value holding a raw
                # field. The pinned copy holds placeholders, the original is
                # decoded instead.
                return _validate_by_decode(original, model, kwargs)
            previous_loc = error.loc
            value = _pin_raw(value, error.loc, model)
            if value is NODEFAULT:
                return NODEFAULT
            continue
        except TypeError:
            # `model` is not a type msgspec supports
            return NODEFAULT
        return None

    # Still complaining about raw fields, the value can't be judged
    return NODEFAULT


def _find_failing_dict_value(obj, value_model, kwargs=None):
    """
    Find the dict entry whose value fails validation.

    Args:
        obj (dict): Raw dict to search
        value_model: Typehint of the dict values
        kwargs (dict): Extra keyword arguments for `convert`, the same ones the
            failed call used. Validation rules may change which entry fails
            (`strict=False` accepts a numeric string), so the search has to see
            exactly the rules the caller's call saw.

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
    kwargs = kwargs or {}
    for key, value in obj.items():
        error = _validate_entry(value, value_model, kwargs)
        if error is None:
            # The entry passes, a raw field was the only complaint
            continue
        if error is NODEFAULT:
            return NODEFAULT
        return key
    return NODEFAULT


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
            # The error path points into one member of the union, the kind comes
            # back with it: naming it is what found the member
            member = _resolve_union_member(model_args, part, obj)
            if member is NODEFAULT:
                # The path segment doesn't match any member of the union
                if not _pop_last_collection(last_collection_data, last_collection_key):
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
            failing_key = _find_failing_dict_value(obj, child_model, kwargs)
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

        # The placeholders above leave the loop on their own, so only a segment
        # that names a field or a position reaches this point: it is the one
        # that needs the kind, and it is named once for the branches below
        if model_kind is None:
            model_kind = is_struct_like(model_origin, unwrap=False)

        # 3. Invalid item in list
        if type(part) is int:
            if type(obj) is not list:
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
                        obj[part] = value
                        return raw_obj, error
                if not _pop_last_collection(last_collection_data, last_collection_key):
                    return NODEFAULT, error
                return raw_obj, error
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

        # 4. Invalid field of a model
        if model_kind:
            # The data tells how the model is decoded: an object model is
            # addressed by field name, an array model by position, which branch
            # 3 above has already handled
            if type(obj) is not dict:
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
                    if not _pop_last_collection(last_collection_data, last_collection_key):
                        return NODEFAULT, error
                    return raw_obj, error
                if value is not NODEFAULT:
                    # msgspec does not validate struct defaults, a default that
                    # msgspec rejects as an explicit value must not be written into
                    # the input. The entry is deleted instead, msgspec applies the
                    # default itself when the field is absent.
                    if default_is_accepted(
                            get_field_typehint(model, part, known_struct_type=model_kind), value):
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
                child_model = get_field_typehint(model, part, known_struct_type=model_kind)
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
                    model = get_field_typehint(model, part, known_struct_type=model_kind)
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


def _handle_obj_repair(
        raw_obj, model, error: ValidationError, guess_default=False, kwargs=None, codec=None, decoder=None,
        raw_spans=None
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

    Returns:
        tuple[any, list[ErrorInfo]]:
    """
    error = parse_msgspec_error(error)
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
            except (DecodeError, TypeError, EncodeError):
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
                    return get_default(model, guess_default=guess_default, return_obj=True), [rejected]
                collected_errors.append(error)
                return get_default(model, guess_default=guess_default, return_obj=True), collected_errors
            raw_obj = filled
        else:
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
                return get_default(model, guess_default=guess_default, return_obj=True), collected_errors

    rejected = ErrorInfo(rejected_msg, type=ErrorType.INPUT_REJECTED, loc=())
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

    for attempt in range(2):
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
        data (bytes): The payload to repair
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
            data, model, e, guess_default=guess_default, kwargs=options)
