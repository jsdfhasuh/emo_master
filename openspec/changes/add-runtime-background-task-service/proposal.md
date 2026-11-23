# Runtime 后台任务服务

## Why

当前业务算子在 executeNode() 中直接执行文件编码和写入，节点必须等待 I/O 完成后才能返回。Runtime 已有独立的预览快照写入线程和日志写入线程，但它们各自拥有专用策略，不能直接作为业务算子的通用后台执行入口。

需要给算子提供一项由 Runtime 统一管理、按需使用的后台任务能力。图片保存是首个落地场景；后续其他算子可以复用，不必自行创建线程、队列和管理退出。

## What Changes

- 在每个正常 Job 进程内建立一个 Runtime 管理的后台任务服务，首版使用一条 FIFO 工作线程和有界队列。
- 通过每次算子执行时注入的 runtimeContext["backgroundTasks"] 提供提交、等待和查询任务结果的能力。
- 任务绑定提交时的 Job / Workflow / Node / Run 身份；后台完成、失败通过现有 Runtime 事件通道报告。
- 明确队列满时的背压、任务结果、Job 正常结束、失败和取消时的收尾规则。
- 为内置 ImageSaver 增加可选后台保存模式，默认仍为同步保存；后台模式返回任务 ID 和待完成状态。
- 延迟发布后台图片的 artifact，只有实际写入成功后才进入现有产物登记与预览链路。
- 让正式 Job 和允许副作用的算子调试执行拥有该能力；纯预览和纯计算试运行不开放真实后台副作用。

## 不做什么

- 不修改 PluginRegistry、manifest 注册流程、算子加载器、OperatorMeta 必需字段或算子继承体系。
- 不强制所有算子异步化，不改变现有同步算子的执行契约。
- 不把 YOLO 推理、图像计算等 CPU 密集型任务放进这条 I/O 工作线程。
- 不合并现有 AsyncPreviewSnapshotWriter 或 RuntimeJsonlLogWriter。
- 不建立跨 Job / 跨进程的全局线程池，不引入图片 IPC 传输。
- 首版不改 DetectionArchive、ResultWriter、SQLiteWriter 的同步语义；不增加通用文件事务、自动重试、断电恢复或任务持久化。
- 不新增 RPC、Designer 配置页面或 Web 功能。

## Impact

主要改动落在 Runtime Job worker、WorkflowRunner、OperatorExecutor、后台任务服务、事件发布和 artifact 完成登记，以及 ImageSaver 的参数声明与实现。现有工程格式、端口类型和算子注册协议不变。

基准源码：main @ b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd。本计划先作为独立 OpenSpec change 评审，未包含正式源码实现。
