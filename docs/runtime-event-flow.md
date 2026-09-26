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
