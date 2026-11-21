# 源码位置与复用依据 · V2.3 算子覆盖补齐

基线：[9d59fd16a1d0d3ec0c60725c839ff38183d6916b](https://github.com/jsdfhasuh/emo_master/tree/9d59fd16a1d0d3ec0c60725c839ff38183d6916b)

以下是本 change 的优先核查入口；行号会随实施分支变化，执行者必须按函数名重新定位。本文区分基线已有能力与 V2.3 待实施改动，不代表实现或测试已完成。82 个内置算子的盘点范围与条件见 [operator-coverage.md](operator-coverage.md)。

| 文件 | 当前能力 / 本 change 关注点 |
|---|---|
| `src/emo_master/apps/designer/ui/log_dialog.py` | 现有日志窗口、`StructuredLogEntry`、来源切换、历史查询、筛选、表格、JSON 详情、`openDeviceHistory()`；主要 UI 重构位置。`exportHistory()` 受 `_historyFilters` / `_historyReply` 影响，新增完整 Job 导出必须绑定明确的 Job 范围，不能直接继承另一视图的筛选 |
| `src/emo_master/apps/designer/ui/log_json_tree.py` | 已有 LogJsonTree：字段 / 值 / 类型、对象与数组惰性展开、长文本查看、复制所选值和当前记录。V2.3 易读层不能以纯文本替代它 |
| `src/emo_master/apps/designer/ui/operator_debug_window.py` | 普通算子的 appendLog 当前使用 str(event)，PLC 有专门格式化；复用通用易读组件，保留现有执行、结果和 PLC 功能，不借此重做整个调试器 |
| `src/emo_master/apps/designer/services/operator_debug.py` | DebugConnection.snapshot 已分别读取 ReadOperatorDebugEvents 与 GetOperatorDebugExecution；前者提供事件 wrapper，后者提供当前 executionId 的执行结果，无需新增 RPC |
| `src/emo_master/apps/runtime/operator_debug/worker.py` | 直接调用 OperatorExecutor，未经过 WorkflowRunner。成功结果已有 outputs / outputAssets / metrics / diagnostics / elapsedMs；日志通过独立 pipe 发送，不能假设存在 node.completed / ioSummary |
| `src/emo_master/apps/runtime/operator_debug/manager.py` | 独立会话 / generation / executionId、sequence 和有界事件缓存；日志 wrapper 的 event 才是内层事件，执行结果通过已有查询取得。复用既有身份与缓存，不增加 Job 映射或副本 |
| `src/emo_master/apps/runtime/preview/executor.py`、`preview/live.py` | 预览使用 BufferedOperatorLogger；纯预览失败带最近消息摘要，实时预览失败可产生 node.preview.failed 且 payload.logs 带缓冲记录。本次只展示实际收到的失败信息，不新增成功预览持久日志流 |
| `src/emo_master/core/contracts/operator_logging.py` | getOperatorLogger / getDeviceLogger 的公共接口；message、code、payload 没有任意业务字段的中文名 / 单位元数据，不重定义该协议 |
| `src/emo_master/apps/runtime/events/operator_logger.py` | OperatorLogManager.emit 将用户 payload 放到事件 payload.data，附 operatorId / phase 及可用 deviceBindings；_safePayload / _safeValue 已有截断、脱敏与类型摘要。V2.3 只消费既有结构 |
| `src/emo_master/apps/runtime/events/jsonl_writer.py` | _projectEvent 被 QueryRuntimeLogs 复用；非设备 completed 事件的白名单保留 metrics / diagnostics / ioSummary，但不提供完整 outputs。原始页展示的是当前收到的投影记录 |
| `src/emo_master/core/contracts/run_inspection.py` | describeValue / summarizePorts / normalizeInspection 提供标量、集合计数、image / array shape 与 dtype、redacted / unavailable 及 omitted / truncated；复用现有类型含义，不由摘要恢复全部值 |
| `src/emo_master/apps/designer/ui/node_result_panel.py` | portText 已按现有端口摘要解释输入 / 输出，节点结果界面与日志数据具有不同职责 |
| `src/emo_master/apps/designer/state/run_result_history.py` | applyEvent 为每个节点保留最近两次 execution；view 提供当前 / 上一次，不能当作任意历史循环结果索引 |
| `src/emo_master/apps/designer/services/inspection_images.py` | _read 依赖检查会话并按 Job / workflowRunId / nodeRunId / 端口查询，核验 revision / captureId；历史结果图并非仅凭一条日志即可取得 |
| `src/emo_master/apps/designer/services/global_logs.py` | `RuntimeGlobalLogs`：唯一 Runtime 实时 reader。当前 `cursor` 是查询/入队进度，不等于界面已应用进度；为详情快照与实时接续补充同一 reader 的交付水位，不能另开持续 Job reader |
| `src/emo_master/apps/designer/services/local_log.py` | `DesignerLogWriter`、当前会话与本机文件归属，继续复用 |
| `src/emo_master/apps/designer/services/runtime_client.py` | `ListRuns`、`GetRunState`、`QueryRuntimeLogs`、`GetDeviceDiagnostics` 客户端包装；新增历史任务查询包装 |
| `src/emo_master/apps/runtime/jobs/read_model.py` | `RunReadRpcMixin` 承载 `GetRuntimeInfo`、`ListRuns` / `GetRunState`；`currentRuns()` 基于 `jobRepository.all()`。新增历史目录与反向日志查询能力时同步 `GetRuntimeInfo.capabilities`，让客户端显式识别支持范围 |
| `src/emo_master/apps/runtime/jobs/repository.py` | `JobRepository.all()` 只读当前进程 `_jobs`；`get()` 有 SQLite 单条回读 |
| `src/emo_master/apps/runtime/context/sqlite_store.py` | `jobs` 表读写、`getJob()`、`queryRuntimeLogs()`；新增历史任务只读分页查询及反向事件页，不新增表。`markOrphanedJobsFailed()` 将重启确认时刻写为 endAt，不能当作旧进程实际结束时间 |
| `src/emo_master/apps/runtime/grpc_server/service.py` | `QueryRuntimeLogs`、`GetDeviceDiagnostics` RPC 及运行读模型 mixin 的装配；QueryRuntimeLogs 的行数/字节上限、终页游标与新旧方向语义需要一致；新增历史任务查询 RPC |
| `src/emo_master/apps/runtime/workflow/loop_runner.py` | `_iterationEvent()`、旧版 `_while` / `_whileV2`、ForEach / Repeat 事件；复用 `loop.started.payload.mode`，不新增 loopType；仅补准确可知的 totalIterations，以及 While completed 的 bodyExecuted 真/假/缺省语义 |
| `src/emo_master/apps/runtime/workflow/context.py` | `workflowRunId`、`parentWorkflowRunId`、`iterationPath`、`cycleId` 的真实语义 |
| `src/emo_master/apps/runtime/workflow/runner.py` | `workflow.started/completed/failed`、`node.started/completed/failed/skipped` 生命周期事件 |
| `src/emo_master/apps/runtime/jobs/worker_main.py` | 连续运行 `_runContinuous()` 每轮创建新的根 RunContext；同一 Job 下产生多个 workflowRunId |
| `src/emo_master/apps/runtime/execution/operator_executor.py` | `executeDevice` 与 `captureDevice` 的 source 字段；统一传递执行上下文 |
| `src/emo_master/apps/runtime/devices/diagnostics.py` | 有界设备诊断缓存、设备摘要/恢复、`query()`。原生事件从 `append()` 经 `_operationalRows()` / `_event()` 输出；在 append 形成 row 前归一化 source，顶层已有可信字段优先。`projectEvent()` 仅处理 node.log / node.failed，不能只改该方法 |
| `src/emo_master/apps/designer/ui/device_workspace.py` | 设备工作台已有诊断页和 `openRuntimeLogs()` 跳转 |
| `src/emo_master/ui/communication_panel.py` | `DeviceDiagnosticsView`、通道筛选、HEX / UTF-8、详情、缓存导出 |
| `src/emo_master/apps/designer/ui/node_result_coordinator.py` | 节点结果“运行日志”入口；补充可用 Job / workflowRunId / nodeRunId 上下文 |
| `src/emo_master/apps/designer/state/node_run_inspection.py` | `NodeRunInspection` 是有界的最近节点观测，按 workflowId / nodeId 保存，并校验 Job、sequence、执行身份；不是全 Job 当前节点权威快照。仅在运行身份匹配且事件接续证据充分时辅助定位，否则显示当前执行位置未确认 |
| `proto/runtime.proto` | Runtime RPC 合同；新增历史任务查询后同步生成物 |
| `scripts/gen_proto.py`、`scripts/build_panel_contract.py` | 现有协议生成入口；不要手工修改生成物代替源 proto |

## 已有文档和验证依据

- `docs/plans/2026-11-03-logging-consolidation-implementation-plan.md`：日志整合 v3.5 已完成的范围和明确边界。
- `docs/testing/2026-11-03-logging-consolidation/README.md`：此前日志整合的测试与交付证据。
- `docs/handoffs/2026-11-05-web-visible-acceptance-repairs.md`：`openDeviceHistory()` 会清理旧查询游标与筛选，避免设备查询被旧条件筛空。
- `tests/runtime/test_device_operational_logs.py`：设备故障摘要、恢复与 EventStore 集成。
- `tests/runtime/test_device_feedback_closure.py`：设备诊断归属、回执与缓存边界。
- `tests/designer/test_operational_log_integration.py`：Runtime 全局日志与 Designer 集成。
- `tests/designer/test_log_dialog.py`：日志窗口筛选、详情、历史查询与导出。
- `tests/designer/test_log_json_tree.py`：已覆盖类型、对象展开、数组分页、长文本和复制；复用这些保障，只补新增易读组件与原始页共享同一数据的接线断言。
- `tests/designer/test_operator_debug_window.py`：已有原生调试窗口及两次结果、输入值区别、结构值分页、执行选择等用例；在相关用例中补日志 wrapper 与执行结果的展示接线，不另造全套调试测试。
- `tests/runtime/test_operator_debug_data.py`、`test_operator_debug_rpc.py`、`test_operator_debug_sessions.py`：调试输出、RPC 与会话身份的现有依据。未改变 Runtime 合同时，不因展示层改动重复新增这些后端测试。
- `tests/designer/test_device_workspace_usability.py`：设备诊断入口与日志跳转。
- `tests/runtime/test_operator_logging.py`：算子日志在循环与子工作流中的关联身份。

## 重要实现边界

- `JobRepository.all()` 不是历史任务数据库；历史列表从现有 SQLite `jobs` 表读取。
- `RuntimeEvent` 已有 `parentWorkflowRunId`，但 Designer `StructuredLogEntry` 当前未完整保留。
- `LoopRunner` 已有 iteration start/completed 事件；补元数据，不另建循环记录表。
- `GetDeviceDiagnostics` 是有界近期缓存，且绑定当前工程加载代次；它不是完整跨重启设备历史。
- `captureDevice` 当前 source 比 `executeDevice` 少执行身份字段，需对齐。
- `QueryRuntimeLogs` 已经能把 `__runtime__` 中明确带有真实 Job ID 的设备摘要纳入 Job 查询；不要把这些摘要再作为独立系统事件重复展示。


## V2 审查补充

- `src/emo_master/apps/runtime/context/sqlite_store.py`：`queryRuntimeLogs()` 当前按全局 `eventId` 升序读取并固定 `upperEventId`；V2 扩展同一请求支持最近页与 `beforeEventId` 向前分页，旧请求语义保持。
- `src/emo_master/apps/runtime/context/sqlite_store.py`：Job 查询已包含 `jobId='__runtime__'` 且 payload `jobId` 匹配的设备摘要；系统事件页必须剔除这些关联项并继续消费空可见页的游标。
- `src/emo_master/apps/runtime/jobs/models.py`：`JobProcessSpec.continuous` 是执行配置；不要把 `JobRecord.executionMode='runtime'` 当成单次 / 连续模式。
- `src/emo_master/apps/runtime/context/sqlite_store.py`：`jobs.startAt` 是插入/受理阶段时间，实际受理时间优先使用记录中的 `acceptedAt`，实际执行开始另有 `startedAt`；`durationMs` 当前按 `startAt` 计算，不应未经核对作为实际执行耗时。
- `src/emo_master/apps/runtime/jobs/read_model.py`：`RunState.active` 包含运行组资源所有权语义，不能单独决定 Job 自身计时是否继续。
- `src/emo_master/apps/runtime/workflow/loop_runner.py`：While false 条件检查仍发出 iteration started/completed，但循环体没有运行；V2 通过 `bodyExecuted` 明确区分。
- `src/emo_master/apps/runtime/devices/manager.py` 与 `devices/diagnostics.py`：设备操作执行标识部分保存在嵌套 source；V2.1 将归一化位置明确为 `DeviceDiagnostics.append()` 形成 row 前，不要求每层重复兜底，不覆盖 Manager 的可信 jobId / scope / 设备归属。
- `proto/runtime.proto` 与 `scripts/gen_proto.py`：新增查询参数后通过现有脚本生成 Python gRPC；`scripts/build_panel_contract.py` 负责同步 Runtime RPC schema / TypeScript 契约镜像。此处只同步契约生成物，不改 Web UI。

## V2.1 实施时必须对照的边界

- `RuntimeGlobalLogs._read()` 当前首次连接直接把 cursor 设到 `upper_event_id`，后续将查询事件入队后才推进 cursor；`take()` 还要由上层消费。详情快照的上界不能与 reader 的查询进度混同。交付水位须表示同一查询范围、同一 Runtime 身份下此前事件已经交付并应用；有限的缺口补取与历史页查询不是第二条持续订阅。
- `QueryRuntimeLogs` 的反向页游标取原始返回页的边界，不取 UI 筛选后可见记录；末页、零记录页及字节预算截断均按 `design.md` 的明确合同处理。旧的正向查询和已有服务端高级筛选保留。
- 现有 `NodeRunInspection` 的 RUNNING 观测和“最新一条日志”都不能单独证明当前执行位置；断线、分页缺口、身份不匹配或缺少起止配对时不能猜测。`ListRuns` / `GetRunState` 也没有提供当前 nodeRunId 的权威字段。
- `JobStatus.STOPPING` 是非终态；有真实 startedAt 且尚无结束时间时仍可显示经过时间。`E_RUNTIME_RESTARTED` 的 endAt 仅为重启确认时间；内部对象缺少配对事件时不据此伪造结束时间。
- `DeviceDiagnostics._operationalRows()` 的聚合键不含 Job 或轮次，occurrences / durationMs 属于设备同类问题聚合；恢复摘要不改变之前失败节点的结果。
- `tests/designer/test_operational_log_integration.py` 已覆盖携带 Job ID 的 Designer 本机错误。一级来源先判 originApp / 已知采集来源，再在 Runtime 内判断 Job 归属。
- `exportHistory()` 的已有固定上界遍历和取消机制应复用；完整 Job 导出需要自己的固定 Job 查询范围，不能因为 `_historyFilters` 中残留等级、设备或时间条件而漏导，也不能把当前有界窗口当作完整 Job。
- 执行树使用 10000 条有界原始事件窗口；选中项及其路径可保留轻量定位状态，但树展开不得成为无限保留原始事件的理由。具体窗口切换与上下文范围提示以 `design.md` 为准。

## V2.2 算子详情核查结论

- `LogDialog` 当前已把 `entry.detailPayload()` 交给 `LogJsonTree`，不是只有原始文本。新增的是默认易读层与数据分区，不是重新实现一个基础 JSON 查看器。
- `OperatorLogManager.emit()` 的真实 node.log payload 是 `{operatorId, phase, data, deviceBindings?, truncated?}`；`data` 只是作者主动写入的日志内容。`phase=lifecycle` 不能区分初始化和清理；code 可能是正常业务代码。
- `_safeValue()` 会按字段名隐藏敏感值，将二进制 / 数组类对象转为说明，并限制字符串、集合和深度；整体数据过大时 `_safePayload()` 返回截断摘要。嵌套值截断不一定传播到顶层 truncated，不能把缺少该标记当作完整性证明。
- `workflow/runner.py` 的 node.started / completed / failed 发布 ioSummary，完成事件还包含 metrics / diagnostics；日志查询投影去掉完整 outputs。可读化必须区分主动日志字段与执行结果摘要。
- 节点结果最近两次记录和图片检查会话均有范围；旧轮日志不能跳到最新图片充当该轮结果。此版不增加任意历史图片恢复或重新执行能力。
- V2.2 新增验证并入 `tests/designer/test_log_dialog.py` 与已有 LogJsonTree 相关测试、G6 和 Q1；复用 `tests/runtime/test_operator_logging.py` 已有数据契约证据，不重复构造限流 / 脱敏测试套。

## V2.3 全算子与多入口覆盖核查

- 正式工作流的普通注册算子通过 OperatorExecutor 取得 logger；基础 node.started / completed / failed / skipped 事件由 WorkflowRunner 产生。结构节点由其专用 runner 执行，不能要求它们调用普通算子的 execute logger。
- 完整读取 82 份 manifest 和 166 个内置 Python 文件，定位 82 个唯一 operatorId、58 个入口模块及相关基类。只有 Huaray 相机、SLMP 读、SLMP 写、SQLite 写四个类显式调用 OperatorLogger；这不是日志功能完成率，亦不是目标机器注册 / 设备运行通过率。
- YOLO 检测的 latencyMs / detectionCount、PatchCore 的诊断摘要、Threshold 的前景比例等已有返回数据主要进入完成事件的 metrics / diagnostics；整数加法只有结果值。D10 把这些当前事件摘要的通用展示改为必需，不再让它们依赖一条不存在的 node.log。
- 相机 captureDevice、PLC executeDevice 的托管路径会绕过兼容直连分支的显式业务日志；优先利用已有返回指标和设备诊断，不重复复制到 node.log。所有内置文件中未发现 getDeviceLogger 调用，不代表设备 Manager 没有产生诊断。
- 单算子调试用 wrapper 的 session / generation / executionId 识别归属，先区分记录类型，再适配内层 event。GetOperatorDebugExecution 的结果是另一份明确标识的数据，不能包装成虚构的 node.completed，也不能加入正式 Job 历史。
- 数值、对象、数组和未知字段使用通用展示；已知端口摘要复用 run_inspection / 结果面板的解释方式。当前事件可直接读，不为字段展示逐条查询其他事件；同次已加载关联信息作为单独来源，不改变原始记录复制内容。
- V2.3 的代表数据与调试入口断言并入既有 G6、6.2 和 Q1；不逐算子写相同测试，不要求加载所有模型或连接真实设备，不新增性能专项、故障组合和跨版本矩阵。
