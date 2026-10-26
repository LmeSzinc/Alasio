# 只发布提供源 wheel：pack server 的 wheel 缓存与 pip 源

> 状态：设计文档，未实现。
>
> 摘要：从 uv 生成的 requirements 注释中读取每个 pin 的下载来源；只有 mod entry 的
> `update_provide_pypi_mirrors` 声明的源需要主动提供，其余统一从官方下载。官方源和未命中
> 提供源的 wheel 只作为构建缓存，不再上传；提供源的 wheel 发布成 pip 可用的索引。构建
> 缓存放在 `pack/{channel}/_wheel_cache`，以下划线开头的路径按约定不上传。

---

## 1. 结论

1. **依赖文件是版本和来源的唯一事实来源**。解析 uv 写出的 `# from <index>` 注释，
   得到每个 `name==version` 的来源 index。没有注释、注释无法解析、来源是官方
   PyPI、或普通 requirements 文件，都视为未定义，下载侧使用 `pypi_mirrors` probe。
2. **只有 `update_provide_pypi_mirrors` 命中的源需要提供**。mod entry 新增
   `update_provide_pypi_mirrors: tuple[str, ...]`，列出必须随更新包发布的 PyPI 镜像
   地址；某个 pin 的 `# from` 命中其中一项，才发布它的 wheel。未命中的统一从官方
   下载，不发布。
3. **下载源使用 mod entry 的 `pypi_mirrors`**。删除 pack server 的
   `PythonDeps.PypiMirror` 配置：非提供源的 wheel 从 `ModEntryInfo.pypi_mirrors`
   描述的镜像结构下载（探测/回退逻辑与客户端一致），提供源直接从命中的地址下载。
4. **提供源的 wheel 全部发布**。对每个命中的 pin，下载该版本的**全部**目标 Python
   兼容 wheel（所有平台，不只 pack 用的纯 Python 那个），发布成 PEP 503 索引 +
   一个 `--find-links` 页面，客户端可以 `pip install`。
5. **发布范围与 `update_pack_deps` 解耦**。pack 通道只覆盖 `update_pack_deps` 的
   纯 Python 依赖；wheel 索引覆盖 latest requirements 里**所有**命中提供源的 pin，
   包括编译扩展、以及没有走 pack 通道的纯 Python 包。
6. **缓存与发布放在同一个 channel 下，用下划线区分**：
   - `{run}/pack/{channel}/_wheel_cache/...`：下载缓存和 pack 构建输入，per channel，
     **不上传**；
   - `{run}/pack/{channel}/wheel/...`：客户端可见的 pip 索引，**上传**。
   上传器/发布记录约定：任意路径段以 `_` 开头的文件或文件夹都不上传。
7. **只发布 latest，不发布 lookback**。游戏脚本的旧版本没有意义，wheel 索引只服务
   latest requirements；现有 pack 通道的 lookback update pack 保持不变，旧 wheel 只进
   缓存。
8. **客户端从 mod mirrors 推导 wheel 索引 URL**：把 URL 最后一段 `packrepo` 换成
   `wheel`，和 `packdep/{name}` 的推导规则一致。安装非 pack 依赖时不再依赖 requirements
   文件里的 `--extra-index-url`。
9. **旧数据可回收**。第一次按新规则生成后，未提供源的 wheel 不再进入发布记录；同步器
   按 `doc/2026-10-26_pack-server-publish-sync.md` 的每个 remote grace 删除。

---

## 2. 现状

### 2.1 生成链路

`PackServer.run_config()`（`alasio/deploy_dev/pack_server/main.py`）按以下顺序运行：

1. `DepGen.run()`：抓取 `update_pack_deps` 声明的依赖 wheel，写
   `pack/{channel}/packdep/{name}/{version}/full.pack` 和 `update_{old}.pack`；
2. `PackRepoGen.run()`：写项目树的 full pack / update pack；
3. `PackRepoGen.commit()`：写 `packrepo/latest.pack`（发布屏障），删除其它版本目录；
4. `DepGen.commit()`：删除窗口外的 wheel 和依赖包。

`DepGen.run()` 当前的 wheel 抓取由 `_fetch_wheels()` 驱动：

- `DepGen.sample` 只包含 `update_pack_deps` 声明的名字；
- `_mirror_of(name)` 从 `PythonDeps.PypiMirror` 取得下载源，默认官方 PyPI
  （现状；本方案将删除这个配置，改用 mod entry 的 `pypi_mirrors`）；
- `WheelFetcher` 把 wheel 和 `index_data.json` 写到
  `{run}/pack/{channel}/wheel/{name}/{version}/`（现状；本方案改为
  `{run}/pack/{channel}/_wheel_cache/`）；
- `WheelFetcher._select_wheel()` 只选择 `py3-none-any` 的纯 Python wheel，
  编译扩展不会被 pack 通道处理；
- `DepGen._remove_stale_wheels()` 以 `sample` 为准，删除窗口外版本的 wheel 目录。

发布侧（`doc/2026-10-26_pack-server-publish-sync.md`）把整个
`{run}/pack/{channel}/` 镜像到静态托管，因此 `wheel/` 下的所有内容（包括官方
wheel 和 `index_data.json`）都会被上传。

### 2.2 `wheel/` 目录的两种用途

当前同一个目录同时承担：

1. **下载缓存**：`index_data.json` 缓存索引页；已下载的 wheel 按 sha256 复用，
   避免重复请求；
2. **构建输入**：`PackWheel` 读取 wheel 生成依赖 pack；
3. **发布产物**：同步器把整个目录上传到静态托管。

这三件事的生命周期不同：

- 缓存希望尽量保留，重复运行不再下载；
- 构建输入只需要覆盖当前 lookback 窗口；
- 发布产物只需要客户端真正需要的文件，且要和 `latest.pack` 屏障一起发布。

混在一起会导致：

- 官方 wheel 被当成发布产物上传，增加存储和流量费用；
- `index_data.json` 这种内部缓存进入公共桶；
- 清理策略冲突：为了省缓存而保留，和为了省发布空间而删除，无法同时满足；
- `wheel/` 目前还不是一个合法的 pip 源（没有索引 HTML，目录层级也不是 PEP 503）。

本方案的解决：把缓存移入 `pack/{channel}/_wheel_cache/`，与发布目录
`pack/{channel}/wheel/` 平级；`_` 开头是私有路径，上传器和发布记录一律跳过。
缓存因此仍然 per channel（损坏不会跨 channel 污染），版本滚动时可以立即清理，
又不会进入对象存储。

### 2.3 为什么不能只保留 pack 用的纯 Python wheel

pack 通道有纯 Python 门禁（`_is_pure_python3`：`abi=none, platform=any`），
所以编译扩展只能走 pip。以 Alas2026 为例，来自 py38deps 的编译扩展包括：

`av`、`cffi`、`msgspec`、`multidict`、`propcache`、`pyyaml`、`zstandard`。

这些包在 py38deps 上有 cp38 的 Windows / Linux / macOS wheel。客户端如果无法访问
py38deps（GitHub Pages / GitHub Releases 在部分网络不可达），就必须从更新包服务
拿到这些 wheel，否则 pip 无法安装。只保留 pack 用的纯 Python wheel 不够。
本方案要求 mod entry 把 py38deps 写入 `update_provide_pypi_mirrors`，作为“需要主动
提供”的源；未写入的源统一从官方下载。

另外，同一个包在 py38deps 上可能有多个平台的 wheel（例如 `av==18.1.0` 的
cp38 wheel 覆盖 `win_amd64`、多个 `manylinux`、`musllinux`、`macosx`），
必须全部发布，pip 才能按客户端平台选择。

### 2.4 真实仓库 Alas2026 的依赖来源

`E:\ProgramData\Pycharm\Alas2026\requirements.txt` 由
`dev_tools/requirements_compile.py` 用 uv 生成，注释格式为
`--emit-index-url --emit-index-annotation --annotation-style=split`。

当前文件共 86 个 pin：

- 61 个来自 `https://pypi.org/simple`；
- 25 个来自 `https://py38deps.github.io/py38deps/simple/`。

py38deps（本方案要求写入 `update_provide_pypi_mirrors`）中的典型 pin：

- 纯 Python：`httpx2==2.13.1`、`starlette==1.9.0`、`trio==0.34.0`、
  `anyio==4.15.1`、`typing-extensions==4.16.0` 等；
