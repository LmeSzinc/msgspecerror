"""
Field access for a dataclass model

msgspec decodes a dataclass field by its Python attribute name, there is no
rename mechanism, so this module has no `get_field_name()`.
"""
from msgspec import NODEFAULT

from .parse_type import default_is_accepted, field_typehint, get_default, unwrap_model


def _field(model, name, unwrap=True):
    """
    Look up the dataclass field that describes a field

    Args:
        model: A dataclass, or a parametrized generic alias of one
        name (str): Field name (the Python attribute name)
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: The `dataclasses.Field` of the field

    Raises:
        AttributeError: if `model` is not a dataclass, or has no such field
    """
    dataclass = unwrap_model(model) if unwrap else model
    try:
        fields = dataclass.__dataclass_fields__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid dataclass')
    try:
        field = fields[name]
    except (KeyError, TypeError):
        raise AttributeError(f'Type {model} has no field with name="{name}"')

    # `__dataclass_fields__` also holds the `ClassVar` and `InitVar` pseudo-fields,
    # msgspec decodes neither of them. A real field is marked by the private
    # `_FIELD`, and the marker is read as it is.
    try:
        field_type = field._field_type.name
    except AttributeError:
        field_type = '_FIELD'
    if field_type != '_FIELD':
        raise AttributeError(f'Type {model} has no field with name="{name}"')

    return field


def get_dataclass_field_typehint(model, name, unwrap=True):
    """
    Get the typehint of a dataclass field

    Args:
        model: A dataclass, or a parametrized generic alias of one
        name (str): Field name (the Python attribute name)
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of model.name

    Raises:
        AttributeError: if failed
    """
    _field(model, name, unwrap=unwrap)
    return field_typehint(model, name, object, unwrap=unwrap)


def get_dataclass_field_default(model, name, validate=False, guess_default=False, unwrap=True):
    """
    Get the default of a dataclass field

    Args:
        model: A dataclass, or a parametrized generic alias of one
        name (str): Field name (the Python attribute name)
        validate (bool): False by default. If True, the default value is checked
            against the field typehint with `msgspec.convert`. msgspec does not
            validate dataclass defaults, a default may be rejected when it is
            explicitly present in the input (e.g. `v: Annotated[int, Meta(ge=18)] = 0`),
            such a default returns NODEFAULT. Note that msgspec still applies that
            default when the field is absent from the input.
        guess_default (bool): False by default. If True, a field that has no
            accepted default falls back to a value guessed from its typehint
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any | NODEFAULT: default value of given field, or a guessed value when
            allowed. NODEFAULT if the field has no default, if its
            default_factory can't be constructed, or if the default value is
            not accepted with `validate=True`

    Raises:
        AttributeError: if failed
    """
    field = _field(model, name, unwrap=unwrap)

    # `dataclasses.MISSING` is the singleton of the private `_MISSING_TYPE`, so
    # the module does not have to be imported to recognise it. The attributes are
    # read as they are: a field object that lacks one has no default at all.
    try:
        value = field.default
        has_default = type(value).__name__ != '_MISSING_TYPE'
    except AttributeError:
        has_default = False

    if not has_default:
        try:
            factory = field.default_factory
            has_factory = type(factory).__name__ != '_MISSING_TYPE'
        except AttributeError:
            has_factory = False
        if not has_factory:
            if not guess_default:
                return NODEFAULT
            typehint = field_typehint(model, name, object, unwrap=unwrap)
            return get_default(typehint, guess_default=True)
        try:
            value = factory()
        except Exception:
            return NODEFAULT

    if validate:
        typehint = field_typehint(model, name, object, unwrap=unwrap)
        if not default_is_accepted(typehint, value):
            return NODEFAULT
    return value


def get_dataclass_model_changes(old, new):
    """
    Compare two instances of the same dataclass field by field

    Args:
        old: Original dataclass instance
        new: New dataclass instance of the same class

    Returns:
        dict[str, Any]: Dict of `{field_name: value_from_new}` for the fields
            whose values differ. Nested objects are compared by `==`, not
            recursed into

    Raises:
        TypeError: If `old` is not a dataclass instance
        AttributeError: If `new` is a different class missing a field
    """
    try:
        fields = old.__class__.__dataclass_fields__
    except AttributeError:
        raise TypeError(
            f"Both arguments must be dataclass instances, "
            f"got {type(old).__name__}"
        )

    result = {}
    for name, field in fields.items():
        try:
            field_type = field._field_type.name
        except AttributeError:
            field_type = '_FIELD'
        if field_type != '_FIELD':
            continue
        old_value = getattr(old, name)
        new_value = getattr(new, name)
        if old_value != new_value:
            result[name] = new_value
    return result
