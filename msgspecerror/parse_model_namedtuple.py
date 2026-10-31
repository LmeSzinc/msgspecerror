"""
Field access for a NamedTuple model

A NamedTuple is decoded from an array, so an error addresses its fields by
position as well as by name. There is no rename mechanism.
"""
from msgspec import NODEFAULT

from .parse_type import default_is_accepted, field_typehint, get_default, unwrap_model


def _fields(model, unwrap=True):
    """
    Read the field names and defaults of a NamedTuple

    Args:
        model: A NamedTuple class
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        tuple[tuple[str, ...], dict]: The field names in order, and the defaults
            of the trailing fields

    Raises:
        AttributeError: if `model` is not a NamedTuple
    """
    named_tuple = unwrap_model(model) if unwrap else model
    try:
        return named_tuple._fields, named_tuple._field_defaults
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid NamedTuple')


def _resolve_name(model, field_names, name_or_index):
    """
    Resolve a field name or a position to the field name

    Args:
        model: The NamedTuple class, used for the error message
        field_names (tuple[str, ...]): The field names in order
        name_or_index (str | int): Field name or position

    Returns:
        str: field name

    Raises:
        AttributeError: if the name does not exist or the position is out of range
    """
    if type(name_or_index) is int:
        try:
            return field_names[name_or_index]
        except IndexError:
            raise AttributeError(f'Type {model} has no field at index {name_or_index}')
    if name_or_index in field_names:
        return name_or_index
    raise AttributeError(f'Type {model} has no field with name="{name_or_index}"')


def get_namedtuple_field_typehint(model, name_or_index, unwrap=True):
    """
    Get the typehint of a NamedTuple field

    Args:
        model: A NamedTuple class
        name_or_index (str | int): Field name, or the position of the field in
            the decoded array, which is how msgspec reports it
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of the field

    Raises:
        AttributeError: if failed
    """
    field_names, _ = _fields(model, unwrap=unwrap)
    name = _resolve_name(model, field_names, name_or_index)
    return field_typehint(model, name, object, unwrap=unwrap)


def get_namedtuple_field_default(model, name_or_index, validate=False, guess_default=False, unwrap=True):
    """
    Get the default of a NamedTuple field

    Only the trailing fields can have a default, and a NamedTuple has no
    default_factory.

    Args:
        model: A NamedTuple class
        name_or_index (str | int): Field name, or the position of the field in
            the decoded array, which is how msgspec reports it
        validate (bool): False by default. If True, the default value is checked
            against the field typehint with `msgspec.convert`. msgspec does not
            validate NamedTuple defaults, a default may be rejected when it is
            explicitly present in the input, such a default returns NODEFAULT.
            Note that msgspec still applies that default when the field is
            absent from the input.
        guess_default (bool): False by default. If True, a field that has no
            accepted default falls back to a value guessed from its typehint
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any | NODEFAULT: default value of given field, or a guessed value when
            allowed. NODEFAULT if the field has no default, or if the default
            value is not accepted with `validate=True`

    Raises:
        AttributeError: if failed
    """
    field_names, field_defaults = _fields(model, unwrap=unwrap)
    name = _resolve_name(model, field_names, name_or_index)

    if name not in field_defaults:
        if not guess_default:
            return NODEFAULT
        typehint = field_typehint(model, name, object, unwrap=unwrap)
        return get_default(typehint, guess_default=True)

    value = field_defaults[name]
    if validate:
        typehint = field_typehint(model, name, object, unwrap=unwrap)
        if not default_is_accepted(typehint, value):
            return NODEFAULT
    return value


def get_namedtuple_model_changes(old, new):
    """
    Compare two instances of the same NamedTuple field by field

    Args:
        old (tuple): Original instance
        new (tuple): New instance of the same NamedTuple

    Returns:
        dict[str, Any]: Dict of `{field_name: value_from_new}` for the fields
            whose values differ. Nested objects are compared by `==`, not
            recursed into

    Raises:
        TypeError: If `old` is not a NamedTuple instance
        AttributeError: If `new` is a different type missing a field
    """
    try:
        field_names = old._fields
    except AttributeError:
        raise TypeError(
            f"Both arguments must be NamedTuple instances, "
            f"got {type(old).__name__}"
        )

    result = {}
    for name in field_names:
        old_value = getattr(old, name)
        new_value = getattr(new, name)
        if old_value != new_value:
            result[name] = new_value
    return result
