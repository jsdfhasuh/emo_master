# 独立 Runtime 起始页、本机目录设置与日志验证

日期：2026-10-25（Asia/Shanghai）。

状态：P1–P4 已实施。初次交付为 **115 passed，241.64 s** 及原生 Windows QWidget 100% / 150% 检查通过；同日复审的两项局部缺陷已修复，收尾扩大回归为 **119 passed，240.78 s**，两种缩放下原生定点回归各 **5 passed**。复审首失败和修复证据见第 6 节，初次交付记录不覆盖。本报告不代表冻结包、设备或生产验收。

## 1. 源码与范围

- 实际 HEAD：`9265547dbe61bc57542d3cf0ea143fcf455600fb`。
- 分支：`agent/runtime-workflow-architecture-v1`。
- 工作区：`C:/Users/jsdfhasuh/my_scripts/emo_master`，解析后为 `D:/jsdfhasuh/documents/my_project/emo_master`（同一 checkout）。
- 本报告针对该 HEAD **加本轮未提交源码**，不能把实现归属为 HEAD 已有代码。
- 本轮开始时已有 Designer 日志/名称、Dispatch 队列、RTSP、资源等未提交修改；这些修改保留，未回退、合并、暂存、提交或推送。本次未连接真实摄像头/PLC，也未读取现场工程或凭据。
- 依据：[实施计划](../plans/2026-10-25-runtime-startup-settings-and-logs-plan.md)。未增加工程字段、设备操作、资源搬迁、日志数据库或后台常驻机制。

### 本轮代码

| 文件（仓库相对路径） | 本轮作用 |
| --- | --- |
| `src/emo_master/apps/operator_runtime/settings.py` | 无 Qt 四级目录解析、绝对设置路径校验、当前实例目录投影 |
| `src/emo_master/apps/operator_runtime/startup_page.py` | 可滚动响应式起始页、本机下次默认设置、保存/覆盖诊断、失败重试入口 |
| `src/emo_master/apps/operator_runtime/log_reader.py` | 有界只读 JSONL 尾读/增量解析、半行、偏移身份、解析计数与局部脱敏 |
| `src/emo_master/apps/operator_runtime/log_view.py` | 共用单在途后台读取、约 1000 ms 可见刷新、级别/文件选择、详情/原始记录和目录打开 |
| `src/emo_master/apps/operator_runtime/main.py` | 工具堆叠页、业务页/会话保留、焦点返回、启动诊断、初始化失败/显式重试、`--check` 同规则 |
| `src/emo_master/apps/operator_runtime/controller.py` | 复用无 Qt 默认解析；给正常初始化失败保留目标、阶段与底层原因 |
| `src/emo_master/apps/runtime/events/jsonl_writer.py` | 仅允许四个明确独立端启动事件；不变更轮转/保留或 Job 表 |
| `src/emo_master/apps/runtime/grpc_server/service.py` | 仅补齐早期缓存初始化失败时日志线程、维护线程和数据锁的局部清理；该文件原有其他未提交差异不是本轮新增 |

新增测试为 `tests/runtime/test_operator_runtime_settings.py`、`tests/runtime/test_operator_runtime_logs.py`、`tests/ui/operator_view/test_startup_settings.py`、`tests/ui/operator_view/test_runtime_log_view.py`。原生窗口探针为 `tests/ui/operator_view/native_runtime_tools_probe.py`，不参与普通 pytest 自动收集。原有窗口测试仅更新旧“按钮集合完全等于旧工具栏”断言，保留选择、失败禁止开始、设备入口与可关闭等语义，并补验证日志入口。

## 2. 实现边界核对

### 目录

