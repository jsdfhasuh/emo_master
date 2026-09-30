# Qt 运行页面 R3 首批实施记录（§3–4）

最新诊断补充：v1已针对未归属窗口的FlowScene导致跨线程GC销毁的问题做最小修正；
v2另移除可滚动详情区多余的固定高度约束，保留内容和原断言，
修复前后复现、新回归与全套结果见 [Qt生命周期诊断](runtime-pages-r3-qt-lifetime.md)。
以下v0的SIGSEGV记录作为历史证据保留，不当作v1仍必然复现；也不因修正一处缺陷宣布计划或现场验收完成。

## 范围与基线

- 分支：`agent/runtime-workflow-architecture-v1`；基线 HEAD：`5659c6125ab2ebb1175bfdd5d559540431b2db59`。
- 实施范围仅为 2026-09-25 主计划 R3 的 §3–4，同一工程、同一明确任务、两页观看。
- 没有自动推进 §5 多图/多作用域/外观扩展、P5-B 冻结包或现场部署；没有操作设备、推送或修改 main。
- Linux 云端 Python 3.10.21 / PySide2 5.15.2.1；依赖按 requirements-dev.txt 安装。
- 当前是基线上的工作区改动；代码摘要、命令日志随交付补丁保留，不能把基线 SHA 当作修改后已测提交。

## 实际工程样本缺口

没有取得用户实际工程副本。`field_tests/global_counter/project.json` 的说明明确它是使用占位设备地址的
现场联调模板，不能视为已知现场配置，未执行其相机或 PLC 路径。新增测试使用合成离线输入、
受测内置 ImageLoader / Blob / Count / CompareNumber / Counter 及明确测试调度器。
它们证明软件路径回归，不证明用户算法、设备时序、节拍或现场许可已经验收。
实际节点/插件版本、输入规格、目标屏幕和真实停止方式仍需用实际工程副本确认。

## 用户操作与实现接点

1. 正常 Designer 主工具栏可发现“页面设计”，不用开发环境开关。
   `main_window.py` / `page_designer/coordinator.py` 复用原 PageCoordinator 和统一 ProjectEditSession；
   打开窗口不升级格式，明确启用和保存才采用 2.2，旧项目失败保存不增加修订。
2. 原“开始运行”调用原 RuntimeController / RuntimeWorker / RuntimeClient / RuntimeService.StartJob。
   有绑定时冻结采集并附加到同一 JobProcessSpec，没有第二个任务或执行器。
   `presentation/normal_capture.py` 校验已安装可信 manifest、当前绑定、声明资源和受支持作用域。
   正常模式为 `runtime`，保留原数据库、全局计数、输出文件参数和工作目录语义；隔离调试仍为 `debug`。
3. “观看当前工程任务”先协商能力，再按精确 projectId 查询目录。
   `page_designer/preview.py` 多任务时要求用户选择、默认原运行任务，保留高级地址/ID方式。
   读取/切页/第二观察者/重连/断开不会启动、重跑、停止或释放正常任务。
4. `core/presentation/coverage.py` 及共享 renderer 验证冻结来源、作用域、摘要及 Runtime/Job/执行修订。
   未采集或修改的来源显示明确原因，原来仍匹配的来源继续显示；不热改计划、不补造值。
   图像、数量和业务布尔结果保持同一 resultKey，执行状态不当成业务 OK/NG。
5. 原 Run 的请求身份、代际及 GetStartRequest 查询处理响应丢失。
   不确定启动只查询原请求，不重发新的 Start；确认终态、IPC/导出和进程资源结束后再释放额度并允许下次明确运行。
   Runtime 独占共享 PresentationService；隔离调试只收尾自己的任务及准备记录。
   客户端/窗口关闭失败保留可重试所有者，不把未退出进程标成已清理。
6. 表单尚未按“应用”的编辑先提交或阻止保存/切页/切控件；错误文本保留。
   `tools.py` / `workspace.py` / `commands.py` 保持统一保存、另存、撤销重做；取消/失败导入不旁路提交画布。
   输出树显示节点友好名，稳定 ID 留在提示中。
7. 默认 Runtime 入口复用已有有界 AioRuntimeServer，同时暴露旧 Runtime 与增量 Display RPC。
   `main.py` 保留 createRuntimeServer 的 start/stop/wait 调用方式，避免展示订阅挤占原同步控制线程池。

