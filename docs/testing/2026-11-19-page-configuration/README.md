# 通用页面配置补齐 · V1.4 实现与验收记录

日期：2026-11-19（北京时间）。计划及初版补丁由 ChatGPT Web 提供，本地整合与浏览器补验由 Codex 执行。

**五项能力已整合，真实 Web 双页验收通过。** Windows Edge 使用交付的工程和页面包，经 Web Gateway 连接软件 Runtime，完成四次归档、跨页回看、切页恢复和一次确认停止。

初版补丁基线为 `a9554a0ea80311a6870f4544af2398b6446c088b`。Codex 快进到该基线后，检查并整合补丁，应用提交为 `70062e6a6b481359a69e0406bd7cf151f69bb5e5`。浏览器验收使用 `53b38e1f69572fd34d7dcbf89866419e50b49683`，该提交仅修正和补强验收脚本。最终运行时验收所用源码、工程和包均匹配该提交。工作区另有四份 11 月 13 日历史证据的 CRLF 行尾差异，以及本机临时资料。

## 1. 五项改动

| 用户提出的差距 | 本轮实现 | 通用配置入口 |
| --- | --- | --- |
| 开始/停止文字和默认按钮外观固定 | 自定义文字、主/次/链接样式、字体、圆角、内距、最小宽高、图标；保持原命令及可用状态 | `shell.startControl/stopControl` |
| 页头和 logo 显示范围固定 | logo 从原资源等比显示，页头表面及主/副标题角色可配置 | `logoWidth/logoHeight/headerSurface/headerTypography` |
| 左标题不跟色、缺左动态圆点 | 标题跟随当前状态、圆点置于标题前；空/未映射值清除旧表现 | indicator 的 `titleFollowsState/markerPosition/showMarker` |
| 没有两个选中中心的连接虚线 | 通用两点构线算子，输出既有 lineCollection，复用 Annotate 的 dashed/dot | `vision.geometry.line_from_points` |
| 历史筛选/排序/分页不能独立隐藏 | 三开关分别控制 UI；外部按条导航和同记录图片/详情继续工作 | `historyView.showFilter/showSort/showPagination` |

Qt 与 Web 共用字段。结构化样式分别生成 QSS 和 CSS，本轮没有任意 QSS 字符串编辑入口。
作者预览显示新配置但不执行启动/停止。旧完成计划保持原结论，本轮任务在
[complete-page-configuration](../../../openspec/changes/complete-page-configuration/README.md) 中跟踪。

附带关闭了已观察到的同名运行识别问题：Web 查看、停止目标和实时预览下拉增加短运行 ID，完整 ID 在提示中；请求仍使用原始完整 ID。

## 2. ChatGPT Web 初版验证

下表来自随包记录，对应 `a9554a0e` 加当时尚未提交的补丁。Windows 上实际补验的项目列在第 4 节。

| 实际执行范围 | 结果 | 证据层次 |
| --- | --- | --- |
| 构线、几何桥、Annotate、注册及参数/核心版本检查 | 21 项通过 | 源码算子与图像结果检查 |
| Qt 状态表现及历史控件 | 32 项通过 | 原生 Qt 自动化；包含隐藏栏后的跨页/返回最新 |
| Qt 作者属性入口 | 2 项通过 | 实际表单、保存和重新加载当前工程 |
| Qt 宿主页头/按钮 + 原多流程控制回归 | 3 项通过 | 原生控件及软件 Runtime 开始/停止 |
| Web 状态/历史、作者字段、宿主、恢复及编辑器相关测试 | 66 项通过 | JSDOM/React 实际控件事件和现有测试；不是浏览器截图 |
| 官方生成物、工程发布、Web 后端资源/历史往返 | 11 项通过 | 源模型/包构建 + HTTP TestClient；后端 Runtime 使用测试夹具 |
| TypeScript、Vite build | 通过 | 当前前端源码实际构建 |
| 本轮 28 个 Python 文件 Ruff；相关 helper mypy | 通过 | 静态检查 |
| OpenSpec 2.1.0 strict | 通过 | 本轮规格格式与结构；关闭 CLI 遥测/更新检查 |
| SDK 1.8.0 | 通过 | 42 份交付文件哈希匹配；wheel 内 16 个 Python 文件与源文件逐字节一致；隔离导入不加载 emo_master |

没有把复测次数重复算作新的场景。测试主要覆盖五项能力的正常路径、直接反例和保存/发布，不增加硬件故障或全部配置排列组合。

