# msgspecerror ![PyPI - Version](https://img.shields.io/pypi/v/msgspecerror)

**| English | [简体中文](README_cn.md) |**

[msgspec](https://github.com/msgspec/msgspec) is a high-performance serialization and validation library, but its `ValidationError` on failure only provides a plain-text message:

```
Expected `int`, got `str` - at `$.user.age`
```

[msgspecerror](https://github.com/LmeSzinc/msgspecerror) parses these plain-text exceptions into structured Python objects, providing error type, field path, and extra context — enabling you to **handle validation errors programmatically**.

msgspec can only report a placeholder for a dict: `$[...]` for a failing value, `$[...key]` for a failing key. msgspecerror walks the error path through the raw data to resolve the real `loc` and the offending value. msgspecerror can also fall back to default values when field validation fails, automatically repairing validation errors.

**Typical use case 1: structured validation error info**

Suppose you use msgspec on the backend to validate user input and want structured errors similar to [pydantic](https://github.com/pydantic/pydantic) / [fastapi](https://github.com/fastapi/fastapi) to return to the frontend. The `*_with_error` family does not raise on a validation failure; it returns the first error as an `ErrorInfo`.

```python
import msgspec
from msgspecerror import load_json_with_error

class User(msgspec.Struct):
    name: str = ''
    age: int = 0

data = b'{"name": "Tom", "age": "abc"}'
user, error = load_json_with_error(data, User)
print(user)
# NODEFAULT
print(error)
# ErrorInfo(msg='Expected `int`, got `str` - at `$.age`',
#           type=<ErrorType.TYPE_MISMATCH>,
#           loc=('age',),
#           ctx=ErrorCtx(expected='int', got='str'))
```

A success returns `(obj, NODEFAULT)`, a failure returns `(NODEFAULT, error)`; test for failure with `obj is msgspec.NODEFAULT`, not by truthiness.

The `*_with_error` family has three functions: `convert_with_error`, `load_json_with_error`, `load_msgpack_with_error`.

**Typical use case 2: detailed error info**

msgspec cannot point at the specific dict entry: a failing value is only reported as `$[...]`, a failing key as `$[...key]`, and the offending value is not reported at all. The `*_with_error_detail` family walks the error path through the raw data to resolve the real `loc` and to collect the offending value.

```python
from typing import Dict

import msgspec
from msgspecerror import load_json_with_error_detail

class ItemInfo(msgspec.Struct):
    amount: int = 0

class Inventory(msgspec.Struct):
    items: Dict[str, ItemInfo] = {}

data = b'{"items": {"apple": {"amount": "ABC"}}}'
inventory, error = load_json_with_error_detail(data, Inventory)
print(inventory)
# NODEFAULT
print(error)
# ErrorInfoDetail(msg='Expected `int`, got `str` - at `$.items[...].amount`',
#                 type=<ErrorType.TYPE_MISMATCH>,
#                 loc=('items', 'apple', 'amount'),
#                 ctx=ErrorCtx(expected='int', got='str'),
#                 error_value='ABC',
#                 loc_resolved=True,
#                 error_value_resolved=True)
```

Two flags describe how far it actually got: `loc_resolved` says whether `loc` points at one concrete entry, `error_value_resolved` says whether the offending value was collected. When it cannot be resolved, `loc` is returned as it is (still holding the `...` / `...key` placeholder) and `error_value` is `msgspec.UNSET`.

The `*_with_error_detail` family has three functions: `convert_with_error_detail`, `load_json_with_error_detail`, `load_msgpack_with_error_detail`.

> Note: to test whether `loc` still holds a placeholder, read `loc_resolved`, do not evaluate `'...' in error.loc or '...key' in error.loc` by hand, or you will not be able to tell a placeholder from a key that really is named `...`.
>
> Note: the `*_with_error_detail` and `*_with_default` families are not recommended on a performance-sensitive backend, because resolving a placeholder to the real loc means someone can craft an input with a large number of key-value pairs and a large number of errors to stall your backend. Prefer the `*_with_error` family, which only pays a fixed string-parsing cost.

**Typical use case 3: fall back to defaults on validation error**

Suppose you use msgspec to read a local configuration file and want to automatically repair validation errors to avoid crashes from corrupted config files. The `*_with_default` family repairs every error with a default value and returns a usable object plus the collected errors.

```python
from typing import Literal

import msgspec
from msgspecerror import load_json_with_default

class BotConfig(msgspec.Struct):
    provider: Literal['deepseek', 'opanai', 'claude'] = 'deepseek'
    mode: Literal['agent', 'yolo'] = 'agent'

data = b'{"provider": "deepsleep"}'
result, errors = load_json_with_default(data, BotConfig)
print(result)
# BotConfig(provider='deepseek', mode='agent')
print(errors)
# [ErrorInfo(msg="Invalid enum value 'deepsleep' - at `$.provider`",
#            type=<ErrorType.INVALID_ENUM_VALUE>,
#            loc=('provider',),
#            ctx=ErrorCtx())]
```

The `*_with_default` family has three functions: `convert_with_default`, `load_json_with_default`, `load_msgpack_with_default`.

## 1. Installation

```bash
pip install msgspecerror
```

msgspecerror versions are tightly coupled to msgspec versions. If your project uses `msgspec>=0.22.0`, you should install `msgspecerror>=0.22.0`, which typically installs `msgspecerror==0.22.0.1`.

`msgspecerror==0.22.0.1` supports parsing errors from `msgspec>=0.18.6,<=0.22.0`.

## 2. How It Works

msgspecerror scans msgspec's C source code for error message formats and maintains a complex, high-performance string parsing module.

1. Classifies errors into the `ErrorType` enum based on message prefix format
2. Parses the path from the message, splitting it into `loc: tuple[str | int]`
3. Extracts `expected` and `got` properties when present
4. Extracts field constraint info (`ge`, `le`, `pattern`, `min_length`, etc.)

The auto-repair process is also high-performance and precise:

1. Optimistically decodes the input; returns immediately on success with no overhead
2. On error, decodes without `type=` to find the failing field's path and type
3. Walks the error path, replacing the failing field with its default value (if one exists)
4. Re-validates, repeating until validation passes or repair is no longer possible

## 3. Using msgspecerror

Pick a function by what you need:

- Only the error message or exception object: `parse_msgspec_error` (3.10)
- Already hold `(error, data, model)` and only want to locate: `resolve_error` (3.2.1)
- Want an `ErrorInfo`, do not care which dict key it is: `*_with_error` (3.1 / 3.4 / 3.7)
- Want the concrete key and the offending value: `*_with_error_detail` (3.2 / 3.5 / 3.8)
- Want an object you can keep using, with failing fields falling back to defaults: `*_with_default` (3.3 / 3.6 / 3.9)

The sections below are grouped by input format. Each family states its full contract at its first entry point, the other entries of the same family only list the signature and the differences:

- 3.1 – 3.3: json payloads, `load_json_*`
- 3.4 – 3.6: msgpack payloads, `load_msgpack_*`
- 3.7 – 3.9: already decoded in-memory objects (e.g. the result of a yaml parse, a database row), `convert_*`
- 3.10: the low-level primitive, `parse_msgspec_error` and `ErrorInfo`

### 3.1 load_json_with_error

Parse json and parse the exception when an error happens. Returns the first error on a validation failure instead of raising. It does not resolve `...` / `...key`, `loc` is exactly the path msgspec reported; it repairs nothing; it decodes once, so it is cheaper than `*_with_error_detail`.

```python
def load_json_with_error(
        data: Union[bytes, bytearray, memoryview, str],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfo, NODEFAULT]]: ...
```

- Success `(obj, NODEFAULT)`, failure `(NODEFAULT, error)`; test for failure with `obj is msgspec.NODEFAULT`
- Data-level problems are always returned: validation failure, malformed input, truncation, invalid utf-8
- Argument-level problems still raise: an illegal type hint, or a msgpack decoder passed in
- No `guess_default` / `utf8_error` here, this family repairs nothing

Example (a failing dict entry keeps its placeholder):

```python
from typing import Dict

import msgspec
from msgspecerror import load_json_with_error

class ItemInfo(msgspec.Struct):
    amount: int = 0

class Inventory(msgspec.Struct):
    items: Dict[str, ItemInfo] = {}

data = b'{"items": {"apple": {"amount": "ABC"}}}'
obj, error = load_json_with_error(data, Inventory)
print(obj)
# NODEFAULT
print(error.loc)
# ('items', '...', 'amount')
```

Use this family when only the error type / message matters, for example to write metrics or logs. When the concrete key and the offending value are needed, use `load_json_with_error_detail` in 3.2.

### 3.2 load_json_with_error_detail

Parse json and parse the exception when an error happens, with detailed error info. msgspec can only report a placeholder for a dict: `$[...]` for a failing value, `$[...key]` for a failing key, and it does not report the offending value. On failure this family decodes the raw payload a second time (without a type) and walks the error path, trying to replace the placeholder with the real key and to collect the offending value.

```python
def load_json_with_error_detail(
        data: Union[bytes, bytearray, memoryview, str],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfoDetail, NODEFAULT]]: ...
```

How the `load_*` form works:

1. typed decode; return directly on success
2. on failure, remember the result of `parse_msgspec_error()`
3. decode the same payload once more without a type to get the raw object
4. `resolve_error(error, raw, model)` walks it and delivers

`ErrorInfoDetail` inherits from `ErrorInfo` (see 3.10) and adds three fields:

```python
class ErrorInfoDetail(ErrorInfo):
    # The offending value, msgspec.UNSET when there is none
    error_value: Any = UNSET
    # Whether loc points at one concrete entry
    loc_resolved: bool = False
    # Whether the offending value was collected (the authoritative flag)
    error_value_resolved: bool = False
```

The two flags describe how far it actually got. Only three of the four combinations occur (in the order `loc_resolved / error_value_resolved`):

- `True / True`: located a concrete entry, and collected the value
- `True / False`: the path is concrete, but there is no value. For example a missing field, a set that cannot be indexed, or a payload that cannot be decoded while the original path was concrete anyway
- `False / False`: the placeholder was not resolved, or the data is not available
- `False / True`: never occurs (collecting a value means the whole path was walked)

Read `error_value_resolved` to decide whether a value was collected, do not infer it from `error_value is UNSET`: the data itself may be `UNSET`. `loc_resolved` is about whether the path was walked, not whether the text still holds `...`: when a real dict key is named `...`, it is `True`.

Locating boundaries:

- when the second decode fails (truncated, malformed, trailing bytes), `loc` is returned as it is and `error_value is UNSET`, nothing is raised
- invalid utf-8 is rewritten to U+FFFD and decoded again so the location can be found; if the located value itself was rewritten, it is not delivered (`True / False`)
- a root error (`loc == ()`) delivers the whole document as `error_value`
- a `...key` error delivers the key itself as `error_value`
- the reported path of `Set[int]` is an index, but a set cannot be indexed, so it is `True / False`
- it only locates, it repairs nothing: the payload, the `data` and the passed error are never modified

A `...key` example (a json key is always a str, `Dict[int, int]` has to coerce it to int):

```python
from typing import Dict
from msgspecerror import load_json_with_error_detail

detail = load_json_with_error_detail(b'{"1": 5, "x": 1}', Dict[int, int])[1]
print(detail.loc)
# ('x',)
print(detail.error_value)
# x
```

Locating validates the raw data once more, so `dec_hook` / `Struct.__post_init__` runs again and its side effects happen twice, see 5.2.

`error_value` delivers the caller's object as it is and is not guaranteed to be json-encodable; to write a log line use `detail.to_log_dict()`, see 3.10 and 5.6.

#### 3.2.1 Locate an existing error: `resolve_error`

If the error did not come from `*_with_error_detail` (e.g. yaml / toml / a database row validated by the caller), and `(error, data, model)` is already at hand, call `resolve_error` to locate it on its own:

```python
def resolve_error(
        error: Union[ErrorInfo, ValidationError, str],
        data: Any,
        model_or_decoder: Any,
        **kwargs,
) -> ErrorInfoDetail: ...
```

- `error` can be an `ErrorInfo`, a `ValidationError`, or an error message string
- `data` must be the raw object before validation, not the converted model instance
- read-only: it does not modify `data`, nor the passed `error`
- it does not decode: use `load_*_with_error_detail` for a payload
- idempotent: an `ErrorInfoDetail` with both flags `True` is returned unchanged (the same object)
- `model_or_decoder` can be a model or a decoder; a decoder only supplies `.type` / `.strict` / `.dec_hook`, it is never used to decode
- `**kwargs` must match the rules of the `convert` call that failed. Different rules locate a different entry: under `strict=False` `'1'` is a valid `int`, re-checking it with the default rules reports it as the failing one
- the object in `data` can be any `Mapping` (a dict subclass, a `UserDict`, a `ChainMap`, a read-only mapping), the array can be a list / tuple (including subclasses)
- when the location does not match the data / model nothing is raised, `loc=()` is delivered with no value

```python
from typing import Dict

import msgspec
from msgspecerror import ErrorInfo, ErrorType, resolve_error

class ItemInfo(msgspec.Struct):
    amount: int = 0

class Inventory(msgspec.Struct):
    items: Dict[str, ItemInfo] = {}

data = {'items': {'apple': {'amount': 'ABC'}}}
error = ErrorInfo(msg='Expected `int`, got `str` - at `$.items[...].amount`',
                  type=ErrorType.TYPE_MISMATCH, loc=('items', '...', 'amount'))
detail = resolve_error(error, data, Inventory)
print(detail.loc)
# ('items', 'apple', 'amount')
print(detail.error_value)
# ABC
```

### 3.3 load_json_with_default

Parse json and fall back to defaults when an error happens. Pass a model or a `msgspec.json.Decoder` object; returns the validated object and the list of errors collected during repair. On failure, returns `msgspec.NODEFAULT` and the collected errors.

```python
def load_json_with_default(
        data: Union[bytes, bytearray, memoryview, str],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
        guess_default: bool = False,
) -> Tuple[Any, List[ErrorInfo]]: ...
```

`guess_default` controls how a value that fails validation is repaired when the model provides no default:

- `guess_default=False` (default): a failing dict entry or list item is deleted, as the minimal repair, keeping all other data untouched.
- `guess_default=True`: a failing dict value or list item is replaced by a guessed default (`0` for `int`, `""` for `str`, `[]` for `list`, `None` for `Optional`, ...), and deleted if the type has no guessable default (Enum, `Literal`, datetime, ...) or the guessed value fails validation (e.g. `Annotated[int, Meta(ge=18)]`). A struct field with no default is repaired by a guessed value too.

The repair logic of load_json_with_default:

1. On field validation error, attempts to use the field's default value
2. If the target path is a `msgspec.Struct`, attempts a default construct — succeeds if all fields have defaults
3. On dict value error, validates again to find the failing value, then deletes the pair (or repairs it with a guessed default when `guess_default=True`); a pair whose nested data can't be repaired is deleted too
4. On dict key error, validates again to find the failing key, then attempts 1 & 2; removes the key if repair fails
5. On list element error, finds the element by index, then deletes the element (or repairs it with a guessed default when `guess_default=True`); an element whose nested data can't be repaired is deleted too
6. On `UnicodeDecodeError`, attempts manual decoding and re-validation
   - `utf8_error=='strict'`: Treats a unicode error as a root-path error, attempts default construction of the root model. You may lose all data because of one unicode error.
   - `utf8_error=='replace'`: Replaces invalid unicode with `�` (U+FFFD), most data will be parsed successfully.
   - `utf8_error=='ignore'`: Drops invalid unicode, most data will be parsed successfully, but you may lose some characters.
   - A `str` payload is encoded to utf-8 before parsing, so a lone surrogate fails there as a `UnicodeEncodeError`; it is repaired the same way, the surrogate becomes `�` or is dropped
7. Stops after 100 repair attempts

It is recommended to set default values on every field in your model to ensure repair succeeds, so that `msgspec.NODEFAULT` is never returned.

### 3.4 load_msgpack_with_error

Parse msgpack and parse the exception when an error happens. Returns the first error on a validation failure instead of raising. Behaves like `load_json_with_error`, except the payload must be bytes-like (`bytes` / `bytearray` / `memoryview`), a `str` is rejected; a json decoder raises `TypeError`.

```python
def load_msgpack_with_error(
        data: Union[bytes, bytearray, memoryview],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfo, NODEFAULT]]: ...
```

### 3.5 load_msgpack_with_error_detail

Parse msgpack and parse the exception when an error happens, with detailed error info. Behaves like `load_json_with_error_detail`, the `ErrorInfoDetail` fields, the two flags and the locating boundaries are the same, see 3.2.

```python
def load_msgpack_with_error_detail(
        data: Union[bytes, bytearray, memoryview],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfoDetail, NODEFAULT]]: ...
```

The only difference is the payload and the second decode: a msgpack string can hold invalid utf-8 while the typed decode still succeeds (the bytes stay in a `msgspec.Raw` field), so when the second decode fails the invalid bytes are rewritten and decoded once more, for locating only; a value that was rewritten is not delivered (`True / False`).

### 3.6 load_msgpack_with_default

Parse msgpack and fall back to defaults when an error happens.

```python
def load_msgpack_with_default(
        data: Union[bytes, bytearray, memoryview],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
        guess_default: bool = False,
) -> Tuple[Any, List[ErrorInfo]]: ...
```

Used exactly like `load_json_with_default`: the meaning of `guess_default`, the repair order and the three `utf8_error` modes are all the same, except the payload must be bytes-like and a `str` is rejected.

msgpack is a binary format, so invalid utf-8 cannot be turned into a str first the way json can, which is why the repair has two levels: it first tries to fix a single string precisely, then falls back to walking the whole msgpack structure and repairing every string. See 5.3 for the details.

### 3.7 convert_with_error

Convert an in-memory object and parse the exception when an error happens. Returns the first error on a validation failure instead of raising. The input is an already decoded in-memory object (e.g. the result of a yaml parse, a database row), so there is no payload decode stage. The success / failure contract is the same as 3.1.

```python
def convert_with_error(
        data: Any,
        model_or_decoder: Any,
        **kwargs,
) -> Tuple[Any, Union[ErrorInfo, NODEFAULT]]: ...
```

- `**kwargs` are the options of `msgspec.convert` (`strict`, `from_attributes`, `dec_hook`, `builtin_types`, `str_keys`); with a decoder its `.strict` / `.dec_hook` are the defaults and an explicit argument overrides them
- this family accepts a decoder of either format and only takes its `.type`, it never decodes with it
- an illegal type hint still raises `TypeError`

### 3.8 convert_with_error_detail

Convert an in-memory object and parse the exception when an error happens, with detailed error info. The input is an in-memory object, so there is no second decode and `data` is available as it is; the `ErrorInfoDetail` fields, the two flags and the locating boundaries are in 3.2.

```python
def convert_with_error_detail(
        data: Any,
        model_or_decoder: Any,
        **kwargs,
) -> Tuple[Any, Union[ErrorInfoDetail, NODEFAULT]]: ...
```

- a `False` flag only has two sources: `loc` does not match the model / data, or there is no value on the path
- `msgspec.Raw` fields: `convert` has no bytes to give, so a placeholder `Raw` is pinned at the reported location and the value is validated again, a real error is still located; when only the `Raw` field complains, `INPUT_REJECTED` is returned (`loc=()`, no value), see 5.4
- `**kwargs` mean the same as in 3.7, and locating uses the same rules

### 3.9 convert_with_default

Convert an in-memory object and fall back to defaults when an error happens. `load_*_with_default` works on a payload, `convert_with_default` works on an already decoded in-memory object, the repair semantics are the same:

```python
def convert_with_default(
        data: Any,
        model_or_decoder: Any,
        *,
        guess_default: bool = False,
        **kwargs,
) -> Tuple[Any, List[ErrorInfo]]: ...
```

- success `(obj, [])`; on failure the result is the same as `load_*_with_default`
- **modifies `data` in place**: the repair writes into the dict / list the caller passed. A caller that still needs the original value must copy it with `copy.deepcopy()`
- when the repair gives up, `data` is exactly as it was before the call, the multi-error path included: values written by earlier rounds are put back, and key order is preserved
- with `from_attributes=True` and an object as `data`, the object is not modified: the library reads the attributes into a dict first, repairs a copy, and then decodes the model from it
- when the model has a `msgspec.Raw` field, the value at that location is encoded as msgpack and filled in, so the model is built (`msgpack.decode(result.payload)` gives the original value back)
- no `utf8_error`: an in-memory object has no byte-encoding problem
- `**kwargs` are the options of `msgspec.convert`, a decoder's are the defaults and an explicit argument overrides them

```python
from typing import Dict

import msgspec
from msgspecerror import convert_with_default

class ItemInfo(msgspec.Struct):
    amount: int = 0

class Inventory(msgspec.Struct):
    items: Dict[str, ItemInfo] = {}

data = {'items': {'apple': {'amount': 'ABC'}}}
result, errors = convert_with_default(data, Inventory)
print(result)
# Inventory(items={'apple': ItemInfo(amount=0)})
print(data)
# {'items': {'apple': {'amount': 0}}}   ← the input was modified in place
print(errors)
# [ErrorInfo(msg='Expected `int`, got `str` - at `$.items[...].amount`',
#            type=<ErrorType.TYPE_MISMATCH>,
#            loc=('items', 'apple', 'amount'),
#            ctx=ErrorCtx(expected='int', got='str'))]
```

### 3.10 Parsing exceptions into structured objects

Parsing exceptions into structured objects. Use parse_msgspec_error to parse any exception.

```python
def parse_msgspec_error(error: Union[str, Exception]) -> ErrorInfo: ...
DECODE_ERRORS = (ValidationError, DecodeError, UnicodeDecodeError, UnicodeEncodeError)
```

Note: use `except DECODE_ERRORS as e:` rather than `except msgspec.ValidationError as e:` to catch exceptions.

msgspecerror can wrap more error types into an ErrorInfo object, such as the `UnicodeDecodeError` from invalid utf-8 in a bytes payload, and the `UnicodeEncodeError` from a lone surrogate in a str payload (msgspec encodes the payload to utf-8 before parsing it).

```python
import msgspec
from msgspecerror import DECODE_ERRORS, parse_msgspec_error
try:
    data = msgspec.json.decode(...)
except DECODE_ERRORS as e:
    error = parse_msgspec_error(e)
```

ErrorInfo class

```python
class ErrorInfo(Struct, omit_defaults=True):
    # Original error message
    msg: str
    # Error type
    type: ErrorType
    # Error location path
    # ('user', 'profile', 'age')
    # ('...', 'RepairThreshold')
    # ('matrix', 0, 1, 'value')
    # Note:
    # - msgspec displays dict values as [...] so the parsed result is "..."
    # - msgspec has a separate format for dict keys, parsed as "...key"
    # - msgspec doesn't tell which specific dict key failed
    #   (the *_with_error_detail family resolves the real key, see use case 2)
    # - list indices are integers, not strings
    loc: Tuple[Union[int, str]] = ()
    # Extra context
    ctx: ErrorCtx = field(default_factory=ErrorCtx)
```

```python
class ErrorCtx(Struct, omit_defaults=True):
    """
    An optional object which contains extra info
    """
    expected: Optional[str] = None
    got: Optional[str] = None
    gt: Union[int, float, None] = None
    ge: Union[int, float, None] = None
    lt: Union[int, float, None] = None
    le: Union[int, float, None] = None
    multiple_of: Union[int, float, None] = None
    pattern: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    tz: Optional[bool] = None
    # Internal hint: the message may be a rejected msgspec.Raw field, a caller normally does not care
    may_raw_rejection: bool = False
```

**Writing an error into a log: `to_log_dict()`**

Both `ErrorInfo` and `ErrorInfoDetail` have `to_log_dict()`, which returns a plain dict that can be handed straight to `msgspec.json.encode()`. The key set is constant (never reduced by defaults), so a log consumer can read it with a fixed schema:

```python
import msgspec
from typing import Dict
from msgspecerror import convert_with_error_detail

error = convert_with_error_detail({'k': 'x'}, Dict[str, int])[1]
print(error.to_log_dict())
# {'msg': 'Expected `int`, got `str` - at `$[...]`', 'type': 'TYPE_MISMATCH',
#  'loc': ('k',), 'ctx': {'expected': 'int', 'got': 'str'},
#  'error_value': 'x', 'loc_resolved': True, 'error_value_resolved': True}
print(msgspec.json.encode(error.to_log_dict()))
# b'{"msg":...}'
```

`ErrorInfoDetail.to_log_dict()` has three keys more than `ErrorInfo`: `error_value`, `loc_resolved`, `error_value_resolved`. When no value was collected `error_value` is `None`, use `error_value_resolved` to tell "the value is `None`" from "there is no value".

`error_value` is the caller's raw object and is not guaranteed to be json-encodable (on the `convert_*` path it can be any Python object, or a reference cycle), so always log with `to_log_dict()` rather than `msgspec.json.encode(error)` directly. See 5.6.

## 4. ErrorType Reference

All `ErrorType` enum members and their corresponding error message formats. `<Path>` represents a JSONPath-like string (e.g., `$.field`).

### 4.1 Group 1: Type Mismatch Errors

**`TYPE_MISMATCH`** — Value type does not match expected type
- Format: `` Expected `<A>`, got `<B>` - at <Path> ``
- Examples:
  - `` Expected `int`, got `str` - at `$.age` ``
  - `` Expected `str`, got `int` - at $.name ``
  - `` Expected `MyCustomClass`, got `str` - at `$.custom_field` ``
  - `` Expected `int`, got `str` - at `$.type` `` (tagged unions)
- Triggered by:
  1. **General Mismatch**: A decoded value's type doesn't match the target Python type
  2. **MsgPack Mismatch**: A msgpack-encoded value cannot be coerced into the target type
  3. **Custom Type Mismatch**: `dec_hook` returns an object that is not an instance of the expected custom type
  4. **Tag Type Mismatch**: The tag field in a tagged union has an incorrect type
  5. **Bool Literal Mismatch** (0.22.0+): A `Literal[True]` / `Literal[False]` target receives a non-bool value, the expected type is named `bool`
- Changed in 0.22.0: A `msgspec.Raw` target is named `raw` instead of the misleading `any`
  - `` Expected `raw`, got `int` - at `$.payload` `` (convert / yaml.decode / toml.decode)

**`TOKEN_TYPE_MISMATCH`** — JSON token type doesn't match the decode position expectation
- Format: `` Expected `<type>` - at <Path> `` (no ", got" part)
- Examples:
  - `` Expected `str` - at `$.kind` `` (JSON: tag field value is non-string)
  - `` Expected `int` - at `$.kind` `` (JSON: tag field value is non-int)
  - `` Expected `str` - at `key` in `$` `` (convert: dict key is non-string)
- Triggered by:
  1. **Tag Value Mismatch** (JSON): The tag field value in a tagged union has the wrong JSON token type
  2. **Map Key Mismatch** (convert): A dict key in `msgspec.convert()` is not a string — the target type (Struct/TypedDict/dataclass) only has string field names
  3. **Token Type Mismatch** (JSON): The decoder expects an `int` or `str` at the current JSON decode position, but the next JSON token has a different type

### 4.2 Group 2: Structural Errors

**`MISSING_FIELD`** — Missing required field
- Format: `` Object missing required field `<field_name>` - at `<Path>` ``
- Example: `` Object missing required field `id` ``
- Triggered by: A Struct, TypedDict, or dataclass is missing a required field with no default value

**`UNKNOWN_FIELD`** — Unknown field encountered
- Format: `` Object contains unknown field `<field_name>` - at `<Path>` ``
- Example: `` Object contains unknown field 'favorite_color' - at `$` ``
- Triggered by: Decoding a Struct with `forbid_unknown_fields=True` and encountering a field not defined on the struct

### 4.3 Group 3: Constraint and Length Errors

**`ARRAY_LENGTH_CONSTRAINT`** — Array length mismatch
- Formats:
  - `` Expected `array` of at (least|most) length <expected>, got <actual> - at <Path> ``
  - `` Expected `array` of length <min> to <max>, got <actual> - at <Path> ``
  - `` Expected `array` of length <expected> - at <Path> ``
- Example: `` Expected `array` of length 2, got 3 - at $.coordinates ``
- Triggered by: Decoding a fixed-size tuple or NamedTuple with a mismatched number of elements
- Note (0.19.0+): A variant without the `- at <Path>` suffix exists due to a known msgspec bug

**`OBJECT_LENGTH_CONSTRAINT`** — Object length constraint
- Format: `` Expected `object` of length <op> <value> - at <Path> ``
- Example: `` Expected `object` of length >= 1 - at $.metadata ``
- Triggered by: A dict-like object violates `min_length`/`max_length` constraints from a `Meta` object

**`LENGTH_CONSTRAINT`** — Length constraint
- Format: `` Expected `<type>` of length <op> <value> - at <Path> ``
- Example: `` Expected `str` of length <= 32 - at $.name ``
- Triggered by: A variable-length sequence or sized scalar violates `min_length`/`max_length` constraints

**`PATTERN_CONSTRAINT`** — Regex pattern constraint
- Format: `` Expected `str` matching regex <pattern> - at <Path> ``
- Example: `` Expected `str` matching regex '\\d{4}-\\d{2}-\\d{2}' - at $.date_str ``
- Triggered by: A string does not match the regex `pattern` constraint from a `Meta` object

**`NUMERIC_CONSTRAINT`** — Numeric constraint
- Formats:
  - `` Expected `<type>` <op> <value> - at <Path> ``
  - `` Expected `<type>` that's a multiple of <value> - at <Path> ``
- Examples: `` Expected `int` >= 0 - at $.age ``, `` Expected `int` that's a multiple of 6 ``
- Triggered by: A numeric value violates a constraint from a `Meta` object (ge, lt, multiple_of, etc.)

**`TIMEZONE_CONSTRAINT`** — Timezone constraint
- Formats:
  - `` Expected `datetime` with (a|no) timezone component - at <Path> ``
  - `` Expected `time` with (a|no) timezone component - at <Path> ``
- Triggered by: A datetime/time object does not satisfy `tz=True` or `tz=False` constraint from a `Meta` object

### 4.4 Group 4: Invalid Value Errors

**`INVALID_ENUM_VALUE`** — Invalid enum value
- Format: `"Invalid enum value <value> - at <Path>"`
- Example: `"Invalid enum value 'admin' - at $.role"`
- Example (0.22.0+): `` "Invalid enum value False - at `$.enabled`" `` — a bool literal type (`Literal[True]` / `Literal[False]`) receives the opposite bool
- Triggered by: A value that is not a valid member of the target `Enum` or `Literal` type

**`INVALID_TAG_VALUE`** — Invalid tag value
- Format: `"Invalid value <value>"` or `` Invalid value `<value>` - at <Path> ``
- Example: `"Invalid value 3 - at $.type"`
- Triggered by: (Tagged Unions) The tag field has the correct type but its specific value does not match any tag in the union

**`INVALID_DATETIME`** — Invalid datetime format
- Format: `"Invalid RFC3339 encoded datetime - at <Path>"`
- Example: `"Invalid RFC3339 encoded datetime - at $.timestamp"`
- Triggered by: A string intended for `datetime.datetime` is malformed

**`INVALID_DATE`** — Invalid date format
- Format: `"Invalid RFC3339 encoded date - at <Path>"`
- Example: `"Invalid RFC3339 encoded date - at $.birth_date"`
- Triggered by: A string intended for `datetime.date` is malformed

**`INVALID_TIME`** — Invalid time format
- Format: `"Invalid RFC3339 encoded time - at <Path>"`
- Example: `"Invalid RFC3339 encoded time - at $.event_time"`
- Triggered by: A string intended for `datetime.time` is malformed

**`INVALID_DURATION`** — Invalid duration format
- Format: `"Invalid ISO8601 duration - at <Path>"`
- Example: `"Invalid ISO8601 duration - at $.period"`
- Triggered by: A string intended for `datetime.timedelta` is malformed

**`UNSUPPORTED_DURATION_UNITS`** — Unsupported duration units
- Format: `"Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations - at <Path>"`
- Triggered by: An ISO8601 duration string contains unsupported units like 'W' (weeks) or 'Y' (years)

**`INVALID_MSGPACK_TIMESTAMP`** — Invalid MsgPack timestamp
- Format: `"Invalid MessagePack timestamp[: nanoseconds out of range] - at <Path>"`
- Triggered by: (MsgPack only) A MessagePack extension with type code -1 (timestamp) is malformed

**`INVALID_UUID`** — Invalid UUID
- Format: `"Invalid UUID - at <Path>"` or `"Invalid UUID bytes - at <Path>"`
- Triggered by: A string or byte sequence is not a valid UUID

**`INVALID_BASE64_STRING`** — Invalid base64 string
- Format: `"Invalid base64 encoded string - at <Path>"`
- Triggered by: A string intended for bytes/bytearray/memoryview is not valid base64

**`INVALID_DECIMAL_STRING`** — Invalid decimal string
- Format: `"Invalid decimal string - at <Path>"`
- Triggered by: A string intended for `decimal.Decimal` cannot be parsed

**`INVALID_EPOCH_TIMESTAMP`** — Invalid epoch timestamp
- Format: `"Invalid epoch timestamp - at <Path>"`
- Triggered by: A non-finite float (inf, -inf, nan) is being decoded as a datetime object

### 4.5 Group 5: Out of Range Errors

**`TIMESTAMP_OUT_OF_RANGE`** — Timestamp out of range
- Format: `"Timestamp is out of range - at <Path>"`
- Triggered by: A numeric or string value representing a datetime outside Python's `datetime` range

**`DURATION_OUT_OF_RANGE`** — Duration out of range
- Format: `"Duration is out of range - at <Path>"`
- Triggered by: A numeric or string value representing a duration outside Python's `timedelta` range

**`INTEGER_OUT_OF_RANGE`** — Integer out of range
- Format: `"Integer value out of range - at <Path>"`
- Triggered by: A string representing an integer is too large (exceeds 4300 digits)

**`NUMBER_OUT_OF_RANGE`** — Number out of range
- Format: `"Number out of range - at <Path>"`
- Triggered by:
  1. A string representing a number exceeds the range of a `double` precision float
  2. (0.22.0+) `msgspec.convert()` of an `int` that is too large for a `float` target

### 4.6 Group 6: Wrapped Errors & Others

**`WRAPPED_ERROR`** — Wrapped user code error
- Format: `"<original error message> - at <Path>"`
- Example: `"passwords cannot be the same - at $"`
- Triggered by: msgspec catches a `TypeError` or `ValueError` from user code (e.g., `dec_hook`, `__post_init__`, custom type `__init__`) and wraps it in a `ValidationError`

**`UNICODE_DECODE_ERROR`** — Unicode codec error (msgspecerror custom type)
- Format: `"<codec>' codec can't decode byte <byte>..."`
- Format: `"<codec>' codec can't encode character <char>..."`
- Examples:
  - `"'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"`
  - `"'utf-8' codec can't decode bytes in position 0-1: unexpected end of data"`
  - `"'utf-8' codec can't encode character '\ud800' in position 7: surrogates not allowed"`
- Triggered by:
  - A bytes payload containing invalid unicode
  - A str payload holding a lone surrogate: msgspec encodes the payload to utf-8 before parsing it, so the failure is the same bad-input class

**`JSON_MALFORMED`** — JSON is malformed
- Format: `"JSON is malformed: <reason> (byte <pos>)"`
- Reasons include: `invalid character`, `trailing characters`, `expected ',' or ']'`, `expected ',' or '}'`, `expected ':'`, `expected '"'`, `trailing comma in array`, `trailing comma in object`, `object keys must be strings`, `invalid number`, `invalid escape character in string`, `invalid character in unicode escape`, `invalid utf-16 surrogate pair`, `unexpected end of hex escape`, `unexpected end of escaped utf-16 surrogate pair`, `invalid escaped character`
- Triggered by: Any JSON text that does not conform to the JSON grammar

**`MSGPACK_MALFORMED`** — MsgPack is malformed
- Format: `"MessagePack data is malformed: <reason> (byte <pos>)"`
- Reasons include: `trailing characters (byte <pos>)`, `invalid opcode '\x<XX>' (byte <pos>)`, `map keys must be hashable (byte <pos>)` (0.22.0+)
- Triggered by: Any data that does not conform to the MessagePack binary format, e.g. trailing bytes after a complete message, an unrecognized opcode, or a map key that decodes to an unhashable object

**`DATA_TRUNCATED`** — Input data was truncated
- Format: `"Input data was truncated"`
- Triggered by:
  1. JSON input that ends mid-token (unclosed braces/brackets/strings)
  2. MessagePack input with an incomplete opcode or payload

**`ENCODE_ERROR`** — Encoding error
- Format: `"Can't encode <obj> longer than 2**32 - 1"`
- Triggered by: Encoding an object that exceeds MsgPack's 32-bit length limit

**`INPUT_REJECTED`** — Input rejected (msgspecerror internal type)
- Formats:
  - `"Input rejected: too many repair cycles"` — exceeds 100 repair attempts
  - `"Input rejected: validation failed on struct defaults"` — struct default value validation failed
  - `"Input rejected: a msgspec.Raw field can not be built from an in-memory object"` — `convert` can not build a `msgspec.Raw` field from a decoded object
  - `"Input rejected: a msgspec.Raw field can not be built from a decoded object"` — this msgspec refuses a `Raw` instance, the object can not be rebuilt
- Triggered by: The msgspecerror auto-repair loop cannot fix the input, or the input cannot be judged at all (`convert_with_error_detail` on a model with a `msgspec.Raw` field and no other error, or a value msgpack cannot encode at such a field), see 5.4
- Note: This type is internal to msgspecerror; msgspec itself never produces this error


## 5. Limitations

### 5.1 Type hints may not be accurate

Every function that accepts `model_or_decoder` (the three `*_with_default`, the three `*_with_error`, the three `*_with_error_detail`) accepts both a model and a decoder, and some IDEs or type checkers may not correctly infer the return type.

```python
class ItemInfo(msgspec.Struct):
    amount: int = 0
result, errors = load_json_with_default(..., ItemInfo)
# result: ItemInfo
decoder = msgspec.json.Decoder(ItemInfo)
result, errors = load_json_with_default(..., decoder)
# result: ItemInfo
```

### 5.2 Locating and repairing validate the data again

msgspec does not say which dict entry failed, so locating and repairing have to validate the raw data once more. With many entries the values are handed to msgspec in one batch and the failing index is read back, so the cost grows linearly with the number of entries; the whole call is still more expensive than a plain `msgspec.convert`, and `_with_default` may have to repeat several rounds.

Worse, **user code is replayed**: locating and repairing use the caller's own validation rules, so `dec_hook` and `Struct.__post_init__` run again on entries that pass, and their side effects happen twice. This cannot be removed while keeping the location exact.

If you want to reduce that cost:

1. Use `msgspec.Struct` for all data structures — msgspecerror can then locate errors precisely from the error path.
2. Avoid string type annotations like `user: "str | None"`. Use `user: Optional[str]` instead, to avoid the overhead of `typing._eval_type` resolving string annotations from forward references.

```python
class ItemInfo(msgspec.Struct):
    amount: int = 0
class Inventory(msgspec.Struct):
    items: Dict[str, ItemInfo]
data = b'{"items":{"apple":{"amount":"ABC"}}}'
result, errors = load_json_with_default(data, Inventory)
print(result)
# Inventory(items={'apple': ItemInfo(amount=0)})
print(errors)
# [ErrorInfo(msg='Expected `int`, got `str` - at `$.items[...].amount`',
#            type=<ErrorType.TYPE_MISMATCH>,
#            loc=('items', 'apple', 'amount'),
#            ctx=ErrorCtx(expected='int', got='str'))]
```

### 5.3 Auto-repair may be slow on a large msgpack with multiple UnicodeDecodeErrors

Since msgpack is binary data, it cannot be directly converted to str to repair invalid unicode.

1. On `UnicodeDecodeError`, msgspecerror first enters fast repair mode: it locates the offending unicode bytes, and if the byte region belongs to a msgpack string and occurs only once, performs a precise repair.
2. If the fast repair still leaves `UnicodeDecodeError`, or random binary content collides with the invalid unicode bytes, it enters slow repair mode that manually parses the msgpack structure to find and repair each string.

The slow repair guarantees linear-time inspection of all string objects — no malicious input with excessive bad characters can cause repair overhead to explode. However, the slow repair is implemented in pure Python; if your input structure is very complex, repair performance may suffer.

### 5.4 `msgspec.Raw` fields

`Raw` holds the raw bytes of the payload, while the `convert_*` family gets an already decoded object, with no bytes to give that field.

- the `load_*` family is fine: the typed decode builds `Raw` fields
- `convert_with_error` faithfully returns `convert`'s own exception (it stops at the `Raw` field), which does not mean the data is wrong
- `convert_with_error_detail` pins a placeholder `Raw` at the reported location and validates again, so a real error is still located; when only the `Raw` field complains it returns `INPUT_REJECTED` (`loc=()`, no value)
- `convert_with_default` encodes the value at that location as msgpack and fills it in, so the model is built and `msgpack.decode(result.payload)` gives the original value back

Also, when `_with_default` repairs a json document that holds `Raw`, the raw bytes are re-encoded: the value is unchanged, but formatting such as whitespace and escapes is lost; for msgpack the bytes are identical. Do not use `_with_default` when byte-level fidelity matters (signatures, content addressing).

### 5.5 Container and `from_attributes` support

The locating side judges by what msgspec accepts: an object can be any `Mapping` (a dict subclass, a `UserDict`, a `ChainMap`, a read-only mapping), an array can be a list / tuple (including subclasses).

The repairing side only writes dicts (including subclasses) and lists (including subclasses). Objects such as `UserDict`, `ChainMap` and `MappingProxyType` are not repaired, the result is `loc=('...',)` and the input is left untouched.

`from_attributes=True` only applies to the two `convert_*` families (a `load_*` payload decodes to a dict, never an object). The locating family reads fields with `getattr()`, so read-only attributes and properties can be read; the repairing family works on a copy of the attributes, and the caller's object is never modified.

### 5.6 `error_value` and a broken location

`error_value` delivers the offending value as it is. On the `convert_*` path it can be a class msgspec does not know, a `complex`, or a reference cycle built by YAML anchors, and `msgspec.json.encode(detail)` rejects all of them. Write logs with `detail.to_log_dict()`: it is guaranteed to encode, `error_value` is turned into an encodable form (`bytes` becomes base64, a custom object becomes its `repr()` text), while `detail.error_value` itself is still the original object.

If `loc` is broken (not a tuple, or holding an illegal segment such as `None` / `bool` / list), or does not match `data` / `model`, locating does not raise but delivers `loc=()` with no value. Read the original path from `msg` then, and do not rely on the empty `loc`.

### 5.7 Path ambiguity

msgspec allows setting field aliases via `field(name=...)`, but some names cause ambiguity in msgspecerror's path parsing.

```python
class WithAliases(Struct):
    """A struct with aliased field names to test repair via encode names."""
    name: str = field(name="userName")
    age: int = field(name="userAge", default=18)
```

While such aliases are uncommon in normal code, the following are noted for completeness.

1. JSON escape characters cannot be used as field names. msgspec itself disallows them.

```
`\` (backslash)
`"` (double quote)
U+0000-U+001F (control characters, including `\t` `\n` `\r`)
```

2. Field names cannot contain `.` (period), as msgspec uses `.` to separate path segments in error messages.
3. Field names cannot contain suffixes matching `[\d*]` or `[...]`, as msgspec uses `[index]` for array indices and `[...]` for dict keys.

```
# ambiguous field alias
user[0], user[...], user[.]
# safe field alias
user[x], user[[], user[]], user[0]name, [x]
```

4. Field names cannot contain the keywords msgspecerror uses to split error messages.

```python
KEY_at = ' - at `$'
KEY_at_key_in = ' - at `key` in `$'
```

5. Field names containing `$`, backticks, or empty string, while confusing, are parsed correctly by msgspecerror.

```
`$.$.x` → ("$", "x")
`$.$`   → ("$",)
```

```
 `$.`       → ("",)         # empty field
 `$..`      → ("", "")      # two consecutive empty fields
 `$.[0]`    → ("", 0)       # empty field then array index
 `$[0].`    → (0, "")       # array index then empty field
 `$..[0]`   → ("", "", 0)   # two empty fields then index
 `$[0]..`   → (0, "", "")   # index then two empty fields
 `$..name`  → ("", "name")  # empty field then named segment
 `$.name.`  → ("name", "")  # named segment then empty field
 `$[...].`  → ("...", "")   # dict key then empty field
 `$.[...]`  → ("", "...")   # empty field then dict key
 `$.[0].`   → ("", 0, "")   # empty + index + empty
 `$[0]..[1]..` → (0, "", "", 1, "", "")  # alternating index and empty
```

```
Object contains unknown field `RepairThresho` ld1` - at `$.op`si`
→ ("op`si", "RepairThresho` ld1")
Expected `MyCustomClass`, got `str` - at `$.custom`field`
→ ("custom`field")
Expected `int`, got `str` - at `$.`items[0]`
→ ("`items", 0)
```