## 支持与拒绝边界

- 本批单结果作用域、单图像来源；两页复用同一来源。第二图像在绑定/启动前明确拒绝。
- 既定 8 MiB 原图、来源/结果/导出/会话预算继续保留；未做新的图像规格或性能承诺。
- 正常任务使用当前可信已安装算子信息，不把隔离测试的四算子白名单复制为产品能力表。
  不能由页面安装插件或增加设备许可；隔离调试的版本/资源保护保持。
- 旧未采集任务显示缺口；不支持的外部 Runtime 能力解释拒绝，不为观看创建本地设备 Runtime。
- 本批请求身份账本每 Runtime 代际最多 1024 条；满额明确拒绝新启动，不静默遗忘身份或自动重启。
  这是连续运行限制，未宣称无限次运行；未知启动或代际改变不可自动重试。
  后续要扩展账本保留策略需单独验证幂等与恢复语义。预算/故障限制不是连续现场运行批准。

## 验证记录（v0过程记录，最新结论见下）

所有命令从仓库根目录运行，环境 `PYTHONPATH=src:. QT_QPA_PLATFORM=offscreen`。
完整明细及最终代码摘要随本批交付日志保存。以下结果分开判定，不把局部通过算成总体验收。

- PASS：新合成正常 GUI 路径：原开始运行、同一 Job 图像/数值/业务判定、两页、第二观察者、断开及再次明确运行。
- PASS：50 项原控制器/客户端/worker 与合成真实运行测试；包含丢失响应查询、取消待决启动、关闭失败重试。
- PASS：27 项原主窗口布局/保存/工作流标签回归；先发现的修订推进与取消导入副作用已修复并重跑。
- PASS：14 项当前任务选择/来源覆盖测试、7 项真实运行/隔离预览所有权测试；一次 66 项页面/展示组合通过。
- PASS：最终 core/plugins/proto 分组482 PASS、1 SKIP（4.57s）；最终 runtime 全组260 PASS（81.96s）。
- PASS：protobuf 生成一致性、Ruff、mypy（279 源文件）及 git diff --check。
- 首轮非 GUI 综合：737 PASS、1 SKIP、2 FAIL。后两项是新增资源退休检查暴露的释放宿主关闭顺序和
  ABORTED 工作目录清理回归，已通过真实退休回调与 Runtime 所有者关闭次序修复；原两个用例原样重跑 2 PASS。
  随后最终 runtime 全组在桥线程退休和P5能力保护修正后为260 PASS（81.96s），日志另附。
- FAIL：完整 `python scripts/ci_check.py` 的 pytest 阶段发生原生 SIGSEGV；protobuf/Ruff/mypy 已通过。
  当前栈为 `testWorkflowPackageImportDialogPreservesEntryAndOpensImportedRoot` → `qt_wait.waitForCatalog`。
  对未修改 5659c61 单独 `git archive` 副本用相同解释器/环境跑完整 pytest，同样在同一测试/位置崩溃。
  这证明该完整组合失败在基线可复现，不证明全部新 UI 组合崩溃都有同一原因。
- FAIL / 未解释：新增 UI 四文件组合曾在正常启动期间 `QApplication.processEvents` 原生崩溃；
  后续同顺序 21 项一次通过、最小候选 pending_inputs→normal_run_viewing 顺序 7 项一次通过，
  未重现且未建立因果修复，不能清除该记录。另有后台+旧预览组合在项目切换收尾时原生崩溃。
  本批不宣布 Qt 组合稳定，不凭单次成功或分文件成功认定修复。
- NOT_RUN：用户实际工程副本验收、Windows、长时间 RSS/句柄稳态、真实设备、冻结环境及性能 SLA。

### 新增的运行中 GUI 停止反例

