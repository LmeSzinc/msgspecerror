"""
Field access for a `msgspec.Struct` model

A struct is the only kind msgspec lets rename a field, and the only one that can
be decoded from an array (`array_like=True`), which is why only this module has
`get_struct_field_name()` and the positional accessors.
"""
from msgspec import NODEFAULT, UNSET, Struct
from msgspec._core import Factory

from .parse_type import default_is_accepted, field_typehint, get_default, unwrap_model


def is_struct_arraylike(model, unwrap=True):
    """
    Check whether a struct is decoded from an array instead of an object

    An `array_like=True` struct is encoded as an array of its field values, so
    an error location addresses it by position rather than by field name.

    Args:
        model: A msgspec.Struct class, or a parametrized generic alias of one
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        bool: True for an `array_like=True` struct
    """
    config = getattr(unwrap_model(model) if unwrap else model, '__struct_config__', None)
    return config is not None and config.array_like


def get_arraylike_field_typehint(model, index, unwrap=True):
    """
    Get the typehint of a field of an `array_like` struct by position

    Args:
        model: An `array_like=True` struct class, or a parametrized generic alias of one
        index (int): Position in the encoded array. A tagged struct encodes its
            tag at position 0, so the first field holds position 1
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of the field at `index`

    Raises:
        AttributeError: if `index` is out of range, addresses the tag, or
            `model` is not a struct
    """
    return get_struct_field_typehint(model, index, unwrap=unwrap)


def _resolve_array_index(struct_model, index):
    """
    Resolve a position of the decoded array to the index of a field

    An `array_like` struct with a tag is encoded as `[tag, field0, field1, ...]`,
    so the position an error reports is one past the field it names. An untagged
    struct starts at the first field, and a bare negative index keeps Python's
    indexing, which is what it meant before.

    Args:
        struct_model (type): A `msgspec.Struct` class, already unwrapped
        index (int): Position in the decoded array

    Returns:
        int: Index of the field

    Raises:
        AttributeError: if the position addresses the tag, which is no field
    """
    config = getattr(struct_model, '__struct_config__', None)
    if config is None or not config.array_like or config.tag is None:
        # Nothing to shift: only a tagged array carries the tag in the array
        return index
    if index > 0:
        return index - 1
    raise AttributeError(
        f'Type {struct_model} has no field at index {index}, the array starts with the tag')


def get_struct_field_name(model, name, unwrap=True):
    """
    Resolve a name to the field name (Python attribute name).

    Accepts both field names and encode names (serialization names defined via ``field(name=...)``).

    Args:
        model: Subclass of msgspec.Struct, or a parametrized generic alias of one (e.g. `Box[int]`)
        name (str): Field name or encode name
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        str: field_name

    Raises:
        AttributeError: if failed
    """
    # 1. Get name lists from magic attributes.
    struct_model = unwrap_model(model) if unwrap else model
    try:
        field_names = struct_model.__struct_fields__
        encode_names = struct_model.__struct_encode_fields__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid msgspec.Struct')

    # 2. Try the name as an encode name first.
    try:
        idx = encode_names.index(name)
    except ValueError:
        pass
    else:
        try:
            return field_names[idx]
        except IndexError:
            # this shouldn't happen, __struct_fields__ should have the same length as __struct_encode_fields__
            raise AttributeError(f'Type {model} field_index={idx} is out of __struct_fields__={field_names}')

    # 3. If not an encode name, try as a field name directly.
    if name in field_names:
        return name

    # 4. Not found in either list.
    raise AttributeError(f'Type {model} has no field with name="{name}"')


def get_struct_field_typehint(model, name_or_index, unwrap=True):
    """
    Get typehint or annotation of model.field_name

    Args:
        model: Subclass of msgspec.Struct, or a parametrized generic alias of one (e.g. `Box[int]`)
        name_or_index (str | int): Field name (Python attribute), encode name
            (serialization name), or the position in the decoded array of an
            `array_like=True` struct, which is how an error reports it: the tag
            of a tagged struct holds position 0, so a field is one past it
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of model.field_name

    Raises:
        AttributeError: if failed
    """
    if type(name_or_index) is int:
        # An `array_like` struct is decoded from an array, the index is its position
        struct_model = unwrap_model(model) if unwrap else model
        try:
            field_names = struct_model.__struct_fields__
        except AttributeError:
            raise AttributeError(f'Type {model} is not a valid msgspec.Struct')
        try:
            field_name = field_names[_resolve_array_index(struct_model, name_or_index)]
        except IndexError:
            raise AttributeError(f'Type {model} has no field at index {name_or_index}')
        return field_typehint(model, field_name, Struct, unwrap=unwrap)

    # Resolve the name to a field name (supports both field_name and encode_name).
    return field_typehint(model, get_struct_field_name(model, name_or_index, unwrap=unwrap), Struct, unwrap=unwrap)


