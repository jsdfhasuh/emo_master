# Runtime 后台任务服务

## Why

当前业务算子直接执行文件编码和写入，节点必须等待 I/O 完成后才能返回。需要给算子提供由 Runtime 统一管理、按需使用的后台任务能力；图片保存是首个落地场景。

算子必须通过 manifest 声明 `backgroundTasks`，Runtime 才向它开放任务提交和等待接口。这样不需要算子自行创建线程，也不会默认向所有算子开放后台能力。

## What Changes

- manifest 增加可选 `runtimeCapabilities`，首版支持 `backgroundTasks`；旧 manifest 缺省为空。
- 每个 Job 进程管理一条后台 FIFO 工作线程和有界任务队列。
- RuntimeContext 仅向已声明能力的算子注入后台任务客户端。
- 提供提交、等待、查询状态、失败报告和 Job 收尾。
- ImageSaver 声明该能力并增加可选后台模式，默认仍同步；后台模式必须使用唯一命名。
- 图片先写任务专属临时文件，完整后原子创建最终文件。若提交回执中的路径已被其他写方占用，任务报错并保留对方文件。Runner 在成功后登记 artifact。
- 正式 Job 或调试子进程退出后，Runtime 回收该进程遗留的受管临时图片。若 Runtime 也已退出，下次在同一数据目录启动时，按持久登记的私有暂存目录自动清理。
- Runner 延迟处理 SQLite `FILE_REFERENCE` 绑定，确保文件真正保存后才交给 SQLiteWriter。
- 配置排空等待已接收的后台写入及其产物登记。Dispatch 在跨 Job 传递文件结果前确认写入，并在每次投递结束时处理该次任务的失败。
- While 在迭代间隔检查后台失败。Designer 将迟到的产物关联到对应的节点执行。普通 Job 失败时给活动任务有限时间清理临时文件。

## 不做什么

- 不增加算子基类、不改变 `executeNode()` 签名、不重构 PluginRegistry。
- 不强制现有算子异步化；同步行为保持不变。
- 不把 CPU 密集型推理放入文件 I/O 线程。
- 不合并预览快照或日志专用线程。
- 不做跨 Job 全局线程池、任务持久化、自动重试或新 RPC。
- 首版不改 DetectionArchive、ResultWriter 和 SQLiteWriter 的事务执行逻辑。

## Impact

改动涉及 PluginManifest/manifest validator、Job worker、WorkflowRunner、OperatorExecutor、后台任务服务、ImageSaver，以及 artifact 和 SQLite 文件引用的衔接。

最初的源码基准为 `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`。独立审查基于 `50f240f9d47fcfa280ba1520153c8ecf46f55976`。后续修正列在实施任务第 6 至 9 节。[实施任务](tasks.md) · [交付记录](delivery.md)
