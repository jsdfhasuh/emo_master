# 源码核查记录

## 核查基准

- 仓库：jsdfhasuh/emo_master
- 分支：main
- 固定提交：b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd
- 核查方式：固定提交的 GitHub 源码只读检查；未修改源码、未运行测试。

## 已确认的当前实现

| 位置 | 当前事实 | 对计划的影响 |
|---|---|---|
| core/plugin/models.py | PluginManifest 是冻结 dataclass，包含静态端口、参数、设备要求等；PluginDescriptor 持有 manifest 和 operatorClass | 可在 PluginManifest 增加可选 runtimeCapabilities；不需要第二套注册表 |
| core/plugin/validator.py | validateManifestFields 解析 manifest 字段并构造 PluginManifest；当前未识别 runtimeCapabilities | 需增加可选字符串列表校验、重复/未知值校验及默认空列表 |
| core/plugin/registry.py | scanRoots 通过 validateManifestFields 解析 manifest，成功后创建 PluginDescriptor | 继续沿用现有注册流程，声明自然保存在 descriptor.manifest |
| core/plugin/validator.py | loadOperatorClass 当前要求 validateParams/executeNode；validateConsistency 比较 meta 的 ID、名称、版本、端口和参数 | 不增加必需方法或 OperatorMeta 字段；运行时能力由 manifest 单点声明 |
| apps/runtime/execution/operator_executor.py | invoke 为每次 executeNode 构造 runtimeContext 字典；正式 Job registry 值为 PluginDescriptor；日志在 executeNode 返回后关闭 | 可从 descriptor.manifest.runtimeCapabilities 判定是否注入；后台任务不能继续使用已关闭 logger |
| plugins/builtins/image_saver/manifest.json | ImageSaver 输出端口 result 为 JSON；现有参数支持 fixed/unique 命名，默认 fixed | 后台模式可通过 JSON 回执携带 taskId；为避免异步覆盖和产物错配，首版需限制 unique 命名 |
| apps/runtime/workflow/runner.py | 节点成功后先调用 _publishArtifacts，再捕获 outgoingBindings；captureBoundValue 会立即调用 toStorage | 仅延迟 artifact 不够；SQLite FILE_REFERENCE 也必须延迟到目标 SQLiteWriter 节点前采集 |
| apps/runtime/workflow/runner.py | _publishArtifacts 立即处理所有输出值中带 path 的 JSON；启用 ArtifactStore 时 registerPath 会复制文件并计算 SHA-256 | pending artifact 必须等 task 成功后再登记 |
| core/workflow/parameter_bindings.py | captureBoundValue 立即调用 SQLite toStorage；FILE_REFERENCE 需要 saved=true 且文件存在 | 后台 ImageSaver 返回 saved=false 时，当前绑定采集必然失败；需 Runner 暂存 pending binding 并在消费者执行前 wait |
| core/contracts/sqlite_writer.py | FILE_REFERENCE 只接受已保存的持久文件；拒绝不存在文件和预览/临时路径 | SQLiteWriter 事务代码不需改，但 Runner 必须先解析 pending 引用 |
| apps/runtime/jobs/worker_main.py | 每个 Job worker 进程扫描插件、构造 WorkflowRunner；continuous cycle 间隔和 Dispatch consumer IDLE 循环在 Runner.run() 外等待；publishTerminal 当前先关闭 previewWriter | 后台失败轮询还需覆盖 Runner.run() 外的等待循环；后台服务 drain 和 Runner completion/artifact drain 必须发生在 previewWriter.close 之前 |
| apps/runtime/jobs/supervisor.py | JobSupervisor 使用 multiprocessing spawn 创建 Job 进程；gracefulStopTimeoutMs 默认 5000ms，超时会终止 worker | 后台任务停止必须尊重现有停止宽限，不承诺无限等待或强行中断 Python I/O |
| apps/runtime/jobs/models.py | JobProcessSpec 是 frozen dataclass，包含 Job worker 参数，当前没有后台 drain deadline | 增加内部 backgroundTaskDrainTimeoutMs 默认值，不暴露为工程配置；Supervisor 仍按原 gracefulStopTimeoutMs 管理用户停止 |
| apps/runtime/preview/async_writer.py | AsyncPreviewSnapshotWriter 有独立线程、有界缓存、同节点合并/丢弃策略 | 可借鉴线程生命周期，不可直接复用其丢弃策略保存业务图片 |
| apps/runtime/preview/executor.py | PurePreviewExecutor 自己使用 ThreadPoolExecutor 执行纯预览，并设置 isPreview=True；runtimeContext 没有通用后台服务 | 纯预览必须继续隔离后台副作用 |
| apps/runtime/operator_debug/contracts.py | trustedEntries 从当前 PluginDescriptor 验证内置算子身份，生成调试准入描述 | 应从这里把已校验 runtimeCapabilities 放入调试 worker spec |
| apps/runtime/operator_debug/worker.py | 单算子调试 worker 直接创建 class-only OperatorExecutor；输出冻结前没有后台任务 drain | 需显式传递能力映射，并在 freezeOutputs 前 drain |
| apps/runtime/workflow_debug/worker.py | 工作流调试 worker 创建 WorkflowRunner；clone-safe trial 另建 OperatorExecutor | 调试会话需共享自己的服务；trial 不继承 |
| plugins/builtins/image_saver/operator.py | 当前在 executeNode 内执行 cv2.imencode、创建目录并直接写最终路径；支持 fixed/unique 命名 | 后台实现必须使用任务专属临时文件，完整写入后原子发布；unique 命名避免跨 Job/周期同路径竞争 |
| plugins/builtins/detection_archive/operator.py | 逐个裁剪并同步调用 ImageSaver，保存后累计 cropPaths 和 progress | 首版不改，避免同时改变部分完成语义 |
| plugins/builtins/result_writer/operator.py | 序列化后执行原子写入 | 保持同步 |
| plugins/builtins/sqlite_writer/operator.py | 从 runtimeContext.mappedOutputs 读取 BoundValue，并在自身执行中完成事务 | SQLiteWriter 本身不需异步化；Runner 负责在执行前解析延迟文件引用 |

