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
| apps/runtime/execution/operator_executor.py | invoke 为每次 executeNode 构造 runtimeContext 字典；执行器持有 operatorRegistry，生产 registry 值为 PluginDescriptor；日志在 executeNode 返回后关闭 | 可从当前 descriptor.manifest.runtimeCapabilities 判定是否注入；后台任务不能继续使用已关闭 logger |
| apps/runtime/workflow/runner.py | WorkflowRunner 创建 OperatorExecutor；Job 运行时节点结束会执行 artifact 发布；_publishArtifacts 目前立即处理所有带 path 的 JSON 输出 | 需要将服务沿 Runner 传入，并延迟处理异步文件产物 |
| apps/runtime/jobs/worker_main.py | 每个 Job worker 进程扫描插件、构造 WorkflowRunner；预览快照 writer 在此创建并在 terminal 前关闭 | 服务应在同一 Job worker 创建，并在 Job terminal 前收尾 |
| apps/runtime/jobs/supervisor.py | JobSupervisor 使用 multiprocessing spawn 创建 Job 进程 | Python 线程只能 Job 内共享，不能跨 Job 共享 |
| apps/runtime/preview/async_writer.py | AsyncPreviewSnapshotWriter 有独立线程、有界缓存、同节点合并/丢弃策略 | 可借鉴线程生命周期，不可直接复用其丢弃策略保存业务图片 |
| apps/runtime/preview/executor.py | PurePreviewExecutor 自己使用 ThreadPoolExecutor 执行纯预览，并设置 isPreview=True；runtimeContext 没有通用后台服务 | 纯预览必须继续隔离后台副作用 |
| apps/runtime/operator_debug/worker.py | 单算子调试 worker 直接创建 OperatorExecutor；调试 registry 当前以 class 构造，不天然保留 PluginDescriptor | 调试请求需携带来自已校验 PluginManifest 的能力声明，不能仅凭 class 自我声明 |
| apps/runtime/workflow_debug/worker.py | 工作流调试 worker 创建 WorkflowRunner；clone-safe trial 另建 OperatorExecutor | 调试会话需传递已校验能力；trial 不继承服务 |
| plugins/builtins/image_saver/operator.py | 当前在 executeNode 内执行 cv2.imencode、创建目录和文件写入，成功后返回 saved/path | 首个接入者，保留同步默认 |
| plugins/builtins/detection_archive/operator.py | 逐个裁剪并同步调用 ImageSaver，保存后累计 cropPaths 和 progress | 首版不改，避免同时改变部分完成语义 |
| plugins/builtins/result_writer/operator.py | 序列化后执行原子写入 | 保持同步 |
| plugins/builtins/sqlite_writer/operator.py | 等待事务提交并返回 receipt | 保持同步，不能把事务提交伪装成排队成功 |

## 关键源码入口

- [PluginManifest / PluginDescriptor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/models.py)
- [插件字段校验](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/validator.py)
- [PluginRegistry](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/registry.py)
- [OperatorExecutor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/execution/operator_executor.py)
- [WorkflowRunner](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/workflow/runner.py)
- [Job worker](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/worker_main.py)
- [JobSupervisor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/supervisor.py)
- [预览异步写入](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/preview/async_writer.py)
- [纯预览执行器](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/preview/executor.py)
- [ImageSaver](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/image_saver/operator.py)
- [DetectionArchive](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/detection_archive/operator.py)

## 尚待实施时确认

- 现有 Worker terminal 发布与 finally 清理顺序的最小调整点。
- BackgroundTaskService 的队列容量默认值及任务结果保留时长；不作为工程配置暴露。
- ArtifactStore 注册回调的具体接口，确保复制/哈希仍在文件完成之后执行。
- 调试请求如何携带并验证 runtimeCapabilities，避免能力声明在 class-only debug registry 中丢失。
- 调试 worker 的任务 drain 与输出冻结先后关系。

以上是实现落点确认项，不代表当前代码已有后台任务服务。
