import datetime
import decimal
import sys
import typing
import uuid
from typing import Any, ForwardRef, Generic, TypeVar, Union

try:
    from types import GenericAlias as _types_GenericAlias
except ImportError:
    # Python 3.8, builtin generics like `list[int]` are not supported there
    _types_GenericAlias = None

import msgspec
from msgspec import NODEFAULT, UnsetType, ValidationError, convert
from msgspec._utils import _CONCRETE_TYPES, _apply_params, _get_class_mro_and_typevar_mappings
from msgspec.inspect import _is_enum
from typing_extensions import Literal, NotRequired, Required

# Backport typing alias in msgspec._utils, so we can keep supporting py3.8
# <-- Start of the copied code -->
try:
    from types import UnionType as _types_UnionType  # type: ignore
except Exception:
    _types_UnionType = type("UnionType", (), {})  # type: ignore

try:
    from typing import Final, TypeAliasType as _TypeAliasType  # type: ignore
except Exception:
    _TypeAliasType = type("TypeAliasType", (), {})  # type: ignore

try:
    from typing_extensions import _AnnotatedAlias
except Exception:
    try:
        from typing import _AnnotatedAlias
    except Exception:
        _AnnotatedAlias = None

# The `is_class` argument was new in 3.11, but was backported to 3.9 and 3.10.
# It's _likely_ to be available for 3.9/3.10, but may not be. Easiest way to
# check is to try it and see.
try:
    typing.ForwardRef("Foo", is_class=True)
except TypeError:

    def _forward_ref(value):
        return typing.ForwardRef(value, is_argument=False)

else:

    def _forward_ref(value):
        return typing.ForwardRef(value, is_argument=False, is_class=True)

# Python 3.13 adds a new mandatory type_params kwarg to _eval_type
if sys.version_info >= (3, 13):

    def _eval_type(t, globalns, localns):
        return typing._eval_type(t, globalns, localns, ())
elif sys.version_info < (3, 10):

    def _eval_type(t, globalns, localns):
        try:
            return typing._eval_type(t, globalns, localns)
        except TypeError as e:
            try:
                from eval_type_backport import eval_type_backport
            except ImportError:
                raise TypeError(
                    f"Unable to evaluate type annotation {t.__forward_arg__!r}. If you are making use "
                    "of the new typing syntax (unions using `|` since Python 3.10 or builtins subscripting "
                    "since Python 3.9), you should either replace the use of new syntax with the existing "
                    "`typing` constructs or install the `eval_type_backport` package."
                ) from e

            return eval_type_backport(
                t,
                globalns,
                localns,
                try_default=False,
            )
else:
    _eval_type = typing._eval_type


# <-- End of the copied code -->

def unwrap_typehint(t):
    """
    Unwrap the type wrappers of a typehint: Annotated, NewType, Final, TypeAliasType

    Unlike `origin_args`, a parametrized generic alias like `Box[int]` is returned as
    it is, its parameters affect the typehints inside it.

    Args:
        t: Any typehint

    Returns:
        Any: The unwrapped typehint
    """
    while True:
        if type(t) is _AnnotatedAlias:
            # Annotated[T, metadata...], the wrapped typehint is the origin
            t = t.__origin__
        elif getattr(t, '__supertype__', None) is not None:
            # NewType('X', T), the wrapped typehint is the supertype
            t = t.__supertype__
        elif getattr(t, '__origin__', None) is Final and getattr(t, '__args__', None):
            # Final[T]
            t = t.__args__[0]
        elif type(t) is _TypeAliasType:
            # type X = T
            t = t.__value__
        else:
            return t


def origin_args(t):
    """
    A simplified version of msgspec.inspect._origin_args_metadata
    Get origin and args of given typehint.
    """
    # Strip wrappers (Annotated, NewType, Final) until we hit a concrete type
    while True:
        try:
            origin = _CONCRETE_TYPES.get(t)
            if origin is not None:
                args = None
                break
        except TypeError:
            # t is not hashable
            pass

        origin = getattr(t, "__origin__", None)
        if origin is not None:
            if type(t) is _AnnotatedAlias:
                t = origin
            elif origin == Final:
                t = t.__args__[0]
            elif type(origin) is _TypeAliasType:
                t = origin.__value__[t.__args__]
            else:
                args = getattr(t, "__args__", None)
                origin = _CONCRETE_TYPES.get(origin, origin)
                break
        else:
            supertype = getattr(t, "__supertype__", None)
            if supertype is not None:
                t = supertype
            elif type(t) is _TypeAliasType:
                t = t.__value__
            else:
                origin = t
                args = None
                break

    origin_type = type(origin)
    if origin_type is _types_UnionType:
        args = origin.__args__
        origin = Union
    if origin is tuple:
        # Handle an annoying compatibility issue:
        # - Tuple[()] has args == ((),)
        # - tuple[()] has args == ()
        if args == ((),):
            args = ()
    return origin, args


try:
    _msgspec_is_struct_type = msgspec.inspect._is_struct
except AttributeError:
    # since 0.20.0, Struct-like check utilities are moved
    _msgspec_is_struct_type = msgspec.inspect.is_struct_type


