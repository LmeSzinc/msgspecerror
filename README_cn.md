# msgspecerror

**| [English](README.md) | 简体中文 |**

[msgspec](https://github.com/msgspec/msgspec) 是一个高性能的序列化与验证库，但其校验失败时返回的 `ValidationError` 只有纯文本消息：

```
Expected `int`, got `str` - at `$.user.age`
```

[msgspecerror](https://github.com/LmeSzinc/msgspecerror) 将这类纯文本异常解析为结构化的 Python 对象，提供错误类型、字段路径、额外上下文等可用信息，让你可以**程序化地处理**校验错误。

msgspecerror 还可以在字段校验错误的时候回退为默认值，自动修复校验错误。

**典型使用场景一：获取结构化的校验错误信息**

假设你在后端中使用了 msgspec 校验用户输入，希望得到与 [pydantic](https://github.com/pydantic/pydantic) / [fastapi](https://github.com/fastapi/fastapi) 相似的结构化错误，返回给前端。

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
    # ErrorInfo(msg='Expected `int`, got `str` - at `$.user.age`',
    #           type=<ErrorType.TYPE_MISMATCH>,
    #           loc=('user', 'age'),
    #           ctx=ErrorCtx(expected='int', got='str'))
```

**典型使用场景二：在校验错误时回退为默认值**

假设你正在使用 msgspec 读取本地配置文件，希望自动修复校验错误，避免配置文件损坏导致程序崩溃。

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

## 1. 安装

```bash
pip install msgspecerror
```

msgspecerror 版本与 msgspec 版本是强绑定的，假设你的项目正在使用 `msgspec>=0.21.1` ，那么你应该安装 `msgspecerror>=0.21.1`，这通常会安装 `msgspecerror==0.21.1.0`。

`msgspecerror==0.21.1.0` 将支持解析 `msgspec>=0.18.6,<=0.21.1`。

## 2. 工作原理

msgspecerror 扫描了 msgspec 的 C 源码中的异常消息格式，维护了一个复杂的 高性能的 字符串解析模块。

1. 根据错误消息的前缀格式，将错误分类到枚举类 `ErrorType` 中
2. 解析消息中的路径，拆分为 `loc: tuple[str | int]`
3. 提取可能存在的 `expected`, `got` 属性
4. 提取字段约束信息（`ge`、`le`、`pattern`、`min_length` 等）

自动修复流程同样是 高性能且精确的：

1. 乐观地解码输入，如果无错误则直接返回，不增加额外开销
2. 在出现错误时，尝试不带 `type=` 进行解码，并解析异常，获取出错字段的路径和类型
3. 沿错误路径逐层修复，将错误字段替换为默认值（如果有默认值）
4. 重新验证，重复直到校验通过或无法继续修复

## 3. 使用msgspecerror

### 3.1 结构化解析异常

使用 parse_msgspec_error 可以解析任意异常。

```python
def parse_msgspec_error(error: Union[str, Exception]) -> ErrorInfo: ...
DECODE_ERRORS = (ValidationError, DecodeError, UnicodeDecodeError)
```

注意：你应该使用 `except DECODE_ERRORS as e:` 捕获异常，而不是 `except msgspec.ValidationError as e:` 捕获异常。msgspecerror 可以将更多错误类型封装进 ErrorInfo 对象，比如 `UnicodeDecodeError`。

```python
import msgspec
from msgspecerror import DECODE_ERRORS, parse_msgspec_error
try:
    data = msgspec.json.decode(...)
except DECODE_ERRORS as e:
    error = parse_msgspec_error(e)
```

ErrorInfo 类

```python
class ErrorInfo(Struct, omit_defaults=True):
    # 原始异常信息
    msg: str
    # 异常类型
    type: ErrorType
    # 异常路径
    # ('user', 'profile', 'age')
    # ('...', 'RepairThreshold')
    # ('matrix', 0, 1, 'value')
    # 注意：
    # - msgspec会将字典值显示为 [...] 因此解析的结果是 "..."
    # - msgspec对字典键有单独异常格式，为了兼容性考虑，我们解析为 "...key"
    # - msgspec不会给出具体哪个字典键值，路径就是字符串 "..." 和 "...key"
    # - 列表索引是整数，而不是字符串
    loc: Tuple[Union[int, str]] = ()
    # 额外上下文信息
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
```

### 3.2 自动回退默认值的json解析

输入校验模型或者 `msgspec.json.Decoder` 对象，返回校验成功的对象 和 在修复期间收集到的错误列表。如果修复失败，返回 `msgspec.NODEFAULT` 和 在修复期间收集到的错误列表。

```python
def load_json_with_default(
        data: Union[bytes, str],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
) -> Tuple[Any, List[ErrorInfo]]: ...
result, errors = load_json_with_default(data, MyStruct)
```

load_json_with_default 的具体修复逻辑如下：

1. 当字段发生校验错误时，尝试获取字段默认值进行修复

2. 当目标路径的类型是 msgspec.Struct 时，尝试进行默认构建 (default construct)，如果 Struct 所有字段都有默认值则默认构建成功

3. 当字典中的某个值校验错误时，遍历所有键值对进行二次校验，寻找到校验失败的值，尝试 1 与 2 进行修复
4. 当字典中的某个键校验错误时，遍历所有键进行二次校验，寻找到校验失败的值，尝试 1 与 2 进行修复，如果修复失败则删除键。
5. 当列表中的某个元素校验错误时，根据列表索引查找元素，尝试 1 与 2 进行修复，如果修复失败则删除元素。
6. 当发生UnicodeDecodeError时，尝试手动解码再进行二次校验
   - `utf8_error=='strict'`：UnicodeDecodeError 将被视为跟路径上的错误，会尝试默认构建根模型。你可能因为一个unicode错误而丢失全部数据。
   - `utf8_error=='replace'`：将错误的unicode替换为 `�`（U+FFFD），大部分数据将成功解析，但你可能多出一些问号字符。
   - `utf8_error=='ignore'`：将错误的unicode丢弃，大部分数据将成功解析，但你可能丢失一些字符。
7. 当修复次数超过100次时，停止修复

建议校验模型中的每个字段都设置默认值，这样能确保自动修复成功，不会返回 `msgspec.NODEFAULT`。

### 3.3 自动回退默认值的msgpack解析

```python
def load_msgpack_with_default(
        data: bytes,
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
) -> Tuple[Any, List[ErrorInfo]]: ...
result, errors = load_msgpack_with_default(data, MyStruct)
```

与 `load_json_with_default` 的使用一样。

## 4. 异常类型 ErrorType

所有 `ErrorType` 枚举成员及其对应的错误消息格式。`<Path>` 表示类似 `$.field` 的 JSONPath 路径。

### 4.1 第1组：类型不匹配错误

**`TYPE_MISMATCH`** — 值与期望类型不匹配
- 格式：``Expected `<A>`, got `<B>` - at <Path>``
- 示例：
  - ``Expected `int`, got `str` - at `$.age```
  - ``Expected `str`, got `int` - at $.name``
  - ``Expected `MyCustomClass`, got `str` - at `$.custom_field```
  - ``Expected `int`, got `str` - at `$.type```（tagged unions）
- 触发条件：
  1. **一般不匹配**：解码值的类型与目标 Python 类型不符
  2. **MsgPack 不匹配**：MsgPack 编码值无法转换为目标 Python 类型
  3. **自定义类型不匹配**：`dec_hook` 返回的对象不是所期望的自定义类型
  4. **标签类型不匹配**：tagged union 中标签字段的类型不正确

**`TOKEN_TYPE_MISMATCH`** — JSON token 类型与解码位置期望不符
- 格式：``Expected `<type>` - at <Path>``（无 ", got" 部分）
- 示例：
  - ``Expected `str` - at `$.kind```（JSON：tag 字段值不是字符串）
  - ``Expected `int` - at `$.kind```（JSON：tag 字段值不是整数）
  - ``Expected `str` - at `key` in `$```（convert：字典键不是字符串）
- 触发条件：
  1. **标签值不匹配**（JSON）：tagged union 中的标签字段具有错误的 JSON token 类型
  2. **映射键不匹配**（convert）：`msgspec.convert()` 中的字典键不是字符串——目标类型（Struct/TypedDict/dataclass）仅支持字符串字段名
  3. **Token 类型不匹配**（JSON）：解码器在当前位置期望 `int` 或 `str`，但下一个 JSON token 类型不符

### 4.2 第2组：结构错误

**`MISSING_FIELD`** — 缺失必填字段
- 格式：``Object missing required field `<field_name>` - at `<Path>```
- 示例：``Object missing required field `id```
- 触发条件：Struct、TypedDict 或 dataclass 缺少没有默认值的必填字段

**`UNKNOWN_FIELD`** — 出现未知字段
- 格式：``Object contains unknown field `<field_name>` - at `<Path>```
- 示例：``Object contains unknown field 'favorite_color' - at `$```
- 触发条件：解码 `forbid_unknown_fields=True` 的 Struct 时，消息中出现了未定义的字段

### 4.3 第3组：约束与长度错误

**`ARRAY_LENGTH_CONSTRAINT`** — 数组长度不符
- 格式：
  - ``Expected `array` of at (least|most) length <expected>, got <actual> - at <Path>``
  - ``Expected `array` of length <min> to <max>, got <actual> - at <Path>``
  - ``Expected `array` of length <expected> - at <Path>``
- 示例：``Expected `array` of length 2, got 3 - at $.coordinates``
- 触发条件：解码定长 tuple 或 NamedTuple 时，数组元素个数不匹配
- 注意（0.19.0+）：存在不带 `- at <Path>` 后缀的变体，是 msgspec 的已知 bug

**`OBJECT_LENGTH_CONSTRAINT`** — 对象长度约束
- 格式：``Expected `object` of length <op> <value> - at <Path>``
- 示例：``Expected `object` of length >= 1 - at $.metadata``
- 触发条件：字典类对象（dict、TypedDict、Struct）违反了 `Meta` 中的 `min_length`/`max_length` 约束

**`LENGTH_CONSTRAINT`** — 长度约束
- 格式：``Expected `<type>` of length <op> <value> - at <Path>``
- 示例：``Expected `str` of length <= 32 - at $.name``
- 触发条件：可变长序列（list、set、frozenset）或定长标量（str、bytes）违反了 `Meta` 中的 `min_length`/`max_length` 约束

**`PATTERN_CONSTRAINT`** — 正则约束
- 格式：``Expected `str` matching regex <pattern> - at <Path>``
- 示例：``Expected `str` matching regex '\\d{4}-\\d{2}-\\d{2}' - at $.date_str``
- 触发条件：字符串未匹配 `Meta` 中的 `pattern` 正则约束

**`NUMERIC_CONSTRAINT`** — 数值约束
- 格式：
  - ``Expected `<type>` <op> <value> - at <Path>``
  - ``Expected `<type>` that's a multiple of <value> - at <Path>``
- 示例：``Expected `int` >= 0 - at $.age``、``Expected `int` that's a multiple of 6``
- 触发条件：数值违反了 `Meta` 中的 `ge`/`lt`/`multiple_of` 等约束

**`TIMEZONE_CONSTRAINT`** — 时区约束
- 格式：
  - ``Expected `datetime` with (a|no) timezone component - at <Path>``
  - ``Expected `time` with (a|no) timezone component - at <Path>``
- 触发条件：datetime/time 不满足 `Meta` 中 `tz=True`（需要时区）或 `tz=False`（不需要时区）的约束

### 4.4 第4组：无效值错误

**`INVALID_ENUM_VALUE`** — 无效的枚举值
- 格式：`"Invalid enum value <value> - at <Path>"`
- 示例：`"Invalid enum value 'admin' - at $.role"`
- 触发条件：值不是目标 `Enum` 或 `Literal` 类型的有效成员

**`INVALID_TAG_VALUE`** — 无效的 tag 值
- 格式：`"Invalid value <value>"` 或 ``Invalid value `<value>` - at <Path>``
- 示例：`"Invalid value 3 - at $.type"`
- 触发条件：（Tagged Unions）标签字段类型正确，但具体值不匹配当前 union 中的任何 tag

**`INVALID_DATETIME`** — 无效的 datetime 格式
- 格式：`"Invalid RFC3339 encoded datetime - at <Path>"`
- 示例：`"Invalid RFC3339 encoded datetime - at $.timestamp"`
- 触发条件：表示 `datetime.datetime` 的字符串格式错误

**`INVALID_DATE`** — 无效的 date 格式
- 格式：`"Invalid RFC3339 encoded date - at <Path>"`
- 示例：`"Invalid RFC3339 encoded date - at $.birth_date"`
- 触发条件：表示 `datetime.date` 的字符串格式错误

**`INVALID_TIME`** — 无效的 time 格式
- 格式：`"Invalid RFC3339 encoded time - at <Path>"`
- 示例：`"Invalid RFC3339 encoded time - at $.event_time"`
- 触发条件：表示 `datetime.time` 的字符串格式错误

**`INVALID_DURATION`** — 无效的 duration 格式
- 格式：`"Invalid ISO8601 duration - at <Path>"`
- 示例：`"Invalid ISO8601 duration - at $.period"`
- 触发条件：表示 `datetime.timedelta` 的字符串格式错误

**`UNSUPPORTED_DURATION_UNITS`** — 不支持的 duration 单位
- 格式：`"Only units 'D', 'H', 'M', and 'S' are supported when parsing ISO8601 durations - at <Path>"`
- 触发条件：ISO8601 时长字符串包含不支持的 'W'（周）或 'Y'（年）等单位

**`INVALID_MSGPACK_TIMESTAMP`** — 无效的 MsgPack 时间戳
- 格式：`"Invalid MessagePack timestamp[: nanoseconds out of range] - at <Path>"`
- 触发条件：（MsgPack 专用）MsgPack 扩展类型码 -1（时间戳）格式错误

**`INVALID_UUID`** — 无效的 UUID
- 格式：`"Invalid UUID - at <Path>"` 或 `"Invalid UUID bytes - at <Path>"`
- 触发条件：字符串或字节序列不是有效的 UUID 格式

**`INVALID_BASE64_STRING`** — 无效的 base64 字符串
- 格式：`"Invalid base64 encoded string - at <Path>"`
- 触发条件：表示 bytes/bytearray/memoryview 的字符串不是有效的 base64

**`INVALID_DECIMAL_STRING`** — 无效的 decimal 字符串
- 格式：`"Invalid decimal string - at <Path>"`
- 触发条件：表示 `decimal.Decimal` 的字符串无法解析

**`INVALID_EPOCH_TIMESTAMP`** — 无效的时间戳
- 格式：`"Invalid epoch timestamp - at <Path>"`
- 触发条件：用无穷大或 NaN 浮点数解码 datetime

### 4.5 第5组：范围错误

**`TIMESTAMP_OUT_OF_RANGE`** — 时间戳超出范围
- 格式：`"Timestamp is out of range - at <Path>"`
- 触发条件：表示 datetime 的数值或字符串超出了 Python `datetime` 支持的范围

**`DURATION_OUT_OF_RANGE`** — 时长超出范围
- 格式：`"Duration is out of range - at <Path>"`
- 触发条件：表示 timedelta 的数值或字符串超出了 Python `timedelta` 支持的范围

**`INTEGER_OUT_OF_RANGE`** — 整数超出范围
- 格式：`"Integer value out of range - at <Path>"`
- 触发条件：表示整数的字符串过大（超过 4300 位数字）

**`NUMBER_OUT_OF_RANGE`** — 数值超出范围
- 格式：`"Number out of range - at <Path>"`
- 触发条件：表示数字的字符串超出了 `double` 精度浮点数的范围

### 4.6 第6组：包装错误及其他

**`WRAPPED_ERROR`** — 包装的用户代码错误
- 格式：`"<原始错误消息> - at <Path>"`
- 示例：`"passwords cannot be the same - at $"`
- 触发条件：msgspec 捕获了用户代码（如 `dec_hook`、`__post_init__`、自定义类型 `__init__`）中的 `TypeError` 或 `ValueError`，包装为 `ValidationError`

**`UNICODE_DECODE_ERROR`** — Unicode 解码错误（msgspecerror 自定义）
- 格式：`"<codec>' codec can't decode byte <byte>..."`
- 示例：
  - `"'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"`
  - `"'utf-8' codec can't decode bytes in position 0-1: unexpected end of data"`
- 触发条件：输入的字节序列包含无效的 unicode

**`JSON_MALFORMED`** — JSON 格式错误
- 格式：`"JSON is malformed: <reason> (byte <pos>)"`
- reason 包括：`invalid character`、`trailing characters`、`expected ',' or ']'`、`expected ',' or '}'`、`expected ':'`、`expected '"'`、`trailing comma in array`、`trailing comma in object`、`object keys must be strings`、`invalid number`、`invalid escape character in string`、`invalid character in unicode escape`、`invalid utf-16 surrogate pair`、`unexpected end of hex escape`、`unexpected end of escaped utf-16 surrogate pair`、`invalid escaped character`
- 触发条件：任何不符合 JSON 语法的文本输入

**`MSGPACK_MALFORMED`** — MsgPack 格式错误
- 格式：`"MessagePack data is malformed: <reason> (byte <pos>)"`
- reason 包括：`trailing characters (byte <pos>)`、`invalid opcode '\x<XX>' (byte <pos>)``
- 触发条件：任何不符合 MessagePack 二进制格式的数据

**`DATA_TRUNCATED`** — 数据被截断
- 格式：`"Input data was truncated"`
- 触发条件：
  1. JSON 输入在 token 中间意外结束（未闭合的括号/字符串）
  2. MessagePack 输入包含不完整的操作码或负载

**`ENCODE_ERROR`** — 编码错误
- 格式：`"Can't encode <obj> longer than 2**32 - 1"`
- 触发条件：编码超过 MsgPack 32 位长度限制的对象

**`INPUT_REJECTED`** — 输入被拒绝（msgspecerror 内部类型）
- 格式：
  - `"Input rejected: too many repair cycles"` — 修复次数超过 100 次上限
  - `"Input rejected: validation failed on struct defaults"` — 结构体默认值的校验失败
- 触发条件：msgspecerror 自动修复循环无法修复输入
- 注意：此类型由 msgspecerror 内部使用，msgspec 本身不会产生这类错误


## 5. 局限

### 5.1 类型提示可能不正确

`load_json_with_default` 与 `load_msgpack_with_default` 都接受 `model_or_decoder`，一些 IDE 或者类型检查工具可能无法正确提示返回值的类型。

```python
class ItemInfo(msgspec.Struct):
    amount: int = 0
result, errors = load_json_with_default(..., ItemInfo)
# result: ItemInfo
decoder = msgspec.json.Decoder(ItemInfo)
result, errors = load_json_with_default(..., decoder)
# result: ItemInfo
```

### 5.2 无法解析具体字典路径

当字典中的值发生校验错误的时候，msgspec 只能给出路径 `[...]` ，而不能给出具体是哪个键值发生错误，因此 msgspecerror 只能解析为 `...`

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
    # ErrorInfo(msg='Expected `int`, got `str` - at `$.items[...].amount`',
    #           type=<ErrorType.TYPE_MISMATCH>,
    #           loc=('items', '...', 'amount'),
    #           ctx=ErrorCtx(expected='int', got='str'))
```

### 5.3 当字典中发生校验错误时，自动修复可能性能不佳

因为 msgspec 无法给出字典中是哪个键值发生错误，所以自动修复会遍历输入字典中的每一个键值对，尝试进行校验，直到找到错误。如果输入的字典非常大，可能性能不佳。

如果你希望避免这个问题：

1. 全部使用 `msgspec.Struct` 定义数据结构，这样 msgspecerror 能根据错误信息精确修复。
2. 不要使用字符串形式的类型定义，比如 `user: "str | None"`，建议改成 `user: Optional[str]`。这样能避免使用复杂的 `typing._eval_type` 解析字符串类型定义。

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

### 5.4 校验大型msgpack发生多个UnicodeDecodeError时，自动修复可能性能不佳

因为 msgpack 是二进制数据，我们无法直接转换为 str 来处理错误的 unicode。

1. 当发生 UnicodeDecodeError 时，msgspecerror 首先进入快速修复模式，查找发生错误的 unicode 字符，如果字符所在区域是 msgpack string 且只出现一次，那么对错误字符进行精确修复。
2. 如果快速修复一次之后仍然发生 UnicodeDecodeError 或者有随机二进制内容与错误 unicode 字符发生碰撞，那么进入慢速修复模式。在慢速修复中手动解析 msgpack 寻找 string，逐个进行修复。

慢速修复模式保证了我们能够在线性时间内检查所有 string 对象，不会因恶意构造的含有大量错误字符的输入，而导致修复开销急剧膨胀。但是慢速修复模式终归是使用纯 python 实现的，如果你的输入内容结构非常复杂，自动修复可能性能不佳。

### 5.5 路径歧义

在 msgspec 中，你可以像这样给字段设置单独的字典别名，但是有一些名称在 msgspecerror 中将带来歧义。

```python
class WithAliases(Struct):
    """A struct with aliased field names to test repair via encode names."""
    name: str = field(name="userName")
    age: int = field(name="userAge", default=18)
```

在一般业务代码中你通常不会使用到这些歧义命名，下面仅作严谨性补充。

1. 不能使用 json 转义字符作为字段名。当然，msgspec 本身也不允许这么做。

```
`\`（反斜杠）
`"`（双引号）
U+0000–U+001F（控制字符）含 `\t` `\n` `\r`
```

2. 字段名不能包含 `.`（句点），因为 msgspec 在错误信息中使用 `.` 来分隔路径。
3. 字段名不能包含 `[\d*]` 模式或者 `[...]` 的后缀，因为 msgspec 使用 `[index]` 来标记数组索引，使用 `[...]` 来标记字典中的键。

```
# ambiguous field alias
user[0], user[...], user[.]
# safe field alias
user[x], user[[], user[]], user[0]name, [x]
```

4. 字段名不能包含 msgspecerror 用来切分错误信息的关键词。

```python
KEY_at = ' - at `$'
KEY_at_key_in = ' - at `key` in `$'
```

5. 字段名包含 `$`（美元符），字段名包含 `` ` `` （反引号），字段名为空字符串，虽然看起来很迷惑，但是 msgspecerror 也能正常解析。

```
`$.$.x` → ("$", "x")
`$.$`   → ("$",)
```

```
 `$.`       → ("",)         # 一个空字段
 `$..`      → ("", "")      # 两个连续的空字段
 `$.[0]`    → ("", 0)       # 空字段在前，数组索引在后
 `$[0].`    → (0, "")       # 数组索引在前，空字段在后
 `$..[0]`   → ("", "", 0)   # 两个空字段在前，索引在后
 `$[0]..`   → (0, "", "")   # 索引在前，两个空字段在后
 `$..name`  → ("", "name")  # 空字段在前，命名段在后
 `$.name.`  → ("name", "")  # 命名段在前，空字段在后
 `$[...].`  → ("...", "")   # dict 键在前，空字段在后
 `$.[...]`  → ("", "...")   # 空字段在前，dict 键在后
 `$.[0].`   → ("", 0, "")   # 空字段 + 索引 + 空字段
 `$[0]..[1]..` → (0, "", "", 1, "", "")  # 交替索引与空字段
```

```
Object contains unknown field `RepairThresho` ld1` - at `$.op`si`
→ ("op`si", "RepairThresho` ld1")
Expected `MyCustomClass`, got `str` - at `$.custom`field`
→ ("custom`field")
Expected `int`, got `str` - at `$.`items[0]`
→ ("`items", 0)
```

