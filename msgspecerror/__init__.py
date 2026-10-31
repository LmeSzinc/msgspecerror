from .const import DECODE_ERRORS, ENCODE_ERRORS, ErrorType
from .parse_anno import (
    get_class_annotation, get_class_annotation_dict, get_msgspec_annotation, get_msgspec_annotation_dict
)
from .parse_ctx import ErrorCtx
from .parse_error import ErrorInfo, ErrorInfoDetail, parse_msgspec_error
from .parse_model import get_field_default, get_field_name, get_field_typehint, get_model_changes
from .parse_type import get_default
from .repair import convert_with_default, load_json_with_default, load_msgpack_with_default
from .with_error import (
    convert_with_error, convert_with_error_detail, load_json_with_error, load_json_with_error_detail,
    load_msgpack_with_error, load_msgpack_with_error_detail, resolve_error
)

# Re-exported names. Listing them here is what marks the imports above as used,
# so they do not need the `import X as X` form that ruff's F401 asks for.
__all__ = [
    'DECODE_ERRORS',
    'ENCODE_ERRORS',
    'ErrorCtx',
    'ErrorInfo',
    'ErrorInfoDetail',
    'ErrorType',
    'convert_with_default',
    'convert_with_error',
    'convert_with_error_detail',
    'get_class_annotation',
    'get_class_annotation_dict',
    'get_default',
    'get_field_default',
    'get_field_name',
    'get_field_typehint',
    'get_model_changes',
    'get_msgspec_annotation',
    'get_msgspec_annotation_dict',
    'load_json_with_default',
    'load_json_with_error',
    'load_json_with_error_detail',
    'load_msgpack_with_default',
    'load_msgpack_with_error',
    'load_msgpack_with_error_detail',
    'parse_msgspec_error',
    'resolve_error',
]

__version__ = version = '0.22.0.1'
__version_tuple__ = version_tuple = (0, 22, 0, 1)
