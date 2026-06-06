# AI 操作手册 #2：检查 msgspec 更新并同步错误格式

## 目的

`msgspecerror/const.py` 中的 `ErrorType` 枚举定义了所有 msgspec 可能抛出的 `ValidationError` 错误格式。当新版本的 msgspec 引入了新的错误消息格式时，需要同步更新 `const.py` 以保持覆盖完整。

---

## 核心原则：阅读 C 源码，而非机械比对

`_core.c` 中的错误消息由 C 代码动态构造。字符串字面量可能被 printf 格式串（`%s`、`%U`、`%zd` 等）拆散、跨行拼接，或被 `if/else` 分支按条件组合。**仅靠静态字符串搜索或程序化比对，一定会遗漏隐藏的错误格式分支**。

正确方法：逐段阅读 `_core.c` 中构造 `ValidationError` 的代码路径，理解每条错误消息的完整形状和触发条件。

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

### 2. 阅读 C 源码，提取错误格式

打开新版 `_core.c`，搜索以下函数和宏——它们是构造 `ValidationError` 的核心入口：

| 搜索目标 | 说明 |
|---|---|
| `ValidationError` (C 函数定义) | 构造错误对象的函数原型和实现 |
| `ms_err_msg` / `err_msg` | 格式化并抛出错误的辅助函数 |
| `raise_validation_error` | msgspec 的 C 层抛出错误 |
| `PyErr_SetObject` + `ValidationError` | 设置 Python 异常对象的路径 |
| 各 decoder 中的 `goto error` 标签 | 解码失败后跳转到错误处理代码块 |

对于每个错误构造点，需要做以下分析：

#### a. 追溯格式字符串的最终形态

C 代码中可能这样写：

```c
if (cond_a) {
    err_msg = "Expected `int`%U";
} else if (cond_b) {
    err_msg = "Expected `int`, got `str`%U";
} else {
    err_msg = "Expected `int` >= 0%U";
}
```

需要对照 `if/else` 条件，列出每种分支组合下用户实际看到的错误消息。

#### b. 追踪 path 后缀的拼接规则

path 部分（` - at <Path>`) 很可能被单独拼接：

```c
if (path != NULL) {
    path_suffix = format_path(path);
    final_msg = concat(err_msg, path_suffix);
}
```

需要确认什么条件下 path 会被省略，什么条件下会附加。

#### c. 检查 `%U` 之外的自定义格式化

msgspec 的 C 代码使用自定义 `%U` 格式说明符来包裹反引号。确认是否有其它自定义格式符会导致字符串布局与静态搜索到的结果不同。

#### d. 对比旧版源码的 diff

直接 diff 两版 `_core.c`，聚焦于错误构造相关的代码区域：

```
git diff msgspeccore/0.21.1/_core.c msgspeccore/0.22.0/_core.c
```

关注：
- `raise_validation_error` 相关修改
- 新增或删除的 `err_msg` 赋值
- 错误条件分支的变化

### 3. 分析新增错误消息

通过阅读 C 源码的原始构造逻辑，判断新增的错误格式属于哪一类别：

判断标准：
- 如果新增的消息可以被已有的某个 `ErrorType` 的格式覆盖 → 无需新增
- 如果新增的消息是一种全新的格式模式 → 需要新增 `ErrorType` 成员
- 如果新增的消息是对已有消息的变体（如补充了更多细节）→ 扩展现有 `ErrorType` 的 docstring 注释

### 4. 更新 `const.py`

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
- 类型错误 → `TYPE_MISMATCH`、`UNEXPECTED_TOKEN`
- 字段错误 → `MISSING_FIELD`、`UNKNOWN_FIELD`
- 约束错误 → `LENGTH_CONSTRAINT`、`PATTERN_CONSTRAINT`
- 无效值错误 → `INVALID_XXX`
- 范围错误 → `XXX_OUT_OF_RANGE`

如果只是修改了已有错误消息的格式，更新对应 `ErrorType` 的 docstring 即可。

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

这是错误抛出的集中入口。搜索 `raise_validation_error(` 找到所有调用点。

### 常见错误构造模式

```c
// 模式 1：直接构造带 path 的错误
raise_validation_error("Expected `float`%U", path);

// 模式 2：带条件分支的错误格式
if (value_is_float) {
    raise_validation_error("Expected `float`%U", path);
} else {
    raise_validation_error("Expected `int`, got `str`%U", path);
}

// 模式 3：通过 switch/case 分发不同错误
switch (err_type) {
    case ERR_MISSING_FIELD:
        raise_validation_error("Object missing required field `%U`%U", field_name, path);
        break;
    case ERR_UNKNOWN_FIELD:
        raise_validation_error("Object contains unknown field `%U`%U", field_name, path);
        break;
}
```

### 关键索引（基于 0.21.1）

以下行号仅作参考，不同版本会有偏移：

| 区域 | 大致位置 | 说明 |
|---|---|---|
| `raise_validation_error` 定义 | ~12500–12600 | 错误抛出的底层函数 |
| `JSONDecoder` 中的错误 | ~6000–9000 | JSON 解码时的校验错误 |
| `MsgPackDecoder` 中的错误 | ~10000–12500 | MsgPack 解码时的校验错误 |
| `Convert` 中的错误 | ~13000–16000 | `msgspec.convert()` 时的校验错误 |
| 约束验证错误 | ~5000–6000 | `min_length`、`max_length`、`ge`、`le` 等约束触发 |

---

## 注意点

- **不要依赖字符串搜索**：printf 格式串可能被宏展开或条件编译改变，静态度量只能作为提示，不能作为证据
- **不要跳过 if/else 分支**：错误消息的条件分支才是真正的完整形态，需要穷举所有路径
- **path 后缀不总是出现**：当错误发生在根路径时，`- at <Path>` 可能被省略，需要在 C 代码中确认
- **不是每个版本都有变化**：msgspec 的错误消息体系相对稳定，通常只有大版本或新增数据类型时才会有变化
- **不要移除旧的 `ErrorType` 成员**：即使上游已不再产生该错误，保留它可以解析历史日志
- **每个新增的 `ErrorType` 成员都要附 Format / Example / Triggered by 三段注释**
