"""
Field access for an attrs model

msgspec decodes an attrs field by its Python attribute name, there is no rename
mechanism.
"""
from msgspec import NODEFAULT

from .parse_type import default_is_accepted, field_typehint, get_default, unwrap_model


def get_attrs_field_typehint(model, name, unwrap=True):
    """
    Get the typehint of an attrs field

    Args:
        model: An attrs class, or a parametrized generic alias of one
        name (str): Attribute name
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of the field

    Raises:
        AttributeError: if failed
    """
    attrs_class = unwrap_model(model) if unwrap else model
    try:
        attrs_class.__attrs_attrs__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid attrs class')

    return field_typehint(model, name, object, unwrap=unwrap)


def get_attrs_field_default(model, name, validate=False, guess_default=False, unwrap=True):
    """
    Get the default of an attrs field

    Args:
        model: An attrs class, or a parametrized generic alias of one
        name (str): Attribute name
        validate (bool): False by default. If True, the default value is checked
            against the field typehint with `msgspec.convert`. msgspec does not
            validate attrs defaults, a default may be rejected when it is
            explicitly present in the input (e.g. `v: Annotated[int, Meta(ge=18)] = 0`),
            such a default returns NODEFAULT. Note that msgspec still applies that
            default when the field is absent from the input.
        guess_default (bool): False by default. If True, a field that has no
            accepted default falls back to a value guessed from its typehint
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any | NODEFAULT: default value of given field, or a guessed value when
            allowed. NODEFAULT if the field has no default, if its factory can't
            be constructed or needs the instance, or if the default value is not
            accepted with `validate=True`

    Raises:
        AttributeError: if failed
    """
    attrs_class = unwrap_model(model) if unwrap else model
    try:
        attributes = attrs_class.__attrs_attrs__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid attrs class')
    for attribute in attributes:
        if attribute.name == name:
            break
    else:
        raise AttributeError(f'Type {model} has no field with name="{name}"')

    # attrs is installed for sure, the model is one of its classes. Imported
    # here so the library does not depend on it
    import attr

    try:
        default_obj = attribute.default
    except AttributeError:
        # an object that is not a real field carries no default, which is what
        # `attrs.NOTHING` means as well
        default_obj = attr.NOTHING

    try:
        # Only an `attrs.Factory` carries these: a plain value and the
        # `attrs.NOTHING` of a required field do not
        takes_self = default_obj.takes_self
        factory = default_obj.factory
    except AttributeError:
        value = default_obj
    else:
        if takes_self:
            # the factory reads the instance being built, which does not exist
            return NODEFAULT
        try:
            value = factory()
        except Exception:
            return NODEFAULT

    if value is attr.NOTHING:
        # a required field, there is no value to write into the input
        if not guess_default:
            return NODEFAULT
        typehint = field_typehint(model, name, object, unwrap=unwrap)
        return get_default(typehint, guess_default=True)

    if validate:
        typehint = field_typehint(model, name, object, unwrap=unwrap)
        if not default_is_accepted(typehint, value):
            return NODEFAULT
    return value


def get_attrs_model_changes(old, new):
    """
    Compare two instances of the same attrs class field by field

    Args:
        old: Original attrs instance
        new: New attrs instance of the same class

    Returns:
        dict[str, Any]: Dict of `{field_name: value_from_new}` for the fields
            whose values differ. Nested objects are compared by `==`, not
            recursed into

    Raises:
        TypeError: If `old` is not an attrs instance
        AttributeError: If `new` is a different class missing a field
    """
    try:
        attributes = old.__class__.__attrs_attrs__
    except AttributeError:
        raise TypeError(
            f"Both arguments must be attrs instances, "
            f"got {type(old).__name__}"
        )

    result = {}
    for attribute in attributes:
        old_value = getattr(old, attribute.name)
        new_value = getattr(new, attribute.name)
        if old_value != new_value:
            result[attribute.name] = new_value
    return result