- 编译扩展：`msgspec==0.22.0`、`zstandard==0.25.0`、`av==18.1.0`、
  `cffi==2.1.1`、`multidict==7.1.0`、`propcache==0.5.4`、`pyyaml==6.0.3`。

官方源中体积较大的常客（`onnxruntime`、`opencv-python-headless`、`numpy`、
`scipy`、`pillow`、`lxml` 等）不需要发布，客户端直接从官方源下载即可。

`av==18.1.0` 的 py38deps 索引页约 75 个链接，覆盖 cp38 ~ cp314 的多个平台；
Python 3.8 运行时的客户端只需要其中 cp38 / py3 兼容的那一小部分（约 8 个）。
这直接说明了“全部 wheel”必须配合目标 Python 过滤，而不是把索引页所有文件都发布。

---

## 3. 关键事实与约束

### 3.1 uv 的注释格式

Alas2026 的 `requirements.txt` 片段：

```text
--extra-index-url https://py38deps.github.io/py38deps/simple/

adbutils==0.11.0
    # via
    #   azurlaneautoscript (pyproject.toml)
    #   uiautomator2
    # from https://pypi.org/simple
anyio==4.15.1
    # via
    #   httpx2
    #   starlette
    # from https://py38deps.github.io/py38deps/simple/
```

约定：

- pin 行是 `name[extras]==version`，位于行首；
- 后续缩进的 `# via` 是依赖关系，忽略；
- 后续缩进的 `# from <url>` 是该 pin 实际由哪个 index 解析出来的，作为来源；
- `--extra-index-url` 是给 pip 的全局选项，**不作为**某个 pin 的来源；
- 无 `# from`、普通 requirements、注释无法解析、或来源就是官方
  `https://pypi.org/simple` → 来源为空（未定义），下载侧使用内置的多镜像 probe；
- 来源 URL 会与 mod entry 的 `update_provide_pypi_mirrors` 逐项比较（规范化尾斜杠、
  scheme/host 大小写）：
  - 命中 → 这个 pin 的 wheel 需要主动提供（发布）；
  - 未命中/来源为空 → 从 `ModEntryInfo.pypi_mirrors` 描述的镜像或官方 PyPI
    下载，不发布；
- `# from` 指向的不是 PEP 503 index（例如直接 wheel URL、`file://`、`git+`）
  → warning，来源视为未定义，不参与提供源匹配；
- 未命中提供源、且官方也没有对应 wheel → 构建失败；mod 作者需要把该来源加入
  `update_provide_pypi_mirrors`，而不是依赖隐式猜测。

### 3.2 pack 通道只接受纯 Python wheel

`WheelFetcher._select_wheel()` 只选择 `py3-none-any` 的 wheel；`PackWheel` 也只接受
纯 Python 的安装树。因此：

- `update_pack_deps` 只能声明纯 Python 依赖；
- 编译扩展必须由 pip 安装；
- wheel 索引必须单独抓取“目标 Python + 所有平台”的 wheel，不能复用
  `WheelFetcher` 的单 wheel 选择逻辑。

### 3.3 客户端时序

当前客户端流程（`doc/2026-10-16_dependency-update-integration.md`）：

1. `update_mods()`：先把项目树更新到新版本；
2. `update_mod_dep()`：再按**新树的** requirements 安装依赖。

因此正常更新流程只需要**新版本 requirements 里的 pin**。游戏脚本的旧版本没有意义，
不需要给旧版本提供完整的依赖源，所以：

- wheel 索引**只发布 latest commit** 的提供源 wheel；
- 不提供 `PublishLookback` 选项，旧版本的提供源 wheel 不发布；
- 现有 pack 通道的 lookback update pack 保持不变：旧版本只作为“从旧版本更新到
  latest”的增量输入，构建它们的 wheel 只进 `_wheel_cache`，不进入 pip 索引；
- 客户端始终先更新 tree，再按新 tree 的 requirements 安装依赖。

### 3.4 静态托管与发布屏障

`doc/2026-10-26_pack-server-publish-sync.md` 的模型：

- 整个 `{run}/pack/{channel}/` 按对象 key 镜像到静态托管；
- **任意路径段以 `_` 开头的文件/文件夹不上传**；`_wheel_cache` 因此天然排除；
- 上传顺序由发布记录控制，`packrepo/latest.pack` 必须最后写；
- 旧对象只在这个 remote 自己发布本代、并经过 grace 后删除；
- 静态托管没有“目录列表”能力，客户端需要的索引页必须由生成器写出来。

因此 wheel 索引必须：

- 在 `DepGen.run()` 中全部写好（wheel 文件 + HTML 页）；
- 进入同一份发布记录；
- 在 `PackRepoGen.commit()` 写 `latest.pack` 之前上传；
- 旧文件由 `DepGen.commit()` 清理，随后发布记录更新，远端按 grace 删除。

---

## 4. 目标与非目标

### 4.1 目标

1. **降低静态托管的存储和流量成本**：官方和未声明源的 wheel 不再上传；
2. **让提供源的依赖可离线安装**：`update_provide_pypi_mirrors` 命中的 wheel 全部随
   更新包发布；
3. **厘清 wheel 目录职责**：`_wheel_cache` 只做缓存，`wheel/` 只做发布；
4. **来源可追溯**：每个 pin 的下载来源来自依赖文件注释 + mod entry 的
   `update_provide_pypi_mirrors`，下载镜像来自 `ModEntryInfo.pypi_mirrors`；
5. **不破坏 pack 通道**：`update_pack_deps`、`full.pack/update_*.pack`、`packdep`
   的行为保持不变；
6. **可迁移**：旧发布目录中的未提供源 wheel 能在若干代之后自动删除。

### 4.2 非目标

1. 不实现完整的 pip 依赖解析器；客户端仍然以 requirements 的完整 pin 为准；
2. 不发布未命中 `update_provide_pypi_mirrors` 的 wheel；
3. 不发布 sdist；提供源必须有可用的 wheel；
4. 不做提供源的完整镜像；只发布 latest 需要的 pin 的 wheel；
5. 不为游戏脚本旧版本提供完整依赖源；
6. 不在本方案中重写发布记录/同步器，只定义 wheel 索引必须满足的契约
   （包括 `_` 前缀不上传的约定）。

---

## 5. 设计决策

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| D1 | `# from` 注释 + `update_provide_pypi_mirrors` 匹配决定是否提供；无注释/官方/无效/未命中 = 未定义，使用 `pypi_mirrors` probe | mod 作者显式声明哪些源必须随更新包提供 |
| D2 | 只发布命中 `update_provide_pypi_mirrors` 的 wheel | 直接消除官方 wheel 的存储/流量 |
| D3 | 提供源发布该 pin 的全部目标 Python 兼容 wheel | 编译扩展需要按平台选择 |
| D4 | 发布集合覆盖所有 latest pin，不只 `update_pack_deps` | 非 pack 依赖也需要 pip 源 |
| D5 | `pack/{channel}/_wheel_cache/` 做缓存，`pack/{channel}/wheel/` 做发布；`_` 前缀不上传 | per channel、清理即时、不会误上传 |
| D6 | 发布 PEP 503 每包索引 + 根 `index.html`（find-links） | 兼容标准 pip 和静态托管无目录列表的情况 |
| D7 | 只发布 latest pin，不发布 lookback | 旧游戏脚本版本没有完整依赖源的意义 |
| D8 | 删除 `PythonDeps.PypiMirror`，非提供源从 `ModEntryInfo.pypi_mirrors` 下载 | 复用客户端已有的镜像配置，去掉重复配置 |
| D9 | 提供源从命中的 URL 下载；未提供源从 `pypi_mirrors`/官方下载 | 提供源可能不在官方；未提供源假定官方可得 |
| D10 | 失败关闭：无可用 wheel/源 → 该 config 失败，旧通道继续服务 | 不发布不完整的 wheel 索引 |

---

## 6. 详细设计

### 6.1 目录布局

```text
{run}/
  config/
  repo/
  pack/
    {Author}_{Repo}_{Branch}/
      packrepo/                      # 项目树 pack，保持现状
      packdep/                       # 依赖 pack，保持现状
      wheel/                         # 发布用 pip 源
        index.html                   # 所有提供源 wheel 的链接，供 --find-links
        {name}/
          index.html                 # PEP 503 项目页
          {version}/
            {filename}.whl           # 只包含提供源、目标 Python 兼容的 wheel
      _wheel_cache/                  # 私有缓存，不上传
        index/
          {source_key}/
            {name}.json              # PEP 503 索引页解析缓存
        wheel/
          {name}/
            {version}/
              {filename}.whl         # 下载缓存，可能包含官方 wheel
```

