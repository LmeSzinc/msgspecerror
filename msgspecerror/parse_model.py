"""
Field access for every model kind msgspec can decode into

The model kinds do not share a representation: a struct has encode names and can
be decoded from an array, a NamedTuple is always decoded from an array, a
TypedDict is a plain dict, and a dataclass or attrs class is an object with
attributes. Each kind keeps its own module, and this one picks the right one
from `is_struct_like()`.
"""
from .parse_model_attrs import get_attrs_field_default, get_attrs_field_typehint, get_attrs_model_changes
from .parse_model_dataclass import (
    get_dataclass_field_default, get_dataclass_field_typehint, get_dataclass_model_changes
)
from .parse_model_namedtuple import (
    get_namedtuple_field_default, get_namedtuple_field_typehint, get_namedtuple_model_changes
)
from .parse_model_struct import (
    get_struct_field_default, get_struct_field_name, get_struct_field_typehint, get_struct_model_changes,
    is_struct_arraylike
)
from .parse_model_typeddict import (
    get_typeddict_field_default, get_typeddict_field_typehint, get_typeddict_model_changes
)
from .parse_type import is_struct_like


def is_arraylike(model, known_struct_type=None, unwrap=True):
    """
    Check whether a model is decoded from an array instead of an object

    An `array_like=True` struct and a NamedTuple are encoded as arrays, so an
    error location addresses their fields by position.

    Args:
        model: A model class, or a parametrized generic alias of one
        known_struct_type (str): Defaults to None. The model kind as returned by
            `is_struct_like()`, when the caller already knows it
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        bool: True for an `array_like=True` struct or a NamedTuple
    """
    if known_struct_type is None:
        known_struct_type = is_struct_like(model, unwrap=unwrap)
    if known_struct_type == 'struct':
        return is_struct_arraylike(model, unwrap=unwrap)
    return known_struct_type == 'namedtuple'


def get_field_name(model, name, known_struct_type=None, unwrap=True):
    """
    Resolve a name to the field name (Python attribute name)

    Only a `msgspec.Struct` renames a field, so only a struct can be asked: the
    other kinds take the attribute name as it is, and there is nothing to
    resolve.

    Args:
        model: A `msgspec.Struct` class, or a parametrized generic alias of one
        name (str): Field name or encode name (serialization name)
        known_struct_type (str): Defaults to None. The model kind as returned by
            `is_struct_like()`, when the caller already knows it
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        str: field name

    Raises:
        AttributeError: if `model` is not a struct, or has no such field
    """
    if known_struct_type is not None and known_struct_type != 'struct':
        raise AttributeError(f'Type {model} has no field name to resolve, it is a {known_struct_type}')

    return get_struct_field_name(model, name, unwrap=unwrap)


def get_field_typehint(model, name, known_struct_type=None, unwrap=True):
    """
    Get the typehint of a field of any model kind msgspec can decode into

    Args:
        model: A `msgspec.Struct`, dataclass, TypedDict, NamedTuple or attrs
            class, or a parametrized generic alias of one
        name (str | int): Field name, encode name (a struct field only, the other
            kinds can not be renamed), or the position of the field in a model
            decoded from an array (`array_like=True` struct, NamedTuple)
        known_struct_type (str): Defaults to None. The model kind as returned by
            `is_struct_like()`, when the caller already knows it: `is_struct_like`
            is skipped then. A wrong value reads the wrong kind
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of the field

    Raises:
        AttributeError: if failed
    """
    if known_struct_type is None:
        known_struct_type = is_struct_like(model, unwrap=unwrap)

    if known_struct_type == 'struct':
        return get_struct_field_typehint(model, name, unwrap=unwrap)
    if known_struct_type == 'dataclass':
        return get_dataclass_field_typehint(model, name, unwrap=unwrap)
    if known_struct_type == 'typeddict':
        return get_typeddict_field_typehint(model, name, unwrap=unwrap)
    if known_struct_type == 'namedtuple':
        return get_namedtuple_field_typehint(model, name, unwrap=unwrap)
    if known_struct_type == 'attrs':
        return get_attrs_field_typehint(model, name, unwrap=unwrap)

    raise AttributeError(f'Type {model} is not a struct-like model')


