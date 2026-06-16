# msgspecerror

msgspecerror 是一个结构化解析 msgspec 异常的库。

在发生校验错误的时候，msgspec 只会返回字符串信息的异常：

```
Expected `int`, got `str` - at `$.user.age`
```

而有了 msgspecerror 之后，你可以得到结构化的异常：

```python
try:
    msgspec.json.Decoder(...).decode(...)
except msgspec.ValidationError as e:
    err = parse_msgspec_error(e)
    print(err)
    # ErrorInfo(msg='Expected `int`, got `str` - at `$.user.age`',
    #           type=<ErrorType.TYPE_MISMATCH>,
    #           loc=('user', 'age'))
```



## 对话要求

- 在对话回复中使用中文
- 在代码注释中使用英文
- 在修改已有代码的时候，不要对无关的部分进行修改

## 完成度要求

以生产级别的要求来编写代码，不能编写 toy project 或者 demo 性质的代码。

代码需要兼容不同系统，支持在 Windows, Mac, Linux 系统下运行，需要兼容 Window7。

不使用 powershell 相关的命令，在桌面端不使用系统 webview。

## 本地环境

使用 Anaconda 虚拟环境中的 python 解释器来执行脚本和运行测试，比如：

```bash
# run a specific file "module/config/gen.py"
E:\ProgramFiles\Anaconda3\envs\star\python.exe -m module.config.gen
# run test file "tests/base/test_servertime.py"
E:\ProgramFiles\Anaconda3\envs\star\python.exe -m pytest tests/base/test_servertime.py
```

不要直接使用 `python` 命令，因为项目有单独配好的虚拟环境，不使用全局 python 环境。

在运行项目内文件的时候不要直接执行 `python module/config/gen.py` 而是使用 `python -m module.config.gen` 作为模块运行，这样运行路径会在项目根目录。

当创建新文件的时候，执行命令 `git add "{file}"` 来添加文件，这样就不需要开发者二次添加了。

不要使用 `git add .` 来添加所有文件，因为项目内通常有很多仍在编写的临时文件。

## python代码开发要求

使用更加pythonic的语法而不是类C语法

### 避免在循环中使用下标索引

避免使用这样的表达

```python
data = [...]
for i in range(len(data)):
	item = data[i]
```

避免使用这样的表达

```python
data = [...]
while i < len(data):
    item = data[i]
```

建议使用这样的表达

```python
for item in data:
    ...
# if you do need the index
for index, item in enumerate(data):
    ..
```

### 尽量使用生成器或列表生成式而不是list.append

避免使用这样的表达

```python
def func():
    output = []
    for item in items:
        item = do_something(item)
        output.append(item)
    return output
```

建议使用列表生成式

```python
output = [do_something(item) for item in items]
```

建议使用生成器

```python
def func():
    for item in items:
        item = do_something(item)
        yield item
```

### 建议使用卫语句(guard clause)而不是嵌套条件判断

使用卫语句来让代码更加清晰，在检查失败后快速退出，直到检查成功

```python
def is_assets_file(file):
    # check file extension
    suffix = get_suffix(file)
    if suffix not in Const.ASSETS_EXT:
        return False
    name = get_rootstem(file)
    # emulator screenshots is not allowed
    # assets should have a name with meaning, not just random timestamp
    if name.startswith('Screenshot'):
        return False
    if name.startswith('MuMu'):
        return False
    if name.startswith('NemuPlayer'):
        return False
    # check name format
    if REGEX_ASSETS_NAME.match(name):
        return True
    else:
        return False
```

### 建议使用str.partition()和str.rpartition()

如果你只需要切分字符串一次，那么避免这样的表达

```python
file = path.split("/")[-1]
ip = serial.split(':')[0]
```

建议使用这样的表达，来避免潜在的IndexError并且略微提升性能

```python
file = path.rpartition("/")[2]
port = serial.partition(":")[0]
```

如果你需要检查分隔符是否存在，可以解包partition的结果

```python
def with_name(path: str, name: str) -> str:
    """
    /abc/def.png -> /abc/xxx
    /abc/def     -> /abc/xxx
    /abc/.git    -> /abc/xxx
    """
    root, sep, _ = path.rpartition('/')
    if sep:
        return f'{root}{sep}{name}'
    else:
        return name
```

## python测试要求

### 测试目标

编写测试的目标是：测试 函数/类 是否正确实现。测试需要覆盖被测函数的所有分支，测试需要以被测函数的预期功能来编写。

当测试发生错误的时候需要修复被测函数的实现，禁止根据当前函数的具体实现来调整测试数据或者调整断言，单纯地让测试通过。