def is_struct_type(t):
    """
    Equivalent to `msgspec.inspect.is_struct_type`, a parametrized generic alias
    of a Struct (e.g. `Box[int]`) is a struct type as well

    Args:
        t: Any typehint

    Returns:
        bool: True if `t` is a msgspec.Struct type
    """
    if _msgspec_is_struct_type(t):
        return True
    origin = getattr(t, '__origin__', None)
    return origin is not None and _msgspec_is_struct_type(origin)


def unwrap_model(model):
    """
    Unwrap a parametrized generic alias to its origin class

    Args:
        model: A model class, or a parametrized generic alias of one (e.g. `Box[int]`)

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


def is_struct_like(t, unwrap=True):
    """
    Name the model kind of a typehint, the kinds `msgspec.inspect` tells apart

    Equivalent to the followings, functions are from msgspec.inspect

    _is_struct(t)
    or _is_typeddict(t)
    or _is_dataclass(t)
    or _is_attrs(t)
    or _is_namedtuple(t)

    Args:
        t: Any typehint
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `t` is already unwrapped

    Returns:
        str: 'struct', 'dataclass', 'typeddict', 'namedtuple' or 'attrs', and ''
            when `t` is none of them
    """
    # _is_struct()
    if is_struct_type(t):
        return 'struct'
    if unwrap:
        # A parametrized generic alias does not forward the class attributes the
        # checks below read (`Box[int].__dataclass_fields__` is missing), so the
        # origin class is what is actually judged
        t = unwrap_model(t)
    # _is_dataclass()
    if hasattr(t, "__dataclass_fields__"):
        return 'dataclass'
    try:
        # _is_typeddict()
        if issubclass(t, dict) and hasattr(t, "__total__"):
            return 'typeddict'
        # _is_namedtuple(t)
        if issubclass(t, tuple) and hasattr(t, "_fields"):
            return 'namedtuple'
    except TypeError:
        return ''
    # _is_attrs()
    if hasattr(t, "__attrs_attrs__"):
        return 'attrs'
    return ''


def get_default(t, guess_default=False, return_obj=False):
    """
    Calculates a default value for a given type hint

    A value is only a default if msgspec itself accepts it. The candidate value is
    checked by `convert` for a wrapped typehint like `Annotated[int, Meta(ge=18)]`,
    whose guessed `0` is rejected and NODEFAULT is returned instead. A typehint that
    needs no unwrapping (`int`, `list`, a struct class...) accepts the candidate by
    definition, so `convert` is skipped for it.

    Args:
        t: Any typehint
        guess_default (bool):
            False by default for safety.
            If True, invalid primitive values (like int, str, bytes) will be replaced by their "zero"
                or "empty" values (0, "", b"").
            If False, a validation error for such a type will cause the repair to fail,
                unless an explicit default is set on the model field.
            This does not affect:
            - container types (e.g., a list field with a non-list value will still default to `[]`),
            - Optional fields (which default to `None`)
            - Enums/Literals (which are never guessed)
        return_obj (bool):
            False to return default value in json
            True to convert default value to type `t`

    Returns:
        Any: If `t` can be default constructed, return a value accepted by
            `msgspec.convert(value, type=t)`
            Otherwise return NODEFAULT
    """
    origin, args = origin_args(t)

    # --- Most common types first for performance ---

    # Handle `None` (very common in Optional[T])
    if origin is None or origin is type(None):
        value = None

    # Handle `str`
    elif origin is str:
        value = "" if guess_default else NODEFAULT

    # Handle `int`
    elif origin is int:
        value = 0 if guess_default else NODEFAULT

    # Handle `list` (the most common collection)
    elif origin is list:
        value = []

    # Handle `dict`
    elif origin is dict:
        value = {}

    # Handle `bool`
    elif origin is bool:
        value = False if guess_default else NODEFAULT

    # --- Structured object types ---

    # All struct-like types default to an empty object (`{}`), representing a
    # valid but empty structure, which is useful for error correction.
    elif is_struct_like(origin, unwrap=False):
        try:
            # The object itself is what `return_obj` asks for, constructing it also
            # tells whether the object can be default constructed at all
            value = convert({}, origin)
        except Exception:
            # TypeError or ValidationError when object cannot be default constructed
            return NODEFAULT
        if not return_obj:
            value = {}

    # --- Union types ---

    elif origin is Union:
        # If None is a member of the union (i.e., Optional[T]), it's the best default.
        # The member defaults are validated by their own `get_default` calls, and a
        # union itself can not carry constraints.
        for arg in args:
            if arg is UnsetType:
                continue
            if arg is None or arg is type(None):
                return None

        # Otherwise, recursively find the first type in the union that can provide a default.
        for arg in args:
            if arg is UnsetType:
                continue
            default = get_default(arg, guess_default, return_obj)
            if default is not NODEFAULT:
                return default

        # If no type in the union can be defaulted, the union itself cannot.
        return NODEFAULT

    # --- Other container types ---

    elif origin is set:
        value = set()

    elif origin is frozenset:
        value = frozenset()

    elif origin is tuple:
        if not args or Ellipsis in args:
            # Bare `tuple`, a variable-length tuple or an empty `Tuple[()]`: `()` is always valid
            value = ()
        else:
            # Fixed-length tuple: an empty tuple fails validation when items are required,
            # so guess each item by its own type
            items = [get_default(arg, guess_default, return_obj) for arg in args]
            if any(item is NODEFAULT for item in items):
                return NODEFAULT
            value = tuple(items)

    # --- Types that cannot be safely guessed ---

    # We cannot safely pick a default value from an Enum or a set of Literals.
    elif _is_enum(origin) or origin is Literal:
        value = NODEFAULT

    # --- Less common scalar and special types ---

    elif origin is float:
        value = 0.0 if guess_default else NODEFAULT

    elif origin is bytes:
        value = b"" if guess_default else NODEFAULT

    elif origin is bytearray:
        value = bytearray()

    elif origin is memoryview:
        # A memoryview of an empty bytes object is a safe default.
        value = memoryview(b"")

    # For complex objects, we avoid guessing a default (e.g., datetime.now())
    # as it can have side effects or be misleading.
    elif origin in (
            datetime.datetime,
            datetime.time,
            datetime.date,
            datetime.timedelta,
            uuid.UUID,
            decimal.Decimal,
            msgspec.Raw,
            msgspec.msgpack.Ext,
    ):
        value = NODEFAULT

    # --- Abstract and generic types ---

    elif origin is Any:
        value = NODEFAULT

    elif isinstance(origin, TypeVar):
        # If the TypeVar is bound to another type, try to get the default of that bound type.
        if origin.__bound__ is not None:
            value = get_default(origin.__bound__, guess_default)
        else:
            value = NODEFAULT

    # --- Fallback ---

    # If the type is not recognized by any of the guards above, it's treated as a
    # custom or unknown type for which we cannot provide a default.
    else:
        value = NODEFAULT

    if value is NODEFAULT:
        return NODEFAULT

    # `origin_args` unwraps the typehint, and a constraint of `Annotated`, `Final`,
    # `NewType` or a type alias still applies to the value, so only a typehint that
    # is already the origin itself (`int`, `list`, a struct class...) is known to
    # accept the value without asking msgspec
    if t is origin:
        return value
    try:
        convert(value, type=t)
    except ValidationError:
        # The value doesn't meet the constraints of `t`, e.g. the guessed `0` is
        # rejected by `Annotated[int, Meta(ge=18)]`, so it can't be used as a default
        return NODEFAULT
    except TypeError:
        # `t` is not a typehint that msgspec supports, it can neither judge the value
        # nor decode such a model, we keep the value as the best we have
        pass
    return value


# Python 3.14 computes the annotations lazily (PEP 649),
# see `field_typehint`
IS_PY314_PLUS = sys.version_info >= (3, 14)


# ?? annotations of a model field ?????????????????????????????????????????

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


def field_typehint(model, field_name, stop_at, unwrap=True):
    """
    Get the annotation a model declares for a field

    The annotation is resolved the same way msgspec resolves it for the decoder:
    a forward reference (including the ones nested in other type hints) is
    evaluated against the namespace of the class that declares the field, and a
    TypeVar is substituted for the parametrized class. Annotations that contain
    neither are returned as-is, without touching the rest of the class.

    Args:
        model: A model class, or a parametrized generic alias of one
        field_name (str): Field name (Python attribute)
        stop_at: Class that ends the MRO walk, `msgspec.Struct` for a struct and
            `object` for the other model kinds
        unwrap (bool): True by default, a parametrized generic alias is unwrapped
            to its origin class. Pass False when `model` is already unwrapped

    Returns:
        Any: typehint of model.field_name

    Raises:
        AttributeError: if failed
    """
    # The alias itself is kept for the TypeVar mapping below, only the MRO walk
    # reads the origin class
    model_class = unwrap_model(model) if unwrap else model
    for cls in model_class.__mro__:
        if cls is stop_at:
            break
        if cls in (Generic, object):
            continue

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
        if getattr(anno, '__origin__', None) in (Required, NotRequired):
            # a TypedDict marker, it tells whether the key is required and is
            # not part of the type msgspec decodes the value as
            try:
                anno = anno.__args__[0]
            except (AttributeError, IndexError):
                # a hand-made object that only looks like the wrapper, the
                # annotation is used as it is
                pass

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

    # this shouldn't happen, `field_name` comes from the model field list
    raise AttributeError(f'Type {model} has no field with name="{field_name}"')


def default_is_accepted(typehint, value):
    """
    Check whether a model default is accepted when it is written into the input

    msgspec does not validate model defaults, a default may be rejected when it is
    explicitly present in the input (e.g. `v: Annotated[int, Meta(ge=18)] = 0`),
    while msgspec still applies it when the field is absent.

    Args:
        typehint: Typehint of the field
        value (Any): The default value

    Returns:
        bool: True if the value is accepted
    """
    try:
        convert(value, type=typehint)
    except ValidationError:
        return False
    except TypeError:
        # the field typehint is not a type msgspec can convert to, we can't
        # judge the value and keep it as the best we have
        return True
    return True
