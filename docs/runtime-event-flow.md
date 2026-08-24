# Runtime 事件流 v0.2

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
`loop.iteration.started/completed`、`artifact.created` 和 Job 终态事件。

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
