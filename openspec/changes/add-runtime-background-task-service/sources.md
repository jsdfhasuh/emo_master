# 源码核查记录

## 核查基准

- 仓库：jsdfhasuh/emo_master
- 分支：main
- 固定提交：b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd
- 核查方式：固定提交的 GitHub 源码只读检查；未修改源码、未运行测试。

## 已确认的当前实现

| 位置 | 当前事实 | 对计划的影响 |
|---|---|---|
| core/plugin/registry.py | PluginRegistry.scanRoots 读取 manifest、导入 entry 类、校验 meta，并形成 activeOperators/rejectedOperators | 后台任务不属于注册发现阶段 |
| core/plugin/validator.py | loadOperatorClass 当前要求 validateParams/executeNode；validateConsistency 比较已声明 meta 字段 | 不增加必需方法或基类 |
| core/plugin/models.py | PluginDescriptor 持有 manifest 和 operatorClass；manifest 无运行时线程字段 | 不扩展 PluginDescriptor 或 manifest 能力声明 |
| apps/runtime/execution/operator_executor.py | invoke 为每次 executeNode 构造 runtimeContext 字典；日志在 executeNode 返回后关闭；设备/通信/全局变量等由 Runtime 注入 | 最小注入点是此处；后台任务不能继续使用已关闭 logger |
| apps/runtime/workflow/runner.py | WorkflowRunner 创建 OperatorExecutor；Job 运行时节点结束会执行 artifact 发布；_publishArtifacts 目前立即处理所有带 path 的 JSON 输出 | 需要将服务沿 Runner 传入，并延迟处理异步文件产物 |
| apps/runtime/jobs/worker_main.py | 每个 Job worker 进程扫描插件、构造 WorkflowRunner；预览快照 writer 在此创建并在 terminal 前关闭 | 服务应在同一 Job worker 创建，并在 Job terminal 前收尾 |
| apps/runtime/jobs/supervisor.py | JobSupervisor 使用 multiprocessing spawn 创建 Job 进程 | Python 线程只能 Job 内共享，不能跨 Job 共享 |
| apps/runtime/preview/async_writer.py | AsyncPreviewSnapshotWriter 有独立线程、有界缓存、同节点合并/丢弃策略 | 可借鉴线程生命周期，不可直接复用其丢弃策略保存业务图片 |
| apps/runtime/preview/executor.py | PurePreviewExecutor 自己使用 ThreadPoolExecutor 执行纯预览，并设置 isPreview=True；runtimeContext 没有通用后台服务 | 纯预览必须继续隔离后台副作用 |
| apps/runtime/operator_debug/worker.py | 单算子调试 worker 直接创建 OperatorExecutor，结果冻结前需要确保异步副作用完成 | 调试执行入口需显式管理服务 |
| apps/runtime/workflow_debug/worker.py | 工作流调试 worker 创建 WorkflowRunner；clone-safe trial 另建 OperatorExecutor | 调试会话可持有服务，trial 不应继承 |
| plugins/builtins/image_saver/operator.py | 当前在 executeNode 内执行 cv2.imencode、创建目录和文件写入，成功后返回 saved/path | 首个接入者，保留同步默认 |
| plugins/builtins/detection_archive/operator.py | 逐个裁剪并同步调用 ImageSaver，保存后累计 cropPaths 和 progress | 首版不改，避免同时改变部分完成语义 |
| plugins/builtins/result_writer/operator.py | 序列化后执行原子写入 | 保持同步 |
| plugins/builtins/sqlite_writer/operator.py | 等待事务提交并返回 receipt | 保持同步，不能把事务提交伪装成排队成功 |

## 关键源码入口

- [PluginRegistry](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/registry.py)
- [插件校验](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/core/plugin/validator.py)
- [OperatorExecutor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/execution/operator_executor.py)
- [WorkflowRunner](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/workflow/runner.py)
- [Job worker](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/worker_main.py)
- [JobSupervisor](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/jobs/supervisor.py)
- [预览异步写入](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/preview/async_writer.py)
- [纯预览执行器](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/apps/runtime/preview/executor.py)
- [ImageSaver](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/image_saver/operator.py)
- [DetectionArchive](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/detection_archive/operator.py)
- [ResultWriter](https://github.com/jsdfhasuh/emo_master/blob/b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd/src/emo_master/plugins/builtins/result_writer/operator.py)

## 尚待实施时确认

- 现有 Worker terminal 发布与 finally 清理顺序的最小调整点。
- BackgroundTaskService 的队列容量默认值及任务结果保留时长；不作为工程配置暴露。
- ArtifactStore 注册回调的具体接口，确保复制/哈希仍在文件完成之后执行。
- 调试 worker 的任务 drain 与输出冻结先后关系。

以上是实现落点确认项，不代表当前代码已有后台任务服务。