def get_field_default(
        model, name, validate=False, guess_default=False, known_struct_type=None, unwrap=True
):
    """
    Get the default of a field of any model kind msgspec can decode into

    Args:
        model: A `msgspec.Struct`, dataclass, TypedDict, NamedTuple or attrs
            class, or a parametrized generic alias of one
        name (str | int): Field name, encode name (a struct field only, the other
            kinds can not be renamed), or the position of the field in a model
            decoded from an array (`array_like=True` struct, NamedTuple)
        validate (bool): False by default. If True, the default value is checked
            against the field typehint with `msgspec.convert`, and a default
            msgspec rejects as an explicit value returns NODEFAULT
        guess_default (bool): False by default. If True, a field that has no
            accepted default falls back to a value guessed from its typehint
        known_struct_type (str): Defaults to None. The model kind as returned by
            `is_struct_like()`, when the caller already knows it: `is_struct_like`
            is skipped then. A wrong value reads the wrong kind
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any | NODEFAULT: default value of given field, or a guessed value when
            allowed. NODEFAULT if the field has no default, or if its default can
            not be constructed

    Raises:
        AttributeError: if failed
    """
    if known_struct_type is None:
        known_struct_type = is_struct_like(model, unwrap=unwrap)

    if known_struct_type == 'struct':
        return get_struct_field_default(
            model, name, validate=validate, guess_default=guess_default, unwrap=unwrap)
    if known_struct_type == 'dataclass':
        return get_dataclass_field_default(
            model, name, validate=validate, guess_default=guess_default, unwrap=unwrap)
    if known_struct_type == 'typeddict':
        return get_typeddict_field_default(
            model, name, validate=validate, guess_default=guess_default, unwrap=unwrap)
    if known_struct_type == 'namedtuple':
        return get_namedtuple_field_default(
            model, name, validate=validate, guess_default=guess_default, unwrap=unwrap)
    if known_struct_type == 'attrs':
        return get_attrs_field_default(
            model, name, validate=validate, guess_default=guess_default, unwrap=unwrap)

    raise AttributeError(f'Type {model} is not a struct-like model')


def get_model_changes(old, new, known_struct_type=None):
    """
    Compare two instances of the same model field by field

    The values are read from the instances, so the kind only decides which field
    list is walked.

    Args:
        old: Original instance
        new: New instance of the same model
        known_struct_type (str): Defaults to None. The model kind as returned by
            `is_struct_like()`, when the caller already knows it: the lookup is
            skipped then. A wrong value reads the wrong kind

    Returns:
        dict[str, Any]: Dict of `{field_name: value_from_new}` for the fields
            whose values differ. Nested objects are compared by `==`, not
            recursed into. A TypedDict key that only one instance holds counts as
            a difference.

    Raises:
        TypeError: If `old` is not an instance of a model kind
        AttributeError: If `new` is a different model missing a field
    """
    if known_struct_type is None:
        # A TypedDict instance is a plain dict, its class carries none of the
        # model metadata, so the type alone can not name the kind
        known_struct_type = 'typeddict' if type(old) is dict else is_struct_like(type(old))

    if known_struct_type == 'struct':
        return get_struct_model_changes(old, new)
    if known_struct_type == 'dataclass':
        return get_dataclass_model_changes(old, new)
    if known_struct_type == 'typeddict':
        return get_typeddict_model_changes(old, new)
    if known_struct_type == 'namedtuple':
        return get_namedtuple_model_changes(old, new)
    if known_struct_type == 'attrs':
        return get_attrs_model_changes(old, new)

    raise TypeError(
        f"Both arguments must be instances of a struct-like model, got {type(old).__name__}"
    )
