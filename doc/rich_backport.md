# rich backport 维护指南

本文档记录了 Alasio 对 `rich` 库的 monkeypatch 机制，用于在未来的 rich 版本更新后，快速评估和适配我们的 patch。

## 背景

### 为什么需要 monkeypatch

rich 从 14.1.0 版本开始支持 `exceptiongroup` 的回溯渲染，但仅在 Python >= 3.11 时启用（因为 `exceptiongroup` 是 Python 3.11+ 的内置模块）。Alasio 通过安装 backport 包 `exceptiongroup` 使 Python 3.8-3.10 也能使用 `BaseExceptionGroup` 和 `ExceptionGroup`。

为了在 Python < 3.11 下也能渲染 ExceptionGroup 的回溯，我们 monkeypatch 了 `rich.traceback.Traceback.extract()` 方法，**移除了 `if sys.version_info >= (3, 11):` 的版本检查**。

### 两个 monkeypatch

共两个 monkeypatch，均在 `alasio/ext/backport/patch_rich.py` 中定义，通过 `alasio/ext/backport/__init__.py` 自动调用：

| Patch 函数 | 目标方法 | 作用 |
|---|---|---|
| `patch_rich_traceback_extract()` | `Traceback.extract()` | 移除 ExceptionGroup 的 Python 版本检查，使 py<3.11 也能渲染 ExceptionGroup 回溯 |
| `patch_rich_traceback_links()` | `Traceback._render_stack()` | 修改帧头格式为 Python 标准格式 `File "path", line N, in func`，使 IDE 可点击跳转 |

## 版本映射与 backport 文件

`patch_rich_traceback_extract()` 根据当前安装的 rich 版本，选择不同的 backport 实现：

| rich 版本范围 | 使用的 backport 文件 |
|---|---|
| 14.1.0, 14.2.0 | `alasio/ext/backport/rich_14_1.py` |
| 14.3.0 ~ 15.0.0 | `alasio/ext/backport/rich_14_3.py` |

## 两个 backport 版本的差异

`rich_14_1.py` 与 `rich_14_3.py` 的 `Traceback.extract()` 差异很小，仅涉及 `locals_max_depth` 参数：

| 差异点 | rich_14_1 (14.1.x - 14.2.x) | rich_14_3 (14.3.x - 15.x) |
|---|---|---|
| `locals_max_depth` 参数 | 不存在 | 新增 `locals_max_depth: Optional[int] = None` |
| `pretty.traverse()` 调用 | 不含 `max_depth=` | 传入 `max_depth=locals_max_depth` |

**换句话说，14.3.0 相比 14.2.0 唯一的变化就是在 `extract()` 方法中新增了 `locals_max_depth` 参数传递给 `pretty.traverse()`。** 而我们的 backport 核心部分（` --- BACKPORT START ---` 到 `--- BACKPORT END ---` 之间的代码）在两者中完全相同。

## 如何检查新 rich 版本是否兼容

### 1. 获取 rich 的 git 仓库

```bash
git clone https://github.com/Textualize/rich.git
cd rich
git fetch --tags
```

### 2. 确定当前最大兼容版本

打开 `alasio/ext/backport/patch_rich.py`，查看 `if ver in [...]` 中最大的版本号。例如当前为 `'15.0.0'`。

### 3. 用 git diff 检查 traceback.py 的变更

假设当前最大兼容版本是 `v15.0.0`，新版是 `v15.1.0`：

```bash
# 检查 15.0.0 到新版本之间 traceback.py 是否变化
git diff v15.0.0 v15.1.0 -- rich/traceback.py

# 如果要检查多个版本（如有 15.0.0, 15.0.1, 15.1.0）
git diff v15.0.0 v15.0.1 -- rich/traceback.py
git diff v15.0.1 v15.1.0 -- rich/traceback.py
```

### 4. 重点关注的代码区域

