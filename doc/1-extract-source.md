# AI 操作手册 #2：提取 msgspec 源码

从 msgspec 源码提取各个版本 `_core.c` 文件，来让未来编写代码的 AI 更方便地参照源码，不需要每次使用 git 命令翻找历史文件。

## 目的

`msgspeccore/` 目录保存了 msgspec 各个版本的 `_core.c` 源文件，按版本号分目录存放：

```
msgspeccore/
├── 0.18.6/_core.c
├── 0.19.0/_core.c
├── 0.20.0/_core.c
├── 0.21.0/_core.c
└── 0.21.1/_core.c
```

当需要追踪新版本或补全缺失版本时，执行以下流程。

---

## 流程

### 1. 拉取最新标签

`msgspecsource` 是 `https://github.com/msgspec/msgspec` 的 git 子模块。
进入子模块目录并拉取最新标签：

```bash
cd msgspecsource
git fetch --tags origin
git tag --list --sort=-version:refname
```

输出示例：

```
0.21.1
0.21.0
0.20.0
0.19.0
0.18.6
...
```

### 2. 比对已存在的版本

查看 `msgspeccore/` 下已有的版本目录，与上游标签做比对，找出缺失或需要更新的版本。

### 3. 定位需要提取的版本

对于每个目标版本，切换到对应标签：

```bash
git checkout tags/{version}
```

例如：

```bash
git checkout tags/0.22.0
```

`_core.c` 源码位于：

```
msgspecsource/src/msgspec/_core.c
```

### 4. 复制到目标目录

在 `msgspeccore/` 下创建版本子目录并复制文件：

```bash
mkdir -p ../msgspeccore/{version}
cp src/msgspec/_core.c ../msgspeccore/{version}/_core.c
```

例如：

```bash
mkdir -p ../msgspeccore/0.22.0
cp src/msgspec/_core.c ../msgspeccore/0.22.0/_core.c
```

### 5. 回到子模块最新状态

提取完毕后回到子模块默认分支，避免影响后续操作：

```bash
git checkout main
```

---

## 完整示例

以提取 msgspec v0.22.0 为例：

```bash
cd msgspecsource
git fetch --tags origin
git checkout tags/0.22.0
mkdir -p ../msgspeccore/0.22.0
cp src/msgspec/_core.c ../msgspeccore/0.22.0/_core.c
git checkout main
cd ..
git add msgspeccore/0.22.0/_core.c
git commit -m "add msgspec 0.22.0 _core.c"
```

---

## 注意事项

- 不要直接修改 `msgspeccore/` 下的 `_core.c`，它们是上游的原始副本。
- 确保复制后检查文件是否为空或损坏，可以用 `wc -l` 确认行数。
- 如果子模块尚未初始化，先执行 `git submodule update --init msgspecsource`。
- 子模块的版本标签不包含 `v` 前缀（如 `0.22.0` 而非 `v0.22.0`）。