## 关键源码入口

- [PluginManifest / PluginDescriptor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/models.py)
- [插件字段校验](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/validator.py)
- [PluginRegistry](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/registry.py)
- [OperatorExecutor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/execution/operator_executor.py)
- [WorkflowRunner](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/workflow/runner.py)
- [参数绑定采集](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/workflow/parameter_bindings.py)
- [SQLite 文件引用校验](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/contracts/sqlite_writer.py)
- [ArtifactStore](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/artifacts/store.py)
- [Job worker](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/worker_main.py)
- [JobSupervisor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/supervisor.py)
- [纯预览执行器](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/preview/executor.py)
- [调试准入声明](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/operator_debug/contracts.py)
- [单算子调试 worker](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/operator_debug/worker.py)
- [工作流调试 worker](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/workflow_debug/worker.py)
- [ImageSaver](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/image_saver/operator.py)
- [DetectionArchive](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/detection_archive/operator.py)

## 尚待实施时确认

- BackgroundTaskService 的任务数量上限、待处理字节预算、单任务字节上限和终态记录上限；作为 Runtime 内部常量，不作为工程配置暴露。
- Runner 完成记录队列的线程归属和 drain 位置，确保 ArtifactStore、ResultCollector 和预览 writer 只由 Runtime 执行线程调用。
- 未观察失败在节点边界/workflow 完成前的发现时机，以及内部 SQLite wait 的失败观察标记。
- 后台 unique 命名的参数校验与原子文件发布实现，尤其是 Windows/POSIX 的 no-overwrite 最终提交语义。
- 调试 worker 如何传递 trustedEntries 生成的能力映射，并在输出冻结前 drain。
- Worker terminal 发布与 finally 清理顺序的最小调整点；必须保持现有 5000ms graceful stop 超时语义。

以上是固定源码核查，不代表当前代码已有后台任务服务。