说明：

- `_wheel_cache` 在 `pack/{channel}/` 内，per channel：一个 channel 的缓存损坏不会
  污染其它 channel；配置版本滚动时可以直接删除对应目录；
- 上传器/发布记录约定：**任意路径段以 `_` 开头就不上传**。因此 `_wheel_cache` 不需要
  单独配置排除规则，它天然不进对象存储；
- `source_key = sha1(normalized_source_url).hexdigest()[:16]`，避免 URL 中的
  `:`、`/` 等字符进入路径；同一个 source 的 index 缓存只存一份；
- `index_data.json` 不再放在发布目录，而是 `_wheel_cache/index/`；
- `pack/{channel}/wheel/` 只保留客户端契约内的文件（HTML + wheel）；
- 旧的 `{run}/pack/{channel}/wheel/` 内容在第一次新流程运行时被当作发布目录清理：
  先写新索引，再在 `DepGen.commit()` 删除不在发布集合里的旧文件；
- 旧的 `{run}/cache/{channel}/`（如果已经按早期方案创建）不再使用，可以手工删除。

### 6.2 来源解析器

#### 6.2.1 数据模型

在 `alasio/deploy/simple_pip/parse_req.py` 增加一个 pin 模型，并让 `Requirements`
同时暴露版本和来源：

```python
class DepInfo(Struct, frozen=True):
    name: str        # PEP 503 normalized
    version: str
    source: str      # normalized index base URL, '' = no annotation
```

```python
class Requirements:
    @cached_property
    def dict_pins(self) -> "dict[str, DepInfo]":
        ...

    @cached_property
    def dict_deps(self) -> "dict[str, str]":
        return {name: pin.version for name, pin in self.dict_pins.items()}

    @cached_property
    def dict_sources(self) -> "dict[str, str]":
        return {name: pin.source for name, pin in self.dict_pins.items()}
```

- `dict_deps` 保持现有行为，现有调用方不需要改；
- `dict_pins` / `dict_sources` 是新增能力；
- 解析只做一次，三个属性共享同一份结果；
- `DepInfo` 可以放在 `parse_req.py`，因为它是客户端 requirements 格式的一部分；
  release 侧通过 `parse_dep` 重新导出；
- 来源 URL 的规范化在 `_read_source()` 内用 `yarl` 完成（scheme/host 小写、去掉
  默认端口和尾部斜杠），不单独暴露规范化函数；后续匹配侧需要按同一规则处理
  `update_provide_pypi_mirrors`。
- `Requirements.extra_index` 是缓存属性：返回第一个 `--extra-index-url` 的 URL，
  没有则为 `''`；从文件开头逐行扫描，命中即早停。`dict_pins` 直接使用已经解析出的
  pins 判断是否所有 `source` 都为空，再决定是否输出汇总 warning，不需要第二次扫描
  content；pin 的状态机不再跟踪这个选项。
- 解析管线（`_iter_logical_line` / `_read_source` / `_iter_dep_info`）是
  `Requirements` 的方法；`_iter_logical_line` 产出 `(text, comment)`：以 `#`
  开头的行整体作为 comment，不再二次切分；requirement 行的第一个空白后的 `#`
  作为行内 comment。uv/pip-compile 的每个注释独占一行。实例上有一个
  `_source_cache`，按 `# from` 的原始 URL 缓存规范化结果。真实文件通常有几十个
  pin、但只有两三种 source，绝大多数注释直接命中缓存；`_read_source` 先做
  `if 'from' not in comment` 的 fast path，跳过 `# via` 等普通注释。

`PyprojectParser` 也实现 `dict_pins`：版本来自现有 `dict_deps`，`source=''`。
它没有 uv 注释，按 D1 视为未定义（下载侧使用 `pypi_mirrors` probe）。

#### 6.2.2 解析状态机

现有 `_iter_requirement()` 只产出 requirement 文本，注释被丢弃。新解析器需要保留
`# from` 与上一个 pin 的关联。uv 和 pip-compile 的注释风格都要支持：

- `--annotation-style=line`：`# via httpx, starlette`
- `--annotation-style=split`：`# via` 后跟缩进的 `#   httpx` / `#   starlette`
- 注释在 pin 行的下一行（缩进）；requirement 行内第一个空白后的 `#` 也会切出
  comment，comment 内部不再二次切分；uv/pip-compile 的每个注释独占一行；
- pip-compile 的 `# via -r requirements.in`、`# via package (setup.py)`、
  `# via package (pyproject.toml)` 都忽略。

状态机：

1. 统一 BOM、CRLF/LF，处理行尾 `\` 续行；
2. 逐逻辑行处理：
   - `--...` 选项行：忽略，不改变当前 pin（extra index 由 `extra_index`
     单独检测，不进入 pin 的状态机）；
   - 空行/普通注释：保持当前 pin，等待后续 `# from`；
   - `# from <url>`：如果当前 pin 存在，记录来源；同一个 pin 的多个 `# from`
     规范化后不同 → 保留第一个来源，后续 warning；
   - 新的 requirement 行：先提交上一个 pin，再解析新 pin；
3. 文件结束时提交最后一个 pin；
4. 只有 `parse_requirement()` 能解析成精确 pin 的行才进入结果；范围、URL、选项、
   裸名字仍然跳过；
5. `dict_pins` 的 key 是 PEP 503 规范化名字，和现有 `dict_deps` 一致。

需要覆盖的边缘情况：

- 普通 requirements（无 `# from`）→ 所有 pin 的 `source=''`；如果文件声明了
  extra index 但所有 pin 的 source 都为空，记录一条汇总 warning
  （`N pins have no provided source, treated as official`），避免逐 pin 刷屏；
- 官方 `https://pypi.org/simple` → `source=''`，下载侧使用 `pypi_mirrors` 的 probe；
- `# from` 在 pin 之前、或没有对应 pin → 忽略或 warning；
- 一个 pin 多个 `# from`，或重复 pin 的不同来源 → 保留第一个来源，后续 warning；
- 重复版本 → 保留第一个版本，后续 warning（尽可能把文件解析完）；
- `--extra-index-url` 存在但没有提供源（所有 pin 的 `source=''`）→ 记录一条
  warning，提示额外的 index 没有被使用；
- `# via` 行忽略；
- 注释行/行内注释、`--hash`、续行、CRLF、BOM 保持现有行为；
- 直接 wheel URL / `file://` / `git+` 的 `# from` → warning，来源置空（未定义）。

#### 6.2.3 多文件合并

`PythonDeps.RequirementFiles` 可能同时列出 `requirements.txt` 和 `pyproject.toml`。
`LookbackWheel._parse_commit()` 目前按文件合并 `name -> version`；需要改为合并
`name -> (version, source)`：

- 版本冲突：保留第一个版本，后续 warning（尽可能把文件解析完）；
- 两个文件都有来源且不同：保留第一个来源，后续 warning；
- 一个文件有来源、另一个为空（pyproject）：保留非空来源；
- 都为空：来源为空（未定义，下载侧使用 `pypi_mirrors` probe）。

推荐约定：**带 uv 注释的 requirements 文件优先于 pyproject.toml**，因为后者没有来源
信息；实现上按“空来源不覆盖非空来源”处理即可。

#### 6.2.4 读取 mod entry 的镜像字段

`PackRepoEntry` 目前只解析 `update_pack_deps`。本方案需要它同时返回：

- `update_pack_deps`：pack 通道名单（现有）；
- `pypi_mirrors`：mod entry 的下载镜像结构，用于非提供源；缺省时使用
  `ModEntryInfo` 的 `DEFAULT_PYPI_MIRRORS`；
- `update_provide_pypi_mirrors`：提供源 URL 列表，缺省 `()`。

解析规则与 `update_pack_deps` 一致：只接受模块级静态声明，支持构造参数和构造后的
属性赋值；动态表达式报错，避免静默丢配置。`pypi_mirrors` 可以是 str、name→url 的
dict、group→mirrors 的嵌套 dict；`update_provide_pypi_mirrors` 必须是 str 的 list /
tuple / set，去重后按源码顺序返回。