复核发现并修复了一个作者入口问题：默认未启用自定义页头时，新子组原先仍能编辑但保存会丢弃；现在这些组随页头总开关启用/禁用，关闭再开启保留当前输入。

SDK 仍使用工程 2.10、页面 1.3、Panel Contract 1.0。只升级共享 SDK 至 1.8.0，不增加旧工程、旧草稿、旧包或旧 SDK 兼容机制。生成脚本保留已有交付文本的行尾，避免跨系统重建引入大量无关差异。

## 3. 同一工程及 Qt 实际截图

交付示例位于 `examples/generic_page_composition/`，包含可编辑的 `inspection.emoproj` 和同工程的 `inspection.emoweb`。
工程的检测输入是明确的合成图与固定输出 ONNX 示例，用于演示页面及数据链路，不是训练完成的现场识别模型。

本轮移除了独立静态边缘图/Hough 连线；现在每轮从检测输出选中 upper/lower 两个目标并提取中心，生成真实线段，再标注到本次图像。新增缺点分支只接受本次显式空列表，不代替已有业务 NG/缺目标判断。

软件 Runtime 的完整归档验证完成了 6 次运行、主图像素与当前结果一致、新连线位置、选中记录和跨页等检查。
正式交付数据库仍为 0 条记录；截图另在临时副本中通过真实 `OperatorWindow` 点击开始 4 次，全部 COMPLETED。

- [生产监控 · 1366×768](qt-production-1366x768.png)
- [历史回看 · 1366×768](qt-history-1366x768.png)
- [生产监控 · 1920×1080](qt-production-1920x1080.png)
- [历史回看 · 1920×1080](qt-history-1920x1080.png)
- [Qt 实际记录](qt-preview.json)

图片均为 `QWidget.grab()` 的实际输出，没有拼接或重绘。Qt 使用 Linux offscreen；虚拟桌面默认 800×600，脚本显式设置实际控件视口并验证图片尺寸。**这不是 Windows 人工桌面或冻结 EXE 验收。** 1366×768 保留正常滚动，较长 UTC 时间可换行。

正式工程 SHA-256：`b7f466d4c5d4fcaa20bd6d75150b8bad65225d7ed6ca9084d9bff4273666775e`。

## 4. Codex 本地整合与真实浏览器验收

Windows 采用 Python 3.10.21、配套 SDK 1.8.0、Playwright 1.65.2 和 Edge 157.0.4341.42。原 Python 环境缺少 WebSocket 依赖，按 Web 后端声明安装后连接成功。旧本地构建目录中的 1.7.0 安装元数据已移到临时目录。最终采集进程和独立 Web 网关均报告 SDK 1.8.0。

| 本地检查 | 实际结果 |
| --- | --- |
| 补丁与契约 | 核对 87 个清单路径（含删除和 wheel 更名）。42 份契约文件的长度和 SHA-256 均匹配；配套 wheel 与补丁内 wheel 相同，16 个 Python 模块与源码一致（忽略文本行尾）。 |
| 构建与定向回归 | TypeScript、Vite 和 `package_frontend.py` 通过。构线、Qt 作者入口、宿主、历史控件和状态表现共 44 项 pytest 通过，使用 Windows Qt offscreen。官方 OpenSpec CLI 2.0.0 strict 通过。 |
| 生产监控 | 点击“开始检测”四次，四个 Job 均为 COMPLETED。按记录键等到本次结果，三条状态行的圆点均位于标题前，圆点、标题和值同色。第 4 条切换为“复核样本／需复核”。在显示主图的两个中心之间检测到 16 个白线采样点和 14 个间隔点。 |
| 页头与控制 | Logo 原图 96×96，按 64×64 等比显示。主／副标题为 26／14 px，两个按钮高 44 px、圆角 8 px。未选停止目标时按钮禁用。另启一次运行，明确选择目标，点击“停止检测”并接受含同一运行 ID 的浏览器确认框，停止请求获受理，Runtime 终态为 ABORTED。 |
| 历史回看 | 内置筛选、排序、分页三栏均隐藏，外部导航可用。按采样序号依次查看 4 → 3 → 2 → 1，再跨页返回 2，切去生产页再回来仍为 2，最后回到最新的 4。每一步核对记录键、序号、日志，并把浏览器显示的主图和局部图像素分别与该条 SQLite 记录引用的文件比对。 |

