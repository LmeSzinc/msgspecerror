"""
Locate the entry of a raw object that failed validation.

The two families that report an error both have to answer "where exactly did
it fail", and msgspec only answers part of it: a failing dict entry is reported
as `$[...]` without the key, a failing key as ``at `key` in `$` `` without the
position. Finding the rest means judging entries against the model again, in
the rules of the failed call, which is what this module does.

Three things live here:

- what msgspec reads as an object / as an array, so a walk never looks into
  something the validation never looked at (`_is_mapping()` and friends);
- probing one value against a model (`_validate_entry()`), including the
  `msgspec.Raw` situation where a decoded object is refused for a reason that
  is not a data error, and pinning the field to get past it (`_pin_raw()`);
- searching an object for the failing entry (`_find_failing_dict_value()` /
  `_find_failing_dict_key()`), which asks msgspec for the position instead of
  probing one entry at a time.

Nothing here repairs anything, and nothing here depends on `repair.py`: the
repair engine imports from this module, not the other way around.
"""
from collections.abc import Mapping
from typing import Any, Dict, List

from msgspec import NODEFAULT, DecodeError, EncodeError, Raw, ValidationError, convert
from msgspec.msgpack import decode as decode_msgpack, encode as encode_msgpack

from .parse_error import parse_msgspec_error
from .parse_model import get_field_typehint, is_arraylike
from .parse_msgpack import AMBIGUOUS_SPAN, MISSING
from .parse_type import is_struct_like, origin_args, unwrap_model, unwrap_typehint

# The data types msgspec reads as an array. A tuple is accepted, `UserList`,
# `deque`, `array.array` and every other sequence is not: msgspec rejects them
# at the array check itself, so no error location ever points inside one and
# there is nothing to read there. When that changes, add the type here and to
# `_WRITABLE_SEQUENCE_TYPES` when it can be written into, and every reader and
# the repair follow, `deque` included.
_SEQUENCE_TYPES = (list, tuple)
# The subset a repair may delete an item from or write an item into. A tuple is
# in `_SEQUENCE_TYPES` but not here: its items can not be replaced in place.
_WRITABLE_SEQUENCE_TYPES = (list,)


def _is_mapping(obj):
    """
    Check whether the data is used as an object by msgspec.

    msgspec takes any mapping where a struct or a dict model is expected: a
    dict, a dict subclass (`OrderedDict`, `defaultdict`, `Counter`), a
    `UserDict`, a `ChainMap`, a read-only mapping. A duck typed class with
    `keys()` and `__getitem__` is taken as well, but it is deliberately not
    covered: `Mapping` only accepts what msgspec is known to accept, so a
    mismatch can never hand back a value msgspec did not look at.

    This is the read side. A repair writes into the data, which a read-only
    mapping or a `ChainMap` (its `pop` only looks at the first map, `clear`
    and `update` do not round-trip) can not do, so `_is_writable_mapping()`
    is the predicate to use before writing.

    Args:
        obj (Any): The raw data at an error location

    Returns:
        bool: True if the data can be read as a mapping
    """
    return type(obj) is dict or isinstance(obj, Mapping)


def _is_writable_mapping(obj):
    """
    Check whether the data is a dict that a repair may write into.

    A dict subclass is included, msgspec reads it like a dict and the repair
    can delete or replace its entries. A `UserDict` is not: writing works, but
    a repair that gives up has to put the container back, and the round trip is
    only known to be faithful for a dict.

    Args:
        obj (Any): The raw data at an error location

    Returns:
        bool: True if the data can be repaired in place
    """
    return isinstance(obj, dict)


def _is_sequence(obj):
    """
    Check whether the data is used as an array by msgspec.

    msgspec takes a list, a list subclass and a tuple where a sequence model is
    expected, and rejects every other sequence at the array check, so a
    location never points inside a `UserList`, a `deque` or an `array.array`.
    The accepted types live in `_SEQUENCE_TYPES`, one place to edit when
    msgspec starts taking one of them.

    This is the read side. A repair writes into the data, and a tuple can not
    be written, so `_is_writable_sequence()` is the predicate to use before
    deleting or replacing an item.

    Args:
        obj (Any): The raw data at an error location

    Returns:
        bool: True if the data can be read as an array
    """
    return isinstance(obj, _SEQUENCE_TYPES)


def _is_writable_sequence(obj):
    """
    Check whether the data is a list that a repair may write into.

    A list subclass is included, msgspec reads it like a list and the repair
    can delete or replace its items.

    Args:
        obj (Any): The raw data at an error location

    Returns:
        bool: True if the data can be repaired in place
    """
    return isinstance(obj, _WRITABLE_SEQUENCE_TYPES)


