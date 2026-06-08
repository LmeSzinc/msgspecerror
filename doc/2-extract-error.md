# AI 操作手册 #2：检查 msgspec 更新并同步错误格式

## 目的

`msgspecerror/const.py` 中的 `ErrorType` 枚举定义了 msgspec 及其关联操作中可能抛出的异常格式分类。
当新版本的 msgspec 引入了新的错误消息格式时，需要同步更新 `const.py` 以保持覆盖完整。

---

## 核心原则：阅读 C 源码，而非机械比对

`_core.c` 中的错误消息由 C 代码动态构造。字符串字面量可能被 printf 格式串（`%s`、`%U`、`%zd` 等）拆散、跨行拼接，或被 `if/else` 分支按条件组合。**仅靠静态字符串搜索或程序化比对，一定会遗漏隐藏的错误格式分支**。

正确方法：逐段阅读 `_core.c` 中构造异常对象的代码路径，理解每条错误消息的完整形状和触发条件。

---

## msgspec 异常体系总览

msgspec decode/encode 过程中可能抛出以下异常，按来源分三类：

### msgspec 原生异常

| 异常类 | 枚举名称 | 说明 |
|---|---|---|
| `msgspec.ValidationError` | `ErrorType.*` | 数据校验失败，值不符合 schema。这是 `const.py` 主要覆盖的范围。 |
| `msgspec.DecodeError` | `JSON_MALFORMED` / `MSGPACK_MALFORMED` | 输入数据格式错误（非法 JSON/MsgPack 结构）。 |
| `msgspec.EncodeError` | `ENCODE_ERROR` | 编码时数据超限（超过 MsgPack 32-bit 长度限制）。 |


### Python 内置异常（msgspec 透传，不包装）

| 异常类 | 触发场景 |
|---|---|
| `UnicodeDecodeError` | 输入 `bytes` 中包含非法 UTF-8 序列，`PyUnicode_DecodeUTF8()` 失败 |
| `UnicodeEncodeError` | 输入 `str` 中包含 surrogate（如 `\ud800`），`str.encode('utf-8')` 失败 |

这些异常由 Python 解释器直接抛出，msgspec 的 C 代码不拦截不包装。它们与 `JSON_MALFORMED` 的区别：

| 特征 | `JSON_MALFORMED` | `UnicodeDecodeError` | `UnicodeEncodeError` |
|---|---|---|---|
| 消息前缀 | `JSON is malformed:` | `'utf-8' codec can't decode byte ...` | `'utf-8' codec can't encode ...` |
| 异常类型 | `msgspec.DecodeError` | `UnicodeDecodeError` | `UnicodeEncodeError` |
| 输入类型 | `bytes`（JSON 语法非法） | `bytes`（UTF-8 解码失败） | `str`（含 surrogate） |
| msgspec 定义 | 可枚举的固定格式 | Python 动态生成 | Python 动态生成 |

---

## 流程

### 1. 提取新版 `_core.c`

如果还没有提取新版 `_core.c`，先参考 `doc/1-extract-source.md` 完成提取。

```
msgspeccore/
├── 0.18.6/_core.c
├── 0.19.0/_core.c
├── 0.20.0/_core.c
├── 0.21.0/_core.c
└── 0.21.1/_core.c    ← 已有最新版本
└── 0.22.0/_core.c    ← 新增
```

### 2. 阅读 C 源码，提取异常格式

打开新版 `_core.c`，根据目标异常类型搜索不同的入口点：

#### 目标 A：`ValidationError` — 搜索 `raise_validation_error`

| 搜索目标 | 说明 |
|---|---|
| `raise_validation_error(` | ValidationError 抛出的集中入口 |
| `ms_raise_validation_error(` | 另一种 ValidationError 抛出函数 |
| `ms_validation_error(` | 构造 ValidationError 对象但不抛出的辅助函数 |

每个调用点的格式字符串（含 `%U`）即为一条候选 `ErrorType` 定义。

#### 目标 B：`DecodeError` — 搜索 `json_err_invalid` / `ms_err_truncated` / `mpack`

`DecodeError` 有三个来源：

**1. `json_err_invalid(self, reason)` — JSON 解析错误**

搜索 `json_err_invalid(self,` 可找到所有 JSON 解析错误原因。当前已知的 17 种 reason：

```
invalid character
trailing characters
expected ',' or ']'
expected ',' or '}'
expected ':'
expected '"'
trailing comma in array
trailing comma in object
object keys must be strings
invalid number
invalid escape character in string
invalid character in unicode escape
invalid utf-16 surrogate pair
unexpected end of hex escape
unexpected end of escaped utf-16 surrogate pair
invalid escaped character
```

消息格式为 `"JSON is malformed: <reason> (byte <pos>)"`。

**2. `ms_err_truncated()` — 数据截断**

搜索 `ms_err_truncated()` 找到所有数据截断的调用点。消息固定为 `"Input data was truncated"`。

**3. 搜索 `MessagePack` + `PyErr_Format` — MsgPack 格式错误**

```
"MessagePack data is malformed: trailing characters (byte %zd)"
"MessagePack data is malformed: invalid opcode '\\x%02x' (byte %zd)"
```

#### 目标 C：`EncodeError` — 搜索 `EncodeError`

