import sys
from typing import ForwardRef, Generic, Type, TypeVar

try:
    from types import GenericAlias as _types_GenericAlias
except ImportError:
    # Python 3.8, builtin generics like `list[int]` are not supported there
    _types_GenericAlias = None

from msgspec import NODEFAULT, Struct, UNSET, ValidationError, convert
from msgspec._core import Factory
from msgspec._utils import _apply_params, _get_class_mro_and_typevar_mappings
from typing_extensions import Literal

from .parse_type import _AnnotatedAlias, _eval_type, _forward_ref

# Python 3.14 computes the annotations lazily (PEP 649),
# see `_get_own_annotations`
IS_PY314_PLUS = sys.version_info >= (3, 14)


def _unwrap_struct_model(model):
    """
    Unwrap a parametrized generic alias to its origin class

    Args:
        model: A msgspec.Struct class, or a parametrized generic alias of one (e.g. `Box[int]`)

    Returns:
        type: The origin class of `model`, `model` itself when it is already a class
    """
    # A class is the common case, `isinstance()` is much cheaper than a missing
    # `__origin__` attribute lookup. A PEP 585 generic alias (`list[int]`) also
    # passes the `isinstance()` check on CPython, so its `__origin__` is still read.
    if isinstance(model, type) and type(model) is not _types_GenericAlias:
        return model
    origin = getattr(model, '__origin__', None)
    if origin is not None and isinstance(origin, type):
        return origin
    return model


def get_field_name(model: Type[Struct], name: str) -> str:
    """
    Resolve a name to the field name (Python attribute name).

    Accepts both field names and encode names (serialization names defined via ``field(name=...)``).

    Args:
        model: Subclass of msgspec.Struct, or a parametrized generic alias of one (e.g. `Box[int]`)
        name (str): Field name or encode name

    Returns:
        str: field_name

    Raises:
        AttributeError: if failed
    """
    # 1. Get name lists from magic attributes.
    struct_model = _unwrap_struct_model(model)
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


def _default_is_accepted(model: Type[Struct], name: str, value):
    """
    Check whether a struct default is accepted when it is written into the input

    msgspec does not validate struct defaults, a default may be rejected when it is
    explicitly present in the input (e.g. `v: Annotated[int, Meta(ge=18)] = 0`),
    while msgspec still applies it when the field is absent.

    Args:
        model (type): Subclass of msgspec.Struct
        name (str): Field name
        value (Any): The default value

    Returns:
        bool: True if the value is accepted
    """
    try:
        convert(value, type=get_field_typehint(model, name))
    except ValidationError:
        return False
    except TypeError:
        # the field typehint is not a type msgspec can convert to, we can't
        # judge the value and keep it as the best we have
        return True
    return True


def get_field_default(model: Type[Struct], name: str, validate: bool = False):
    """
    Get default and default_factory of model.field_name

    Args:
        model: Subclass of msgspec.Struct, or a parametrized generic alias of one (e.g. `Box[int]`)
        name (str): Field name (Python attribute) or encode name (serialization name)
        validate (bool): False by default. If True, the default value is checked
            against the field typehint with `msgspec.convert`. msgspec does not
            validate struct defaults, a default may be rejected when it is
            explicitly present in the input (e.g. `v: Annotated[int, Meta(ge=18)] = 0`),
            such a default returns NODEFAULT. Note that msgspec still applies that
            default when the field is absent from the input.

    Returns:
        Any | NODEFAULT:
            default value of given field
            NODEFAULT if field doesn't have a default
            NODEFAULT if default factory can't be constructed
            NODEFAULT if the default value is not accepted with `validate=True`

    Raises:
        AttributeError: if failed
    """
    # 1. Get name lists from magic attributes.
    struct_model = _unwrap_struct_model(model)
    try:
        field_names = struct_model.__struct_fields__
        defaults = struct_model.__struct_defaults__
    except AttributeError:
        raise AttributeError(f'Type {model} is not a valid msgspec.Struct')

    # 2. Resolve the name to a field name (supports both field_name and encode_name).
    field_name = get_field_name(model, name)

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
        if validate and not _default_is_accepted(model, field_name, value):
            return NODEFAULT
        return value

    return NODEFAULT