在 `rich/traceback.py` 中，需要对比两个方法：

#### a) `Traceback.extract()`

查看以下关键点（行号因版本而异，请用搜索定位）：

- `if sys.version_info >= (3, 11):` 周围的 ExceptionGroup 处理逻辑是否变化
- 方法签名是否新增 / 删除 / 重命名参数
- `locals_max_depth` 相关逻辑是否变化
- `pretty.traverse()` 调用参数是否变化

#### b) `Traceback._render_stack()`

查看返回的 `Group.renderables` 结构是否变化，以及帧头的 `Text` 对象格式是否变化。

### 5. 判断结果

- **如果 `traceback.py` 没有任何 diff** → 只需在 `patch_rich.py` 的版本白名单中添加新版本号，并在 `pyproject.toml` 中提上限
- **如果 `extract()` 方法仅添加了新参数（带默认值）** → 可以复制最新的 `extract()` 创建新的 backport 文件（如 `rich_15_1.py`），照原有方式插入 BACKPORT START/END 块
- **如果 `_render_stack()` 变化** → 评估 `patch_rich_traceback_links()` 是否需要调整
- **如果 ExceptionGroup 处理逻辑大幅重写** → 需要评估整个 backport 是否需要重新设计

## 更新步骤

### 1. 更新 `alasio/ext/backport/patch_rich.py`

在 `patch_rich_traceback_extract()` 函数的版本白名单中添加新版本：

```python
# 如果新版无变化，直接复用现有 backport
elif ver in ['14.3.0', '14.3.1', '14.3.2', '14.3.3', '14.3.4', '15.0.0', '15.1.0']:
    from alasio.ext.backport.rich_14_3 import RichTracebackBackport

# 如果新版需要新的 backport
elif ver in ['15.1.0']:
    from alasio.ext.backport.rich_15_1 import RichTracebackBackport
```

注意：`patch_rich_traceback_links()` 无需随版本更新，它直接 monkeypatch 运行时类，不依赖版本特定的代码。

### 2. （如需要）创建新的 backport 文件

如果需要新 backport：

1. 复制最新的 backport 文件作为模板
2. 从 rich 新版的 `traceback.py` 复制 `extract()` 方法
3. 移除导入中不需要的部分，保留 backport 需要的导入
4. 在 ExceptionGroup 处理处插入 `# --- BACKPORT START ---` 和 `# --- BACKPORT END ---` 标记
5. 移除 `if sys.version_info >= (3, 11):` 版本检查

### 3. 更新 `pyproject.toml`

修改 rich 依赖的上限版本：

```toml
"rich>=14.1.0,<=15.1.0",
```

## 当前状态

| 属性 | 值 |
|---|---|
| 最后更新日期 | 2026-05-06 |
| 最大兼容版本 | 15.0.0 |
| 已验证兼容的版本 | 14.1.0, 14.2.0, 14.3.0, 14.3.1, 14.3.2, 14.3.3, 14.3.4, 15.0.0 |
| 当前 backport 文件 | `rich_14_1.py` (14.1-14.2), `rich_14_3.py` (14.3-15.0) |

## 相关文件一览

| 文件 | 作用 |
|---|---|
| `alasio/ext/backport/patch_rich.py` | 主入口，根据 rich 版本选择 backport 并执行 monkeypatch |
| `alasio/ext/backport/rich_14_1.py` | rich 14.1.x - 14.2.x 的 `Traceback.extract()` backport |
| `alasio/ext/backport/rich_14_3.py` | rich 14.3.x - 15.x 的 `Traceback.extract()` backport |
| `alasio/ext/backport/once.py` | `@patch_once` 装饰器，确保 monkeypatch 只执行一次 |
| `alasio/ext/backport/__init__.py` | 自动调用所有 `patch_*` 函数 |
| `pyproject.toml` | rich 版本约束 `"rich>=14.1.0,<=15.0.0"` |