# The model kinds whose data is an object with attributes. A TypedDict is a dict
# and a NamedTuple is an array, neither has attributes to read.
_OBJECT_MODEL_KINDS = ('struct', 'dataclass', 'attrs')
# A nested object can refer to itself; the snapshot is not the place to find out
_MAX_SNAPSHOT_DEPTH = 32


def _snapshot_field_names(model, model_kind):
    """
    List the attributes an object model reads, or None when they can not be used.

    A struct may rename its fields for the encoded form (`rename='camel'`). The
    error location names the attribute while a dict is decoded by the encoded
    name, and a snapshot can only be keyed one way, so a renamed struct is
    refused here and the caller keeps its old behaviour. A dataclass and an
    attrs class have no rename: the attribute name is the key.

    Args:
        model (type): The model to read
        model_kind (str): Its kind, as `is_struct_like()` names it

    Returns:
        tuple[str] | None: The attribute names, or None when this kind has none
            or the names can not be used as dict keys
    """
    model_class = unwrap_model(model)
    if model_kind == 'struct':
        names = getattr(model_class, '__struct_fields__', None)
        encoded = getattr(model_class, '__struct_encode_fields__', None)
        if not names or encoded is None or tuple(names) != tuple(encoded):
            # Renamed, or no fields at all
            return None
        return tuple(names)
    if model_kind == 'dataclass':
        # `__dataclass_fields__` also holds the `ClassVar` and `InitVar` pseudo
        # fields, which msgspec decodes neither of. A real field is marked by the
        # private `_FIELD`, the same marker `parse_model_dataclass._field()` reads.
        names = []
        for name, field in getattr(model_class, '__dataclass_fields__', {}).items():
            try:
                field_type = field._field_type.name
            except AttributeError:
                field_type = '_FIELD'
            if field_type == '_FIELD':
                names.append(name)
        return tuple(names) if names else None
    if model_kind == 'attrs':
        return tuple(f.name for f in getattr(model_class, '__attrs_attrs__', ()))
    return None


def _copy_containers(value, seen):
    """
    Copy the dicts and lists of a value, so a repair can not reach the original.

    The snapshot shares its values with the caller's object. A repair writes into
    the dicts and lists it walks, so those have to be copies, or fixing the
    snapshot would change the object the caller still holds. A cycle is copied
    once and the copy refers to itself, the same way the original does.

    Args:
        value (Any): The value to copy
        seen (dict): Identity of every container copied so far, to its copy

    Returns:
        Any: The value, with its containers copied
    """
    if isinstance(value, dict):
        copied = seen.get(id(value))
        if copied is not None:
            return copied
        copied = {}
        seen[id(value)] = copied
        for key, item in value.items():
            copied[key] = _copy_containers(item, seen)
        return copied
    if isinstance(value, list):
        copied = seen.get(id(value))
        if copied is not None:
            return copied
        copied = []
        seen[id(value)] = copied
        copied.extend(_copy_containers(item, seen) for item in value)
        return copied
    return value


