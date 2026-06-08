# msgspecerror

[msgspec](https://github.com/msgspec/msgspec) is a high-performance serialization and validation library, but its `ValidationError` on failure only provides a plain-text message:

```
Expected `int`, got `str` - at `$.user.age`
```

[msgspecerror](https://github.com/LmeSzinc/msgspecerror) parses these plain-text exceptions into structured Python objects, providing error type, field path, and extra context — enabling you to **handle validation errors programmatically**.

msgspecerror can also fall back to default values when field validation fails, automatically repairing validation errors.

**Typical use case 1: structured validation error info**

Suppose you use msgspec on the backend to validate user input and want structured errors similar to [pydantic](https://github.com/pydantic/pydantic) / [fastapi](https://github.com/fastapi/fastapi) to return to the frontend.

```python
import msgspec
from msgspecerror import DECODE_ERRORS, parse_msgspec_error

try:
    data = msgspec.json.decode(...)
except DECODE_ERRORS as e:
    print(e)
    # Expected `int`, got `str` - at `$.user.age`
    error = parse_msgspec_error(e)
    print(error)
    # MsgspecError(msg='Expected `int`, got `str` - at `$.user.age`',
    #              type=<ErrorType.TYPE_MISMATCH>,
    #              loc=('user', 'age'),
    #              ctx=ErrorCtx(expected='int', got='str'))
```

**Typical use case 2: fall back to defaults on validation error**

Suppose you use msgspec to read a local configuration file and want to automatically repair validation errors to avoid crashes from corrupted config files.

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
# [MsgspecError(msg="Invalid enum value 'deepsleep' - at `$.provider`",
#               type=<ErrorType.INVALID_ENUM_VALUE>,
#               loc=('provider',),
#               ctx=ErrorCtx())]
```

## Installation

```bash
pip install msgspecerror
```

msgspecerror versions are tightly coupled to msgspec versions. If your project uses `msgspec>=0.21.1`, you should install `msgspecerror>=0.21.1`, which typically installs `msgspecerror==0.21.1.0`.

`msgspecerror==0.21.1.0` supports parsing errors from `msgspec>=0.18.6,<=0.21.1`.

## How It Works

msgspecerror scans msgspec's C source code for error message formats and maintains a high-performance string parsing module.

1. Classifies errors into the `ErrorType` enum based on message prefix format
2. Parses the path from the message, splitting it into `loc: tuple[str | int]`
3. Extracts `expected` and `got` properties when present
4. Extracts field constraint info (`ge`, `le`, `pattern`, `min_length`, etc.)

The auto-repair process is also high-performance and precise:

1. Optimistically decodes the input; returns immediately on success with no overhead
2. On error, decodes without `type=` to find the failing field's path and type
3. Walks the error path, replacing the failing field with its default value (if one exists)
4. Re-validates, repeating until validation passes or repair is no longer possible

## Using msgspecerror

### Parsing exceptions into structured objects

Use `parse_msgspec_error` to parse any exception.

```python
def parse_msgspec_error(error: Union[str, Exception]) -> MsgspecError: ...
DECODE_ERRORS = (ValidationError, DecodeError, UnicodeDecodeError)
```

Note: use `except DECODE_ERRORS as e:` rather than `except msgspec.ValidationError as e:` to catch exceptions. msgspecerror can wrap more error types into MsgspecError objects, such as `UnicodeDecodeError`.

```python
import msgspec
from msgspecerror import DECODE_ERRORS, parse_msgspec_error
try:
    data = msgspec.json.decode(...)
except DECODE_ERRORS as e:
    error = parse_msgspec_error(e)
```

**MsgspecError class:**

```python
class MsgspecError(Struct, omit_defaults=True):
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
    # - list indices are integers, not strings
    loc: Tuple[Union[int, str]] = ()
    # Extra context
    ctx: ErrorCtx = field(default_factory=ErrorCtx)
```

**ErrorCtx class:**

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
```

### Auto-repair JSON parsing with defaults

Pass a model or `msgspec.json.Decoder` object; returns the validated object and a list of errors collected during repair. On failure, returns `msgspec.NODEFAULT` and the collected errors.

```python
def load_json_with_default(
        data: Union[bytes, str],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
) -> Tuple[Any, List[MsgspecError]]: ...
result, errors = load_json_with_default(data, MyStruct)
```

The repair logic of `load_json_with_default`:

1. On field validation error, attempts to use the field's default value
2. If the target path is a `msgspec.Struct`, attempts a default construct — succeeds if all fields have defaults
3. On dict value error, iterates all key-value pairs to find the failing value, then attempts 1 & 2
4. On dict key error, iterates all keys to find the failing key, then attempts 1 & 2; removes the key if repair fails
5. On list element error, finds the element by index, then attempts 1 & 2; removes the element if repair fails
6. On `UnicodeDecodeError`, attempts manual decoding and re-validation
   - `utf8_error=='strict'`: Treats `UnicodeDecodeError` as a root-path error, attempts default construction of the root model
   - `utf8_error=='replace'`: Replaces invalid unicode with `\ufffd` (U+FFFD)
   - `utf8_error=='ignore'`: Drops invalid unicode bytes
7. Stops after 100 repair attempts

It is recommended to set default values on every field in your model to ensure repair succeeds.

### Auto-repair msgpack parsing with defaults

```python
def load_msgpack_with_default(
        data: bytes,
        model_or_decoder: Any,
) -> Tuple[Any, List[MsgspecError]]: ...
result, errors = load_msgpack_with_default(data, MyStruct)
```

Same usage as `load_json_with_default`, but without the `utf8_error` parameter. Since msgpack is binary data, it cannot be converted to str for unicode repair. `UnicodeDecodeError` is treated as a root-path error, equivalent to `utf8_error=='strict'`.

## ErrorType Reference

All `ErrorType` enum members and their corresponding error message formats. `<Path>` represents a JSONPath-like string (e.g., `$.field`).

### Group 1: Type Mismatch Errors

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

### Group 2: Structural Errors

**`MISSING_FIELD`** — Missing required field
- Format: `` Object missing required field `<field_name>` - at `<Path>` ``
- Example: `` Object missing required field `id` ``
- Triggered by: A Struct, TypedDict, or dataclass is missing a required field with no default value

**`UNKNOWN_FIELD`** — Unknown field encountered
- Format: `` Object contains unknown field `<field_name>` - at `<Path>` ``
- Example: `` Object contains unknown field 'favorite_color' - at `$` ``
- Triggered by: Decoding a Struct with `forbid_unknown_fields=True` and encountering a field not defined on the struct

### Group 3: Constraint and Length Errors

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

### Group 4: Invalid Value Errors

**`INVALID_ENUM_VALUE`** — Invalid enum value
- Format: `` Invalid enum value <value> - at <Path> ``
- Example: `` Invalid enum value 'admin' - at $.role ``
- Triggered by: A value that is not a valid member of the target `Enum` or `Literal` type

**`INVALID_TAG_VALUE`** — Invalid tag value
- Format: `` Invalid value <value> `` or `` Invalid value `<value>` - at <Path> ``
- Example: `` Invalid value 3 - at $.type ``
- Triggered by: (Tagged Unions) The tag field has the correct type but its specific value does not match any tag in the union

**`INVALID_DATETIME`** — Invalid datetime format
- Format: `` Invalid RFC3339 encoded datetime - at <Path> ``
- Example: `` Invalid RFC3339 encoded datetime - at $.timestamp ``
- Triggered by: A string intended for `datetime.datetime` is malformed

**`INVALID_DATE`** — Invalid date format
- Format: `` Invalid RFC3339 encoded date - at <Path> ``
- Example: `` Invalid RFC3339 encoded date - at $.birth_date ``
- Triggered by: A string intended for `datetime.date` is malformed

**`INVALID_TIME`** — Invalid time format
- Format: `` Invalid RFC3339 encoded time - at <Path> ``
- Example: `` Invalid RFC3339 encoded time - at $.event_time ``
- Triggered by: A string intended for `datetime.time` is malformed

**`INVALID_DURATION`** — Invalid duration format
- Format: `` Invalid ISO8601 duration - at <Path> ``
- Example: `` Invalid ISO8601 duration - at $.period ``
- Triggered by: A string intended for `datetime.timedelta` is malformed

**`UNSUPPORTED_DURATION_UNITS`** — Unsupported duration units
- Format: `` Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations - at <Path> ``
- Triggered by: An ISO8601 duration string contains unsupported units like 'W' (weeks) or 'Y' (years)

**`INVALID_MSGPACK_TIMESTAMP`** — Invalid MsgPack timestamp
- Format: `` Invalid MessagePack timestamp[: nanoseconds out of range] - at <Path> ``
- Triggered by: (MsgPack only) A MessagePack extension with type code -1 (timestamp) is malformed

**`INVALID_UUID`** — Invalid UUID
- Format: `` Invalid UUID - at <Path> `` or `` Invalid UUID bytes - at <Path> ``
- Triggered by: A string or byte sequence is not a valid UUID

**`INVALID_BASE64_STRING`** — Invalid base64 string
- Format: `` Invalid base64 encoded string - at <Path> ``
- Triggered by: A string intended for bytes/bytearray/memoryview is not valid base64

**`INVALID_DECIMAL_STRING`** — Invalid decimal string
- Format: `` Invalid decimal string - at <Path> ``
- Triggered by: A string intended for `decimal.Decimal` cannot be parsed

**`INVALID_EPOCH_TIMESTAMP`** — Invalid epoch timestamp
- Format: `` Invalid epoch timestamp - at <Path> ``
- Triggered by: A non-finite float (inf, -inf, nan) is being decoded as a datetime object

### Group 5: Out of Range Errors

**`TIMESTAMP_OUT_OF_RANGE`** — Timestamp out of range
- Format: `` Timestamp is out of range - at <Path> ``
- Triggered by: A numeric or string value representing a datetime outside Python's `datetime` range

**`DURATION_OUT_OF_RANGE`** — Duration out of range
- Format: `` Duration is out of range - at <Path> ``
- Triggered by: A numeric or string value representing a duration outside Python's `timedelta` range

**`INTEGER_OUT_OF_RANGE`** — Integer out of range
- Format: `` Integer value out of range - at <Path> ``
- Triggered by: A string representing an integer is too large (exceeds 4300 digits)

**`NUMBER_OUT_OF_RANGE`** — Number out of range
- Format: `` Number out of range - at <Path> ``
- Triggered by: A string representing a number exceeds the range of a `double` precision float

### Group 6: Wrapped Errors & Others

**`WRAPPED_ERROR`** — Wrapped user code error
- Format: `` <original error message> - at <Path> ``
- Example: `` passwords cannot be the same - at $ ``
- Triggered by: msgspec catches a `TypeError` or `ValueError` from user code (e.g., `dec_hook`, `__post_init__`, custom type `__init__`) and wraps it in a `ValidationError`

**`UNICODE_DECODE_ERROR`** — Unicode decode error (msgspecerror custom type)
- Format: `` <codec>' codec can't decode byte <byte>... ``
- Examples:
  - `` 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte ``
  - `` 'utf-8' codec can't decode bytes in position 0-1: unexpected end of data ``
- Triggered by: Input bytes contain invalid unicode

**`JSON_MALFORMED`** — JSON is malformed
- Format: `` JSON is malformed: <reason> (byte <pos>) ``
- Reasons include: `invalid character`, `trailing characters`, `expected ',' or ']'`, `expected ',' or '}'`, `expected ':'`, `expected '"'`, `trailing comma in array`, `trailing comma in object`, `object keys must be strings`, `invalid number`, `invalid escape character in string`, `invalid character in unicode escape`, `invalid utf-16 surrogate pair`, `unexpected end of hex escape`, `unexpected end of escaped utf-16 surrogate pair`, `invalid escaped character`
- Triggered by: Any JSON text that does not conform to the JSON grammar

**`MSGPACK_MALFORMED`** — MsgPack is malformed
- Format: `` MessagePack data is malformed: <reason> (byte <pos>) ``
- Reasons include: `trailing characters (byte <pos>)`, `invalid opcode '\\x<XX>' (byte <pos>)`
- Triggered by: Any data that does not conform to the MessagePack binary format

**`DATA_TRUNCATED`** — Input data was truncated
- Format: `` Input data was truncated ``
- Triggered by:
  1. JSON input that ends mid-token (unclosed braces/brackets/strings)
  2. MessagePack input with an incomplete opcode or payload

**`ENCODE_ERROR`** — Encoding error
- Format: `` Can't encode <obj> longer than 2**32 - 1 ``
- Triggered by: Encoding an object that exceeds MsgPack's 32-bit length limit

**`INPUT_REJECTED`** — Input rejected (msgspecerror internal type)
- Formats:
  - `` Input rejected: too many repair cycles `` — exceeds 100 repair attempts
  - `` Input rejected: validation failed on struct defaults `` — struct default value validation failed
- Triggered by: The msgspecerror auto-repair loop cannot fix the input
- Note: This type is internal to msgspecerror; msgspec itself never produces this error

## Limitations

### Type hints may not be accurate

Both `load_json_with_default` and `load_msgpack_with_default` accept `model_or_decoder`. Some IDEs or type checkers may not correctly infer the return type.

```python
class ItemInfo(msgspec.Struct):
    amount: int = 0
result, errors = load_json_with_default(..., ItemInfo)
# result: ItemInfo
decoder = msgspec.json.Decoder(ItemInfo)
result, errors = load_json_with_default(..., decoder)
# result: ItemInfo
```

### Cannot resolve specific dict keys

When a dict value fails validation, msgspec can only report the path as `[...]`, not the specific key. msgspecerror parses this as `...`.

```python
class ItemInfo(msgspec.Struct):
    amount: int = 0
class Inventory(msgspec.Struct):
    items: Dict[str, ItemInfo]
data = b'{"items":{"apple":{"amount":"ABC"}}}'
try:
    _ = msgspec.json.decode(data, type=Inventory)
except DECODE_ERRORS as e:
    print(e)
    # Expected `int`, got `str` - at `$.items[...].amount`
    error = parse_msgspec_error(e)
    print(error)
    # MsgspecError(msg='Expected `int`, got `str` - at `$.items[...].amount`',
    #              type=<ErrorType.TYPE_MISMATCH>,
    #              loc=('items', '...', 'amount'),
    #              ctx=ErrorCtx(expected='int', got='str'))
```

### Auto-repair may have poor performance with dicts

Since msgspec cannot report which specific dict key failed, auto-repair iterates over every key-value pair to find the error. For very large dicts this may be slow. To avoid this, use `msgspec.Struct` for nested data structures — msgspecerror can then locate errors precisely from the path.

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
# [MsgspecError(msg='Expected `int`, got `str` - at `$.items[...].amount`',
#               type=<ErrorType.TYPE_MISMATCH>,
#               loc=('items', 'apple', 'amount'),
#               ctx=ErrorCtx(expected='int', got='str'))]
```

### Cannot handle UnicodeDecodeError when decoding msgpack

Since msgpack is binary data, it cannot be converted to str to repair invalid unicode.

### Path ambiguity

msgspec allows setting field aliases via `field(name=...)`. Some names may cause ambiguity in msgspecerror's path parsing.

```python
class WithAliases(Struct):
    """A struct with aliased field names to test repair via encode names."""
    name: str = field(name="userName")
    age: int = field(name="userAge", default=18)
```

While such aliases are uncommon in normal code, the following are noted for completeness:

1. JSON escape characters cannot be used as field names (msgspec itself disallows them):
   ```
   `` (backslash)
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

4. Field names cannot contain the keywords msgspecerror uses to split error messages:
   ```python
   KEY_at = ' - at `$'
   KEY_at_key_in = ' - at `key` in `$'
   ```

5. Field names containing `$`, backticks, or empty string, while confusing, are parsed correctly by msgspecerror.
   ```
   `$.$.x` → ("$", "x")
   `$.$`   → ("$",)
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