- 优先级：显式 `--data-root` / `OperatorWindow(dataRoot=...)` > `EMO_RUNTIME_DATA_DIR` > `QSettings("EmoMaster", "Runtime")` 的 `storage/defaultDataRoot` > 平台默认。
- 本机只保存绝对目录；CLI 相对目录仍从启动 CWD 解析，包含 checkout junction 的实际路径归一化。
- 运行中保存不改变当前 DB、workspace、writer、Job 或工程。清除本机覆盖后才恢复系统默认；存在 CLI/环境覆盖时不误称当前已生效。
- `QSettings.sync()` 后检查状态；失败不报成功，并恢复本进程中的旧待保存值，避免另一个窗口误用未确认设置。
- `--check` 使用同一目录规则计算受保护路径，不建立 Runtime、数据库、日志目录或 Job，不改本机设置。
- UNC 测试只验证语法，mock 路径探测，不访问真实共享；**不是网络共享连通性或 SQLite 在真实共享上的验收**。

### 页面/启动

- 工具页只切换堆叠区域，不调用 `_removePages()` / `_closeSession()`；连续和双流程测试核对 Job ID、页面、hub、DisplaySession 对象身份不变。
- 顶部真实运行状态、开始/停止入口保留；设置/日志不依赖 `canLoad`。无工程不新增 Job，原有记忆/在线恢复、重新加载、自动开始以及关闭中不开始继续回归。
- 初始化失败仍可设置、查看日志、关闭、外部修正后显式重试同一目标。普通根/DB/显示缓存/preview-cache 文件目录冲突均测试；不自动切到新保存目录或静默退回其他根。

### 日志

- 只读取当前真实 `runtime-*.jsonl`，历史有明确标记；文件选择和来源在同一 feed 中维护，writer 首次就绪不会覆盖用户已选择的历史。
- 初读最多 2 MiB，并在 JSON 解码**之前**裁到最多 2000 行；每轮最多 256 KiB / 2000 行（无效行也计入预算），视图最多 10000 条。没有读取整个文件或先解析所有小行再裁剪。
- 只有一个读取线程在途；起始页与日志页共用状态，业务页或窗口隐藏时不发新读，关闭等待已发出的有界读取收尾。
- 半行、重复消息、正常追加/轮转、手选历史、读取失败后刷新、选择变化丢弃旧响应均有测试。文件与字节偏移保留真实重复事件，不按消息去重。
- 表格、详情、原始记录与错误均为纯文本；JSON 敏感键、URL 用户信息、常见 Bearer/Basic 与带引号密码/token 文本在视图/新增启动事件中脱敏，不改写原日志文件。
- 仅新增 `operator.storage.resolved`、`operator.runtime.ready`、`operator.runtime.failed`、`operator.project.load.failed` 四个允许项。启动诊断不伪造 Job，也不进入 Job 生命周期表。
- writer 不可用时展示实际 `enabled` / `failureMessage`；本窗口最多 64 条诊断，区分“未写入/未确认”与“已提交 writer，落盘以文件为准”。
- 打开目录使用实际当前 logs 与 `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`。自动化拦截系统打开调用，验证路径及失败/成功反馈；**没有把未实际打开 Explorer 写成系统集成验收通过**。

## 3. 首轮失败与修复记录（不由后续绿色结果覆盖）