def _attribute_snapshot(obj, model, model_kind, depth=0, seen=None):
    """
    Read the attributes a model names off an object, into a plain dict.

    `from_attributes=True` decodes an object by reading its attributes. Repairing
    that object would mean writing attributes back, which raises questions the
    library has no answer for (a read-only property, a setter with a side
    effect), and the repair engine can only write into a dict. Reading the
    attributes into a dict first sidesteps both: the engine repairs the dict it
    owns, and the caller's object is never touched.

    A field whose typehint is an object model is read the same way, so a nested
    object is repaired too instead of failing the whole document.

    Args:
        obj (Any): The object the failed call was converting
        model (type): The model it was being converted into
        model_kind (str): Its kind, as `is_struct_like()` names it
        depth (int): Recursion depth, the guard against an object cycle.
            Defaults to 0
        seen (set | None): Identity of the objects already read, the guard
            against an object that holds itself. Defaults to None

    Returns:
        dict | None: `{field_name: value}`, or None when the object can not be
            read this way and the caller has to keep its old behaviour
    """
    if model_kind not in _OBJECT_MODEL_KINDS:
        # A dict model, a sequence model, a scalar: the data is not read by
        # attribute, so there is nothing to snapshot
        return None
    if _is_mapping(obj) or _is_sequence(obj):
        # Decoded by key, or by position: not by attribute. The predicates are the
        # ones the repair already uses to decide what it may walk, so a subclass
        # is recognised here without a list of its own.
        return None
    if type(obj).__module__ == 'builtins':
        # A scalar, a container, a class: every builtin type lives in that
        # module, so no list of them has to be kept up to date. A user class is
        # never in it, and a container subclass was caught above.
        return None
    names = _snapshot_field_names(model, model_kind)
    if names is None:
        return None
    if depth > _MAX_SNAPSHOT_DEPTH:
        return None
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return None
    seen = seen | {id(obj)}

    copied = {}
    snapshot = {}
    for name in names:
        try:
            value = getattr(obj, name)
        except AttributeError:
            # The attribute is missing, the decode reports that field as missing
            continue
        value_type = type(value)
        if value_type.__module__ == 'builtins':
            # Every builtin type lives in that module, so no list of them has to
            # be kept up to date. The two builtin containers are copied, because
            # a repair deletes from the containers it walks; every other builtin
            # value is a scalar and is taken as it is.
            if value_type is dict or value_type is list:
                snapshot[name] = _copy_containers(value, copied)
            else:
                snapshot[name] = value
            continue
        if _is_writable_mapping(value) or _is_writable_sequence(value):
            # A container subclass, which the engine writes into all the same:
            # decided by the engine's own predicates rather than by its exact type
            snapshot[name] = _copy_containers(value, copied)
            continue
        # Only a value from outside the builtin module can be an object model, and
        # only then is the typehint worth looking up: it walks the class for
        # every annotation
        try:
            typehint = get_field_typehint(model, name, known_struct_type=model_kind)
        except AttributeError:
            typehint = None
        if typehint is not None:
            child_kind = is_struct_like(typehint, unwrap=False)
            if child_kind in _OBJECT_MODEL_KINDS:
                nested = _attribute_snapshot(value, typehint, child_kind, depth + 1, seen)
                if nested is not None:
                    snapshot[name] = nested
                    continue
        snapshot[name] = _copy_containers(value, copied)
    # No attribute was readable: a scalar, or an object of another shape
    return snapshot or None


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
        # The index is the field order of an `array_like` struct or a NamedTuple.
        # `origin_args()` has already split a parametrized alias into the class
        # and its arguments, and a field declared with a `TypeVar` only resolves
        # with the arguments put back on: `Box[Leaf]` holds a `Leaf` at that
        # position, the bare `Box` holds the `~T` it was declared with.
        field_model = model_origin
        if model_args:
            try:
                field_model = model_origin[model_args]
            except TypeError:
                # Not every origin can be parametrized again, the bare class is
                # still the best answer for the fields that use no argument
                pass
        try:
            return get_field_typehint(field_model, index, known_struct_type=known_struct_type)
        except AttributeError:
            return NODEFAULT
    return _get_item_model(model_origin, model_args, index)


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
        except (EncodeError, TypeError, RecursionError):
            # The value can not be encoded by this codec. A `RecursionError` is
            # a value that refers to itself, which a YAML anchor produces: the
            # encoder can not walk it, so the field can not be filled either.
            return NODEFAULT

    part = loc[0]
    rest = loc[1:]
    model = unwrap_typehint(model)
    origin, args = origin_args(model)

    if part == '...':
        # A dict value failure, the failing key is unknown, pin every entry
        if not _is_mapping(value):
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
        if not _is_sequence(value) or not 0 <= part < len(value):
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

    if not _is_mapping(value):
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


# Below this many entries one probe per entry is cheaper than one batch. The
# batch pays a fixed cost the per-entry search does not: build the list model,
# copy the container, and parse the error message into a location. Measured at
# about 13 us in total, of which `parse_msgspec_error()` alone is 6.5 us, the
# rest being the `convert` floor (about 2 us) and the two copies. A probe costs
# about 0.35 us per entry, which puts the crossover at 13 to 16 entries;
# measured 13.9 us against 14.7 us at 16, 16.5 against 31.2 at 64.
_BATCH_SEARCH_MINIMUM = 16


def _position_of(loc):
    """
    Read the position of a failing list item out of an error location.

    Args:
        loc (tuple): Location of the list error, its first segment is the position

    Returns:
        int | NODEFAULT: The position, or NODEFAULT if the location does not
            start with one
    """
    if not loc:
        return NODEFAULT
    position = loc[0]
    # `type(position) is int` is False for bool, which is not a position
    if type(position) is not int or position < 0:
        return NODEFAULT
    return position


def _key_at(obj, position):
    """
    Turn the position of a failing list item back into the key of the entry.

    Args:
        obj (dict | Mapping): The object the values were taken from
        position (int): Index of the failing item

    Returns:
        Any: The key at that position, or NODEFAULT if there is none
    """
    try:
        return list(obj)[position]
    except IndexError:
        # The position is not in the object, the location and the data disagree
        return NODEFAULT