**真实仓库的入口形态**：Alas2026 当前写成

```python
entry = DICT_MOD_ENTRY['alas']
entry.root = ...
```

而现在的解析器要求 `entry = ModEntryInfo(...)`，会拒绝 dict 下标。实施时必须二选一：

1. 扩展解析器：识别 `from alasio.config.entry.const import DICT_MOD_ENTRY` 后的
   `DICT_MOD_ENTRY['alas']`，用 alasio 自带的默认 entry 作为基底，再读取后面的属性赋值；
2. 修改真实仓库的 entry：使用显式 `ModEntryInfo(...)`，或至少显式声明
   `entry.pypi_mirrors` / `entry.update_provide_pypi_mirrors`。

推荐方案 1，因为 mod 仓库已经习惯 `DICT_MOD_ENTRY['alas']`；否则这是本方案在真实仓库
落地的阻塞项。

### 6.3 提供源与下载源

#### 6.3.1 规范化与匹配

匹配侧需要一个规范化步骤（与解析侧 `_read_source()` 内联的规则一致）：

```python
def normalize_source(url: str) -> str:
    ...  # lowercase scheme/host, strip default ports, strip trailing '/'

def is_provided_source(source: str, provide_mirrors) -> bool:
    if not source:
        return False
    return normalize_source(source) in {
        normalize_source(url) for url in provide_mirrors
    }
```

- `source`：从 `# from` 解析出的 index base URL；
- `provide_mirrors`：`ModEntryInfo.update_provide_pypi_mirrors`；
- 命中 → **提供源**：下载并发布该 pin 的 wheel；
- 未命中/空来源 → **非提供源**：从 `pypi_mirrors`/官方下载，不发布；
- 不再需要 `PythonDeps.OfficialIndexes` 或“未知 = 自定义 = 发布”的猜测：提供源是
  mod entry 显式声明的白名单。

#### 6.3.2 下载源

- **提供源**：直接从命中的 URL 下载。它可能不在官方 PyPI 上，不能替换成其它镜像。
  下载失败 → 该 config 失败。如果这个 URL 同时是 `pypi_mirrors` 的成员，两者指向
  同一个 index，实现可以复用 `pypi_mirrors` 的探测结果，但语义上以命中 URL 为准；
- **非提供源**：从 `ModEntryInfo.pypi_mirrors` 描述的镜像结构下载。该结构已经是客户端
  使用的形式，可以复用 `Mirrors.from_input()`；pack server 是同步流程，可以复用
  `probe_sync` 或按 group/成员的声明顺序直接尝试：
  - 按 group/成员的声明顺序尝试；
  - 探测/请求失败时切换到下一个成员；
  - 全部失败或结构非法 → config 失败（不能静默改用别的源）；
- 空来源按官方处理，同样从 `pypi_mirrors`/官方下载；
- 未提供源如果官方也没有对应 wheel → 构建失败；mod 作者需要把该来源加入
  `update_provide_pypi_mirrors`，而不是依赖隐式猜测。

#### 6.3.3 无效的来源

- `# from` 指向的不是 PEP 503 index（直接 wheel URL、`file://`、`git+`）→ warning，
  来源视为未定义；
- 解析不出 URL 的 `# from` 文本 → 同样 warning，来源视为未定义；
- 官方 `https://pypi.org/simple` → 来源视为未定义，交给 `pypi_mirrors` 的 probe；
- `update_provide_pypi_mirrors` 中的 URL 不是 HTTP(S) base URL → 解析 entry 时失败；
- `pypi_mirrors` 不符合 `Mirrors.from_input()` 接受的形状 → 解析 entry / 构建时失败。

**为什么不逐个检查官方 PyPI 是否有同版本 wheel**：`update_provide_pypi_mirrors` 已经
把“哪些源必须随更新包提供”显式写出来了。逐个探测官方源会把策略从“声明式”变成“运行
时猜测”，还会增加网络请求和缓存；本方案选择显式白名单。未列出的源如果官方没有 wheel，
就应当由 mod 作者补进 `update_provide_pypi_mirrors`，失败信息会直接指出这一点。

### 6.4 采样与数据模型

`LookbackWheel` 目前提供：

```python
latest_deps: dict[(name, version), commit]
lookback_deps: dict[(name, version), commit]
```

改为携带来源：

```python
class PinnedVersion(Struct):
    name: str
    version: str
    source: str      # index base URL, '' = no annotation
    commit: str

latest_pins: dict[str, PinnedVersion]              # latest commit 的全部 pin
latest_deps: dict[(name, version), PinnedVersion]  # pack 窗口的 latest 版本
lookback_deps: dict[(name, version), PinnedVersion]
```

- `latest_pins` 不再按 `update_pack_deps` 过滤，包含 latest requirements 的所有 pin；
- `latest_deps` / `lookback_deps` 的过滤逻辑保持不变，只多带 `source`；
- **没有 `window_pins`**：游戏脚本旧版本不需要完整依赖源，wheel 索引只使用
  `latest_pins`；`lookback_deps` 只用于生成 pack 通道的 update pack；
- 同一个 `(name, version)` 在不同 commit 来源不同时，最新 commit 的来源优先；
  lookback 版本只作为 pack 构建输入，不参与发布分类。

`DepGen` 侧：

```python
class DepVersion(Struct):
    name: str
    version: str
    commit: str
    target: bool
    source: str = ''
```

- `DepGen.sample` 继续只包含 `update_pack_deps` 的名字，但每个 `DepVersion` 带来源；
- `DepGen.entry` / `PackRepoEntry` 返回 mod entry 的 `pypi_mirrors` 和
  `update_provide_pypi_mirrors`（在 latest commit 上解析，并缓存）；
- 新增 `DepGen.publish_pins`：`{(name, version): source}`，来自 `latest_pins`，
  只保留 `is_provided_source(source, update_provide_pypi_mirrors)` 的项；
- 新增 `DepGen.publish_names` / `publish_versions` 之类的辅助属性，供清理和日志使用；
- `DepGen._fetch_wheels()` 不再用 `_mirror_of(name)` 决定所有来源，而是：
  - 提供源：用 `dep.source`（命中 `update_provide_pypi_mirrors` 的 URL）；
  - 非提供源：用 `pypi_mirrors` 解析出的镜像结构，按 group/成员顺序探测，
    失败则回退到官方 PyPI；
- `PythonDeps.PypiMirror` / `_mirror_of()` 被删除；下载源只来自 mod entry 的
  `pypi_mirrors` 和提供源 URL。

### 6.5 构建缓存

新增 `SimpleIndex`（建议放在 `fetch_wheel.py` 或新的 `wheel_index.py`）作为索引页的
统一读取器：

- `SimpleIndex(cache_root, source, name, client)`：`cache_root` 是
  `{run}/pack/{channel}/_wheel_cache`；
  - `index_data`：读取 `_wheel_cache/index/{source_key}/{name}.json`；不存在或过期
    时请求 `{source}/{name}/`，解析并原子写回；
  - `wheels_of(version)`：返回该版本的全部 `.whl`；
  - `pure_wheel_of(version)`：复用现有 `_is_pure_python3` 逻辑；
  - `download(wheel, target_folder)`：流式下载、校验 sha256、原子写。
- `WheelFetcher` 保留为 pack 构建的便捷封装：调用 `SimpleIndex.pure_wheel_of()`，
  下载到 `_wheel_cache/wheel/{name}/{version}/`；
- 发布用的 all-wheel 抓取调用 `SimpleIndex.wheels_of()`，把每个 wheel 下载到同一
  缓存目录；
- `index_data.json` 新格式可以带 `source`、`fetched_at`，用于 TTL 和来源校验；
  旧格式可以按“无时间戳、立即刷新”处理；
- 刷新策略：提供源每次运行最多重新读取一次索引页（约 25 个请求），或使用
  可配置的 TTL（例如 6 小时）。pack 构建的纯 Python wheel 可以继续信任缓存；
- 网络失败时可以回退到过期的 `index_data.json` 和已缓存的 wheel，并记录 warning；
  只有既没有缓存、也无法请求索引时才让该 config 失败，避免源站短暂抖动阻塞整个发布；
- 文件名安全：从索引页解析出的 `filename` 必须通过 `validate_filename()`，
  拒绝 `/`、`..` 等路径穿越；不要直接 `joinpath(untrusted_filename)`。