### 使用类组织测试文件

使用 Pytest 编写测试，测试文件放在 `tests/` 目录的对应路径下，比如 `alasio/base/servertime.py` 的测试放在 `tests/base/test_servertime.py`。

使用类来组织测试文件，比如需要测试 `parse_timezone()` 函数就编写 `TestParseTimezone` 类，然后用类函数分别编写不同的测试场景。

```python
class TestParseTimezone:
    def test_parse_timezone_success():
    def test_parse_timezone_exceptions():
    def test_identity_check():
```

### 使用 pytest.mark.parametrize 装饰器

如果需要输入多个测试数据来测试函数，使用 `@pytest.mark.parametrize` 装饰器。

```python
class TestGetLcpStr:
    """Tests for get_lcp with str inputs."""
    @pytest.mark.parametrize("s1, s2, expected", [
        ("abc", "abd", "ab"),
        ("prefix", "prefix", "prefix"),
        ("abc", "xyz", ""),
        ("", "abc", ""),
        ("abc", "", ""),
        ("", "", ""),
        ("a", "a", "a"),
        ("a", "b", ""),
        ("case", "CASE", ""),
        ("hello world", "hello there", "hello "),
        # Unicode tests
        ("café", "café au lait", "café"),
        ("你好世界", "你好", "你好"),
        ("你好世界", "你好吗", "你好"),
        # Very long strings
        ("a" * 1000 + "b", "a" * 1000 + "c", "a" * 1000),
    ])
    def test_str_lcp(self, s1, s2, expected):
        """get_lcp with str inputs should return str."""
        result = get_lcp(s1, s2)
        assert result == expected
        assert isinstance(result, str)
```



## python异常处理指引

### 对list使用下标取值的时候需要考虑IndexError

```python
try:
    item = items[index]
except IndexError:
    # handle errors
```

### 对dict取键值的时候需要考虑KeyError

```python
try:
    item = items[key]
except KeyError:
    # handle errors
```

### 使try except包含最少的内容而不是在最外层套用异常处理

避免使用这样的表达

```python
try:
    with open(file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        data = {}
    # Do a lot of stuff here
    regex = re.compile(r'^(.*?):(.*?)$')
    for line in lines:
        ...
    return data
except FileNotFoundError:
    return {}
```

建议使用这样的表达

```python
try:
    with open(file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
except FileNotFoundError:
    return {}
# Do a lot of stuff here
data = {}
regex = re.compile(r'^(.*?):(.*?)$')
for line in lines:
    ...
return data
```

### 避免在except块中嵌套异常处理

避免使用这样的表达

```python
try:
    # do something
except:
    try:
        # fix error
    except:
        pass
```

### 避免在except块重新执行可能出错的代码

避免使用这样的表达

```python
try:
    # May raise AdbError
    self.screenshot()
except AdbError:
    # May raise AdbError
    self.adb_reconnect()
    # May raise AdbError
    self.screenshot()
```

建议使用这样的表达，来避免嵌套异常处理

```python
init = None
for _ in range(RETRY_TRIES):
    try:
        if init is not None:
            # May raise AdbError
            init()
            # May raise AdbError
            return self.screenshot()
    except AdbError:
        # Define function won't raise error
        def init():
            self.adb_reconnect()
raise ...
```

## python IO操作指引

### 避免事先检查路径是否存在

对于所有 io 操作都不要进行预检查（比如检查路径是否存在，检查目标是否是一个文件夹），因为预检查会增加大量 io。直接执行你要做的操作，捕获异常再处理异常，这样可以保证在 normal case 中具有最少的 io 操作，同时又能够处理各种异常。

避免使用这样的表达：

```python
if os.path.exist(file):
    with open(file) as f:
        ...
```

建议使用这样的表达：

```python
try:
	with open(file) as f:
		...
except FileNotFoundError:
	...
```

### 优化os.stat调用

避免这样的表达，因为 `os.path.isfile()` ，`os.path.isdir()`，`os.path.exists()` 的底层都是调用 `os.stat()`。

```python
if os.path.isfile(file) or os.path.isdir(file):
    stat = os.stat(file)
```

建议使用这样的表达来减少 IO 操作。

```python
try:
    st = os.stat(abspath)
except FileNotFoundError:
    # remove from index
    self._stage_restore(path)
    return False

mode = st.st_mode
if stat.S_ISREG(mode):
    # File
    pass
elif stat.S_ISDIR(mode):
    # Directory, can't add directory directly
    return False
elif stat.S_ISLNK(mode):
    # Symlink
    pass
else:
    # This should not happen
    return False
```

