# Runtime 事件流 v0.6

Runtime 的事件是 Job 范围内的不可变 DTO。每条事件有单调递增的
`sequence`、真实 `timestampMs`、结构化 JSON payload，以及项目、工作流和
运行实例上下文。

## 生命周期

```text
ACCEPTED -> STARTING -> RUNNING -> COMPLETED
                         |       -> FAILED
                         |       -> ABORTED
                         -> STOPPING -> ABORTED
```

常见事件包括 `job.accepted`、`job.started`、`node.started`、`node.skipped`、
`node.completed`、`node.failed`、`workflow.started/completed/failed`、
`loop.iteration.started/completed`、`node.log`、`artifact.created` 和 Job 终态事件。

## 事件字段

```text
jobId, projectId, workflowId, workflowRunId, parentWorkflowRunId,
nodeId, nodeRunId, iterationPath, eventType, level, code, message,
payload, sequence, timestampMs
```

`workflowRunId` 区分同一工作流的不同调用；`parentWorkflowRunId` 连接
Subflow；`iterationPath` 表示嵌套 Loop 的索引路径。Designer 使用
`workflowRunId:nodeId` 作为运行状态键，并将 `artifact.created` 的
`payload.artifact.path` 直接用于预览。旧版本的完成消息路径解析只保留为 fallback。

## 重放和实时订阅

`StreamJobEvents(after_sequence=N)` 返回 `sequence > N` 的历史事件。
`follow=true` 会先重放 SQLite，再等待新事件直到 Job 进入终态或客户端取消。
内存 retention 截断不会隐藏 SQLite 中的早期事件；Runtime 重启后仍可读取
完整历史。

跨进程只传 JSON-safe 事件和 `ArtifactRef`，不传 Qt 对象、gRPC channel、
算子实例或 NumPy 图像。

## Worker 心跳与事件积压

Supervisor 为每个新 Job 创建固定大小的共享心跳时间戳；现有心跳线程在
发送 `process.heartbeat` 前更新它。看门狗使用同机跨进程的 monotonic 时钟，
从进程启动起保持原 `heartbeatTimeoutMs`，不把 SQLite 持久化、Supervisor
锁等待或旧事件排队时间当作 worker 停止发心跳。正式心跳事件仍按原队列顺序
持久化，不跳过事件，也不提前公布尚未持久化的终态。

共享时间戳的读写只尝试非阻塞锁；锁不可读时使用最后一次有效时间戳或启动
基线，不重新计时。过期、负值和未来时间戳不能延长存活期限；即使事件队列
持续非空，真正停止心跳的 worker 仍按原期限失败。共享资源随实际进程和
EventBridge 一起退役，强杀失败或桥接线程未退出时不提前回收。

`runJobProcess` 保留三个参数，`JobProcessSpec.heartbeatCell` 为可选内部字段；
未提供该字段的旧调用保留原路径。带共享心跳的 worker 在所有终态事件入队时，
通过本地锁停止后续正式心跳入队；已有队列 `close()/join_thread()` 收尾期间，
同一心跳线程继续更新共享时间戳，排空结束或失败后才停止。这样计算完成但
仍持有 feeder 的进程不会被误判失联；这只是显式等待原有队列收尾，不提前
公布终态，也不增加故障期限。无共享心跳的旧直接调用不主动 join 队列，
允许调用者在函数返回后自行消费。

此机制不加快持久化、不缩短积压后的终态等待，也不使被阻塞的桥接线程成为
独立实时看门狗。

## 终态受理、回调与 StopJob

正常终态在 Supervisor 锁内写入原终态事件、冻结状态及 endedAt、安装唯一受理栅栏并登记
终态所有者。该 Job 的后续普通事件与竞争终态不再覆盖已受理结果。原调用线程退出全部
Supervisor 锁作用域后，按受理 FIFO 执行冻结的终态回调链；不增加后台 finalizer。
回调至多执行一次，结束（含抛错）后才尝试发布 JobRepository 终态；发布成功后才调用
`EventStore.markTerminal`。A 的资产提升不再占用 Supervisor 锁，B 的普通事件及资源查询
可继续；B 的终态回调仍受 FIFO 约束，公共资产读取仍可能等待原资产锁。