缓存键：

```text
{run}/pack/{channel}/_wheel_cache/index/{sha1(normalized_source)[:16]}/{normalized_name}.json
{run}/pack/{channel}/_wheel_cache/wheel/{normalized_name}/{version}/{filename}.whl
```

- 同一个 `(name, version)` 如果来自不同 source，缓存目录里可能有两份；这是期望行为，
  避免把镜像 wheel 当成提供源 wheel；
- pack 构建和发布共享同一份 wheel 缓存；发布时复制/硬链接到 `pack/{channel}/wheel/`；
- 缓存清理保留集合 = `sample`（pack 窗口，含 lookback）∪ `publish_pins`（latest 发布集合）；
- `_wheel_cache` 的路径段以 `_` 开头，发布记录/上传器跳过；缓存清理失败只影响磁盘，
  不影响客户端。

### 6.6 pack 构建

pack 构建逻辑不变：

- `DepGen.sample` 仍然只包含 `update_pack_deps` 的名字；
- `_fetch_wheels()` 为每个版本抓取一个纯 Python wheel；
- `_write_packs()` 生成 `packdep/{name}/{version}/full.pack` 和
  `update_{old}.pack`；
- `packdep` 快照只包含 pack 通道的依赖，不包含 wheel 索引；
- 非提供源的 pack wheel 只进 `_wheel_cache`，不进 `pack/{channel}/wheel/`。

差异只有两点：

1. wheel 的抓取目录从 `pack/{channel}/wheel/` 改为
   `pack/{channel}/_wheel_cache/wheel/`；
2. wheel 的下载源来自 mod entry：提供源用命中的 URL，非提供源用 `pypi_mirrors`
   解析出的镜像结构；不再使用 `PythonDeps.PypiMirror` / `_mirror_of()`。

### 6.7 wheel 索引发布

#### 6.7.1 发布集合

`publish_pins` 的取值：

- `latest_pins` 的全部 pin；
- 只保留 `is_provided_source(source, update_provide_pypi_mirrors)`；
- 不按 `update_pack_deps` 过滤；
- **没有 lookback**：旧游戏脚本版本不发布 wheel 索引，旧版本的 pack wheel 只进
  `_wheel_cache`，用于生成 update pack。

配置一致性：`PythonDeps.RequirementFiles` 必须包含客户端 entry 的
`requirements_file`（Alas2026 是根目录 `requirements.txt`），否则 wheel 索引可能缺少
客户端实际安装的 pin。推荐只列这个锁文件；`pyproject.toml` 只用于补充没有锁文件的
pin，且它的来源按官方处理。

#### 6.7.2 抓取

对每个 `(name, version, source)`（`source` 已命中 `update_provide_pypi_mirrors`）：

1. 读取 `SimpleIndex(source, name)` 的索引页；
2. 选出该版本、目标 Python 兼容的全部 `.whl`（见 §6.8）；
3. 逐个下载到 `pack/{channel}/_wheel_cache/wheel/{name}/{version}/`，校验 sha256；
4. 如果该版本没有可用 wheel，抛 `WheelNotFoundError`，让 config 失败；
5. 把文件复制/硬链接到 `pack/{channel}/wheel/{name}/{version}/`；
6. 生成 HTML 索引页。

去重：如果 pack 构建已经下载了同一个纯 Python wheel，直接复用缓存文件；不要重复下载。

#### 6.7.3 HTML 页面

两种页面，内容都很小：

1. **每包 PEP 503 页**：`wheel/{name}/index.html`

```html
<!DOCTYPE html>
<html><body>
<a href="2.13.1/httpx2-2.13.1-py3-none-any.whl#sha256=...">httpx2-2.13.1-py3-none-any.whl</a><br/>
<a href="2.13.1/httpx2-2.13.1-cp38-cp38-win_amd64.whl#sha256=...">...</a><br/>
</body></html>
```

2. **根 find-links 页**：`wheel/index.html`

```html
<!DOCTYPE html>
<html><body>
<a href="httpx2/2.13.1/httpx2-2.13.1-py3-none-any.whl#sha256=...">...</a><br/>
<a href="msgspec/0.22.0/msgspec-0.22.0-cp38-cp38-win_amd64.whl#sha256=...">...</a><br/>
</body></html>
```

要求：

- 每个 `<a>` 的文本是 wheel 文件名，`href` 是相对 URL；
- 始终带上 `#sha256=`：源链接自带时用源值，没有时用下载时计算的 sha256；
  便于 pip 校验，也让发布记录的内容可复现；
- 文件名做 HTML escape；
- 页面原子写（tmp + replace），避免同步器读到半个页面；
- 页面内容稳定排序（name、version、filename），避免无意义 diff；
- 只列 `pack/{channel}/wheel/` 下实际存在的文件；
- 不生成 `index_data.json` 之类的缓存文件。

#### 6.7.4 顺序与失败

`DepGen.run()` 内的顺序：

1. 抓取 pack 纯 Python wheel（如果有 pack 依赖）；
2. 写 pack 文件（如果有）；
3. 抓取并发布提供源 wheel + HTML 索引；
4. `_generated = True`。

`PackServer.run_config()` 随后才调用 `PackRepoGen.run()/commit()` 写
`packrepo/latest.pack`。这样 wheel 索引在树屏障之前落地，符合发布同步的设计。

任何一步失败：

- 该 config 失败，`PackRepoGen.commit()` 不执行；
- 旧 `latest.pack` 继续服务；
- 失败运行新写的 wheel/HTML 只是本地文件，不在发布记录中，不会被上传；
  `DepGen.commit()` 不会运行，因此旧索引文件不会被删除；
- 下一轮重新生成，原子覆盖同名文件，并在成功后的 `commit()` 清理残留。

#### 6.7.5 清理

`DepGen.commit()`：

- **即使本次 `publish_pins` 为空也要执行**：配置可能从“有提供源”变成“全是非提供源”，
  旧 `wheel/` 必须被清空，否则它会一直留在发布记录里；不要沿用当前
  `if not self.sample: return` 的早退；
- 删除 `pack/{channel}/wheel/` 下不在当前 `publish_pins` 的 name/version/wheel；
- 删除不再需要的 `{name}/index.html`；
- 重新生成/删除根 `index.html`；
- 删除旧布局遗留的 `index_data.json`（如果存在）；
- 清理 `pack/{channel}/_wheel_cache/wheel/` 中不在 `sample ∪ publish_pins` 的版本；
- 清理失败只 log，下一轮重试。

第一次从旧布局迁移时：

1. 旧 `pack/{channel}/wheel/` 里有未提供源 wheel；新的一次运行先写新的提供源索引；
2. `DepGen.commit()` 删除不在发布集合里的未提供源 wheel；
3. 发布记录在 `DepGen.commit()` 之后生成，因此不再包含未提供源 wheel；
4. 同步器按每个 remote 自己的 grace 删除远端旧对象。

如果不想重新下载，可以在第一次运行前把旧 `pack/{channel}/wheel/` 移到
`pack/{channel}/_wheel_cache/wheel/`；否则第一次会重新抓取 pack 依赖的 wheel。
这个迁移脚本可作为一次性运维步骤，不进入主流程。

### 6.8 Python/平台过滤

新增 `PythonDeps.TargetPython`（默认 `'3.8'`，与当前 shipped runtime 一致）。

对索引页里的每个 wheel 文件名：

- 用 `packaging.utils.parse_wheel_filename()` 或现有 `_parse_wheel_file()` 解析 tags；
- 保留目标 Python 兼容的 wheel：
  - 纯 Python：`py3`、`py2.py3`、`py38`；
  - CPython 3.8：`cp38`；
  - `abi3` 且 CPython 次版本 <= 8（例如 `cp36-abi3`、`cp38-abi3`）；
- 丢弃：`cp39+`、`cp310+`、`cp311+`、py2-only、PyPy-only（除非目标运行时改成 PyPy）；
- **平台不过滤**：保留 Windows / Linux / macOS / 其它平台的 wheel，因为客户端可能
  运行在任意支持的系统上；
- 如果过滤后没有任何 wheel → 报错，并列出该 pin、source 和索引页里的文件名。

`av==18.1.0` 的例子：索引页约 75 个链接，cp38 兼容的约 8 个；过滤可以把发布量
降到十分之一左右，同时仍然覆盖所有平台。

