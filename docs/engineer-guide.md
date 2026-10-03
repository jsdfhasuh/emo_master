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

Job workspace 位于数据目录的 `jobs/<jobId>/`：失败和中止的 Job 在终态后等实际所有者
退役再清理；成功 Job 为保留 `ArtifactRef` 在 Runtime 关闭前保留，关闭时统一回收。
启动时还会清理未知、已终态和上次 Runtime 遗留的 workspace。Supervisor reap 只在安全
进程/桥接退役边界关闭 Process 和 Queue；终态尝试、发布恢复或失败退役仍保留所有权。
终态受理、Stop 答复与回调错误顺序见 [Runtime 事件流](runtime-event-flow.md#终态受理回调与-stopjob)。

同一数据目录同一时刻只允许一个跨进程 Runtime 实例；第二个实例会在启动时快速失败，避免把仍由第一个实例管理的 Job 误判为孤儿。同一进程内的嵌入式测试实例共享锁，但只有第一个实例执行孤儿 Job 恢复。开发时按开发指南使用独立目录，不要占用或清理现场数据目录。

### 关闭与有限恢复

Runtime.close 先封住新工作；失败后仍保持 closing。终态尝试、恢复、实际进程/桥接和预览
生产者未结束前，不关闭 Presentation/PreviewAssetStore 或持久化资源，不释放数据目录锁，
也不设置 closed。live preview 会话保留到最后一次事件发布结束；native dispose 结果未知
时继续持有会话和算子，后续 close 报原错误，不再执行该 dispose。

每次显式 close 的恢复阶段，对每个缺失且已证明幂等的步骤至多重试一次：冻结的终态
UPDATE、成功发布后的 markTerminal，以及已识别的内置 workspace 清理等。同次 close
可以先在 shutdown 首次发布失败，再恢复一次 UPDATE；已完成步骤不重做。任意回调、未知
部分 native close/unlink 结果不会因重试变成安全操作，仍明确保留未完成状态。内置目录
删除原有 OSError 延后到下次启动清理的语义保留。有限尝试次数不承诺可中断 OS fsync 或
第三方 native 调用，也不扩大原等待期限。

该契约不覆盖所有启动失败清理：既有 `_cleanupFailedStart` 的立即关闭分支仍可能抑制
Process/Queue.close 异常；不能据此宣称所有启动/native 清理故障均已修复。

## 修改边界

新增项目字段先修改 Pydantic 模型、migration、Designer store、Runtime loader 和文档。新增 RPC 先修改 `proto/runtime.proto`，运行 `python scripts/gen_proto.py`，再运行 `python scripts/gen_proto.py --check`。不要把项目真相放回单个 `MainWindow.loadedGraph/currentStatus`，也不要在 RuntimeService 中实现执行器逻辑。

运行能力、部署能力和验收状态分开记录：新增代码入口不自动代表安装包已有该入口；源码测试通过不自动代表冻结资源或现场设备通过。对应部署命令和验证要求必须同步到开发、部署文档。