发布失败优先于通知失败，通知失败优先于回调失败。每次首次尝试结束都会推进 FIFO、唤醒
等待者；发布/通知失败继续保留恢复所有权，不阻塞后续 Job 的首次回调。恢复只重试缺失的
幂等步骤，不重放回调、资产提升或终态事件，也不重新计算冻结的 endedAt。回调失败但发布
和通知都成功时，原调用者仍得到回调错误，但不永久保留终态恢复所有者。调用者在回调开始
前异常退出会保留 `CALLBACK_NOT_RUN/OWNER_ABANDONED`，让出 FIFO，但不补跑回调或虚报完成。
共享故障记录只保存有界文本，不保存异常/traceback；调用者可收到原主异常。

`GetJobStatus` 与 Display GetJob/ListJobs 的终态 status 字段及终态事件本身只报告执行状态，
不是持久终态或资源释放确认：事件可在回调结束前可见，仓库持久化失败也可能已经修改内存
终态。Display 的独立 `resources_released` 字段另做展示配置和 Supervisor 所有权检查。
真正的 `StopJob` RPC 与内部 stop 共用 Supervisor 决策，先检查待完成/恢复/退役所有权，
再判断内存终态；没有进程句柄也不能跳过该检查：

- 无既有终态受理时，正常 graceful Stop 的 `ok=true, status=STOPPING` 只表示停止请求
  已受理，不是终态或资源释放确认。该答复固定于本次受理点；后台宽限期线程在答复送达前
  完成或失败，不改写这次受理结果。后续 Stop 仍按当时的待完成/失败所有权判断。
- 已有首次尝试时，在生产锁外等它结束并复查结果，不再发停止事件或取消原 Job。
  RPC 取消/期限只结束该等待者，不取消获胜回调、不完成共享票据、不归还所有权；仍可返回
  答复时为 `ok=false, message=E_JOB_FINALIZATION_PENDING`。无期限的内部 stop 保持同步等待。
- 发布/通知尚未确认时，返回 `ok=false`、当前执行状态及
  `E_JOB_FINALIZATION_FAILED: publication` / `notification`。Stop 本身不做恢复写入。
  已知退役清理失败返回 `E_JOB_RETIREMENT_INCOMPLETE`。
- 自己拥有的回调失败返回 `E_JOB_FINALIZATION_FAILED: callback`；若发布、通知已成功，
  另一个等待者或后续 Stop 可确认已终止。普通进程/桥接退役尚在进行时，Stop 成功也不代表释放。
- 终态尝试/恢复的当前线程同步重入同一或其他 Job 的可变入口，返回或抛出
  `E_FINALIZATION_REENTRANT`，在任何目标副作用或外层锁之前拒绝；不隐式延后执行。
  `getProcess`、`ownsJobResources` 等只读查询仍允许重入且不等待终态票据。

内部宽限期执行器遇到另一执行器已受理的首次终态尝试时，在 Supervisor 锁外等该次尝试
结束，再走原退役检查；首次尝试已经完成但获胜线程尚未退役时，也可由观察者完成这一步。
这不跳过恢复所有权、仍存活的桥接线程或未知原生清理故障，也不重放回调、终态事件或恢复
写入。没有已受理终态的退出进程仍交给桥接线程排空事件，不能据此提前关闭其队列。

保留原 `E_EVENT_PERSISTENCE` 降级路径：先设置内存终态并尝试通知，再执行最佳努力回调，抑制
其回调异常。该路径结束后的 Stop 是执行停止确认，答复保留 `E_EVENT_PERSISTENCE` 上下文，
**不是终态事件/状态已经持久化的确认**。待完成回调或失败簿记仍受所有权栅栏约束；正常
终态发布失败不能改走此路径来清除占用。既有持久化、SQLite 策略及故障期限均不改变。

## 算子结构化日志