未来如果运行时升级到 Python 3.9+，只改 `TargetPython`；如果只想发布部分平台，
再增加 `Platforms` 配置，不在第一版实现。

### 6.9 与发布记录/同步的关系

按 `doc/2026-10-26_pack-server-publish-sync.md` 的模型：

- 发布记录扫描 `pack/{channel}/`，因此自动包含：
  - `wheel/index.html`
  - `wheel/{name}/index.html`
  - `wheel/{name}/{version}/{filename}.whl`
- 发布记录/上传器自动排除任意路径段以 `_` 开头的文件/文件夹，因此 `_wheel_cache`
  不进记录、不上传；
- `packrepo/latest.pack` 仍是屏障，wheel 索引文件在它之前上传；
- 未提供源 wheel 不在新记录里，同步器在 remote grace 后删除；
- `index_data.json` 移入 `_wheel_cache` 后，§14.3 的“是否排除 index_data.json”问题
  自动消失；
- 如果同一个 commit 只更新了 wheel 索引（例如提供源补了一个平台 wheel），
  `latest.pack` 内容可能不变；同步器用 `LastModified` 作为该 remote 的发布时间，
  仍能正确执行 grace 删除；
- HTML 页面建议设置 `Cache-Control: no-cache`（或短 TTL），避免 CDN 长时间返回旧索引；
  wheel 文件可以长缓存，因为文件名/版本应当不可变。

### 6.10 客户端契约

#### 6.10.1 URL 推导

mod 的 mirrors 指向 `.../{channel}/packrepo`。沿用 `PipPack._dep_url()` 的规则：

```text
packrepo                         -> wheel
.../{channel}/packrepo           -> .../{channel}/wheel
```

- 保留 mirrors 的 group/name 结构，只改 URL 最后一段；
- 客户端可以用与 pack 通道相同的 mirror 选择/探测结果，选择可用的 wheel 索引；
- 建议在 `PipPack` 或新的 `PipWheel` 上提供 `wheel_mirrors()` / `wheel_url()`，复用
  `Mirrors` 结构，避免重复探测逻辑。

#### 6.10.2 pip 调用

对非 pack 依赖，推荐每个 pin 单独调用 pip：

```text
{python} -m pip install --no-deps --only-binary=:all: --no-input --isolated \
    --index-url https://pypi.org/simple \
    --find-links {wheel_url}/index.html \
    {name}=={version}
```

或，如果静态托管支持目录索引：

```text
{python} -m pip install --no-deps --only-binary=:all: \
    --index-url https://pypi.org/simple \
    --extra-index-url {wheel_url}/ \
    {name}=={version}
```

推荐优先使用 `--find-links {wheel_url}/index.html`：

- 不依赖静态托管的目录 index 行为；
- 根页面已经列出所有提供源 wheel；
- 官方 wheel 从 `--index-url` 下载，提供源 wheel 从 find-links 页面下载；
- 不需要让 pip 访问 py38deps。

更精确的做法是：非提供源的 pin 只传官方 `--index-url`；提供源的 pin 再额外传
`--find-links`。客户端的 `dict_pins` 已经带 source，可以结合 mod entry 的
`update_provide_pypi_mirrors` 直接区分。两种方式都能工作：前者候选更少，后者实现更简单。
客户端不强制需要 `update_provide_pypi_mirrors` 才能工作；把 wheel 索引作为所有非 pack
pin 的额外 find-links 也是安全的（wheel 索引里只有提供源文件）。

官方 index 继续使用客户端已有的 `ModEntryInfo.pypi_mirrors` / 官方 PyPI；wheel 索引
只补充提供源 wheel，不替代客户端的 PyPI 镜像配置。

**不要**把原始 `requirements.txt` 直接交给 pip：文件里的
`--extra-index-url https://py38deps...` 会让 pip 仍然尝试不可达的源。客户端应：

- 用 `Requirements`/`dict_pins` 解析 pin；
- 对 pack 依赖走 `InstallBase.install_pack()`；
- 对非 pack 依赖逐 pin 调用 pip（如上），或生成一个去掉所有 index 选项的临时
  requirements 文件再交给 pip；
- 这样 py38deps 是否可达都不影响安装。

#### 6.10.3 与 pack 通道的分工

| 依赖类型 | 安装方式 | wheel 索引的作用 |
| --- | --- | --- |
| `update_pack_deps` 且纯 Python | pack 通道 | 可选 fallback |
| 非 pack、非提供源 | 官方 PyPI / `pypi_mirrors` | 不使用 |
| 非 pack、提供源（纯 Python） | wheel 索引 / pip | 主来源 |
| 非 pack、提供源（编译扩展） | wheel 索引 / pip | 唯一离线来源 |

wheel 索引不替代 pack 通道；两者可以同时存在，互不冲突。

---

## 7. 真实仓库示例（Alas2026）

### 7.1 解析结果

```text
总 pin 数：86
非提供源（官方）：61
提供源（py38deps，写入 update_provide_pypi_mirrors）：25
```

对应的 mod entry 声明（`pypi_mirrors` 保持默认的 cn/global 镜像）：

```python
entry = DICT_MOD_ENTRY['alas']
entry.update_provide_pypi_mirrors = ('https://py38deps.github.io/py38deps/simple/',)
```

### 7.2 预期发布集合

假设 `update_pack_deps` 只包含纯 Python 的 pack 依赖，wheel 索引仍会发布全部
25 个提供源（py38deps）pin 的 wheel，包括：

- 纯 Python：`httpx2`、`starlette`、`trio`、`anyio`、`idna`、`rich`、
  `pygments`、`typing-extensions`、`wsproto`、`h2`、`hpack`、`hyperframe`、
  `httpcore2`、`hypercorn`、`markdown-it-py`、`pyjwt`、`truststore`、`attrs`；
- 编译扩展：`msgspec`、`zstandard`、`av`、`cffi`、`multidict`、`propcache`、
  `pyyaml`。

非提供源的 `onnxruntime`、`opencv-python-headless`、`numpy`、`scipy`、`pillow`、
`lxml` 等**不发布**，即使它们在 `update_pack_deps` 里（pack 仍然可以构建，
但 wheel 只进缓存）。

### 7.3 `av==18.1.0` 的过滤

- 索引页链接：约 75 个（cp38 ~ cp314，多平台）；
- 目标 Python 3.8 兼容：约 8 个 cp38 wheel；
- 发布这 8 个即可覆盖 Windows / Linux / macOS；
- cp39+ 的 wheel 不进入 `pack/{channel}/wheel/`。

### 7.4 成本变化

- 非提供源 wheel 不再上传：以 `onnxruntime`、`opencv-python-headless`、`numpy`、
  `scipy` 等大包为主，节省的存储和流量是主要收益；
- 提供源 wheel 仍然上传：25 个包 × 目标 Python 平台 wheel，数量可控；
- `_wheel_cache` 不受上传成本影响，可继续保留以减少重复下载。

---

## 8. 代码改动清单（建议）

### 8.1 客户端解析层

- `alasio/deploy/simple_pip/parse_req.py`
  - 新增 `DepInfo`；
  - `Requirements.dict_pins` / `dict_sources`；
  - `_iter_requirement()` 或新的内部迭代器保留 `# from`；
  - 支持 uv / pip-compile 的 line、split 注释风格；
  - 来源 URL 规范化、异常、warning。
- `alasio/deploy_dev/pack_server/parse_dep.py`
  - 重新导出 `DepInfo` / `dict_pins`；
  - `build_pins` 从 `parse_req.py` 移到本模块（只有 release 侧使用）；
  - `PyprojectParser.dict_pins`（source=''）；
  - 多文件合并辅助函数。
- `alasio/deploy_dev/pack_server/lookback_wheel.py`
  - `PinnedVersion` / `latest_pins`；
  - `_parse_commit()` 合并 source；
  - 来源冲突处理。

### 8.2 pack server

- `alasio/deploy_dev/pack_server/fetch_wheel.py`
  - 抽出 `SimpleIndex`；
  - `WheelFetcher` 改用缓存目录和 `SimpleIndex`；
  - 新增 all-wheel 选择/下载；
  - 文件名安全校验；
  - index 缓存格式和 TTL。