`testActiveNormalObserverDisconnectStopRetireAndExplicitRestart` 使用100次有界离线调度循环，
从正常GUI入口启动，先证实有结果且任务仍RUNNING，再断开观察者，确认任务仍RUNNING且任务数为1。
随后调用原GUI停止入口，首轮确实失败：`RuntimeEventStream.close` 跨线程关闭正在迭代的嵌入式生成器，
抛出 `ValueError: generator already executing`。这是一项具体功能缺陷，不能当作旧Qt原生崩溃解释。
已按消费者关闭所有权修复：取消只发送线程安全信号，执行next()的消费者负责关闭；尚未退休的流继续受追踪。
原用例重跑1 PASS（4.92s），明确覆盖观察者在任务RUNNING时断开不停止任务、原GUI停止、ABORTED、
实际资源退休、下次明确运行及强制停止。53项事件流/客户端/worker/控制器相关回归PASS，完整正常GUI测试文件2 PASS。
此具体缺陷已取得修复证据，但§4.7仍因Qt组合稳定性/实际工程缺口保持部分完成。

## 结论

已实现并在离线合成回归中验证本批主要正常运行/观看接点。功能路径、稳定性、可交付范围和现场许可
分别判断：v2最新Linux聚合检查通过，历史Qt崩溃的场景所有权缺陷和布局约束已针对性修正；
实际工程/Windows/长期稳态/性能现场证据仍缺，不能标记原需求或现场验收完成。

## 逐条审计：是否真的完成

状态口径：下表“代码已实现”只描述当前工作区；“部分完成”代表还有本条验收缺口，不能勾选计划验收完成。
所有测试均为离线合成或替身，未取得实际工程副本。本表不能用测试总数抵消未通过项。

### §4 八项

| 项 | 当前判断 | 已有代码/证据 | 尚缺 / 未解决 |
| --- | --- | --- | --- |
| 1 正常入口、复用协调器、显式升级 | 代码已实现；实际工程验收部分完成 | `main_window.py`、`PageCoordinator`；`testNormalEntryDoesNotMigrateWithoutExplicitEnable`；原保存失败修订测试原样通过 | 未在用户实际旧工程/Windows正常主程序完成交互验收 |
| 2 实例输出树、友好名、直接绑定 | 代码已实现；实际工程验收部分完成 | `EditingTools.refreshCatalog`、`outputChoices`、`PageCommands.bind`；`testNativeDragComponentVariablePropertiesAndOverlap`、同类不同节点绑定测试 | 当前单scope/单图限制明确保留；未核实用户实际全部节点/插件与调用结构 |
| 3 原明确运行创建同一Job，计数命名空间不变 | 部分完成 | `RuntimeController.startJob`、`RuntimeWorker`、`RuntimeService.StartJob`、`freezeNormalCapture`；`testOriginalStartCapturesInstalledOperatorsAndProductionCounter`；`testOriginalRunAndLostAcknowledgementShareOneJobThenExplicitlyRunAgain` | v0出现过SIGSEGV；v1修正已识别场景所有权缺陷，v2Linux聚合通过；Windows/长稳态/实际工程仍缺 |
| 4 当前工程明确选任务、外部能力协商 | 代码已实现；实际工程验收部分完成 | `PreviewController.watchCurrent/_chooseJob`；`testCurrentSelectionFiltersProjectAndShowsStatusChoice`、`testNoGlobalLastJobFallback`、`testUnsupportedRuntimeFailsBeforeEndpointOrLocalBackend` | 真实外部Runtime部署和用户多任务场景未验收；当前loopback边界保留 |
| 5 旧任务不补采集、已有结果可看、新来源明确提示 | 代码已实现；实际工程验收部分完成 | `CaptureCoverage`、`RuntimePages._resolve`；`testNewSourceDoesNotHideExistingSameJobResults`、`testChangedSourceAndScopeAreDistinctAndNeverShowStaleValues`、身份栅栏测试 | 未用真实运行工程编辑绑定后验证；原生Qt组合稳定性缺口仍适用 |
| 6 两页同结果图/数/业务判定，不重复采集 | 部分完成 | `testNormalRunTwoPagesShareImageNumberDecisionAndRestart`、`testImageCountDecisionShareOneNormalResultAndPreserveOutput`；同jobId/resultKey、第二观察者和数据库路径断言 | 合成160×120输入与同scope/同图通过；短窗口滚动另有验证，仍不能代表实际工程、现场性能或多图/多工位 |
| 7 停止、真清理、再次明确启动、未知请求核实 | 部分完成 | `GetStartRequest`、generation fence、`releaseDisplayJob`、监督器资源检查；`testExplicitStopReleaseAndRestartNormalJob`（graceful/force）、关闭失败重试测试 | 本轮发现并修复了旧释放宿主/ABORTED目录回归；桥线程退休与运行中GUI停止/断开/重启已有合成回归，v2Linux聚合通过；Windows/实际工程仍缺，1024请求账本上限保留 |
| 8 共同保存/另存/撤销/重开、未提交输入、拒绝不支持来源 | 代码已实现；实际工程验收部分完成 | `ProjectEditSession`/协调器/`EditingTools.commitPending`；`testUnifiedPagesFlowSaveReopenAndUndo`、4项pending_inputs测试、`testSecondImageRejectedAtBindingButSameSourceAcrossPagesAllowed` | 实际工程资源另存与完整交互尚缺；无效输入仍使用原协议编辑方式，§5易用性项没有实现 |

