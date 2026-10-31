"""
Field access for a TypedDict model

A TypedDict is a plain `dict` at runtime, so a key is read straight from the
instance and there is no rename mechanism.
"""
from msgspec import NODEFAULT

from .parse_type import default_is_accepted, field_typehint, get_default, unwrap_model

# Marks a key that the instance does not hold, so an absent key differs from a
# key whose value is `None`
_MISSING = object()


def _annotations(model, unwrap=True):
    """
    Read the annotations of a TypedDict, the key list msgspec decodes

    Args:
        model: A TypedDict
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        dict: The annotations of `model`

    Raises:
        AttributeError: if `model` is not a TypedDict
    """
    typed_dict = unwrap_model(model) if unwrap else model
    # A caller that got here already believes this is a TypedDict, so the
    # annotations are read directly; a type without them is not one
    try:
        return typed_dict.__annotations__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid TypedDict')


def get_typeddict_field_typehint(model, name, unwrap=True):
    """
    Get the typehint of a TypedDict key

    Args:
        model: A TypedDict
        name (str): Key name
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of the key, a `NotRequired`/`Required` marker peeled off

    Raises:
        AttributeError: if failed
    """
    if name not in _annotations(model, unwrap=unwrap):
        raise AttributeError(f'Type {model} has no field with name="{name}"')
    return field_typehint(model, name, object, unwrap=unwrap)


def get_typeddict_field_default(model, name, validate=False, guess_default=False, unwrap=True):
    """
    Get the default of a TypedDict field

    Args:
        model: A TypedDict
        name (str): Key name
        validate (bool): False by default. If True, a guessed value is checked
            against the field typehint with `msgspec.convert`. A TypedDict has no
            default value of its own, so a guess is the only value there is.
        guess_default (bool): False by default. If True, a field returns a value
            guessed from its typehint
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any | NODEFAULT: NODEFAULT unless a value is guessed

    Raises:
        AttributeError: if failed
    """
    if name not in _annotations(model, unwrap=unwrap):
        raise AttributeError(f'Type {model} has no field with name="{name}"')

    # `NotRequired` (or `total=False`) only says the key may be absent, msgspec
    # leaves it out instead of filling a value, so there is no default. The only
    # value this can answer with is one guessed from the typehint.
    if not guess_default:
        return NODEFAULT
    typehint = field_typehint(model, name, object, unwrap=unwrap)
    value = get_default(typehint, guess_default=True)
    if value is NODEFAULT:
        return NODEFAULT
    if validate:
        if not default_is_accepted(typehint, value):
            return NODEFAULT
    return value


def get_typeddict_model_changes(old, new):
    """
    Compare two instances of the same TypedDict key by key

    Args:
        old (dict): Original instance
        new (dict): New instance of the same TypedDict

    Returns:
        dict[str, Any]: Dict of `{key: value_from_new}` for the keys whose values
            differ, `new` holding a key `old` does not included. A key dropped in
            `new` has no new value and is not reported. Nested objects are
            compared by `==`, not recursed into

    Raises:
        TypeError: If `old` is not a dict
        AttributeError: If `new` is not a dict
    """
    if not isinstance(old, dict):
        raise TypeError(
            f"Both arguments must be dict instances, "
            f"got {type(old).__name__}"
        )

    result = {}
    for key, new_value in new.items():
        if old.get(key, _MISSING) != new_value:
            result[key] = new_value
    return result