- `alasio/deploy_dev/pack_server/dep_gen.py`
  - `DepVersion.source`；
  - `publish_pins`（latest ∩ `update_provide_pypi_mirrors`）；
  - `cache_wheel_folder = {repo_folder}/_wheel_cache`；
  - 从 `PackRepoEntry` 读取 `pypi_mirrors` / `update_provide_pypi_mirrors`；
  - `_fetch_wheels()` 用 per-pin source；
  - `_publish_wheel_index()`；
  - `_remove_stale_cache()` / `_remove_stale_wheel_index()`；
  - `run()` 在无 pack 依赖时仍发布 wheel；`commit()` 在发布集合为空时仍清理旧 wheel 索引。
- 新文件 `alasio/deploy_dev/pack_server/wheel_index.py`
  - wheel 标签过滤；
  - HTML 页面生成；
  - 复制/硬链接、排序、原子写；
  - 发布集合清理。
- `alasio/config/entry/const.py`
  - `ModEntryInfo` 新增 `update_provide_pypi_mirrors: Tuple[str, ...] = ()`
    （py3.8 兼容写法，等价于 `tuple[str, ...]`）；
  - `pypi_mirrors` 保持现有字段，作为下载镜像结构。
- `alasio/deploy_dev/pack_server/parse_entry.py`
  - 解析 `pypi_mirrors` / `update_provide_pypi_mirrors`；
  - 支持真实仓库的 `DICT_MOD_ENTRY['alas']` 入口或要求显式 `ModEntryInfo`；
  - 静态声明校验、URL 规范化、测试。
- `alasio/deploy_dev/pack_server/model.py`
  - 删除 `PythonDeps.PypiMirror` / `PythonMirrorInfo`；
  - 保留 `PythonDeps.TargetPython`（默认 `'3.8'`）；
  - 不增加 `OfficialIndexes` / `PublishLookback` / `PublishWheelIndex` 配置。
- `alasio/deploy_dev/pack_server/main.py`
  - 基本不变；确认顺序和日志。
- `alasio/deploy_dev/pack_server/serve.py`
  - 开发服务器可按同一约定跳过 `_` 前缀（可选，至少上传器必须跳过）。

### 8.3 客户端安装层（后续）

- `alasio/deploy/simple_pip/pip_pack.py`
  - `wheel_mirrors()` / `wheel_url()`，由 `packrepo` 推导 `wheel`；
- 非 pack 依赖的 pip install 流程（当前尚未实现）
  - 逐 pin 生成 pip 命令；
  - 使用 `--find-links {wheel_url}/index.html`；
  - 不再依赖 requirements 文件的 `--extra-index-url`。

### 8.4 发布记录/同步（后续）

- `record.write()` / 上传器扫描 `pack/{channel}/` 时，跳过任意路径段以 `_` 开头的
  文件/文件夹，因此 `_wheel_cache` 不进记录、不上传；
- 发布记录包含 `wheel/index.html`、`wheel/{name}/index.html`、`wheel/{name}/{version}/*.whl`；
- 测试确认 `_wheel_cache` 不在记录中；
- 成本表更新：未提供源 wheel 不再发布。

---

## 9. 实施步骤

### Phase 0：解析器（无行为变化）

- 实现 `DepInfo` / `dict_pins` / `dict_sources`；
- `PyprojectParser` 支持空来源；
- `ModEntryInfo` 增加 `update_provide_pypi_mirrors`；
- `PackRepoEntry` 解析 `pypi_mirrors` / `update_provide_pypi_mirrors`；
- 处理真实仓库的 `DICT_MOD_ENTRY['alas']` 入口；
- 用 Alas2026 的真实文件做 fixture；
- 现有测试全部通过，`dict_deps` 行为不变。

验收：能正确解析 86 个 pin，61 非提供源 / 25 提供源；普通 requirements 全部官方；
能读到 mod entry 的两个镜像字段。

### Phase 1：来源传播与删除 PypiMirror（仍不改变发布）

- `PinnedVersion` / `latest_pins` / `DepVersion.source`；
- 删除 `PythonDeps.PypiMirror` / `PythonMirrorInfo` / `_mirror_of()`；
- 非提供源从 mod entry 的 `pypi_mirrors` 下载，提供源从命中 URL 下载；
- `publish_pins` 已计算并打日志，但 wheel 仍写到旧的
  `pack/{channel}/wheel/`，发布行为暂不变更；
- 保持现有发布行为，便于观察来源和提供源匹配是否正确。

验收：Alas2026 的 25 个提供源 pin 从 py38deps 抓取；非提供源从 `pypi_mirrors`/
官方抓取；现有测试更新后通过。

### Phase 2：缓存分离与 `_` 上传约定

- 新增 `pack/{channel}/_wheel_cache/wheel/` 和 `_wheel_cache/index/`；
- `WheelFetcher` 改为 `_wheel_cache`；
- 发布记录/上传器实现“任意路径段以 `_` 开头不上传”；
- 构建完成后，仍把所有抓到的 wheel 复制到 `pack/{channel}/wheel/`（保持旧行为）；
- 清理 `pack/` 下旧 `index_data.json`；
- 测试路径和上传过滤。

验收：发布内容与旧行为一致；`_wheel_cache` 不出现在发布记录/对象存储；重启运行不会
重复下载。

### Phase 3：只发布提供源 wheel

- 实现 `publish_pins`（latest ∩ `update_provide_pypi_mirrors`）；
- 实现 all-wheel 抓取和 Python/平台过滤；
- 实现 HTML 索引页；
- `DepGen.run()` 在无 pack 依赖时仍发布 wheel；
- `DepGen.commit()` 清理未提供源 wheel 和旧索引；
- 首次运行时删除旧的未提供源 wheel；
- 不发布 lookback 版本。

验收：Alas2026 的 `pack/{channel}/wheel/` 只包含 25 个提供源 pin 的目标 Python
wheel + HTML；`onnxruntime` 等非提供源 wheel 不再出现；pack 通道不变。

### Phase 4：客户端接入

- `PipPack` 提供 wheel index URL；
- 非 pack 依赖逐 pin pip install，使用 `--find-links`；
- 不再把原始 requirements 文件直接交给 pip；
- 离线测试：只允许官方 PyPI + wheel 索引，不允许 py38deps，客户端仍能安装
  `msgspec==0.22.0`。

验收：断网 py38deps 时，提供源编译依赖仍可安装；非提供源依赖从官方源安装。

### Phase 5：发布记录/同步与迁移

- 记录扫描确认包含 wheel HTML/wheel，排除 `_wheel_cache`；
- 同步器按 grace 删除旧未提供源 wheel；
- 首次迁移：清理旧 `pack/{channel}/wheel/`、更新发布记录；
- 可选：把旧 wheel 移到 `_wheel_cache` 避免重新下载。

验收：桶内未提供源 wheel 在 grace 后消失；客户端索引可用；旧版本回滚仍可用。

### Phase 6：文档与旧数据清理

- 删除旧的 `PythonDeps.PypiMirror` 相关文档/配置示例；
- 清理早期方案可能创建的 `{run}/cache/{channel}/`；
- 更新 pack server / publish-sync 文档的 wheel 说明。

---

## 10. 测试计划

### 10.1 解析器

`tests/deploy/simple_pip/test_parse_req.py`：

- 普通 requirements → `source=''`；
- 官方 `# from` → `source=''`（下载侧 probe）；
- py38deps `# from` → 提供源（当 `update_provide_pypi_mirrors` 包含它）；
- trailing slash / 大小写 host 规范化；
- `# via` 忽略；
- 一个 pin 多个 `# from` → 保留第一个 + warning；
- 重复版本 → 保留第一个 + warning；
- 无提供源但有 `--extra-index-url` → `source=''` + warning；
- 注释行/行内注释、`--hash`、续行、CRLF、BOM；
- 直接 wheel URL / `file://` → warning + `source=''`；
- 真实 Alas2026 文件片段（86 pin 计数）。

`tests/deploy_dev/pack_server/test_parse_dep.py`：

- `PyprojectParser.dict_pins` 的 source 为空；
- requirements + pyproject 合并；
- 来源冲突；
- 版本冲突和来源冲突都保留第一个 + warning。

`tests/deploy_dev/pack_server/test_parse_entry.py`：

- 解析 `pypi_mirrors` 的 str / dict / 嵌套 dict / 模块级常量；
- 解析 `update_provide_pypi_mirrors` 的 tuple/list/set，去重，非 str 报错；
- 动态值报错；
- `DICT_MOD_ENTRY['alas']` + 属性赋值的入口形态；
- 缺省时 `pypi_mirrors` 使用 `DEFAULT_PYPI_MIRRORS`、provide 列表为空。

