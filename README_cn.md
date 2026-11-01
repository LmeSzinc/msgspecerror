# msgspecerror ![PyPI - Version](https://img.shields.io/pypi/v/msgspecerror)

**| [English](README.md) | 简体中文 |**

[msgspec](https://github.com/msgspec/msgspec) 是一个高性能的序列化与验证库，但其校验失败时返回的 `ValidationError` 只有纯文本消息：

```
Expected `int`, got `str` - at `$.user.age`
```

[msgspecerror](https://github.com/LmeSzinc/msgspecerror) 将这类纯文本异常解析为结构化的 Python 对象，提供错误类型、字段路径、额外上下文等可用信息，让你可以**程序化地处理**校验错误。

msgspec 对字典只能给出占位符：值出错时是 `$[...]`，键出错时是 `$[...key]`。msgspecerror 会沿错误路径走查原始数据，尝试解析出真实的 `loc` 和出错的值。msgspecerror 也可以在字段校验错误的时候回退为默认值，自动修复校验错误。

**典型使用场景一：获取结构化的校验错误信息**

假设你在后端中使用了 msgspec 校验用户输入，希望得到与 [pydantic](https://github.com/pydantic/pydantic) / [fastapi](https://github.com/fastapi/fastapi) 相似的结构化错误，返回给前端。`*_with_error` 系列在校验失败时不抛异常，而是把第一个错误作为 `ErrorInfo` 返回。

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

成功时返回 `(obj, NODEFAULT)`，失败时返回 `(NODEFAULT, error)`；判断失败请用 `obj is msgspec.NODEFAULT`，不要用真值判断。

`*_with_error` 系列一共有 `convert_with_error`, `load_json_with_error`, `load_msgpack_with_error` 三个函数。

**典型使用场景二：获取详细错误信息**

msgspec 定位不到字典中的具体条目：值出错时只给出 `$[...]`，键出错时只给出 `$[...key]`，也不会给出出错的值。`*_with_error_detail` 系列会沿错误路径走查原始数据，尝试解析出真实的 `loc`，并取回出错的值。

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

两个标记说明实际做到了哪一步：`loc_resolved` 表示 `loc` 是否指到了具体条目，`error_value_resolved` 表示是否取回了出错的值。解析不了时 `loc` 原样保留（仍带 `...` / `...key` 占位符）、`error_value` 为 `msgspec.UNSET`。

`*_with_error_detail` 系列一共有 `convert_with_error_detail`, `load_json_with_error_detail`, `load_msgpack_with_error_detail` 三个函数。

> 注意：如需判断 `loc` 是否仍带占位符，请使用 `loc_resolved` 属性，不要手动判断 `'...' in error.loc or '...key' in error.loc`，否则你将无法区分 key 是占位符还是真的叫 `...`。
>
> 注意：不建议在性能敏感的后端使用 `*_with_error_detail` 系列和 `*_with_default` 系列，因为他们需要将占位符解析为真实 loc，如果有人恶意构造包含大量 key-value 和大量错误的输入可能会卡死你的后端。建议使用 `*_with_error` 系列，它只有固定的字符串解析开销。

**典型使用场景三：在校验错误时回退为默认值**

假设你正在使用 msgspec 读取本地配置文件，希望自动修复校验错误，避免配置文件损坏导致程序崩溃。`*_with_default` 系列会修复所有错误并回退默认值，返回可用的对象和收集到的错误列表。

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

`*_with_default` 系列一共有 `convert_with_default`, `load_json_with_default`, `load_msgpack_with_default` 三个函数。

## 1. 安装

```bash
pip install msgspecerror
```

msgspecerror 版本与 msgspec 版本是强绑定的，假设你的项目正在使用 `msgspec>=0.22.0` ，那么你应该安装 `msgspecerror>=0.22.0`，这通常会安装 `msgspecerror==0.22.0.1`。

`msgspecerror==0.22.0.1` 将支持解析 `msgspec>=0.18.6,<=0.22.0`。

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

先按需求选函数：

- 只有错误消息或异常对象：`parse_msgspec_error`（3.10）
- 已经拿到 `(error, data, model)`，只想定位：`resolve_error`（3.2.1）
- 想要 `ErrorInfo`，不关心字典里具体是哪个键：`*_with_error`（3.1 / 3.4 / 3.7）
- 想要具体键和出错的值：`*_with_error_detail`（3.2 / 3.5 / 3.8）
- 想要一个能继续用的对象，坏字段回退默认值：`*_with_default`（3.3 / 3.6 / 3.9）

下面按输入格式分组，每族在第一个入口里把契约讲完，同族的其它入口只列签名和差异：

- 3.1 – 3.3：json 负载，`load_json_*`
- 3.4 – 3.6：msgpack 负载，`load_msgpack_*`
- 3.7 – 3.9：已经解码好的内存对象（如 yaml 的解析结果、数据库行），`convert_*`
- 3.10：底层原语，`parse_msgspec_error` 与 `ErrorInfo`

### 3.1 load_json_with_error

解析 json 并在发生错误时解析异常。校验失败时返回第一个错误，而不是抛异常。它不解析 `...` / `...key`，`loc` 与 msgspec 报的路径完全一致；不修复任何数据；只做一次解码，比 `*_with_error_detail` 便宜。

```python
def load_json_with_error(
        data: Union[bytes, bytearray, memoryview, str],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfo, NODEFAULT]]: ...
```

- 成功 `(obj, NODEFAULT)`，失败 `(NODEFAULT, error)`；判断失败请用 `obj is msgspec.NODEFAULT`
- 数据层面的问题一律返回：校验失败、格式错误、截断、非法 utf-8
- 参数层面的错误照常抛：非法类型注解，或者传进来一个 msgpack decoder
- 不提供 `guess_default` / `utf8_error`，这一族不修复任何东西

示例（字典条目失败时仍保留占位符）：

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

只关心错误类型 / 消息时用这一族即可，例如写监控指标或日志。需要具体键和出错的值时用 3.2 的 `load_json_with_error_detail`。

### 3.2 load_json_with_error_detail

解析 json 并在发生错误时解析异常，获取详细错误信息。msgspec 对字典只能报占位符：值出错是 `$[...]`，键出错是 `$[...key]`，也不会给出出错的值。这族在失败后把原始负载再解码一次（不带类型），沿错误路径走查，尝试把占位符替换成真实的键，并取回出错的值。

```python
def load_json_with_error_detail(
        data: Union[bytes, bytearray, memoryview, str],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfoDetail, NODEFAULT]]: ...
```

`load_*` 版的流程：

1. typed decode；成功直接返回
2. 失败时记录 `parse_msgspec_error()` 的结果
3. 用同一份 payload 做一次不带类型的解码，拿到原始对象
4. `resolve_error(error, raw, model)` 走查并交付

`ErrorInfoDetail` 继承 `ErrorInfo`（见 3.10），并提供额外三个字段：

```python
class ErrorInfoDetail(ErrorInfo):
    # 出错的值，没有值时为 msgspec.UNSET
    error_value: Any = UNSET
    # loc 是否指到了具体条目
    loc_resolved: bool = False
    # 是否取回了出错的值（权威标记）
    error_value_resolved: bool = False
```

两个标记描述实际做到了哪一步。四种组合里只有三种会出现（按 `loc_resolved / error_value_resolved` 的顺序）：

- `True / True`：定位到具体条目，并取回了值
- `True / False`：路径是具体的，但没值。例如字段缺失、集合不可下标、负载解不开而原路径本来就具体
- `False / False`：占位符没解析掉，或数据拿不到
- `False / True`：不会出现（拿到值必然走完了整条路径）

判断有没有拿到值请读 `error_value_resolved`，不要用 `error_value is UNSET` 反推：数据本身就可以是 `UNSET`。`loc_resolved` 看的是「路径有没有走通」，不是「文本里还有没有 `...`」：字典里真实的键就叫 `...` 时，它是 `True`。

定位边界：

- 二次解码失败（截断、malformed、尾部多余字节）时，`loc` 原样返回、`error_value is UNSET`，不抛异常
- 非法 utf-8 会先改写成 U+FFFD 再解码以便定位；如果定位到的值本身被改写过，就不交付它（`True / False`）
- 根错误（`loc == ()`）时 `error_value` 是整个文档
- `...key` 错误时 `error_value` 是那个键本身
- `Set[int]` 的报错路径是下标，但集合不可下标，所以是 `True / False`
- 只定位，不修复：payload、`data` 和传入的错误都不会被修改

`...key` 的例子（json 的 key 恒为 str，`Dict[int, int]` 要把它强转成 int）：

```python
from typing import Dict
from msgspecerror import load_json_with_error_detail

detail = load_json_with_error_detail(b'{"1": 5, "x": 1}', Dict[int, int])[1]
print(detail.loc)
# ('x',)
print(detail.error_value)
# x
```

定位会在原始数据上再校验一遍，`dec_hook` / `Struct.__post_init__` 会再跑一次，副作用会发生两次，见 5.2。

`error_value` 原样交付调用方的对象，不保证可以 json 编码；写日志请用 `detail.to_log_dict()`，见 3.10 与 5.6。

#### 3.2.1 直接定位已有错误：`resolve_error`

如果错误不是由 `*_with_error_detail` 产生的（例如 yaml / toml / 数据库行由调用方自己校验），而手上已经有 `(error, data, model)`，可以调用 `resolve_error` 单独定位：

```python
def resolve_error(
        error: Union[ErrorInfo, ValidationError, str],
        data: Any,
        model_or_decoder: Any,
        **kwargs,
) -> ErrorInfoDetail: ...
```

- `error` 可以是 `ErrorInfo`、`ValidationError` 或错误消息字符串
- `data` 必须是校验前的原始对象，不是转换后的模型实例
- 只读：不修改 `data`，也不修改传入的 `error`
- 不做解码：payload 请走 `load_*_with_error_detail`
- 幂等：两个标记都为 `True` 的 `ErrorInfoDetail` 会原样返回（同一个对象）
- `model_or_decoder` 可以是模型或 decoder；decoder 只提供 `.type` / `.strict` / `.dec_hook`，不会用它解码
- `**kwargs` 必须与失败那次 `convert` 的规则一致。规则不同会定位到另一个条目：`strict=False` 下 `'1'` 是合法的 `int`，用默认规则复核就会把它当成失败项
- `data` 的对象可以是任意 `Mapping`（dict 子类、`UserDict`、`ChainMap`、只读 mapping），数组可以是 list / tuple（含子类）
- 位置与数据 / 模型对不上时不抛异常，交付 `loc=()` + 无值

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

解析 json 并在发生错误时自动回退默认值。输入校验模型或者 `msgspec.json.Decoder` 对象，返回校验成功的对象 和 在修复期间收集到的错误列表。如果修复失败，返回 `msgspec.NODEFAULT` 和 在修复期间收集到的错误列表。

```python
def load_json_with_default(
        data: Union[bytes, bytearray, memoryview, str],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
        guess_default: bool = False,
) -> Tuple[Any, List[ErrorInfo]]: ...
```

`guess_default` 控制模型没有提供默认值时，校验失败的值如何修复：

- `guess_default=False`（默认）：校验失败的字典键值对或者列表元素将被删除，作为最小修复，其他数据保持不变。
- `guess_default=True`：校验失败的字典值或者列表元素将被替换为推测的默认值（`int` 为 `0`，`str` 为 `""`，`list` 为 `[]`，`Optional` 为 `None` 等），如果该类型没有可推测的默认值（如 Enum、`Literal`、datetime）或者推测的默认值校验失败（如 `Annotated[int, Meta(ge=18)]`）则删除该键值对或者元素。没有默认值的结构体字段也会被推测的默认值修复。

load_json_with_default 的具体修复逻辑如下：

1. 当字段发生校验错误时，尝试获取字段默认值进行修复
2. 当目标路径的类型是 msgspec.Struct 时，尝试进行默认构建 (default construct)，如果 Struct 所有字段都有默认值则默认构建成功
3. 当字典中的某个值校验错误时，二次校验找出校验失败的值，然后删除该键值对（当 `guess_default=True` 时替换为推测的默认值）；如果该键值对内部的数据无法修复，也会被删除
4. 当字典中的某个键校验错误时，二次校验找出校验失败的键，尝试 1 与 2 进行修复，如果修复失败则删除键。
5. 当列表中的某个元素校验错误时，根据列表索引查找元素，然后删除该元素（当 `guess_default=True` 时替换为推测的默认值）；如果该元素内部的数据无法修复，也会被删除
6. 当发生UnicodeDecodeError时，尝试手动解码再进行二次校验
   - `utf8_error=='strict'`：unicode 错误将被视为根路径上的错误，会尝试默认构建根模型。你可能因为一个 unicode 错误而丢失全部数据。
   - `utf8_error=='replace'`：将错误的 unicode 替换为 `�`（U+FFFD），大部分数据将成功解析。
   - `utf8_error=='ignore'`：将错误的 unicode 丢弃，大部分数据将成功解析，但你可能丢失一些字符。
   - str 负载会先编码成 utf-8 再解析，因此孤立代理对会在这一步产生 `UnicodeEncodeError`；修复方式相同，代理对被替换为 `�` 或丢弃。
7. 当修复次数超过100次时，停止修复

建议校验模型中的每个字段都设置默认值，这样能确保自动修复成功，不会返回 `msgspec.NODEFAULT`。

### 3.4 load_msgpack_with_error

解析 msgpack 并在发生错误时解析异常。校验失败时返回第一个错误，而不是抛异常。行为与 `load_json_with_error` 相同，区别是负载必须是 bytes-like（`bytes` / `bytearray` / `memoryview`），不能传 `str`；传 json decoder 会抛 `TypeError`。

```python
def load_msgpack_with_error(
        data: Union[bytes, bytearray, memoryview],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfo, NODEFAULT]]: ...
```

### 3.5 load_msgpack_with_error_detail

解析 msgpack 并在发生错误时解析异常，获取详细错误信息。行为与 `load_json_with_error_detail` 相同，`ErrorInfoDetail` 的字段、两个标记与定位边界也一样，见 3.2。

```python
def load_msgpack_with_error_detail(
        data: Union[bytes, bytearray, memoryview],
        model_or_decoder: Any,
) -> Tuple[Any, Union[ErrorInfoDetail, NODEFAULT]]: ...
```

区别只在负载与二次解码：msgpack 的字符串可以含非法 utf-8 而 typed decode 仍能通过（字节留在 `msgspec.Raw` 字段里），所以二次解码失败时同样会先改写非法字节再解一次，只用于定位；被改写过的值不会交付（`True / False`）。

### 3.6 load_msgpack_with_default

解析 msgpack 并在发生错误时自动回退默认值。

```python
def load_msgpack_with_default(
        data: Union[bytes, bytearray, memoryview],
        model_or_decoder: Any,
        *,
        utf8_error: Literal['strict', 'replace', 'ignore'] = 'replace',
        guess_default: bool = False,
) -> Tuple[Any, List[ErrorInfo]]: ...
```

与 `load_json_with_default` 的使用一样：`guess_default` 的含义、修复顺序、`utf8_error` 的三种取值都相同，区别是负载必须是 bytes-like，不能传 `str`。

msgpack 是二进制格式，非法 utf-8 无法像 json 那样先转成 str 再修复，所以修复分两级：先尝试精确修复单条字符串，失败后回退到遍历整个 msgpack 结构逐条修复。细节见 5.3。

### 3.7 convert_with_error

转换内存对象并在发生错误时解析异常。校验失败时返回第一个错误，而不是抛异常。输入是已经解码好的内存对象（如 yaml 的解析结果、数据库行），因此没有负载解码阶段。成功 / 失败契约与 3.1 相同。

```python
def convert_with_error(
        data: Any,
        model_or_decoder: Any,
        **kwargs,
) -> Tuple[Any, Union[ErrorInfo, NODEFAULT]]: ...
```

- `**kwargs` 是 `msgspec.convert` 的选项（`strict`、`from_attributes`、`dec_hook`、`builtin_types`、`str_keys`）；传 decoder 时它的 `.strict` / `.dec_hook` 作为默认值，显式参数覆盖
- 这一族接受任意格式的 decoder，只取它的 `.type`，不会用它解码
- 非法类型注解仍然抛 `TypeError`

### 3.8 convert_with_error_detail

转换内存对象并在发生错误时解析异常，获取详细错误信息。输入是内存对象，所以没有二次解码，`data` 天然可用；`ErrorInfoDetail` 的字段、两个标记与定位边界见 3.2。

```python
def convert_with_error_detail(
        data: Any,
        model_or_decoder: Any,
        **kwargs,
) -> Tuple[Any, Union[ErrorInfoDetail, NODEFAULT]]: ...
```

- 标记为 `False` 的来源只有两个：`loc` 与 model / data 对不上，或者路径上没有值
- `msgspec.Raw` 字段：`convert` 拿不到字节，会先在报错位置钉入一个占位 `Raw` 再校验，真错误仍能定位；如果只有 `Raw` 字段报错，返回 `INPUT_REJECTED`（`loc=()`、无值），见 5.4
- `**kwargs` 的语义与 3.7 相同，定位用的也是同一套规则

### 3.9 convert_with_default

转换内存对象并在发生错误时自动回退默认值。`load_*_with_default` 处理的是负载，`convert_with_default` 处理已经解码好的内存对象，修复语义与它们一致：

```python
def convert_with_default(
        data: Any,
        model_or_decoder: Any,
        *,
        guess_default: bool = False,
        **kwargs,
) -> Tuple[Any, List[ErrorInfo]]: ...
```

- 成功 `(obj, [])`；失败时与 `load_*_with_default` 的结果一致
- **会就地修改 `data`**：修复写的是调用方传进来的 dict / list。需要保留原始数据的调用方请自己 `copy.deepcopy()`
- 修复失败时 `data` 与调用前完全一致，多错误路径也一样：前几轮写入的值会被撤回，键顺序也保留
- `from_attributes=True` 且 `data` 是对象时不修改对象：库先把属性读进一个 dict，在副本上修复，再解码出模型
- 模型里有 `msgspec.Raw` 字段时，会把该位置的值编码成 msgpack 回填，模型能正常构建（`msgpack.decode(result.payload)` 拿回原值）
- 不接受 `utf8_error`：内存对象没有字节编码问题
- `**kwargs` 是 `msgspec.convert` 的选项，decoder 的作为默认值，显式参数覆盖

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
# {'items': {'apple': {'amount': 0}}}   ← 输入被就地修改
print(errors)
# [ErrorInfo(msg='Expected `int`, got `str` - at `$.items[...].amount`',
#            type=<ErrorType.TYPE_MISMATCH>,
#            loc=('items', 'apple', 'amount'),
#            ctx=ErrorCtx(expected='int', got='str'))]
```

### 3.10 结构化解析异常

结构化解析异常。使用 parse_msgspec_error 可以解析任意异常。

```python
def parse_msgspec_error(error: Union[str, Exception]) -> ErrorInfo: ...
DECODE_ERRORS = (ValidationError, DecodeError, UnicodeDecodeError, UnicodeEncodeError)
```

注意：你应该使用 `except DECODE_ERRORS as e:` 捕获异常，而不是 `except msgspec.ValidationError as e:` 捕获异常。

msgspecerror 可以将更多错误类型封装进 ErrorInfo 对象，比如 bytes 负载里的非法 utf-8 产生的 `UnicodeDecodeError`，以及 str 负载里的孤立代理对产生的 `UnicodeEncodeError`（msgspec 会先把 str 负载编码成 utf-8 再解析）。

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
    #   （*_with_error_detail 系列会尝试解析出真实的键，见前言的使用场景二）
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
    # 内部提示：报错消息可能是一个被拒绝的 msgspec.Raw 字段，调用方一般不需要关心
    may_raw_rejection: bool = False
```

**把错误写进日志：`to_log_dict()`**

`ErrorInfo` 和 `ErrorInfoDetail` 都有 `to_log_dict()`，返回一个可以直接交给 `msgspec.json.encode()` 的普通 dict。键集合恒定（不会因为默认值被省略），日志消费方可以按固定 schema 读取：

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

`ErrorInfoDetail.to_log_dict()` 比 `ErrorInfo` 多三个键：`error_value`、`loc_resolved`、`error_value_resolved`。没有取到值时 `error_value` 是 `None`，用 `error_value_resolved` 区分「值是 `None`」和「没有值」。

`error_value` 是调用方的原始对象，不保证可以 json 编码（`convert_*` 那条路上可以是任意 Python 对象、循环引用），所以日志请统一用 `to_log_dict()`，而不是直接 `msgspec.json.encode(error)`。详见 5.6。

## 4. 异常类型 ErrorType

所有 `ErrorType` 枚举成员及其对应的错误消息格式。`<Path>` 表示类似 `$.field` 的 JSONPath 路径。

### 4.1 第1组：类型不匹配错误

**`TYPE_MISMATCH`** — 值与期望类型不匹配
- 格式：`` Expected `<A>`, got `<B>` - at <Path> ``
- 示例：
  - `` Expected `int`, got `str` - at `$.age` ``
  - `` Expected `str`, got `int` - at $.name ``
  - `` Expected `MyCustomClass`, got `str` - at `$.custom_field` ``
  - `` Expected `int`, got `str` - at `$.type` ``（tagged unions）
- 触发条件：
  1. **一般不匹配**：解码值的类型与目标 Python 类型不符
  2. **MsgPack 不匹配**：MsgPack 编码值无法转换为目标 Python 类型
  3. **自定义类型不匹配**：`dec_hook` 返回的对象不是所期望的自定义类型
  4. **标签类型不匹配**：tagged union 中标签字段的类型不正确
  5. **布尔字面量不匹配**（0.22.0+）：`Literal[True]` / `Literal[False]` 收到了非布尔值，期望类型显示为 `bool`
- 0.22.0 变更：`msgspec.Raw` 的期望类型由误导性的 `any` 改为 `raw`
  - `` Expected `raw`, got `int` - at `$.payload` ``（convert / yaml.decode / toml.decode）

**`TOKEN_TYPE_MISMATCH`** — JSON token 类型与解码位置期望不符
- 格式：`` Expected `<type>` - at <Path> ``（无 ", got" 部分）
- 示例：
  - `` Expected `str` - at `$.kind` ``（JSON：tag 字段值不是字符串）
  - `` Expected `int` - at `$.kind` ``（JSON：tag 字段值不是整数）
  - `` Expected `str` - at `key` in `$` ``（convert：字典键不是字符串）
- 触发条件：
  1. **标签值不匹配**（JSON）：tagged union 中的标签字段具有错误的 JSON token 类型
  2. **映射键不匹配**（convert）：`msgspec.convert()` 中的字典键不是字符串——目标类型（Struct/TypedDict/dataclass）仅支持字符串字段名
  3. **Token 类型不匹配**（JSON）：解码器在当前位置期望 `int` 或 `str`，但下一个 JSON token 类型不符

### 4.2 第2组：结构错误

**`MISSING_FIELD`** — 缺失必填字段
- 格式：`` Object missing required field `<field_name>` - at `<Path>` ``
- 示例：`` Object missing required field `id` ``
- 触发条件：Struct、TypedDict 或 dataclass 缺少没有默认值的必填字段

**`UNKNOWN_FIELD`** — 出现未知字段
- 格式：`` Object contains unknown field `<field_name>` - at `<Path>` ``
- 示例：`` Object contains unknown field 'favorite_color' - at `$` ``
- 触发条件：解码 `forbid_unknown_fields=True` 的 Struct 时，消息中出现了未定义的字段

### 4.3 第3组：约束与长度错误

**`ARRAY_LENGTH_CONSTRAINT`** — 数组长度不符
- 格式：
  - `` Expected `array` of at (least|most) length <expected>, got <actual> - at <Path> ``
  - `` Expected `array` of length <min> to <max>, got <actual> - at <Path> ``
  - `` Expected `array` of length <expected> - at <Path> ``
- 示例：`` Expected `array` of length 2, got 3 - at $.coordinates ``
- 触发条件：解码定长 tuple 或 NamedTuple 时，数组元素个数不匹配
- 注意（0.19.0+）：存在不带 `- at <Path>` 后缀的变体，是 msgspec 的已知 bug

**`OBJECT_LENGTH_CONSTRAINT`** — 对象长度约束
- 格式：`` Expected `object` of length <op> <value> - at <Path> ``
- 示例：`` Expected `object` of length >= 1 - at $.metadata ``
- 触发条件：字典类对象（dict、TypedDict、Struct）违反了 `Meta` 中的 `min_length`/`max_length` 约束

**`LENGTH_CONSTRAINT`** — 长度约束
- 格式：`` Expected `<type>` of length <op> <value> - at <Path> ``
- 示例：`` Expected `str` of length <= 32 - at $.name ``
- 触发条件：可变长序列（list、set、frozenset）或定长标量（str、bytes）违反了 `Meta` 中的 `min_length`/`max_length` 约束

**`PATTERN_CONSTRAINT`** — 正则约束
- 格式：`` Expected `str` matching regex <pattern> - at <Path> ``
- 示例：`` Expected `str` matching regex '\\d{4}-\\d{2}-\\d{2}' - at $.date_str ``
- 触发条件：字符串未匹配 `Meta` 中的 `pattern` 正则约束

**`NUMERIC_CONSTRAINT`** — 数值约束
- 格式：
  - `` Expected `<type>` <op> <value> - at <Path> ``
  - `` Expected `<type>` that's a multiple of <value> - at <Path> ``
- 示例：`` Expected `int` >= 0 - at $.age ``、`` Expected `int` that's a multiple of 6 ``
- 触发条件：数值违反了 `Meta` 中的 `ge`/`lt`/`multiple_of` 等约束

**`TIMEZONE_CONSTRAINT`** — 时区约束
- 格式：
  - `` Expected `datetime` with (a|no) timezone component - at <Path> ``
  - `` Expected `time` with (a|no) timezone component - at <Path> ``
- 触发条件：datetime/time 不满足 `Meta` 中 `tz=True`（需要时区）或 `tz=False`（不需要时区）的约束

### 4.4 第4组：无效值错误

**`INVALID_ENUM_VALUE`** — 无效的枚举值
- 格式：`"Invalid enum value <value> - at <Path>"`
- 示例：`"Invalid enum value 'admin' - at $.role"`
- 示例（0.22.0+）：`` "Invalid enum value False - at `$.enabled`" ``——布尔字面量类型（`Literal[True]` / `Literal[False]`）收到了相反的布尔值
- 触发条件：值不是目标 `Enum` 或 `Literal` 类型的有效成员

**`INVALID_TAG_VALUE`** — 无效的 tag 值
- 格式：`"Invalid value <value>"` 或 `` Invalid value `<value>` - at <Path> ``
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
- 触发条件：
  1. 表示数字的字符串超出了 `double` 精度浮点数的范围
  2. （0.22.0+）`msgspec.convert()` 将过大的 `int` 转为 `float` 目标类型

### 4.6 第6组：包装错误及其他

**`WRAPPED_ERROR`** — 包装的用户代码错误
- 格式：`"<原始错误消息> - at <Path>"`
- 示例：`"passwords cannot be the same - at $"`
- 触发条件：msgspec 捕获了用户代码（如 `dec_hook`、`__post_init__`、自定义类型 `__init__`）中的 `TypeError` 或 `ValueError`，包装为 `ValidationError`

**`UNICODE_DECODE_ERROR`** — Unicode 编解码错误（msgspecerror 自定义）
- 格式：`"<codec>' codec can't decode byte <byte>..."`
- 格式：`"<codec>' codec can't encode character <char>..."`
- 示例：
  - `"'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"`
  - `"'utf-8' codec can't decode bytes in position 0-1: unexpected end of data"`
  - `"'utf-8' codec can't encode character '\ud800' in position 7: surrogates not allowed"`
- 触发条件：
  - bytes 负载包含无效的 unicode
  - str 负载含孤立代理对：msgspec 会先把 str 负载编码成 utf-8 再解析，因此与 bytes 负载同属一类错误

**`JSON_MALFORMED`** — JSON 格式错误
- 格式：`"JSON is malformed: <reason> (byte <pos>)"`
- reason 包括：`invalid character`、`trailing characters`、`expected ',' or ']'`、`expected ',' or '}'`、`expected ':'`、`expected '"'`、`trailing comma in array`、`trailing comma in object`、`object keys must be strings`、`invalid number`、`invalid escape character in string`、`invalid character in unicode escape`、`invalid utf-16 surrogate pair`、`unexpected end of hex escape`、`unexpected end of escaped utf-16 surrogate pair`、`invalid escaped character`
- 触发条件：任何不符合 JSON 语法的文本输入

**`MSGPACK_MALFORMED`** — MsgPack 格式错误
- 格式：`"MessagePack data is malformed: <reason> (byte <pos>)"`
- reason 包括：`trailing characters (byte <pos>)`、`invalid opcode '\x<XX>' (byte <pos>)`、`map keys must be hashable (byte <pos>)`（0.22.0+）
- 触发条件：任何不符合 MessagePack 二进制格式的数据，例如完整消息后的多余字节、无法识别的操作码，或 map 键解码为不可哈希对象

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
  - `"Input rejected: a msgspec.Raw field can not be built from an in-memory object"` — `convert` 无法从已解码对象构造 `msgspec.Raw` 字段
  - `"Input rejected: a msgspec.Raw field can not be built from a decoded object"` — 当前 msgspec 不接受 `Raw` 实例，对象无法重建
- 触发条件：msgspecerror 无法修复输入，或输入无法判定（`convert_with_error_detail` 遇到带 `msgspec.Raw` 字段的模型且没有别的错误，或该位置的值 msgpack 编不出），见 5.4
- 注意：此类型由 msgspecerror 内部使用，msgspec 本身不会产生这类错误


## 5. 局限

### 5.1 类型提示可能不正确

所有接受 `model_or_decoder` 的函数（三个 `*_with_default`、三个 `*_with_error`、三个 `*_with_error_detail`）都同时接受模型和 decoder，一些 IDE 或者类型检查工具可能无法正确提示返回值的类型。

```python
class ItemInfo(msgspec.Struct):
    amount: int = 0
result, errors = load_json_with_default(..., ItemInfo)
# result: ItemInfo
decoder = msgspec.json.Decoder(ItemInfo)
result, errors = load_json_with_default(..., decoder)
# result: ItemInfo
```

### 5.2 定位与修复会再校验一次数据

msgspec 不会告诉是哪个字典条目失败，所以定位与修复必须在原始数据上再校验一遍。条目较多时会批量交给 msgspec 一次校验并取回失败的下标，开销随条目数线性增长；但整体仍然比直接 `msgspec.convert` 贵，`_with_default` 还可能要重复多轮。

更要紧的是**用户代码会被重放**：定位与修复用的是调用方自己的校验规则，所以 `dec_hook`、`Struct.__post_init__` 会在通过校验的条目上再执行一次，副作用会发生两次。这一点无法在保持定位精度的前提下消除。

如果你希望减少这种开销：

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

### 5.3 校验大型msgpack发生多个UnicodeDecodeError时，自动修复可能性能不佳

因为 msgpack 是二进制数据，我们无法直接转换为 str 来处理错误的 unicode。

1. 当发生 UnicodeDecodeError 时，msgspecerror 首先进入快速修复模式，查找发生错误的 unicode 字符，如果字符所在区域是 msgpack string 且只出现一次，那么对错误字符进行精确修复。
2. 如果快速修复一次之后仍然发生 UnicodeDecodeError 或者有随机二进制内容与错误 unicode 字符发生碰撞，那么进入慢速修复模式。在慢速修复中手动解析 msgpack 寻找 string，逐个进行修复。

慢速修复模式保证了我们能够在线性时间内检查所有 string 对象，不会因恶意构造的含有大量错误字符的输入，而导致修复开销急剧膨胀。但是慢速修复模式终归是使用纯 python 实现的，如果你的输入内容结构非常复杂，自动修复可能性能不佳。

### 5.4 `msgspec.Raw` 字段

`Raw` 存的是负载的原始字节，而 `convert_*` 族拿到的是已经解码好的对象，没有字节可给这个字段。

- `load_*` 族正常：typed decode 能构建 `Raw` 字段
- `convert_with_error` 忠实返回 `convert` 自己的异常（它在 `Raw` 字段上停下），这不代表数据有错
- `convert_with_error_detail` 会在报错位置钉入一个占位 `Raw` 再校验，真错误仍能定位；如果只有 `Raw` 字段报错，返回 `INPUT_REJECTED`（`loc=()`、无值）
- `convert_with_default` 会把该位置的值编码成 msgpack 回填，模型能构建，`msgpack.decode(result.payload)` 拿回原值

另外，`_with_default` 修复含 `Raw` 的 json 文档时，`Raw` 的原始字节会被重新编码：值不变，但空白、转义等格式丢失；msgpack 的字节完全一致。需要字节级保真（签名、内容寻址）时不要用 `_with_default`。

### 5.5 容器与 `from_attributes` 的支持范围

定位一侧按「msgspec 接受什么」判定：对象可以是任意 `Mapping`（dict 子类、`UserDict`、`ChainMap`、只读 mapping），数组可以是 list / tuple（含子类）。

修复一侧只写 dict（含子类）和 list（含子类）。`UserDict`、`ChainMap`、`MappingProxyType` 这类对象不会被修复，结果是 `loc=('...',)` 且输入不动。

`from_attributes=True` 只对 `convert_*` 两族有效（`load_*` 解出来的负载是 dict，不会是对象）。定位族用 `getattr()` 读字段，所以只读属性和 property 都能读；修复族在属性副本上工作，调用方的对象不会被修改。

### 5.6 `error_value` 与损坏的位置

`error_value` 原样交付出错的值。`convert_*` 那条路上它可以是 msgspec 不认识的类、`complex`，或 YAML 锚点造出的循环引用，这些 `msgspec.json.encode(detail)` 一律拒绝。写日志请用 `detail.to_log_dict()`：它保证可以编码，`error_value` 会转成可编码形式（`bytes` 变 base64、自定义对象变 `repr()` 文本），而 `detail.error_value` 本身仍是原对象。

如果 `loc` 损坏（不是 tuple、含 `None` / `bool` / list 等非法段），或者与 `data` / `model` 对不上，定位不会抛异常，而是交付 `loc=()` + 无值。此时读 `msg` 里的原始路径，不要依赖空的 `loc`。

### 5.7 路径歧义

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
