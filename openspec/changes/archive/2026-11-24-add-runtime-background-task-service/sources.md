# 源码核查记录

## 基准

- 仓库：`jsdfhasuh/emo_master`
- 分支：`main`
- 固定提交：`b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`
- 核查方式：固定提交只读源码检查；未修改源码、未运行测试。

## 关键实现事实

| 位置 | 当前实现 | 对计划的影响 |
|---|---|---|
| `core/plugin/models.py` | PluginManifest 是 frozen dataclass，末尾已有默认字段 | 新 runtimeCapabilities 放最后并给默认空 tuple，保持旧构造兼容 |
| `core/plugin/validator.py` | validateManifestFields 手动解析 manifest 并构造 PluginManifest | 在此增加可选能力字段校验，不另建 schema/注册系统 |
| `core/plugin/registry.py` | scanRoots 通过 validator 生成 PluginDescriptor | 继续沿用原注册流程 |
| `core/plugin/validator.py` | 算子加载校验 validateParams/executeNode；meta 一致性检查端口和参数 | 不新增算子基类、必需方法或 OperatorMeta 字段 |
| `apps/runtime/execution/operator_executor.py` | 每次 executeNode 构造 runtimeContext；正式 registry 值为 PluginDescriptor；执行后关闭 OperatorLogger | 由 descriptor.manifest 决定是否注入；后台任务不能使用已关闭 logger |
| `apps/runtime/workflow/runner.py` | 节点成功后立即发布 artifact，再采集 outgoingBindings；captureBoundValue 会立即 toStorage | pending artifact 和 SQLite FILE_REFERENCE 需要 Runner 延迟处理 |
| `apps/runtime/artifacts/store.py` | ArtifactStore.registerPath 会复制文件并计算 SHA-256 | 文件成功后才能登记 |
| `core/workflow/parameter_bindings.py` | captureBoundValue 立即调用 SQLite toStorage | pending FILE_REFERENCE 必须在 SQLiteWriter 执行前 wait |
| `core/contracts/sqlite_writer.py` | FILE_REFERENCE 要求 saved=true 且文件存在 | SQLiteWriter 事务逻辑不需要改 |
| `apps/runtime/jobs/worker_main.py` | Job worker 创建 WorkflowRunner；terminal 路径先关闭 preview writer | 后台 drain 和 artifact capture 必须先于 preview writer close |
| `apps/runtime/jobs/supervisor.py` | multiprocessing spawn；gracefulStopTimeoutMs 默认 5000ms | 线程归 Job worker 所有；用户 Stop 继续沿用原超时 |
| `apps/runtime/jobs/models.py` | JobProcessSpec 是 frozen dataclass | 本计划不增加新的 JobProcessSpec 配置字段 |
| `apps/runtime/preview/async_writer.py` | 预览快照 writer 有独立队列和丢弃策略 | 不复用它处理可靠业务文件 |
| `apps/runtime/preview/executor.py` | PurePreviewExecutor 独立执行纯预览 | 纯预览不开放后台副作用 |
| `apps/runtime/operator_debug/contracts.py` | trustedEntries 从 PluginDescriptor 校验调试准入 | 调试能力从这里传递已验证 manifest 声明 |
| `apps/runtime/operator_debug/worker.py` | 单算子调试使用 class-only executor | 需要显式传递能力声明 |
| `apps/runtime/workflow_debug/worker.py` | 工作流调试创建 WorkflowRunner；clone-safe trial 另建 executor | 调试会话可使用服务；trial 不继承 |
| `plugins/builtins/image_saver/operator.py` | 同步执行 cv2.imencode、创建目录和写最终路径 | 后台模式必须改成临时文件完成后原子发布 |
| `plugins/builtins/image_saver/manifest.json` | 当前支持 fixed/unique，默认 fixed | 后台模式限制 unique；同步模式保持原样 |
| `plugins/builtins/detection_archive/operator.py` | 逐个裁剪并同步调用 ImageSaver | 首版不改，避免改变部分完成语义 |
| `plugins/builtins/result_writer/operator.py` | 原子文件写入 | 保持同步 |
| `plugins/builtins/sqlite_writer/operator.py` | 从 mappedOutputs 读取 BoundValue 并执行事务 | 只改 Runner 的延迟绑定准备，不改 SQLiteWriter 事务 |

## 关键源码入口

- [PluginManifest](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/models.py)
- [manifest validator](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/validator.py)
- [PluginRegistry](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/registry.py)
- [OperatorExecutor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/execution/operator_executor.py)
- [WorkflowRunner](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/workflow/runner.py)
- [ArtifactStore](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/artifacts/store.py)
- [parameter bindings](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/workflow/parameter_bindings.py)
- [SQLite contract](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/contracts/sqlite_writer.py)
- [Job worker](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/worker_main.py)
- [JobSupervisor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/supervisor.py)
- [operator debug contracts](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/operator_debug/contracts.py)
- [ImageSaver](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/image_saver/operator.py)

## 核查边界

以上是固定源码的静态核查，不代表后台服务已经存在。实施时如果发现当前分支源码与基准不同，应先重新核对实际基准，再调整计划和验收范围。