`tests/config/entry/test_mod_entry.py`：

- `update_provide_pypi_mirrors` 默认 `()`；
- 声明后的 tuple 值和副本行为。

### 10.2 采样

`tests/deploy_dev/pack_server/test_lookback_wheel.py`：

- latest / lookback 的 source 传播；
- 同一版本在不同 commit 的来源；
- 缺失来源、来源冲突；
- `update_pack_deps` 过滤不变；
- 没有 `window_pins`，旧版本只通过 `lookback_deps` 进入 pack 构建。

### 10.3 缓存与索引

`tests/deploy_dev/pack_server/test_fetch_wheel.py`：

- `SimpleIndex` 缓存命中/刷新；
- all-wheel 选择（cp38 / py3 / abi3 / 排除 cp39+）；
- 无兼容 wheel 报错；
- sha256 校验；
- 文件名路径穿越被拒绝。

新 `tests/deploy_dev/pack_server/test_wheel_index.py`：

- 每包 HTML 的链接、顺序、hash；
- 根 find-links 页；
- 原子写；
- 只列实际存在的文件；
- 清理旧页面/旧 wheel。

### 10.4 DepGen / 发布

`tests/deploy_dev/pack_server/test_model.py`：

- `PythonDepsConfig` 不再有 `PypiMirror` / `PythonMirrorInfo`；
- `TargetPython` 默认 `'3.8'`。

`tests/deploy_dev/pack_server/test_dep_gen.py`：

- 删除 `TestMirror` / `PythonMirrorInfo` 相关测试，新增从 mod entry `pypi_mirrors`
  选择下载源的测试；
- pack 依赖仍只抓纯 Python wheel；
- 非提供源 wheel 只在 `_wheel_cache`，不在 `pack/{channel}/wheel/`；
- 提供源 wheel 在 `_wheel_cache` 和 `wheel/` 都有；
- 无 pack 依赖时仍发布提供源 wheel；
- 无兼容 wheel 时 config 失败；
- `commit()` 清理 stale `_wheel_cache` / `wheel/`；
- 旧布局 `index_data.json` 被清理；
- 发布记录只包含 `pack/` 下非 `_` 路径的文件。`_wheel_cache` 不在记录中。

### 10.5 客户端

`tests/deploy/simple_pip/test_pip_pack.py`：

- `packrepo` → `wheel` URL 推导；
- mirrors group/name 保留；
- pip 命令使用 `--find-links`，不读取 requirements 的 extra index。

### 10.6 端到端

- 用 `MockMirror` 模拟非提供源和提供源；
- 用一个 `StaticFiles` app 提供 `pack/{channel}/wheel/`；
- 校验根 index 页的链接能被 pip/HTML parser 识别；
- 可选：在临时 venv 中 `pip install --no-index --find-links` 安装一个提供源 wheel；
- 确认非提供源 wheel 不出现在发布目录；
- 确认 `_wheel_cache` 不被写入发布记录/对象存储。

---

## 11. 风险与开放问题

1. **提供源清单必须完整**：`update_provide_pypi_mirrors` 是显式白名单；未列出的源
   统一按官方处理。如果某个私有源的 wheel 官方没有、又忘了加入清单，构建会失败。
   失败信息必须明确提示“把该 source 加入 `update_provide_pypi_mirrors`”。
2. **entry 入口形态**：Alas2026 使用 `DICT_MOD_ENTRY['alas']`，当前解析器只接受
   `ModEntryInfo(...)`。需要扩展解析器（推荐）或修改真实仓库 entry；否则读不到
   `pypi_mirrors` / `update_provide_pypi_mirrors`，整个方案无法在真实仓库落地。
3. **`pypi_mirrors` 的语义**：它同时被客户端 pip 和 pack server 下载使用。修改它会
   同时影响两边；它必须是官方 PyPI 的镜像结构。如果它指向私有源，这个源必须同时
   出现在 `update_provide_pypi_mirrors` 中，否则会被当作非提供源而不发布。
4. **提供源可达性**：pack server 必须能访问 py38deps（GitHub）；如果构建机也不可达，
   需要本地 wheel 缓存或代理。方案目前假设 pack server 可达；不可达时 config 失败，
   旧通道继续服务。
5. **sdist**：提供源如果只提供 sdist，本方案无法发布；要求 requirements 编译时保证
   wheel 存在，否则 config 失败。
6. **旧客户端/旧 tree**：wheel 索引只发布 latest；如果存在“未更新 tree 就手工 pip
   install”的场景，本方案不覆盖。游戏脚本旧版本没有完整依赖源是明确决策。
7. **静态托管的目录索引**：PEP 503 的 `{name}/` 是否能返回 `index.html` 取决于托管配置；
   根 `index.html` + `--find-links` 是更稳的回退。
8. **CDN 缓存**：HTML 索引需要 no-cache/短 TTL；wheel 文件名不可变，可长缓存。
9. **同名 wheel 内容变化**：如果提供源重新发布同名同版本但不同内容的 wheel，
   需要检测 sha256 变化并重写索引；CDN 可能短暂返回旧文件。建议把这种情况视为源错误。
10. **体积**：提供源的平台 wheel 仍可能较大（如 `av`）；需要监控
    `pack/{channel}/wheel/` 大小，并评估是否增加 `Platforms` 配置。
11. **Python 版本**：`TargetPython` 默认 3.8；未来运行时升级时需要同步更新。
12. **缓存隔离**：`_wheel_cache` 按 channel 独立，避免损坏跨 channel 污染；代价是多个
    channel 共享同版本时可能重复下载，后续可以按 source+name+version 共享（可选）。
13. **环境标记**：解析器不评估 marker；如果 lock 文件里有对 Python 3.8 不生效的 pin，
    它仍可能被抓取/发布。当前 requirements 是按 3.8 编译的，影响很小；未来多版本 lock
    需要先做 marker 过滤。
14. **`_` 前缀约定**：发布记录/上传器必须统一实现“任意路径段以 `_` 开头不上传”；
    否则 `_wheel_cache` 会被误传。开发服务器可选用同一约定。
15. **安全**：requirements 来自 repo，来源 URL 会被用于网络请求；需要限制 scheme、
    拒绝路径穿越文件名，必要时增加来源 host allowlist。
16. **lookback 版本的提供源清单**：`update_provide_pypi_mirrors` 从 latest entry 读取；
    旧 commit 的 pin 如果来自最新列表已移除的源，构建旧 update pack 时会被当作非提供源，
    需要从官方下载。旧 wheel 需要在 `_wheel_cache` 中，或在 lookback 窗口内保留该源。
    这只影响旧版本 update pack 的构建，不影响 latest 的 wheel 索引。

---

## 12. 验收标准

1. 对 Alas2026 运行 pack server 后：
   - `pack/{channel}/wheel/` 只包含 25 个提供源 pin 的目标 Python wheel 和 HTML；
   - `onnxruntime`、`opencv-python-headless`、`numpy` 等非提供源 wheel 不在发布目录；
   - `pack/{channel}/_wheel_cache/wheel/` 仍包含 pack 构建所需的非提供源 wheel；
   - `packdep` / `packrepo` 的行为和体积不变。
2. `av==18.1.0` 只发布 cp38 兼容的约 8 个平台 wheel，不发布 cp39+ 的 wheel。
3. 客户端在无法访问 py38deps 的网络中，仍能安装 `msgspec==0.22.0`、
   `zstandard==0.25.0` 等提供源编译依赖。
4. 客户端能从官方 PyPI 安装 `onnxruntime`、`opencv-python-headless` 等非提供源依赖。
5. 发布记录/上传器不包含任何 `_` 前缀路径，`_wheel_cache` 不上传；同步器上传的
   wheel 对象只有提供源文件 + HTML。
6. 旧未提供源 wheel 在 remote grace 后被删除，回滚到旧版本仍能工作。
7. pack server 能从 Alas2026 的 mod entry 读取 `pypi_mirrors` 和
   `update_provide_pypi_mirrors`（`DICT_MOD_ENTRY['alas']` 形态）。
8. 现有 pack server 测试、客户端依赖测试全部通过（Python 3.8 / Windows 7 兼容）。