`EncodeError` 固定格式：

```
"Can't encode %s longer than 2**32 - 1"
"Can't encode bytes-like objects longer than 2**32 - 1"
"Can't encode strings longer than 2**32 - 1"
"Can't encode Ext objects with data longer than 2**32 - 1"
```

### 3. 分析新增异常

根据 C 源码的原始构造逻辑，判断新增的异常格式属于哪一类别：

- **ValidationError** 格式 → 需要新增或扩展现有 `ErrorType` 成员
- **JSON 解析错误** → 添加到 `JSON_MALFORMED` 的 reason 列表
- **MsgPack 解析错误** → 添加到 `MSGPACK_MALFORMED` 的 reason 列表
- **编码超限** → 添加到 `ENCODE_ERROR` 的 example 列表

- **Python 内置异常**（UnicodeDecodeError / UnicodeEncodeError） → 不属于 msgspec 定义的异常格式，无法枚举所有可能的消息，只需在 `UNICODE_DECODE_ERROR` 中注明即可

### 4. 更新 `const.py`

#### ValidationError 格式

如果需要新增 `ErrorType` 成员，按照以下模板添加：

```python
NEW_ERROR_TYPE = "NEW_ERROR_TYPE"
"""
Format: "<用户实际看到的错误格式>"
Example: "<具体输出示例>"
Triggered by: <触发条件>
"""
```

保持枚举成员的命名风格：
- 类型错误 → `TYPE_MISMATCH`、`TOKEN_TYPE_MISMATCH`
- 字段错误 → `MISSING_FIELD`、`UNKNOWN_FIELD`
- 约束错误 → `LENGTH_CONSTRAINT`、`PATTERN_CONSTRAINT`
- 无效值错误 → `INVALID_XXX`
- 范围错误 → `XXX_OUT_OF_RANGE`
- 其他错误 → 按功能命名，放在 Group 6

如果只是修改了已有错误消息的格式，更新对应 `ErrorType` 的 docstring 即可。

#### 非 ValidationError 格式

对于 `JSON_MALFORMED`、`MSGPACK_MALFORMED`、`ENCODE_ERROR`，
这些成员不追踪精确的格式字符串，而是记录异常类和已知的消息模式。
新增的 reason 或 example 直接追加到对应成员的 docstring 列表中即可。

### 5. 验证

确保更新后 `const.py` 仍然是合法的 Python 代码：

```bash
python -c "from msgspecerror.const import ErrorType; print(ErrorType)"
```

同时检查 `msgspecerror` 包的其他模块是否引用了新增的枚举成员，如果引用了，需要同步更新。

---

## C 源码阅读指南

`_core.c` 约 22000 行。以下是定位错误构造代码的重点区域：

### `raise_validation_error` 函数

这是 ValidationError 抛出的集中入口。搜索 `raise_validation_error(` 找到所有调用点。

### `json_err_invalid` 函数

这是 `DecodeError("JSON is malformed: ...")` 的构造入口。搜索 `json_err_invalid(self,` 找到所有 17 种 reason。

### 常见错误构造模式

```c
// ValidationError
raise_validation_error("Expected `float`%U", path);

// DecodeError - JSON malformed
return json_err_invalid(self, "trailing characters");

// DecodeError - truncated
if (MS_UNLIKELY(!json_remaining(self, len))) return ms_err_truncated();

// EncodeError
PyErr_SetString(self->mod->EncodeError, "Can't encode strings longer than 2**32 - 1");

// Definition Error (TypeError)
PyErr_SetString(PyExc_TypeError, "All base classes must be types");
```

### UnicodeDecodeError 透传

`PyUnicode_DecodeUTF8(s, size, NULL)` 在以下位置被调用。若数据包含非法 UTF-8 序列，Python 会直接抛出 `UnicodeDecodeError`，msgspec 的 C 代码没有捕获或重新包装：

```
src/msgspec/_core.c:
  ~5264  ms_invalid_cstr_value()
  ~15451 mpack_decode_str()
  ~17621 JSON 字符串值解码
  ~17675 JSON 字符串值解码（custom type）
```

---

## 注意点

- **不要依赖字符串搜索**：printf 格式串可能被宏展开或条件编译改变，静态度量只能作为提示，不能作为证据
- **不要跳过 if/else 分支**：错误消息的条件分支才是真正的完整形态，需要穷举所有路径
- **path 后缀不总是出现**：当错误发生在根路径时，`- at <Path>` 可能被省略，需要在 C 代码中确认
- **不是每个版本都有变化**：msgspec 的错误消息体系相对稳定，通常只有大版本或新增数据类型时才会有变化
- **不要移除旧的 `ErrorType` 成员**：即使上游已不再产生该错误，保留它可以解析历史日志
- **每个新增的 `ErrorType` 成员都要附 Format / Example / Triggered by 三段注释**
- **`UnicodeDecodeError` / `UnicodeEncodeError` 不是 msgspec 定义的错误**，它们是 Python 内置异常，消息由 Python 运行时动态生成。无需也无法枚举所有可能的消息格式，只需确认异常类型即可区分
- **区分 `DecodeError` 和 `ValidationError`**：前者是输入数据格式错误（JSON 语法、MsgPack 结构），后者是数据格式正确但值不符合 schema 约束
