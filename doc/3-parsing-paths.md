# msgspec 错误路径解析指南

> 本文面向需要编写 msgspec `ValidationError` 消息解析器的开发者。
>
> 解析目标：从错误消息中提取出结构化的路径信息，例如将 `` `$.user.addresses[0].city` `` 解析为 `["user", "addresses", 0, "city"]`。

## 一、错误消息格式

msgspec 的验证错误消息有以下几种格式：

```
Expected `int`, got `str` - at `$.path.here`
Expected `int`, got `str` - at `$`
Invalid enum value "foo" - at `$.path.here`
Missing required field `name` - at `$.path.here`     (仅 forbid_unknown_fields=False)
Object missing required field `name`                   (顶级)
```

核心部分是 `- at \`<path>\`` 后缀（可能不存在，如 `Object missing required field`）。路径部分由反引号包裹。

## 二、路径格式（BNF）

```
path        = "`$" component* "`"
component   = "." field_name
            | "[" number "]"
            | "[...]"
field_name  = <任意的 unicode 字符串，不含 `\`、"`"、控制字符>
```

三种节点类型：

| 语法 | 含义 | 来源 |
|------|------|------|
| `.field` | 结构体字段 | 结构体 `Struct` 的字段名 |
| `[0]` `[1]` `[n]` | 数组索引 | `List[T]` 中的具体元素 |
| `[...]` | dict 键 / 未知索引 | `Dict[K, V]` 的键（键名被隐藏）|

### 路径示例

```
`$`                         根，没有字段
`$.name`                    根 → 字段 name
`$.user.addresses`          根 → 字段 user → 字段 addresses
`$.data[0]`                 根 → 字段 data → 数组索引 0
`$.data[0].city`            根 → 字段 data → 数组索引 0 → 字段 city
`$.items[...].val`          根 → 字段 items → dict 键（隐藏）→ 字段 val
`$.items[...][1]`           根 → 字段 items → dict 键（隐藏）→ 数组索引 1
```

## 三、msgspec 强制禁止的字符（不会出现在路径中）

`structmeta_construct_encode_fields`（`_core.c` 第 6282 行）通过 `json_str_requires_escaping` 拦截以下字符。作为解析器，你可以假设**路径中永远不会出现**这些字符：

| 字符 | 原因 | 错误信息 |
|------|------|---------|
| `\`（反斜杠） | JSON 转义字符 | `ValueError: Renamed field names must not contain '\'...` |
| `"`（双引号） | JSON 转义字符 | 同上 |
| U+0000–U+001F（控制字符）含 `\t` `\n` `\r` | JSON 控制字符 | 同上 |

这意味着字段名中不会包含以下路径语法中的元字符：`\` 被禁，`"` 被禁。但 `.`、`[`、`]`、`$`、`` ` `` 没有被禁。

## 四、不同正确解析的字段名（解析器的 Ambiguity 边界）

以下字段名会导致路径文本有**多种合法的解析结果**，解析器无法区分。建议在文档中声明"不支持"。

### 4.1 字段名包含 `.`（句点）

```
rename="a.b"  → 路径 `$.a.b`
```

歧义：这是字段 `a.b`，还是字段 `a` → 子字段 `b`？

解析器无法从文本上区分这两种情况。除非你从类型系统的上下文推断，否则不可消除歧义。

**结论：无法可靠解析。遇到字段名中包含 `.` 的路径，统一解析为按 `.` 分隔，忽略字段名存在 `.` 的可能。用户需要避免使用含有 `.` 的字段名。**

### 4.2 字段名包含 `[数字]` 模式（数组索引冲突）

```
rename="user[0]"  → 路径 `$.user[0]`
```

歧义：这是字段 `user[0]`，还是字段 `user` → 数组索引 0？

更致命的是，这两种情况在真实 msgspec 中都会出现：

```python
# A: 字段名为 "user[0]"
class T1(msgspec.Struct):
    val: int = msgspec.field(name="user[0]")
# decode {"user[0]":"bad"}  → `$.user[0]`

# B: 数组索引
class T2(msgspec.Struct):
    user: List[int]
# decode {"user":["bad"]}   → `$.user[0]`  ← 完全相同的文本
```

**结论：无法可靠解析。遇到 `[数字]` 模式的字段名路径，统一解析为数组索引，用户需要避免使用类似 `user[0]` 的字段名。**

### 4.3 字段名包含切分关键词

下面是解析器用来切分错误信息的关键词：

```python
KEY_at = ' - at `$'
KEY_at_key_in = ' - at `key` in `$'
```

**结论：解析结果无法预期。用户需要避免使用这样的字段名。**

## 五、需要仔细测试的字段名（解析正确性依赖实现细节）

以下字段名不会破坏路径格式，也不会产生不可消除的歧义，但解析器需要特殊处理才能正确解析。

### 5.1 字段名包含 `$`（美元符）

```
rename="$.x"  → 路径 `$.$.x`
rename="$"    → 路径 `$.$`
```

`$` 在路径中的含义取决于位置：
- 在 `` ` `` 之后第一位 → 根标记
- 在 `.` 之后 → 字段名的一部分

所以 `$.$.x` 的解析：根（固定位）→ 字段 `$.x`。规则明确，没有歧义。

**解析策略**：硬编码路径开头 `` `$ `` 的 `$` 为根标记，之后所有的 `$` 都是字段名字符。

### 5.2 字段名包含 `[*]` 且括号内不是纯数字

```
rename="[x]"     → 路径 `$.[x]`
rename="user[]"  → 路径 `$.user[]`
rename="[[]"     → 路径 `$.[[]`
rename="[]"     → 路径 `$.[]]`
```

这些路径与数组索引 `[0]` 或 dict 键 `[...]` 的区别在于括号内内容：
- `[数字]` → 数组索引
- `[...]` → dict 键（三个点）
- `[其他内容]` → 字段名的一部分

### 5.3 空字符串字段名

```
rename=""  → 路径 `$.`
```

路径末尾有一个多余的 `.`，没有字段名跟在后面。

又或者有多个空字符串作为字段名：

```
 `$..` → ("", "")
 `$[1].` → ("", 1)
 `$[...].` → ("...", "")
 `$.[1].` → ("", 1, "")
 `$.[x].` → ("[x]", "")
 `$.[...].` → ("", "...", "")
```

**解析策略**：解析 `.` 后的字段名时，允许空字符串作为有效字段名。测试应覆盖空字段名作为路径组件的情况。

### 5.4 字段名包含 `` ` ``

```
Object contains unknown field `RepairThresho` ld1` - at `$.op`si`
→ ("op`si", "RepairThresho` ld1")
Expected `MyCustomClass`, got `str` - at `$.custom`field`
→ ("custom`field")
Expected `int`, got `str` - at `$.`items[0]`
→ ("`items", 0)
```

**解析策略**：前缀内容中的 `expected`, `got` 等 都是符合 python 语法的对象，不含有 `` ` ``，因此可以一步一步切分字符串找到正确的开头。对于有两个自定义路径组件的 `MISSING_FIELD` 和 `UNKNOWN_FIELD`，使用 `` KEY_at = ' - at `$' `` 进行切分。

