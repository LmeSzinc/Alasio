# alasio_speedup

alasio 的可选 C 加速模块，独立分发。目前提供 bit2coding 的编码器。

模块是**可选**的：`alasio/speedup` 只从 site-packages 加载它，加载失败（未安装、无法构建、无法加载）时 alasio 自动回退到纯 Python 实现，因此没有安装的机器依然可以正常工作。

## 目录

每个加速模块是「一个绑定模块 + 一个 C 源」的一对，互相独立，各自一个动态库：

- `bit2.py`：bit2coding 编码器的 ctypes 绑定与公开 API（`bit2_encode.c` 的对外入口）
- `bit2_encode.c`：C 编码器（精确字节成本的 DP + 无损剪枝 + opcode 打包为字节流）
- `_library.py`：通用件——按需构建、ctypes 加载、校验库声明的接口版本（所有加速模块共用）
- `build.py`：编译包内的每个 `.c` 为同名动态库（与源码同目录，增量判断时间戳）
- `__init__.py`：`ACCELERATORS` 加速模块清单与新增指南

## API

```python
from alasio_speedup.bit2 import check, encode_bit2_opcode, encode_bit2_stream

check()  # 加载（必要时构建）动态库，失败抛出异常

stream = encode_bit2_stream([0, 1, 2, 3], ext8=False)   # list[int]，每个 int 一个字节
stream = encode_bit2_stream(values, ext8=True)          # 允许字面值 4~7

opcodes = encode_bit2_opcode(values)                    # DP 结果本身，供测试与基准使用
```

`encode_bit2_stream` 输出的是 `encode_bit2()` 去掉 VINT 计数前缀后的字节流：

```python
from alasio.ext.algorithm.bit2coding import decode_bit2
from alasio.ext.algorithm.vint import encode_vint

encoded = encode_vint(len(values)) + bytes(encode_bit2_stream(values))
values, read = decode_bit2(encoded)
```

`encode_bit2_opcode` 返回 `(0, list[int])` 字面值、`(1, run_value, run_length)` 连续值、`(2, offset, length)` 复制，与 `encode_bit2_opcode_iter()` 的元组一致。

**编码器只有一个冻结配置**：精确字节成本 DP + 无损剪枝 + 哈希链无上限 + 不做平局偏置。`ext8` 是数据格式而不是开关；唯一保留的开关是 `lossless_prune`（默认即冻结值），供测试把无损剪枝关掉、与 plain DP 对比，证明剪枝一个字节都不花。开发期比较过的其余变体（无剪枝的 plain DP、限制链长、字面值平局偏置）只写在 `bit2_encode.c` 的注释与 `doc/2026-09-27_bit2coding-c-encoder.md` 里，调用侧无法改变它们：改配置等于改编码器的字节输出，那是新版本，不是新参数。

## 新增一个加速模块

加速模块之间的可用性是**独立**的：一个模块没装好、编不过、库不对版，不影响其它模块，调用侧各自回退自己的纯 Python 实现（见 `alasio/speedup`）。新增一个只需要三步：

1. 写 `<area>_<what>.c`：导出 `abi_version()`（返回本文件的接口版本）与加速入口。接口一变就 bump，wrapper 与库版本不符会被拒绝加载
2. 写 `<area>.py`：`LIBRARY = AcceleratorLibrary('<area>_<what>', ABI_VERSION)`，加一个 `check()`（加载库并编一小段数据验证），再写导出的 wrapper。照抄 `bit2.py` 即可
3. 把 `'<area>'` 加进 `__init__.py` 的 `ACCELERATORS`

不需要改的地方：`build.py` 会自己扫包内的每个 `.c`；`alasio/speedup` 按 `ACCELERATORS` 逐个导入并 `check()`，各自独立成败。

## 构建与安装

```powershell
$py = "E:\ProgramFiles\Anaconda3\envs\alasio\python.exe"

# 构建（源码比产物新时自动重编，编译器可用 BIT2_CC / CC 指定）
& $py -m alasio_speedup.build

# 安装到 site-packages，alasio.speedup 加载的就是这一份
& $py -m pip install --no-deps --no-build-isolation ./alasio_speedup
```

没有编译器时构建失败，`alasio.speedup` 会把加速器视为不可用并回退纯 Python，**不会**让 pack 构建失败。

## 契约

1. `decode_bit2(encode_vint(len(data)) + bytes(encode_bit2_stream(data)))` 还原输入（`ext8` 同理）
2. 输出体积 `<=` 纯 Python 编码器（DP 的成本模型是精确字节成本，无损剪枝不改变体积）
3. 相同输入的输出是确定的；解析形状允许与 Python 不同
4. 动态库声明自己的接口版本（每个库导出 `abi_version()`）：与 wrapper 不匹配、或者根本不导出这个符号的库会被**拒绝加载**，由 alasio 回退到纯 Python，而不是按错误的签名调用
