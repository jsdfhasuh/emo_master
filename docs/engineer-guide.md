# 工程师导读：EmoMaster Runtime v0.2

EmoMaster 由 Designer、Runtime 和 `core` 契约层组成。`core` 只定义项目、
工作流、插件和执行 DTO；它不能 import `apps`。protobuf 只出现在 gRPC
边界，磁盘项目使用 Pydantic v2 严格模型。

## 代码地图

- `core/project/models.py`：`ProjectDocument`、`WorkflowDefinition` 和运行设置。
- `core/project/migration.py`：v1 到 v2 的纯内存迁移。
- `core/workflow/compiler.py`：端口、节点、DAG、Subflow 调用图和 Loop 校验。
- `apps/designer/state/workflow_store.py`：多工作流、顺序、入口和引用关系。
- `apps/designer/controllers/workflow_controller.py`：工作流切换、画布投影、Subflow 端口和 Loop 配置。
- `apps/designer/services/runtime_worker.py`：QThread 后台 StartJob/follow 消费。
- `apps/runtime/workflow/runner.py`：确定性拓扑执行、Subflow 和事件发布。
- `apps/runtime/jobs/supervisor.py`：一个 Job 一个 spawn 子进程、停止、心跳和并发限制。
- `apps/runtime/events/event_store.py`：内存 retention 与 SQLite 历史合并读取。
- `apps/runtime/context/sqlite_store.py`：schema migration、Job/Event 持久化和孤儿恢复。
- `apps/runtime/grpc_server/service.py`：薄 RPC 适配层。

## 执行主线

1. Designer 通过 `WorkflowStore` 保存 v2 项目，并调用 `LoadProject`。
2. `StartJob` 创建 `JobRecord`、写入 `job.accepted`，立即返回 `ACCEPTED`。
3. `JobSupervisor` 用 `multiprocessing.get_context("spawn")` 启动顶层
   `worker_main.runJobProcess`。
4. Worker 重新扫描字符串 plugin roots，读取项目快照，编译并运行入口工作流。
5. `EventBridge` 将 JSON-safe 事件写入 EventStore；EventStore 同时写 SQLite。
6. Designer 的 `RuntimeWorker` 在后台接收 `follow=true` 事件，更新日志、节点
   状态、`iterationPath` 和 artifact 预览。

## 工作流规则

普通工作流必须是 DAG。Repeat、ForEach、While 只能通过 Loop 节点调用子工作流，
每轮开始和结束检查 CancellationToken，并受 `maxIterations` 和 `timeoutMs`
限制。第一版禁止 Break、Continue、Return 和递归 Subflow。

旧 `executeGraph()`、内置 Image Loader、Canny、Image Saver、If、Switch、
embedded Runtime 和 external gRPC Runtime 继续保留兼容入口。

## Runtime 数据与 Job workspace

默认 SQLite 路径是 `~/.emo_master/runtime/emo_master.db`，也可以通过
`EMO_RUNTIME_DB_PATH` 指定完整文件路径，或通过 `EMO_RUNTIME_DATA_DIR` 指定
数据目录。显式构造参数优先于环境变量。Job workspace 位于数据目录的
`jobs/<jobId>/`：失败和中止的 Job 在终态回调中清理，成功 Job 为了保留
`ArtifactRef` 在 Runtime 关闭前保留，关闭时统一回收；启动时还会清理未知、
已终态和上次 Runtime 遗留的 workspace。进程和 Queue 在 Supervisor reap 路径
中关闭并移除句柄。

## 修改边界

新增项目字段先修改 Pydantic 模型、migration、Designer store、Runtime loader
和文档。新增 RPC 先修改 `proto/runtime.proto`，运行 `python scripts/gen_proto.py`，
再运行 `python scripts/gen_proto.py --check`。不要把项目真相放回单个
`MainWindow.loadedGraph/currentStatus`，也不要在 RuntimeService 中实现执行器逻辑。