| Edge 实际视口 | 生产监控 | 历史回看 |
| --- | --- | --- |
| 1366×768 | [生产截图](web-production-1366x768.png) | [历史截图](web-history-1366x768.png) |
| 1920×1080 | [生产截图](web-production-1920x1080.png) | [历史截图](web-history-1920x1080.png) |

四张截图均已逐张查看。两种视口都保留页头、页签和并列内容区，无横向溢出。1366×768 需向下滚动使用历史主图下方的导航；1920×1080 首屏可见导航、记录列表和局部图。完整结果、运行 ID 和每一步记录键见 [Edge 实际记录](web-preview.json)。ChatGPT Web 初版环境的 AF_UNIX IPC 阻断另保存在[原始阻断记录](web-preview-chatgpt-web-blocked.json)。

在仓库根目录和 Python 3.10 环境执行。浏览器脚本会复制工程到临时目录并启动回环网关。交付的 SQLite 库保持 0 条记录。

```powershell
python -m pip install --no-deps web_panel/contracts/panel-contract-v1/python/emo_panel_protocol-1.8.0-py3-none-any.whl
python -m pip install -e ./web_panel playwright
npm --prefix web_panel/frontend ci
npm --prefix web_panel/frontend run build
python web_panel/scripts/package_frontend.py
$env:PYTHONPATH = (Resolve-Path src).Path
$env:CHROMIUM_PATH = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
python examples/generic_page_composition/capture_web_preview.py examples/generic_page_composition/inspection.emoproj --output .tmp/page-configuration-preview
```

本地直接复用已安装的前端依赖。脚本修正了页面包导入后的等待方式、运行终态字段和状态标题选择器，并在浏览器内比对已解码的图片像素。验收期间未修改宿主命令、归档逻辑或历史查询。

复现 Qt 证据：

```text
python examples/generic_page_composition/capture_preview.py examples/generic_page_composition/inspection.emoproj --output docs/testing/2026-11-19-page-configuration
```

Linux 无桌面环境可设置 `QT_QPA_PLATFORM=offscreen`；Windows 原生走查应使用正常桌面，不把 offscreen 等同于人工验收。

## 5. CI 集成收尾

**3.5 已完成。** 正式代码 `912af529` 的 Source CI 和 Web CI 均已通过。[Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44509448074/attempts/2) · [Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44509447970)

ChatGPT Web 提供计划、初版补丁和对 `e3b6947e` 的审查意见。Codex 核对 Actions 日志，修复以下问题并执行补验。任务 3.4 的工程、发布包和四张 Edge 截图沿用第 4 节的记录。

| 提交 | 失败原因与修复 | 本地复验 |
| --- | --- | --- |
| `3bff260d` | Linux mypy 无法识别 MVS 的 Windows DLL 回退路径。为该分支增加显式平台条件。前端目录测试和双 Job 浏览器测试改为检查包含短 ID、大写状态的正式标签。 | Linux 和 Windows 两种 mypy 目标均通过。Windows MVS 模拟测试 24 项、运行状态测试 32 项通过。Edge 实际跑完两次启停。 |
| `e3d6a737` | 官方预览样例同时显示页头 Logo 和页面图片，历史页采用隐藏工具栏的列表。分别定位两张图片，在正式列表验证记录恢复及空图回收，在启用工具栏的表格验证列、筛选、刷新保留选择和清除。 | 官方只读预览和带可见工具栏的表格浏览器流程均通过。 |
| `912af529` | 仓库外安装 Web wheel 的检查仍精确要求 SDK 1.7.0。更新为配套 SDK 1.8.0，继续核对已安装元数据、模块版本、协议夹具、静态资源与依赖隔离。 | 在临时目录重建 Web wheel，在新的 Python 环境中安装并执行现有检查和 CLI，通过。 |

本地定向检查使用对应提交前的工作树；工作区另有四份 11 月 13 日的文档行尾差异。GitHub 运行使用提交 `912af5298cb6dfbe1a8ede9efed230a37d379e74` 的检出内容。

**源码分片。** 已核对各分片的 JUnit 和 `ci-shard.json`。每个平台收集 9,659 项，两分片无重叠，执行集合与所选集合一致。下表按 pytest 用例计数，子测试未另行累加。两平台覆盖汇总均通过。

