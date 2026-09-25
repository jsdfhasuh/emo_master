# 工程师导读：Designer、Runtime 与核心契约

本文描述 `agent/runtime-workflow-architecture-v1` 的工程结构，应用版本以 `src/emo_master/__init__.py` 和 `pyproject.toml` 为准，不再用早期 Runtime 阶段编号作为整份指南的版本。

首次搭建环境见 [开发指南](development-guide.md)，Windows 交付、本地构建与发布见 [部署与发布指南](deployment-guide.md)，项目入口见 [README](../README.md)。

EmoMaster 由 Designer、Runtime 和 `core` 契约层组成，具体算法和设备能力由插件算子提供。`core` 只定义项目、工作流、插件和执行 DTO；它不能 import `apps`。protobuf 只出现在服务适配边界，磁盘项目使用 Pydantic v2 严格模型。

## 运行形态与入口

Designer 是设计和调试界面；Runtime 是执行服务，不是第二套图形工作台。未设置 `EMO_RUNTIME_TARGET` 时，Designer 直接创建并调用内嵌 `RuntimeService`，没有网络 gRPC 连接。设置目标地址后才建立 gRPC channel，连接独立 Runtime。

`apps/designer/main.py` 和 `apps/runtime/main.py`（均位于 `src/emo_master` 下）是两个源码入口，`scripts/dev.py` 提供对应快捷命令。Runtime 默认监听 `127.0.0.1:50051` 并等待请求；启动服务并不自动启动项目。Windows 冻结入口 `apps/windows_entry.py` 默认进入 Designer，目前没有 `--runtime` 分派。

无论内嵌还是外部服务模式，每个正式 Job 都由 Runtime 的 spawn worker 执行。因此，Designer/Runtime 的职责划分、两者的进程部署方式和每个 Job 的子进程隔离，是三个不同层面。

## 代码地图

- `core/project/models.py`：`ProjectDocument`、`WorkflowDefinition` 和运行设置。
- `core/project/migration.py`：v1 到 v2 的纯内存迁移。
- `core/workflow/compiler.py`：端口、节点、DAG、Subflow 调用图和 Loop 校验。
- `core/workflow/loop_contracts.py`：Repeat/ForEach/While 的唯一端口派生与接口契约。
- `apps/designer/state/workflow_store.py`：多工作流、顺序、入口和引用关系。
- `apps/designer/controllers/workflow_controller.py`：工作流切换、画布投影、Subflow 端口和 Loop 配置。
- `apps/designer/services/runtime_worker.py`：QThread 后台 StartJob/follow 消费。
- `apps/runtime/workflow/runner.py`：确定性拓扑执行、Subflow 和事件发布。
- `apps/runtime/jobs/supervisor.py`：一个 Job 一个 spawn 子进程、停止、心跳和并发限制。
- `apps/runtime/events/event_store.py`：内存 retention 与 SQLite 历史合并读取。
- `apps/runtime/context/sqlite_store.py`：schema migration、Job/Event 持久化和孤儿恢复。
- `apps/runtime/grpc_server/service.py`：薄 RPC 适配层。

上述相对路径均位于 `src/emo_master` 下。算子注册和图标资源见 [注册流程](plugin-registration-flow.md) 与 [图标说明](operator-icon-assets.md)。

## 执行主线

1. Designer 通过 `WorkflowStore` 保存 v2 项目，并调用 `LoadProject`。
2. `StartJob` 创建 `JobRecord`、写入 `job.accepted`，立即返回 `ACCEPTED`。
3. `JobSupervisor` 用 `multiprocessing.get_context("spawn")` 启动顶层 `worker_main.runJobProcess`。
4. Worker 重新扫描字符串 plugin roots，读取项目快照，编译并运行入口工作流。
5. `EventBridge` 将 JSON-safe 事件写入 EventStore；EventStore 同时写 SQLite。
6. Designer 的 `RuntimeWorker` 在后台接收 `follow=true` 事件，更新日志、节点状态、`iterationPath` 和 artifact 预览。

## 工作流规则

普通工作流必须是 DAG。Repeat、ForEach、While 只能通过 Loop 节点调用子工作流，每轮开始和结束检查 CancellationToken，并受 `maxIterations` 和 `timeoutMs` 限制。第一版禁止 Break、Continue、Return 和递归 Subflow。

旧 `executeGraph()`、内置 Image Loader、Canny、Image Saver、If、Switch、embedded Runtime 和 external gRPC Runtime 继续保留兼容入口。

## Runtime 数据与 Job workspace

默认 SQLite 路径是 `~/.emo_master/runtime/emo_master.db`，也可以通过 `EMO_RUNTIME_DB_PATH` 指定完整文件路径，或通过 `EMO_RUNTIME_DATA_DIR` 指定数据目录。显式构造参数优先于环境变量，数据库路径变量优先于目录变量。

Job workspace 位于数据目录的 `jobs/<jobId>/`：失败和中止的 Job 在终态回调中清理，成功 Job 为了保留 `ArtifactRef` 在 Runtime 关闭前保留，关闭时统一回收；启动时还会清理未知、已终态和上次 Runtime 遗留的 workspace。进程和 Queue 在 Supervisor reap 路径中关闭并移除句柄。

同一数据目录同一时刻只允许一个跨进程 Runtime 实例；第二个实例会在启动时快速失败，避免把仍由第一个实例管理的 Job 误判为孤儿。同一进程内的嵌入式测试实例共享锁，但只有第一个实例执行孤儿 Job 恢复。开发时按开发指南使用独立目录，不要占用或清理现场数据目录。

## 修改边界

新增项目字段先修改 Pydantic 模型、migration、Designer store、Runtime loader 和文档。新增 RPC 先修改 `proto/runtime.proto`，运行 `python scripts/gen_proto.py`，再运行 `python scripts/gen_proto.py --check`。不要把项目真相放回单个 `MainWindow.loadedGraph/currentStatus`，也不要在 RuntimeService 中实现执行器逻辑。

运行能力、部署能力和验收状态分开记录：新增代码入口不自动代表安装包已有该入口；源码测试通过不自动代表冻结资源或现场设备通过。对应部署命令和验证要求必须同步到开发、部署文档。