### 原子文件读写

使用 `/alasio/ext/path/atomic.py` 中的 `atomic_read_text()`，`atomic_read_bytes()`，`atomic_write()` 等原子性函数来操作，而不是手动 `open(file)`。

`atomic_*` 系列函数利用了文件替换（os.replace）在各系统都是原子的特性，先写入临时文件再重命名来保证文件的原子写入。辅以 `atomic_failure_cleanup` 函数来清除先前的失败文件。

### 使用PathStr处理路径

使用 `/alasio/ext/path/pathstr.py` 中的 `PathStr` 类来操作路径，而不是使用 pathlib 或者 os.path。

`PathStr` 有数倍于 os.path 的性能提升，直接调用 `/alasio/ext/path/calc.py` 中的函数还可以再次获得数倍于 PathStr 的性能提升。当你只需要单次处理路径的时候，直接使用 calc.py 中的函数，当你需要基于一个 base path 来创建多个路径字符串的时候，使用 PathStr。

PathStr 继承自 str，因此可以直接地将 PathStr 对象作为路径字符串输入。

如果从一个任意路径创建 PathStr ，使用 `PathStr.new()`

### 避免创建临时文件

不要创建临时文件，所有操作尽量在内存中完成。如果需要写入就直接写入，如果需要读取就直接读取，不要复制为临时文件再读取。

## python代码格式化规范

### 使用 Google-style docstrings 注释函数的参数和返回值

### 将typehint转换为Google-style docstrings

为了保持低版本 python 的兼容性，取消 typehint 改用 Google-style docstrings 来注释类型

例子，格式化前：

```python
def is_tmp_file(file: str) -> bool:
    """
    Check if a filename is tmp file
    """
```

格式化后：

```python
def is_tmp_file(file):
    """
    Check if a filename is tmp file

    Args:
        file (str): File name to check

    Returns:
        bool: True if file is a temporary file
    """
```

### 去除 typing 中 Union List Dict Optional 等的使用

例子：`Union[str, bytes]` 格式化为 `str | bytes`，`Optional[int]` 格式化为 `int | None`，`List[str]` 格式化为 `list[str]`，`Dist[str, int]` 格式化为 `dict[str, int]`

### 去除 typing 库的引入

typing 库从 py3.5 到 py3.12 到现在仍然在不断变化，为了保持低版本 python 的兼容性，不要引入 typing 库。

例子：删除 `from typing import Iterable, Union`，删除 `import typing as t` 等，并且确认代码中没有使用到 typing 库中的内容。

### 如果确实需要使用 typing，用双引号括起来

例子：

```python
def run_set(modify: t.List[str]) -> t.Dict[str, str]:
```

格式化为：

```python
def run_set(modify: "t.List[str]") -> "t.Dict[str, str]":
```

虽然各种网络资料告诉你 python typing 不会影响运行速度，但很遗憾确实会，不信你可以试试：

```python
import typing as t
a: t.List[1, 1] = []
```

你会得到 `TypeError: Parameters to generic types must be types. Got 1.`，因为 python 会在运行时调用 `typing.List.__getitem__` 来检查 typehint 是否合法。增加引号不会影响 IDE 的类型推断，还能保持低版本兼容性，何乐而不为呢。

### 对于有默认值的函数参数不需要添加optional

例子，格式化前：

```python
def atomic_read_text(file, encoding='utf-8', errors='strict'):
    """
    Atomic file read with minimal IO operation

    Args:
        file (str): Source file path
        encoding (str, optional): Text encoding. Defaults to 'utf-8'.
        errors (str, optional): Error handling strategy. Defaults to 'strict'.
            'strict', 'ignore', 'replace' and any other errors mode in open()
```

格式化后：

```python
def atomic_read_text(file, encoding='utf-8', errors='strict'):
    """
    Atomic file read with minimal IO operation

    Args:
        file (str): Source file path
        encoding (str): Text encoding. Defaults to 'utf-8'.
        errors (str): Error handling strategy. Defaults to 'strict'.
            'strict', 'ignore', 'replace' and any other errors mode in open()
```

### 保留泛型的typehint

不需要把泛型的 typehint 转换为 google docstring 或者增加引号，因为 IDE 很难推断 docstring 中的泛型。像这样保留泛型的typehint：

```python
class ResourceCache(Generic[T]):
    def __init__(self):
        self._create_lock = Lock()
        self._cache: "dict[str, T]" = {}
        self._lock: "dict[str, Lock]" = {}
    def get(self, file: str, **kwargs) -> T:
        ...
```