| 平台与分片 | 通过 | 原有跳过 | 失败／错误 |
| --- | ---: | ---: | ---: |
| [Linux Runtime](https://github.com/jsdfhasuh/emo_master/actions/runs/44509448074/job/135509799227) | 3,383 | 10 | 0 |
| [Linux 其他](https://github.com/jsdfhasuh/emo_master/actions/runs/44509448074/job/135509799191) | 6,236 | 30 | 0 |
| [Windows Runtime 重跑](https://github.com/jsdfhasuh/emo_master/actions/runs/44509448074/job/135530779293) | 3,391 | 2 | 0 |
| [Windows 其他](https://github.com/jsdfhasuh/emo_master/actions/runs/44509448074/job/135509799198) | 6,262 | 4 | 0 |

**Web CI 三个任务均通过。** Linux 完整前端逻辑套件 204 项全部通过。Linux 和 Windows 都完成了官方只读预览、桌面与移动端浏览器检查，以及源码目录外安装 SDK 1.8.0 和 Web wheel。第三个任务使用真实软件 Runtime。[Linux Web 检查](https://github.com/jsdfhasuh/emo_master/actions/runs/44509447970/job/135509529267) · [Windows Web 检查](https://github.com/jsdfhasuh/emo_master/actions/runs/44509447970/job/135509528503) · [软件 Runtime 浏览器检查](https://github.com/jsdfhasuh/emo_master/actions/runs/44509447970/job/135509528978)

**云端双 Job 证据。** 已下载并核对该提交的 `product-browser/report.json` 与 `websocket.json`。同一 Runtime 受理两次启动和两次停止。两个 Job 的短 ID 依次为 `90bdcdc2`、`2c2f5fde`，结果键前缀分别为 `dc6701f6`、`a9fe17c4`。第二个 Job 的图片归属键与其完整结果键相同，浏览器显示尺寸为 320×240。两个 Job 停止后的权威状态均为 `ABORTED`，执行资源均已释放。该时刻的展示资源状态均为 `displayResourcesReleased=false`，页面按 Runtime 回报显示。浏览器错误列表为空。[Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44509447970)

**Windows Runtime 首次运行。** 两条多图用例未取得预期的 `COMPLETE`。其中双 Job 用例的工作流已结束，后图编码已完成，但组装器到达 500 ms 封口期限时仍在等待回调，记录为 `EXPORT_TIMEOUT`。同一份诊断记录了原图接收入库耗时 406 ms。另一条用例只记录了结果完整性断言失败。两条用例所属源码与测试从上次通过的 `f5e0a120` 到本次提交无差异。本机 Windows 原样运行 `test_multi_capture.py` 得到 23 项通过。CI 保留原预算、资源约束和断言，仅在同一代码 SHA 上重跑失败分片及其汇总。[首次 Windows Runtime 日志](https://github.com/jsdfhasuh/emo_master/actions/runs/44509448074/job/135509799270)

**运行中配额测试。** 完成记录 `9c96c68e` 再次触发完整 Source CI。Windows Runtime 有 1 项失败，另 3,390 项通过、2 项原有跳过。第一次启动已经产生显示结果，第二次启动也已按配额拒绝，但仓库中的原 Job 当时仍为 `STARTING`。该测试要求原 Job 处于 `RUNNING`。`429ac2e9` 在第二次启动前等待同一 Job 进入这个状态，保留原来的配额、回收等待、Job 数量和最终状态断言。本机 Windows 在提交前运行完整的 `test_normal_restart.py`，3 项通过。[原始失败日志](https://github.com/jsdfhasuh/emo_master/actions/runs/44520264621/job/135546880381) · [修复提交](https://github.com/jsdfhasuh/emo_master/commit/429ac2e91622c246f0a355d5e8ef110069bdc410)

`429ac2e9` 的配额测试在 Linux 和 Windows 均通过。Windows Runtime 分片另有 1 项失败（3,390 项通过、2 项原有跳过）。模拟相机请求序号已到 2，Job 仓库仍显示 `STARTING`，用例在发出停止请求前失败。设备 IPC 和监督器分别上报进度。模拟设备测试现用原有 30 秒时限等待请求序号和同一 Job 的 `RUNNING` 状态，再执行停止、终态、宿主退出及资源释放断言。本机 Windows 完整运行 `test_cancellable_device_wait.py`，4 项通过。[Windows Runtime 日志](https://github.com/jsdfhasuh/emo_master/actions/runs/44526752371/job/135569301229)

未执行 Windows 原生 Qt 人工走查、冻结 EXE、现场相机／PLC 或工位精度验收。
