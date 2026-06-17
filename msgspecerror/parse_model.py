from typing import Any, Dict, TypeVar

T = TypeVar('T')


def get_model_changes(old: T, new: T) -> Dict[str, Any]:
    """
    Compare two msgspec Struct instances field-by-field at the top level.

    Returns a dict mapping each differing field name to its value from ``new``.
    Nested objects are compared by value equality (``==``), not recursed into
    for further sub-diffs.

    Args:
        old: Original msgspec Struct instance.
        new: New msgspec Struct instance.

    Returns:
        dict[str, Any]: Dict of ``{field_name: value_from_new}`` for fields
            where ``getattr(old, field) != getattr(new, field)``.

    Raises:
        TypeError: If ``old`` is not a msgspec Struct instance.
        AttributeError: If ``new`` is a different struct type missing fields
            that ``old`` has.
    """
    try:
        fields = old.__class__.__struct_fields__
    except AttributeError:
        raise TypeError(
            f"Both arguments must be msgspec.Struct instances, "
            f"got {type(old).__name__}"
        )

    result = {}
    for f in fields:
        vo = getattr(old, f)
        vn = getattr(new, f)
        if vo != vn:
            result[f] = vn
    return result