def get_struct_field_default(model, name_or_index, validate=False, guess_default=False, unwrap=True):
    """
    Get default and default_factory of model.field_name

    Args:
        model: Subclass of msgspec.Struct, or a parametrized generic alias of one (e.g. `Box[int]`)
        name_or_index (str | int): Field name (Python attribute), encode name
            (serialization name), or the position in the decoded array of an
            `array_like=True` struct, which is how an error reports it: the tag
            of a tagged struct holds position 0, so a field is one past it
        validate (bool): False by default. If True, the default value is checked
            against the field typehint with `msgspec.convert`. msgspec does not
            validate struct defaults, a default may be rejected when it is
            explicitly present in the input (e.g. `v: Annotated[int, Meta(ge=18)] = 0`),
            such a default returns NODEFAULT. Note that msgspec still applies that
            default when the field is absent from the input.
        guess_default (bool): False by default. If True, a field that has no
            accepted default falls back to a value guessed from its typehint
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any | NODEFAULT:
            default value of given field, or a guessed value when allowed
            NODEFAULT if field doesn't have a default
            NODEFAULT if default factory can't be constructed
            NODEFAULT if the default value is not accepted with `validate=True`

    Raises:
        AttributeError: if failed
    """
    # 1. Get name lists from magic attributes.
    struct_model = unwrap_model(model) if unwrap else model
    try:
        field_names = struct_model.__struct_fields__
        defaults = struct_model.__struct_defaults__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid msgspec.Struct')

    # 2. Resolve the position or the name to a field name (supports both
    # field_name and encode_name). Only an `array_like` struct is decoded from an
    # array, so only it is addressed by position.
    if type(name_or_index) is int:
        try:
            field_name = field_names[_resolve_array_index(struct_model, name_or_index)]
        except IndexError:
            raise AttributeError(f'Type {model} has no field at index {name_or_index}')
    else:
        field_name = get_struct_field_name(struct_model, name_or_index, unwrap=False)

    # 3. Find the index of the target field.
    idx = field_names.index(field_name)

    num_required = len(field_names) - len(defaults)
    if idx >= num_required:
        # 3. Find field default
        default_idx = idx - num_required
        try:
            default_obj = defaults[default_idx]
        except IndexError:
            # this shouldn't happen, __struct_defaults__ should have corresponding length
            raise AttributeError(f'Type {model} default_idx={default_idx} is out of __struct_defaults__={defaults}')

        if default_obj is NODEFAULT or default_obj is UNSET:
            return NODEFAULT
        if type(default_obj) is Factory:
            try:
                value = default_obj.factory()
            except Exception:
                return NODEFAULT
        else:
            value = default_obj
        if validate:
            typehint = field_typehint(model, field_name, Struct)
            if not default_is_accepted(typehint, value):
                return NODEFAULT
        return value

    if not guess_default:
        return NODEFAULT
    typehint = field_typehint(model, field_name, Struct)
    return get_default(typehint, guess_default=True)


def get_struct_model_changes(old, new):
    """
    Compare two instances of the same struct field by field

    Args:
        old (Struct): Original struct instance
        new (Struct): New struct instance of the same struct class

    Returns:
        dict[str, Any]: Dict of `{field_name: value_from_new}` for the fields
            whose values differ. Nested objects are compared by `==`, not
            recursed into

    Raises:
        TypeError: If `old` is not a msgspec Struct instance
        AttributeError: If `new` is a different struct type missing a field
    """
    try:
        field_names = old.__class__.__struct_fields__
    except AttributeError:
        raise TypeError(
            f"Both arguments must be msgspec.Struct instances, "
            f"got {type(old).__name__}"
        )

    result = {}
    for field_name in field_names:
        old_value = getattr(old, field_name)
        new_value = getattr(new, field_name)
        if old_value != new_value:
            result[field_name] = new_value
    return result