def _scan_annotation(tp):
    """
    Scan a type annotation for forward references and TypeVars

    The scan tells whether the annotation needs to be evaluated by `_eval_type`
    (forward references) and whether its TypeVars need to be substituted.

    Args:
        tp: Any typehint

    Returns:
        tuple[bool, bool]: (contains a forward reference, contains a TypeVar)
    """
    tp_type = type(tp)
    if tp_type is str:
        # a str annotation is a quoted type, i.e. a forward reference;
        # `type()` is used instead of `isinstance()` for speed, annotations are
        # exact `str`, never a str subclass
        return True, False
    if isinstance(tp, ForwardRef):
        return True, False
    if isinstance(tp, TypeVar):
        return False, True
    args = getattr(tp, '__args__', None)
    if not args:
        return False, False
    if getattr(tp, '__origin__', None) is Literal:
        # the args of `Literal` are values, a str arg is not a forward reference
        return False, False
    # `Annotated[T, metadata...]`, only the first arg is a type hint. The identity
    # check is much faster than `hasattr(tp, '__metadata__')`, which falls through
    # `_GenericAlias.__getattr__`
    if tp_type is _AnnotatedAlias or (_AnnotatedAlias is None and hasattr(tp, '__metadata__')):
        args = args[:1]

    has_ref = False
    has_typevar = False
    for arg in args:
        arg_has_ref, arg_has_typevar = _scan_annotation(arg)
        has_ref |= arg_has_ref
        has_typevar |= arg_has_typevar
        if has_ref and has_typevar:
            break
    return has_ref, has_typevar


def _contains_typevar(tp):
    """
    Recursively check if a type annotation contains a TypeVar.
    """
    if isinstance(tp, TypeVar):
        return True
    args = getattr(tp, '__args__', None)
    if args:
        for arg in args:
            if _contains_typevar(arg):
                return True
    return False


def get_field_typehint(model: Type[Struct], name: str):
    """
    Get typehint or annotation of model.field_name

    The annotation is resolved the same way msgspec resolves it for the decoder:
    a forward reference (including the ones nested in other type hints) is
    evaluated against the namespace of the class that declares the field, and a
    TypeVar is substituted for the parametrized class. Annotations that contain
    neither are returned as-is, without touching the rest of the class.

    Args:
        model: Subclass of msgspec.Struct, or a parametrized generic alias of one (e.g. `Box[int]`)
        name (str): Field name (Python attribute) or encode name (serialization name)

    Returns:
        Any: typehint of model.field_name

    Raises:
        AttributeError: if failed
    """
    # Resolve the name to a field name (supports both field_name and encode_name).
    field_name = get_field_name(model, name)

    for cls in _unwrap_struct_model(model).__mro__:
        if cls in (Generic, object):
            continue
        # A classic MRO of msgspec model would be like
        # (<class '__main__.Team'>, <class 'msgspec.Struct'>, <class 'msgspec._core._StructMixin'>, <class 'object'>)
        # Digging into msgspec.Struct won't get more, we just stop
        if cls is Struct:
            break

        if IS_PY314_PLUS:
            # Python 3.14 (PEP 649) computes the annotations on first access,
            # they never land in the class dict
            annotations = cls.__annotations__
        else:
            # The class dict holds the annotations of the class itself, the
            # attribute can not be used: before Python 3.10 it falls back to a
            # base class and returns another class's annotations
            annotations = cls.__dict__.get('__annotations__')
            if annotations is None:
                # the class has no annotation of its own, check its bases
                continue
        anno = annotations.get(field_name, NODEFAULT)
        if anno is NODEFAULT:
            continue

        has_ref, has_typevar = _scan_annotation(anno)
        if has_ref:
            # convert forward ref, module globals and the class body are the
            # namespaces the decoder resolves the annotation against
            cls_locals = dict(vars(cls))
            cls_globals = getattr(sys.modules.get(cls.__module__, None), '__dict__', {})
            if type(anno) is str:
                anno = _forward_ref(anno)
            anno = _eval_type(anno, cls_globals, cls_locals)
            # a TypeVar may only become visible after the refs are evaluated
            if not has_typevar:
                has_typevar = _contains_typevar(anno)

        if anno is None:
            # msgspec treats an annotation of `None` as `NoneType`
            anno = type(None)

        # return directly if it doesn't contain TypeVar
        if not has_typevar:
            return anno

        # convert TypeVar, msgspec substitutes it per parametrized class
        _, typevar_mappings = _get_class_mro_and_typevar_mappings(model)
        mapping = typevar_mappings.get(cls)
        if mapping:
            anno = _apply_params(anno, mapping)
        return anno

    # this shouldn't happen, `field_name` comes from `__struct_fields__`
    raise AttributeError(f'Type {model} has no field with name="{name}"')
