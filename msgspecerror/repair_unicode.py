from collections import deque
from typing import Any

from .const import ErrorType
from .parse_error import ErrorInfo


def _collect_unicode_replace(obj: Any) -> "list[ErrorInfo]":
    """
    Iteratively finds all locations within a Python object that contain a
    Unicode replacement character (U+FFFD). Use this after decoding JSON data
    with ``errors='replace'`` to enumerate the fields that had invalid UTF-8.

    Args:
        obj: A Python object decoded from JSON (or equivalent) to scan.

    Returns:
        A list of ``ErrorInfo`` objects, one per string field that contains
        at least one U+FFFD replacement character. Each error has
        ``type=ErrorType.UNICODE_DECODE_ERROR`` and a ``loc`` pointing to the
        field path.
    """
    obj_type = type(obj)

    # --- Guard Clauses for Non-Container Root Objects ---
    if obj_type is str:
        if '\ufffd' in obj:
            error = ErrorInfo(
                'Invalid UTF-8 sequence in root',
                type=ErrorType.UNICODE_DECODE_ERROR, loc=())
            return [error]
        else:
            return []

    if obj_type is not dict and obj_type is not list:
        return []

    # --- Main Iterative Traversal for Containers ---
    errors = []
    stack = deque()
    stack.append((obj, ()))
    replacement_char = '\ufffd'

    while 1:
        new_stack = deque()
        for container, path in stack:
            if type(container) is dict:
                for key, value in container.items():
                    value_type = type(value)

                    if type(key) is str and replacement_char in key:
                        error = ErrorInfo(
                            'Invalid UTF-8 sequence in dict key',
                            type=ErrorType.UNICODE_DECODE_ERROR, loc=path + (key,))
                        errors.append(error)

                    if value_type is str:
                        if replacement_char in value:
                            error = ErrorInfo(
                                'Invalid UTF-8 sequence in dict value',
                                type=ErrorType.UNICODE_DECODE_ERROR, loc=path + (key,))
                            errors.append(error)
                    elif value_type is dict or value_type is list:
                        new_stack.append((value, path + (key,)))
            else:
                for i, item in enumerate(container):
                    item_type = type(item)

                    if item_type is str:
                        if replacement_char in item:
                            error = ErrorInfo(
                                'Invalid UTF-8 sequence in list item',
                                type=ErrorType.UNICODE_DECODE_ERROR, loc=path + (i,))
                            errors.append(error)
                    elif item_type is dict or item_type is list:
                        new_stack.append((item, path + (i,)))

        if new_stack:
            stack = new_stack
        else:
            break

    return errors