| 轮次 | 首次结果 | 分类、原因与处理 |
| --- | --- | --- |
| 原有独立窗口 + JSONL | 21 passed / 1 failed | 旧夹具：`testInvalidRememberedProjectStillAllowsSelectionAndClosing` 将按钮集合写死为旧工具栏。更新为明确保留原操作及验证新入口，没有删除失败/关闭断言。 |
| 新增设置/reader/UI 用例首轮 | 32 passed / 4 failed | **产品问题 1**：`preview-cache` 正常路径冲突后，构造期遗留 writer/维护线程和数据锁；补早期清理。测试问题 3：相对 CWD 期望未解析 C→D junction；两个测试用了当前 PySide2 没有的 `QTest.qWait`。分别修正期望及改为 Qt 事件循环等待。 |
| 上述修复后组合复跑 | 58 passed | 原有 22 与当时新增 36 项；不是全仓测试。 |
| 扩大源码回归首轮 | 110 passed / 1 failed，263.69 s | 新增测试问题：双流程测试在未切到第二页时要求其图像已到达。现有 DisplaySession 的可见需求语义不保证隐藏页图像；改为依次访问两页再核对身份，不修改产品显示订阅策略。首轮输出保留为 `manual_test_workspace/runtime_startup_settings/offscreen-regression.txt`。 |
| 双流程测试修正后 | 20 passed | 当时设置与日志 UI 全部测试。 |
| 原生探针最初 100% / 150% | 两轮 PASS | 覆盖当时八类自动化检查及截图，但不能据此声称“返回后恢复编辑焦点”已验证；当时未断言这一点。 |
| 加强原生 150% 焦点断言 | FAIL：返回后 `panel.nextEdit.hasFocus()` 为 false | **产品问题 2**：Qt 堆叠页默认没有恢复编辑框焦点。局部记录每页最后焦点，在返回时恢复，不重建页面。该失败日志保留为 `manual_test_workspace/runtime_startup_settings/native-150-refined.txt`；同时修正探针等待展开布局后再滚动的时序，确保输入框真实位于 viewport 内。 |
| 焦点修复后原生复跑 | 100% / 150% 均 PASS | 结果位于 `native-100-final` / `native-150-final`，包括恢复输入焦点断言。后续 reader 小行预算与脱敏补充另有最终源码复核。 |
| 初始加载期间打开日志的直接反例 | 1 failed，2.76 s | **产品问题 3**：加载完成时保留了日志页，但返回链只有起始页，无法返回新业务页。给现有返回链补上业务入口，不关闭工具、不重建业务页。首失败输出保留为 `startup-navigation-first.txt`，针对该普通异步路径补回归。 |
| 最后补充与交付复跑 | 114 passed，240.52 s；最终 115 passed，241.64 s | 114 项覆盖 reader 小行/无效行预算、文本授权信息脱敏与 writer 就绪保留历史；加入启动期导航直接反例后，从最终源码完整复跑 115 项。原生 150% 另跑导航/下次目录两个直接用例，2 passed，5.50 s。 |

测试中的文件/目录冲突和慢读注入用于验证计划声明的普通初始化失败、单在途读取和退出；不据此扩展成断电、多故障或任意历史混用验收。初次实施发现的三项适用产品问题已经局部修复；没有据想象新增全局锁、后台重试或迁移框架。同日复审另发现的两项缺陷及收尾见第 6 节。

## 4. 环境、命令及证据

- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，3.10.21。
- Qt：项目 PySide2，offscreen 回归与 `QT_QPA_PLATFORM=windows` 的原生 QWidget/QTest 探针分开运行。
- 所有窗口使用临时根、独立 INI 格式 QSettings，工程由现有 `currentProductionProject` 使用 `WorkflowStore().toPayload()` 创建完整 2.7，校验、保存重读并编译；双流程补充同样重新校验/保存重读/编译。
- 原生窗口为 1060×800、900×700 逻辑像素；进程级 `QT_SCALE_FACTOR=1` / `1.5`，不更改 Windows 系统缩放设置。原生真实 QWidget 像素截图已经检查；小窗口上下布局使用滚动区域，展开长路径后可滚到保存操作，不遮挡顶部停止/返回。
- 这是**原生 Qt 自动化 + 截图像素检查**，不是用户人工验收、冻结 EXE 或真实显示器 DPI 切换验收；没有把网页预览当作 Runtime 证据。

完整扩大回归命令（PowerShell，仓库根目录）：

```powershell
$python = "$env:USERPROFILE/.conda/envs/emo_master/python.exe"
$env:QT_QPA_PLATFORM = 'offscreen'
$productionTests = (Get-ChildItem tests/runtime/test_production*.py).FullName
& $python -X utf8 -u -m pytest tests/ui/operator_view `
  tests/runtime/test_jsonl_logging.py `
  tests/runtime/test_operator_runtime_settings.py `
  tests/runtime/test_operator_runtime_logs.py @productionTests -q --tb=short
```

原生探针命令（分别在隔离进程运行，`$scale` 为 `1` 或 `1.5`）：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = $scale
$env:PYTHONPATH = 'src;.'
& $python -X utf8 -u -m tests.ui.operator_view.native_runtime_tools_probe `
  --output "manual_test_workspace/runtime_startup_settings/native-$scale"