### §8 十行验收操作

| 操作 | 当前判断 | 证据与明确缺口 |
| --- | --- | --- |
| 正常主程序打开已有工程并进入页面设计 | 部分完成 | 无开关入口/不隐式升级/原配置保留的合成回归通过；“已有实际工程”未取得 |
| 友好名选择输出并拖入控件 | 部分完成 | 静态目录、原生拖放、稳定ID绑定通过；用户实际来源支持面未验收 |
| 正常运行入口同时看流程与两页 | 部分完成 | 同Job合成图/数/判定通过；已修正场景所属缺陷且v2Linux聚合通过，未做现场长期稳定性验收 |
| 切页、重连、关闭一个观察者 | 部分完成 | 新任务/停止调用计数反例、第二观察者与重连合成测试通过；连续现场多窗口未测 |
| 停止后再次启动，含状态不确定 | 部分完成 | 真实spawn停止/重启、丢失ack查询、代际栅栏通过；运行中GUI断开不停止/停止后真正退休/再运行亦通过；实际工程/Windows/长期收尾稳定性仍未测 |
| 流程编辑后保存页面、另存、重开 | 部分完成 | 原生合成保存/撤销/重开和失败保留测试通过；真实工程资源和Windows未测 |
| OK/NG、字号、空值、表格字段和详情动作易用编辑 | 未完成 | 仅本批必要的未提交输入保护与错误拒绝；§5.1类型化判定/字体/动作编辑/字段选择等未实施，不能把已有底层协议当作好用的配置界面 |
| 既有工程所需算子、两作用域、原图/结果图 | 未完成 | 普通已安装算子有合成计数/比较输出证据；仍只支持一个结果scope/一个图像来源；实际工程未知，未做多源验收 |
| 不同图像尺寸及旧/新采集策略 | 未完成 | 既有预算和旧预览策略保留，未改变重复采集策略；未测用户规格、实际成本、性能SLA |
| 原回归、Qt组合、长稳态、设备/冻结环境 | 部分完成 | v2完整Linux检查1223 PASS、真实相机烟测1 SKIP；历史崩溃与布局FAIL记录保留并有修正证据。长稳态/Windows/设备/冻结NOT_RUN，历史性能FAIL不改判 |

### 完成边界

- 不能答“计划全部完成”。§5及其依赖的后续交付范围没有执行。
- 不能答“§4已验收完成”。当前只是主要接点的实现和离线回归，仍有真实工程证据缺口，Windows与长期稳定性尚未验收。
- 没有发布、合并、部署，也没有用“测试通过数”代替逐条操作验收。


## v2最新验证结论

`PYTHONPATH=src:. QT_QPA_PLATFORM=offscreen python scripts/ci_check.py` 在修正后的Linux源码上实际完整执行：
protobuf生成一致性PASS、Ruff PASS、mypy PASS（279源文件）、pytest **1223 PASS、1 SKIP**，耗时222.06秒，
脚本退出0并打印all checks passed。唯一跳过项是显式需`HUARAY_CAMERA_SMOKE=1`才能执行的真实华睿相机烟测；本批未启用设备。

这是当前Linux软件验证结果，不是“整个计划完成”：实际用户工程副本、Windows、长期资源稳态、
真实设备、冻结环境及现场性能SLA仍NOT_RUN，§5的易用性、多图/多作用域、交付扩展仍未实施。
旧FAIL日志保留；新结论来自修正后的执行证据，未删断言、降阈值或用分组次数拼成完整通过。
