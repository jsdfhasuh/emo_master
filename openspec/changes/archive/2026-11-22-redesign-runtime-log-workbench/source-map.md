# 源码位置与复用依据 · V2.4 完整整合计划

## 1. 当前基线与使用方式

当前实现基线为 [`fa4b72d751189c2689882a0a0d127fd280a35fde`](https://github.com/jsdfhasuh/emo_master/tree/fa4b72d751189c2689882a0a0d127fd280a35fde)，分支 `codex/runtime-log-workbench`。本文按该提交已有实现定位，**不沿用早期 `9d59fd1` 时“工作台尚未实现”的描述**。

当前已有原生 Qt 工作台、运行目录与历史查询、有界事件窗口、真实执行结构、易读详情、设备诊断往返和独立算子调试接线。原实施事实保留在 [implementation-report.md](implementation-report.md)；该报告记录的是既有实现及当时验证，不代表本轮 7.x 改进已完成。

本包只有一个 change：`redesign-runtime-log-workbench`。完整目标、任务进度和验收分别见 [design.md](design.md)、[tasks.md](tasks.md)、[acceptance.md](acceptance.md)。源码表中“7.x”指这次待完成的界面与语义改进，具体勾选项以任务清单为准。按函数名核对当前代码，行号只作固定提交的辅助证据，不机械应用旧补丁。

## 2. 完整日志工作台的现有实现

### 2.1 来源、任务目录与入口

| 源码位置 | fa4b72d7 已有能力 | 7.x 的复用与修改边界 |
| --- | --- | --- |
| [`apps/designer/ui/log_dialog.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/ui/log_dialog.py) | `StructuredLogEntry` 保留执行身份、原始记录和来源；`isRuntime()`、`effectiveJobId()`、`isRuntimeSystem()` 区分 Runtime/Designer 及真实 Job 归属。`LogDialog` 已接入任务页、系统事件与高级历史查询。 | 保留两个一级来源，不能因 Designer 记录携带 Job ID 而改来源。仅完善已知框架默认消息和日期条件显示，不改原始事件与导出内容。 |
| [`apps/designer/ui/runtime_job_logs.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/ui/runtime_job_logs.py) | `RuntimeJobLogs` 已有目录、任务详情、设备页；`_refreshCatalog()` 读取 `ListRuns` 与最近历史，`_readCatalogHistory()` 读取更早目录。首页 recent 当前请求 `limit=20`。 | `_renderCatalog()` 从以 active 分组改为按 status 分组；将 current terminal-active 与普通 recent 合并去重，实时对象优先。保留现有分页和选择，不增加任务账本。 |
| [`apps/designer/ui/main_window.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/ui/main_window.py) | 正常日志入口已创建工作台；全局事件分发、Runtime 切换和日志显示已经接入。 | 继续复用入口与事件交付，不为关联任务增加一个独立日志窗口服务或第二个持续 reader。 |
| [`apps/designer/services/local_log.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/services/local_log.py) | Designer 当前会话与本机文件日志来源。 | 保持本机来源和既有文件读取语义，时间输入共用适配时不改其归属。 |
| [`apps/runtime/jobs/read_model.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/jobs/read_model.py) | `runState()`、`currentRuns()`、`ListRuns`、`GetRunState`、`ListJobHistory` 和能力声明均已存在。历史 RPC 从 SQLite 读取，默认页大小 50；不是当前进程内存列表。 | 本轮用已有 status、active、组/来源字段与 allowed_actions 解释目录，不改变 Runtime 生命周期或新增 RPC。 |
| [`apps/designer/services/runtime_client.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/services/runtime_client.py) | `listRuns()`、`listJobHistory()`、`getRunState()`、`queryRuntimeLogs()` 及设备查询已有包装。另有 `dispatchQueueState()`。 | 关联数量使用现有 ListRuns 快照；本轮不强制新增队列轮询。日期控件转换为原请求字段，不扩协议。 |

### 2.2 持久查询、有限窗口与实时接续

| 源码位置 | fa4b72d7 已有能力 | 本轮应保留的合同 |
| --- | --- | --- |
| [`apps/runtime/context/sqlite_store.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/context/sqlite_store.py) | `queryJobHistory()` 在现有 jobs 表执行终态 keyset 分页；`queryRuntimeLogs()` 已支持前向、`latestFirst`、`beforeEventId`、固定 upper、真实 Job 及带 payload Job 归属的系统分区事件。 | 不新增表或全量扫描。目录时间使用 endAt，事件时间使用 timestampMs；两者均起点包含、终点不包含。历史生产者实例与当前查询目标实例不是同一概念。 |
| [`apps/runtime/grpc_server/service.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/grpc_server/service.py) 与 [`proto/runtime.proto`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/proto/runtime.proto) | 日志正反向查询、字节/行数限制、运行读模型和设备诊断 RPC 已接入。 | 游标以原始返回页和服务端 next/hasMore 为准。界面本地筛选后为空，不代表历史耗尽。7.x 没有新增 proto 需求。 |
| [`apps/designer/services/global_logs.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/services/global_logs.py) | `RuntimeGlobalLogs._read()` 依次排入事件和 progress；`take(includeProgress=True)` 支持上层按交付顺序处理。已有断线通知和受控重连。 | 使用原 reader 及交付水位；有限历史页查询和缺口补取不另建常驻订阅。保留既有行为，不为新提示追加重试层。 |
| [`apps/designer/state/job_log.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/state/job_log.py) | `JobEventWindow` 按有效 Job 和 eventId 去重，窗口上限 10000；暂停时计新增而不持续塞入载荷，向前合并保护选中范围。 | 新关联导航只存轻量书签，不为每个任务持有一个长期事件窗口。返回按现有查询重建可用范围。 |
| `runtime_job_logs.py::_openLatest()`、`_requestLatest()`、`_fillGap()`、`_readEarlier()`、`appendEntry()` | 已实现最近页、固定上界、有限实时补齐、更早页、阅读暂停和返回最新。 | 日期和文案调整不改变这些读模型；关联返回复用 eventId、结构键和查询上界，不重写窗口算法。 |

### 2.3 执行结构、循环、连续运行和时间

| 源码位置 | fa4b72d7 已有能力 | 7.x 的复用与修改边界 |
| --- | --- | --- |
| `state/job_log.py::ExecutionTree`、`ExecutionBranch` | 用 workflowRunId、parentWorkflowRunId、nodeRunId、iterationPath 建树；保留真实分支，投影筛选保留祖先；有代表事件和上下文定位能力。 | 精简主行的完整 UUID 和重复文字，原始键不改。名称相同不能合并调用，缺字段不能按日志顺序猜父子关系。 |
| [`apps/runtime/workflow/loop_runner.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/workflow/loop_runner.py) | 现有 While 路径、ForEach、Repeat 事件已有 mode、可知 totalIterations，以及 While 迭代 bodyExecuted 的真/假证据。 | 本轮只使阅读更简洁。false 条件检查不计为执行循环体；旧记录缺省仍是未记录，不反查工程推算。 |
| [`apps/runtime/workflow/context.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/workflow/context.py) 与 [`workflow/subflow_runner.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/workflow/subflow_runner.py) | 普通子工作流创建新的调用 ID，保留同一 Job；同步调用返回后才发布子工作流完成。 | 普通嵌套调用留在该任务执行树中，不变成首页“关联任务”。详见第 3 节。 |
| [`apps/runtime/workflow/runner.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/workflow/runner.py) 与 [`jobs/worker_main.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/jobs/worker_main.py) | 工作流和节点生命周期事件已存在；`_runContinuous()` 在同一 Job 内为每次根调用创建新 RunContext。 | 连续根调用与循环轮次均不是独立 Job。没有全量历史时，不把可见位置伪装成全局运行次数。 |
| `state/job_log.py::jobExecutionTime()`、`ExecutionTime`、`ExecutionBranch.executionTime()` | 已区分实际开始、终态固定耗时、运行中经过时间、不完整配对和重启确认时间。 | 短标签只改呈现；详细原因留在详情/提示。terminal-active 的主 Job 不继续计时，下游 Job 用自己的时间。 |
| `runtime_job_logs.py::_render()`、`_updateTimes()`、`_eventSelected()` | 树/时间视图、选中恢复、抽屉、运行时间和当前位置已有接线。 | 移除“下游资源仍在收尾”统一解释，按真实关联或未知原因分别显示；保留定位所需的连续性证据。 |

### 2.4 算子易读详情、独立调试与预览边界

| 源码位置 | fa4b72d7 已有能力 | 7.x 的复用与修改边界 |
| --- | --- | --- |
| [`apps/designer/ui/log_record_detail.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/ui/log_record_detail.py) | `LogRecordDetail` 已有易读/原始页、值展开与复制、同形对象数组表格；`_eventPayload()` 读取生命周期、ioSummary、metrics、diagnostics 和主动日志 data。 | 只完善已知状态与框架默认文案的中文和层次，保留未知/用户文本；不为每个算子建立专属渲染器。 |
| [`apps/designer/ui/log_json_tree.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/ui/log_json_tree.py) | 原始记录、对象/数组惰性展开、长文本与复制的共用能力。 | 易读页面继续共享真实值，false、0、null、空集合和缺字段不能互换，不改复制的原始记录。 |
| [`core/contracts/run_inspection.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/core/contracts/run_inspection.py) 与 [`apps/runtime/events/jsonl_writer.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/events/jsonl_writer.py) | 端口摘要的类型/尺寸/计数、截断和不可用原因已有合同；查询投影保留摘要、指标和诊断，并不保证携带完整 outputs。 | 不从摘要恢复完整图像、数组或任意旧轮结果；原始页是当前实际收到的记录。 |
| [`apps/runtime/execution/operator_executor.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/execution/operator_executor.py) 与 [`events/operator_logger.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/events/operator_logger.py) | 正式普通算子执行注入 logger 与调用上下文；主动日志进入实际 node.log.payload.data。框架节点完成记录不依赖算子主动写日志。 | 复用已收到的结果摘要与主动数据，不为提升“覆盖率”批量添加重复日志，也不重定义 logging 协议。 |
| [`apps/designer/ui/operator_debug_window.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/ui/operator_debug_window.py) | 已从普通 str(event) 列表升级为共享易读详情；`showLog()`、`matchesResult()`、`openLogResult()` 按 session/generation/executionId 对应真实结果，保留 PLC 与输出端口视图。 | 共享显示改进覆盖此入口即可，不重做调试器，不增加正式 Job，不把另一次结果显示成所选日志的结果。 |
| [`apps/designer/services/operator_debug.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/designer/services/operator_debug.py)、[`runtime/operator_debug/worker.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/operator_debug/worker.py)、[`runtime/operator_debug/manager.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/operator_debug/manager.py) | 独立事件 wrapper 和结果读取已存在，调试 worker 直接执行算子，不经过 WorkflowRunner。 | 保留独立会话身份；不能虚构 node.completed/ioSummary，也不能将单算子调试并入生产 Job 历史。 |
| [`apps/runtime/preview/executor.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/preview/executor.py)、[`preview/live.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/preview/live.py) | 预览失败使用既有消息或缓冲日志；实时预览失败事件可带 payload.logs。 | 继续展示实际收到的失败信息，不新增成功预览持久日志管线。 |

节点结果检查与日志有不同数据范围：`apps/designer/state/run_result_history.py`、`services/inspection_images.py`、`ui/node_result_panel.py` 继续服务既有有限结果/图片读取；不因为日志中有旧轮执行 ID 就承诺任意历史图片可取。历史算子盘点见随包 `operator-coverage.md`，其静态盘点不能替代当前产品的逐设备运行验证。

### 2.5 设备诊断与导出

| 源码位置 | fa4b72d7 已有能力 | 本轮应保留的合同 |
| --- | --- | --- |
| `execution/operator_executor.py` 的设备执行上下文与 [`apps/runtime/devices/diagnostics.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/devices/diagnostics.py) | 采图/设备操作传递可用执行身份；`DeviceDiagnostics.append()` 已集中提取 source 中的调用、父调用、节点及轮次，保留可信顶层身份。 | 不再次给各驱动加同一套兜底。设备同类问题聚合不等于本 Job 这一轮的统计。 |
| [`apps/runtime/devices/manager.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/devices/manager.py) 与 `diagnostics.py::query()` | Runtime、工程加载范围、设备和现有有界近期诊断缓存。 | 诊断不是第三个永久日志数据库；历史任务不自动拥有当前设备缓存。 |
| [`ui/communication_panel.py`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/ui/communication_panel.py)、`runtime_job_logs.py::_openDevice()`、`apps/designer/ui/device_workspace.py` | 设备诊断下钻、通道选择、HEX/UTF-8、缓存导出与返回已有实现。 | 保留原范围检查、选择和返回路径；7.x 布局改动不丢设备入口。 |
| `log_dialog.py::StructuredLogView` 的历史/导出路径与 `RuntimeJobLogs` 导出信号 | 完整 Job 导出已经走独立固定 Job 范围和现有前向分页/取消机制。 | 本地日期、WARN、节点条件不侵入完整 Job 导出；高级历史的“完整选定范围”仍使用其已提交条件。无需新导出器。 |

## 3. “子工作流”与“关联任务”的源码判定

### 普通子工作流是同一 Job 内的同步调用

`RunContext.childWorkflow()` 保留 `jobId=self.jobId`，生成新的 `workflowRunId`，并记录 `parentWorkflowRunId=self.workflowRunId`。`SubflowRunner.run()` 第 63–72 行同步调用子工作流，返回后才发布 `subflow.completed`。`worker_main.runJobProcess()` 在 `runCycle()` 返回后才发 `job.completed`。

所以正常执行下，不能解释为“普通子工作流还在执行，父 Job 已经成功完成”。这些子调用和循环轮次显示在同一任务的执行结构里，不增加首页任务数。

### 派发产生的是独立下游 Job

[`jobs/supervisor.py::_startRunGroup()`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/jobs/supervisor.py) 为 slot 生成新 Job ID、建立 `JobRecord`，记录运行组和来源 Job，并分别启动下游任务。

[`frame_dispatch/runner.py::DispatchRunner.run()`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/frame_dispatch/runner.py) 的 Dispatch 分支取得接收结果后返回；Join 分支则等待目标结果。未等待的下游因此可能在源任务完成后继续处理。是否等待取决于真实流程，不因出现“子流程”名称就一概推断。

[`frame_dispatch/scheduler.py::RunGroup.next()`](https://github.com/jsdfhasuh/emo_master/blob/fa4b72d751189c2689882a0a0d127fd280a35fde/src/emo_master/apps/runtime/frame_dispatch/scheduler.py) 在源已结束且组内没有待处理/正在执行记录时才关闭消费者。`read_model.runState()` 同时保留组成员活动与所有权信息。

### active 不能单独证明有子任务，更不能证明设备正在关闭

`runState()` 使用 `active = not record.isTerminal or owned or groupActive`。`JobSupervisor._ownedJobs()` 包含 `_handles`、`_bridges`、`_finalizations`、`_recoveries`、`_retirements`。因此无下游 Job 时，也可能仍存在进程、事件桥或结束处理记录。

7.x 只在同 Runtime、工程、非空运行组及明确来源 Job 关系匹配时，展示实际未终态成员数量和入口。无法确认具体关联项时显示“结束处理未完成”，不推测设备动作或自动推进。`RunState` 没有更具体原因字段，本轮不为文案增加新字段。

## 4. 7.x 待改位置总表

| 待完成目标 | 优先修改点 | 保持不变的部分 |
| --- | --- | --- |
| 状态分组、终态 active 保留、唯一目录行 | `RuntimeJobLogs._refreshCatalog()`、`_renderCatalog()` | 当前 ListRuns / history RPC、原分页与 Runtime 切换边界 |
| 真实关联说明、合法停止、终态时长 | `_updateTimes()`、`_catalogActions()`、`_stopSelectedJob()` | allowed_actions、操作前身份检查、源 Job 的既有停止组行为、jobExecutionTime |
| 关联查看与返回 | `openJob()`、`home()`、已有选中/定位字段 | 一个轻量来源书签；单个有界窗口；不可查锚点如实提示 |
| 可空日期时间、毫秒与范围 | `_buildCatalog()`、`_buildDetail()`、`_scopePredicate()`、`_advancedHistory()`；`StructuredLogView` 时间条件读取、回填和提交 | 本机时区、开始含/结束不含、目录与事件字段区别、既有导出范围 |
| 常用/技术筛选分层 | 原高级条件控件、名称映射、已加载轮次选项 | 精确 ID、原始路径、折叠不清值、固定任务范围；不新增目录全量查询 |
| 树主行与共享详情更易读 | ExecutionBranch 标题/显示适配、StructuredLogEntry.displayMessage、LogRecordDetail | 原始执行键、未知/用户消息、原始 JSON/复制/导出和共享调试组件 |
| 原生 Qt 布局与证据 | 原页头、筛选容器、树/详情 QSplitter、已有 Q1/Q2 | Python 3.10、PySide2 5.15.2.1 / Qt Widgets；无 WebView/QML/Qt6，无生产模拟工具条 |

## 5. 现有验证与本轮落点

| 原有验证位置 | 已有职责 | 7.x 最小补充 |
| --- | --- | --- |
| `tests/designer/test_job_log.py` | 有界窗口、执行结构、配对时间与当前位置等模型/接线 | 目录按状态合并、真实关联与直接反例；已有时间/结构断言继续复用 |
| `tests/designer/test_runtime_job_workbench.py` | Q1：实际 Qt 与软件 Runtime 的持续任务、告警、摘要、设备往返和单算子调试 | 在既有流程核对新首页说明、关联返回、筛选折叠与阅读布局，不新建完整第三流程 |
| `tests/designer/test_operational_log_integration.py` | Q2：重启、历史范围查询、来源隔离与完整 Job 导出 | 日期控件接线、有效范围返回及完整导出不继承本地日期条件 |
| `tests/designer/test_log_dialog.py`、`test_log_json_tree.py`、`test_log_export.py`、`test_operator_debug_window.py` | 筛选、真实值/复制、原始详情、导出与调试身份 | 优先补当前受影响入口；共享展示验证一次，不逐算子复制 |
| `tests/runtime/test_runtime_persistence_contract.py`、`test_run_catalog.py`、`test_workflow_loop_contracts.py`、`test_workflow_loop_contracts_v2.py`、`test_operator_logging.py`、设备相关既有测试 | 既有查询、循环、日志和诊断合同 | 保留原必要门禁；没有改变后端合同时，不把本轮展示改进扩成后台故障矩阵 |
| `scripts/gen_proto.py`、`scripts/build_panel_contract.py` 与仓库静态检查 | 现有协议/镜像生成及质量检查 | 保留仓库要求。本轮不新增协议；发现确实修改到契约时才按既有脚本生成与验证 |

验证命令、执行环境、实际 HEAD 与未提交修改应记入本轮实施记录。原报告中 Windows Qt、软件 Runtime 和测试数量是旧实施者的记录，不是本轮重新执行的结果。原型、静态布局图和旧 Qt 截图分别标识，不能代替修改后的实际界面与行为证据。