Runner 在普通执行的 `runtimeContext["logger"]` 以及生命周期初始化的
`initContext["logger"]` 中注入 `OperatorLogger`。算子应通过公共 helper 获取，确保在旧
Runtime、独立单测和预览环境中安全退化为空 logger：

```python
from emo_master.core.contracts import getOperatorLogger

logger = getOperatorLogger(runtimeContext)
logger.info(
    "camera opened",
    payload={"selector": "camera-1", "attempt": 1},
)
```

接口为 `debug/info/warning/error/log/isEnabledFor`，固定等级为
`DEBUG/INFO/WARN/ERROR`。每条记录成为 `eventType=node.log` 的普通 Runtime 事件，payload
额外带 `operatorId`、`phase=execute|lifecycle` 和算子提供的 `data`。它不替代正式 error、
metrics 或 diagnostics，也不会通过工作流端口传递。执行 logger 在节点终态前关闭，后台线程
之后写入的迟到日志会被丢弃；生命周期 logger 保留到 `disposeOperator()` 完成。

Runtime 会自动限制消息和 payload 大小、嵌套深度、DEBUG/INFO 频率及每 Job 日志总量，
并遮蔽 password、secret、token、authorization、credential、apiKey、privateKey 等字段。
算子不要记录原始图像、完整网络报文、认证信息或完整 PLC 批量数据。

## SQLite 与本地 JSONL

SQLite 是事件查询和重放的权威存储，默认位于
`~/.emo_master/runtime/emo_master.db`。终态 Job 的事件默认保留 30 天，但每个项目至少保留
最近 100 个 Job；运行中的 Job 不参与清理。`eventRetentionPerJob` 只限制内存缓存，不会改变
磁盘保留策略。

Runtime 还异步写入面向运维的 UTF-8 JSONL，默认目录为
`~/.emo_master/runtime/logs`，可通过 `EMO_RUNTIME_LOG_DIR` 覆盖。文件达到 64 MiB 后轮转，
关闭文件保留 14 天，目录总量上限 2 GiB；当前活动文件不会被清理。JSONL 记录 Job、
Workflow、Loop、Node、artifact 生命周期及 `node.log`，完成事件不会复制大体积 outputs。
ERROR 和 Job 终态立即 flush/fsync，其余至少每秒刷新。JSONL 故障不影响作业，Runtime 会把
`runtime.logfile.failed` 仅写入 SQLite，避免递归失败。

算子最低采集等级默认为 INFO，可通过 `EMO_RUNTIME_OPERATOR_LOG_LEVEL` 设置为
`DEBUG`、`WARN` 或 `ERROR`。显示端筛选不会改变已经写入 SQLite/JSONL 的内容。

## Designer 日志视图

Designer 主窗口只有一个底部日志 Dock，可停靠或拖出为非模态窗口。表格保留事件的 Job、
Workflow、Node、iteration、错误码与 payload，上方可按等级、来源、Job、节点、事件类型和
全文搜索筛选；选择一行后可查看格式化 JSON 详情。“清空视图”只清理本次界面内容，不删除
SQLite 或 JSONL。Dock 状态、列宽和筛选条件保存在本机 QSettings，不写入项目。

Runtime 的 `node.log` 来源标记为 `runtime`；Designer 自身和 `EditorContext.log()` 分别标记为
`designer`、`editor`，只保留在当前 Designer 会话中。

### Runtime 的空闲 SQLite 连接所有权

Runtime 初始化完成后明确取得一个无事务的空闲连接，使正常每事件写连接关闭时，
数据库仍有一个所属连接。每事件独立连接、事务、commit、WAL及busy timeout不变；
不共享writer，也不把正式事件批量丢弃或提前标为终态。独立SqliteStore默认不取得它。

空闲连接可以在实际关闭线程释放，取得/归还有单独锁，不参与写事务串行化。取得后
先登记句柄，配置或读取失败时尝试关闭；失败清理保留未就绪所有权及原原因，下一次
取得先处理旧句柄。Runtime只有在Job、日志writer及维护线程退出后才归还此连接，
关闭失败保留所有权和数据目录锁以允许重试，不用终态标签替代资源实际退休。
