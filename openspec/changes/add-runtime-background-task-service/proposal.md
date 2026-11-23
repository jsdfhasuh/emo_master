# Runtime 后台任务服务

## Why

当前业务算子在 executeNode() 中直接执行文件编码和写入，节点必须等待 I/O 完成后才能返回。Runtime 已有独立的预览快照写入线程和日志写入线程，但它们各自拥有专用策略，不能直接作为业务算子的通用后台执行入口。

需要给算子提供一项由 Runtime 统一管理、按需使用的后台任务能力。图片保存是首个落地场景；后续其他算子可以复用，不必自行创建线程、队列和管理退出。同时，算子是否具备使用该服务的资格应在插件 manifest 中明确声明，由注册校验和 Runtime 执行器共同落实，而不是默认向所有算子开放。

## What Changes

- 在每个 Job 进程内建立 Runtime 管理的后台任务服务，首版使用一条 FIFO 工作线程和有界队列。
- 在插件 manifest 增加可选 runtimeCapabilities 声明；首版支持 backgroundTasks。注册时校验并保存在 PluginManifest 中。
- 只有声明 backgroundTasks 的算子，Runtime 才在其 runtimeContext 中注入后台任务客户端。
- 通过客户端提供提交、等待和查询任务结果能力；任务绑定提交时的 Job / Workflow / Node / Run 身份。
- 明确队列满时的背压、任务结果、Job 正常结束、失败和取消时的收尾规则。
- 为内置 ImageSaver 声明 backgroundTasks，并增加可选后台保存模式，默认仍为同步保存。
- 延迟发布后台图片的 artifact，只有实际写入成功后才进入现有产物登记与预览链路。
- 让正式 Job 和允许副作用的算子调试执行拥有已声明的能力；纯预览和纯计算试运行不开放真实后台副作用。

## 不做什么

- 不增加算子基类、不新增必需算子方法、不改变 executeNode() 签名。
- 不强制所有算子异步化，不改变现有同步算子的执行契约。
- 不把 YOLO 推理、图像计算等 CPU 密集型任务放进这条 I/O 工作线程。
- 不合并现有 AsyncPreviewSnapshotWriter 或 RuntimeJsonlLogWriter。
- 不建立跨 Job / 跨进程的全局线程池，不引入图片 IPC 传输。
- 首版不改 DetectionArchive、ResultWriter、SQLiteWriter 的同步语义；不增加通用文件事务、自动重试、断电恢复或任务持久化。
- 不新增 RPC、Designer 配置页面或 Web 功能。

## Impact

需要小幅扩展插件注册描述：PluginManifest 增加可选 runtimeCapabilities，manifest validator 识别受支持的能力；PluginRegistry 继续沿用现有 scanRoots / PluginDescriptor 流程。OperatorMeta 不增加对应字段，因为运行时能力属于 manifest 声明，不是算子端口或参数元数据。

主要改动落在 Runtime Job worker、WorkflowRunner、OperatorExecutor、后台任务服务、事件发布和 artifact 完成登记，以及 ImageSaver 的 manifest、参数声明与实现。旧 manifest 缺少 runtimeCapabilities 时按空列表处理，不需要迁移。

基准源码：main @ b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd。本计划先作为独立 OpenSpec change 评审，未包含正式源码实现。