```

本地截图/逐轮输出位于 `manual_test_workspace/runtime_startup_settings/`（仓库已忽略目录，没有写入现场工程）；测试代码和本文为可追踪的复现依据。首轮文件不覆盖，最终结果目录另列。静态检查仅声称本轮文件的 Ruff / 语法编译及 diff whitespace 检查，不声称全仓 mypy 或完整测试套件通过。

## 5. 初次交付结果与未执行项

初次交付扩大回归（包括最后启动期工具返回修复）为 **115 passed，0 failed，0 skipped，241.64 s**：其中本轮新增 42 项、原有相关回归 73 项。范围是上述命令的独立窗口、基本反馈、并行/五会话、生产配置/边界、原 JSONL 及新增设置/日志测试，**不是全仓测试**。

最终输出：`manual_test_workspace/runtime_startup_settings/offscreen-delivery.txt`。上一轮 114 项输出为 `offscreen-final-current.txt`，没有覆盖首次失败输出。静态复核：本轮源码/测试 Ruff 通过，语法编译通过，`git diff --check` 未报告空白错误（只提示仓库现有 LF→CRLF 转换）。最后复核 HEAD 仍为 `9265547dbe61bc57542d3cf0ea143fcf455600fb`。

原生最终 reader/来源补充后：`native-100-current/result.json`、`native-150-current/result.json` 均为 `PASS`，各 8 类检查、8 张真实 QWidget 截图；已查看默认/窄窗口、长路径、错误详情及运行中停止入口等像素。最后的启动期返回链局部修复另有 `native-late-navigation.txt` 的 2 passed，覆盖 150% 原生窗口中的初次加载日志返回和本机目录下次生效。

示例本地截图（临时数据根、纯图片工程，不是现场工程）：

- 起始页：`manual_test_workspace/runtime_startup_settings/native-100-current/home-1060x800.png`。
- 窄窗口：`manual_test_workspace/runtime_startup_settings/native-150-current/home-900x700.png`。
- 长路径/保存入口：`manual_test_workspace/runtime_startup_settings/native-150-current/settings-expanded-long-path.png`。
- 错误详情：`manual_test_workspace/runtime_startup_settings/native-150-current/logs-error-details.png`。
- 运行中查看及停止入口：`manual_test_workspace/runtime_startup_settings/native-150-current/running-logs-stop-visible.png`。

| 层次 | 结论 |
| --- | --- |
| 新增普通路径单元/Qt 源码回归 | PASS；最终扩大相关回归 115 passed（新增 42 / 原有 73），无失败/跳过 |
| 原生 Qt 100% / 150% | PASS；各 8 类探针与真实截图，另有最后导航/下次目录 2 项原生回归 |
| 全仓测试、全仓 mypy | NOT_RUN；本轮只执行相关回归与本轮文件静态检查 |
| 实际 Explorer 打开 | NOT_RUN；路径与系统接口结果由拦截验证，不实际打开用户文件管理器 |
| 真实 UNC / SMB / 共享库读写 | NOT_RUN；只有隔离语法校验 |
| 冻结构建、自测、旧 EXE 升级 | NOT_RUN，本轮未进入打包仓、未构建或发布 |
| 真实相机/PLC、模型业务准确性、生产节拍/现场安装 | NOT_RUN；纯图片/模拟测试不是设备验收 |
| Git 提交、推送、PR | NOT_RUN；需另行授权 |

使用新源码需重启该源码 Runtime 进程；现有 EXE 不会热更新，必须另行重建才能包含本轮实现。本机默认目录只有下一次启动才重新解析；这与代码更新后需要重启是两件不同的事。原数据根/在线版本/日志没有自动迁移。

## 6. 复审修复与收尾验证（2026-10-25）

用户要求审查计划完成情况后，复跑当时新增的 42 项测试得到 **42 passed，22.09 s**，但补充反例确认了两个原测试未覆盖的 P2 缺陷。随后按用户要求进行局部修复。实际 HEAD 仍为 `9265547dbe61bc57542d3cf0ea143fcf455600fb`，以下结果针对该 HEAD 加当前未提交源码，不归属于该提交。

### 缺陷与修改

| 缺陷 | 真实后果与局部修改 |
| --- | --- |
| 设置保存失败后不能在同一窗口恢复 | `QSettings.status()` 保留对象生命周期内首次错误，导致修正写入位置后仍误报失败并恢复旧值。`startup_page.py` 每次保存通过相同存储位置、相同格式的新 `QSettings` 句柄同步并检查结果，保留失败时旧待写值恢复。无需重启窗口，仍只修改下次默认，不改当前 Runtime 或其他偏好键。 |
| 日志达到 10000 条后阅读位置漂移 | 头部记录被淘汰后，同一个滚动数值对应了另一条日志。`log_view.py` 改为保留首个可见记录的文件/字节偏移身份及行内像素偏移；记录仍保留时恢复到它，已被淘汰时显示最早仍保留的记录。选中记录/详情不变，跟随最新仍滚到底部，不改变读取和保留上限。 |

新增 4 个参数化回归案例：保存新目录和清除本机覆盖各 1 项；逐行/像素滚动各 1 项。原保存失败测试改用真实隔离 INI 的写入失败，保留全部失败反馈断言，并验证失败值不泄漏。新增用例还验证修正后连续保存、原 `projectPath` 保留、首条可见记录被淘汰时的正常出口和重新跟随最新。只修改上述两个产品模块、两个测试文件及计划/本报告，没有改动其他工作区内容。

### 结果与证据

以下输出均新增到 `manual_test_workspace/runtime_startup_settings/`，没有覆盖前轮记录。

| 轮次 | 结果 | 证据文件 |
| --- | --- | --- |
| 新回归运行于修复前实现 | **4 failed，1 passed，21 deselected，4.51 s**；两种保存操作均在修正后重试失败，两个滚动模式均从第 3500 条跳到第 3700 条 | `review-fixes-first.txt` |
| 局部修复后定点回归 | **5 passed，21 deselected，2.26 s** | `review-fixes-targeted.txt` |
| 同原交付范围的完整相关回归 | **119 passed，0 failed，0 skipped，240.78 s**；原 115 项加本次 4 项，不是全仓测试 | `review-fixes-regression.txt` |
| 原生 Windows Qt，100% 进程缩放 | **5 passed，21 deselected，2.90 s** | `review-fixes-native-100.txt` |
| 原生 Windows Qt，150% 进程缩放 | **5 passed，21 deselected，2.86 s** | `review-fixes-native-150.txt` |

定点命令如下；原生验证使用相同命令，将 `QT_QPA_PLATFORM` 改为 `windows`，并分别设置 `QT_SCALE_FACTOR=1` / `1.5`：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
& "$env:USERPROFILE/.conda/envs/emo_master/python.exe" -X utf8 -u -m pytest `
  tests/ui/operator_view/test_startup_settings.py `
  tests/ui/operator_view/test_runtime_log_view.py `
  -k 'PreferenceSyncFailure or ViewCapPreserves' -q --tb=short
```

扩大回归复用第 4 节命令，新增案例由原测试文件自动收集。这里的 21 deselected 是定点选择外未执行的用例，不是通过或跳过；扩大回归中全部相关用例均执行。两个产品文件和两个测试文件的 Ruff、语法编译检查通过。

本次原生结果是隔离 QWidget 自动化，不是人工 UI、完整截图探针重跑或真实显示器 DPI 切换验收。所有设置均为临时 INI，未写入用户真实应用设置；日志为合成记录，未访问真实设备或现场工程。初次交付的完整截图证据仍保留，不能将其改称本次新截图。第 5 节未执行的全仓检查、冻结包、系统集成、真实 UNC、真实设备和发布边界不变。两项复审缺陷已在本轮适用源码范围内修复并回归通过，未提交、推送或发布。
