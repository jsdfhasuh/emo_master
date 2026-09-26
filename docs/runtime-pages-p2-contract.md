# P2 显式结果通道

本通道属于正式模块 `apps/runtime/presentation`，默认应用入口不创建它。
`PresentationService` 复用已有 Runtime、JobManager、Supervisor 和 spawn Worker，
`prepare` 只生成稳定副本和编译记录；只有 `start(preparedId)` 创建任务。
所有客户端读取操作均不得调用 StartJob 或 StopJob。

## 首批契约

- `ClosedSource.reasonCode` 兼容新增；旧记录可只有 reason。新采集器始终输出稳定代码。
  OPTIONAL_ABSENT、BRANCH_SKIPPED、NODE_FAILED、EXECUTION_CANCELLED、EXPORT_TIMEOUT、
  EXPORT_FAILED、RESOURCE_EXPIRED、BUDGET_EXCEEDED、INVALID_VALUE、SOURCE_MISSING、IPC_ERROR。
- 执行 COMPLETED 且所有来源 AVAILABLE 才 COMPLETE；可选未输出也记 INCOMPLETE，
  不丢清单项。执行 FAILED/CANCELLED 优先；以上状态均不是产品 OK/NG。
- 非有限指数、字面量、超 256 KiB 来源、4096 元素、12 层、16384 值节点拒绝，
  0/false/空集合/null 保留。每结果值 1 MiB。
- callPath 包含 subflow / loop_body / loop_condition；每个显式 scope 的每次调用独立封闭。
  本轮只接收本调用作用域的 node_output / workflow_output。跨作用域混合来源和
  global_counter/runtime_status 绑定明确拒绝准备，不能静默读最后值；实际计数节点输出支持。
- 检测开始分配业务序号。最新开始序号立即清除旧 latest；旧封闭不能覆盖新的指针。
  消息游标另行递增。保留至多 32 结果及 8 MiB 元数据；并非完整历史。
- 单 Runtime 至多 2 个展示 Job、8 个准备记录。每 Job 至多 8 个 OPEN；
  超额采集拒绝计入共享计数器；不会结束检测任务。终态 Job 需显式 release 释放保留状态。
- debug SQLite/outputs 使用 P1 的隔离命名空间，不迁移生产 SQLite，不创建第二个 Runtime。
- 新 protobuf DisplayService 与旧 RuntimeService 并存；不替换旧客户端与无页面项目。

首批只验证标量；图像资源实现和分类 aio 入口随之后批次补齐，不能把首批标量通过视为 P2 退出。