def _find_failing_dict_value(obj, value_model, kwargs=None):
    """
    Find the dict entry whose value fails validation.

    msgspec reports a failing dict entry as `$[...]`, without saying which key
    it was. The same values handed over as a list give away the position,
    because msgspec numbers the items of an array, so one `convert` judges
    every entry at once and the first failing item names the entry: a dict of
    `n` entries used to cost `n` probes, each entering msgspec on its own.
    The position maps straight back to a key, dict order is insertion order.

    A batch is only trusted when the values can be judged as list items. A
    decoded object at a `msgspec.Raw` field is an artifact of the probe rather
    than a data error, and settling that needs the per-entry retry of
    `_validate_entry()`, so the one-by-one search still answers for it.

    Args:
        obj (dict | Mapping): Raw object to search
        value_model: Typehint of the dict values
        kwargs (dict): Extra keyword arguments for `convert`, the same ones the
            failed call used. Validation rules may change which entry fails
            (`strict=False` accepts a numeric string), so the search has to see
            exactly the rules the caller's call saw.

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
    kwargs = kwargs or {}
    if len(obj) < _BATCH_SEARCH_MINIMUM:
        # A small object is not worth the fixed cost of the batch
        return _find_failing_dict_value_each(obj, value_model, kwargs)
    try:
        item_model = List[value_model]
    except TypeError:
        # `value_model` is not a typehint that can be the item type of a list
        return _find_failing_dict_value_each(obj, value_model, kwargs)
    try:
        convert(list(obj.values()), type=item_model, **kwargs)
    except ValidationError as e:
        error = parse_msgspec_error(e)
        if error.ctx.may_raw_rejection:
            return _find_failing_dict_value_each(obj, value_model, kwargs)
        position = _position_of(error.loc)
        if position is NODEFAULT:
            return NODEFAULT
        return _key_at(obj, position)
    except TypeError:
        # `value_model` is not a type msgspec supports
        return NODEFAULT
    return NODEFAULT


def _find_failing_dict_value_each(obj, value_model, kwargs):
    """
    Find the failing entry one probe at a time, see `_find_failing_dict_value`.

    Args:
        obj (dict | Mapping): Raw object to search
        value_model: Typehint of the dict values
        kwargs (dict): Extra keyword arguments for `convert`

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
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

    A key alone can not be judged: a json payload coerces string keys to the
    key type (`str_keys`), and that rule only exists for the key of a dict. Each
    key is wrapped back into a one-entry dict for that reason, and the wrapped
    entries are handed over as a list, which msgspec numbers: one `convert`
    reports the position of the first bad key, and the position names the key.
    Probing one key at a time cost one call, one built dict and one parsed
    message per key; a thousand keys with the bad one last went from 3.2 ms to
    0.18 ms.

    Args:
        obj (dict | Mapping): Raw object to search
        key_model: Typehint of the dict keys
        kwargs (dict): Extra keyword arguments for `convert`, see
            `_find_failing_dict_value`

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
    kwargs = kwargs or {}
    # `Dict[key_model, Any]` keeps the key coercion rules of the failed call,
    # and `Any` keeps the value out of the way: only the key is on trial
    try:
        key_dict_model = Dict[key_model, Any]
    except TypeError:
        # `key_model` is not a typehint that can be used as a dict key type
        return NODEFAULT
    if len(obj) < _BATCH_SEARCH_MINIMUM:
        return _find_failing_dict_key_each(obj, key_dict_model, kwargs)

    keys = list(obj)
    try:
        convert([{key: None} for key in keys], type=List[key_dict_model], **kwargs)
    except ValidationError as e:
        position = _position_of(parse_msgspec_error(e).loc)
        if position is NODEFAULT:
            return NODEFAULT
        try:
            return keys[position]
        except IndexError:
            # The position is not in the object, the location and the data disagree
            return NODEFAULT
    except TypeError:
        # `key_model` is not a type msgspec supports, or not one that can be
        # the key of a dict at all
        return NODEFAULT
    # No key is rejected, a corrupt location asked for a `...key` segment
    return NODEFAULT


def _find_failing_dict_key_each(obj, key_dict_model, kwargs):
    """
    Find the failing key one probe at a time, see `_find_failing_dict_key`.

    Args:
        obj (dict | Mapping): Raw object to search
        key_dict_model (type): `Dict[key_model, Any]`
        kwargs (dict): Options of the equivalent `msgspec.convert` call

    Returns:
        Any: The first failing key, or NODEFAULT if no key can be identified
    """
    for key in obj.keys():
        try:
            convert({key: None}, type=key_dict_model, **kwargs)
        except ValidationError:
            return key
        except TypeError:
            # `key_model` is not a type msgspec supports
            break
    return NODEFAULT
