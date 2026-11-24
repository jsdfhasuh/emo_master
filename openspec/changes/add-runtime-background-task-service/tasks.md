# 实施任务

## 1. 注册与注入

- [ ] 1.1 在 PluginManifest 增加末尾可选字段 runtimeCapabilities，默认空 tuple；validator 校验 backgroundTasks 声明。
- [ ] 1.2 通过现有 PluginDescriptor.manifest 保留声明；旧 manifest 兼容；不改 OperatorMeta、算子基类和 executeNode 签名。
- [ ] 1.3 ImageSaver manifest 声明 backgroundTasks；OperatorExecutor 只向已声明算子注入客户端。
- [ ] 1.4 调试能力由 Runtime trustedEntries() 传递；PurePreview 和 clone-safe trial 不开放。

## 2. 后台任务服务

- [ ] 2.1 新增 Job-scoped BackgroundTaskService、节点级客户端和任务引用；一条 FIFO worker。
- [ ] 2.2 实现 submit、wait、status、任务状态/结果、来源身份和任务完成/失败记录。
- [ ] 2.3 实现任务数量/字节双重限额、队列背压、单任务超限错误及有限任务记录保留；pending artifact/binding 依赖期间固定保留记录。
- [ ] 2.4 实现 TaskControl 取消检查和最终发布门禁；实现 drain、取消排队任务、内部 30 秒 drain 上限及幂等 close。

## 3. Runtime / Runner 接入

- [ ] 3.1 Job worker 创建服务并沿 WorkflowRunner → OperatorExecutor → runtimeContext 注入；同一 Job 的循环/子流程/连续周期共用。
- [ ] 3.2 Runner 在节点边界、workflow run 开始/完成前处理任务完成记录、失败和 pending artifact；ArtifactStore、ResultCollector、预览对象只由 Runtime 执行线程调用。
- [ ] 3.3 连续运行间隔和 Dispatch consumer IDLE 等待定期调用 Runner poll。
- [ ] 3.4 单算子调试、工作流调试传递已验证能力声明并在输出冻结/最终快照前 drain；保持 PurePreview 和 clone-safe trial 隔离。
- [ ] 3.5 Job terminal 前先关闭提交入口并 drain、处理剩余产物，再关闭 AsyncPreviewSnapshotWriter，最后发布 terminal event；保留现有 5000ms Stop 规则。

## 4. ImageSaver 与现有文件链路

- [ ] 4.1 增加 saveMode=sync/background，默认 sync；后台模式仅允许 namingMode=unique，fixed 明确报错；同步模式不变。
- [ ] 4.2 后台模式复制图像、提交带 estimatedBytes 的任务，写入任务专属临时文件并通过 TaskControl 原子发布最终文件；返回 SUBMITTED/taskId，不返回 saved=true。
- [ ] 4.3 Runner 延迟登记 pending artifact；成功后 registerPath、SHA-256、artifact.created 和预览捕获，失败不登记。
- [ ] 4.4 Runner 延迟 SQLite FILE_REFERENCE 绑定：SQLiteWriter 执行前 wait，成功后完成 artifact 处理并调用现有 captureBoundValue/toStorage；失败交给现有 failurePolicy。
- [ ] 4.5 更新算子开发指南，说明 manifest 能力声明、提交/等待、任务函数边界和图像数据所有权。

## 5. 验证与交付

- [ ] 5.1 运行 manifest 注册与注入测试：旧 manifest、有效声明、非法声明、未声明算子不可访问。
- [ ] 5.2 运行后台服务测试：非阻塞提交/FIFO/wait/失败/背压/取消/任务记录保留/来源身份；使用 Event 控制时序，不新增长时间 sleep 或压力测试。
- [ ] 5.3 运行 Job 集成测试：多算子共享、不同 Job 隔离、连续/Dispatch 空闲期间失败发现、Job drain 与 terminal 顺序。
- [ ] 5.4 运行 ImageSaver 和 Runner 集成测试：同步回归、后台 unique 命名、源图快照、临时文件原子发布、artifact 延迟登记、SQLite FILE_REFERENCE 成功/失败。
- [ ] 5.5 覆盖调试模式能力传递和 PurePreview/clone-safe trial 隔离。
- [ ] 5.6 运行受影响的现有回归及 OpenSpec strict validation；记录固定源码 SHA、实际结果和未执行项。不新增全算子遍历、现场设备或极限性能测试。
- [ ] 5.7 更新计划状态和交付记录；推送时附实际执行角色的 ChatGPT Web / Codex / dot 评论。
