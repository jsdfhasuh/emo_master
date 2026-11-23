# 实施任务

## 1. 后台任务服务

- [ ] 1.1 新增 Job-scoped BackgroundTaskService、节点级 BackgroundTaskClient 和任务引用；首版一条 FIFO 工作线程、有界队列。
- [ ] 1.2 实现 submit、wait、状态查询、任务结果、完成/失败事件和提交时运行身份冻结；不复用已关闭的 OperatorLogger。
- [ ] 1.3 队列满时实施可取消背压；实现正常 drain、取消待执行任务、等待当前任务结束和幂等 close。
- [ ] 1.4 明确未观察任务失败的 Job 终态行为；原始工作流错误优先，后台清理错误作为附加诊断。

## 2. Runtime 接入

- [ ] 2.1 在 Job worker 创建服务，并沿 WorkflowRunner → OperatorExecutor → 每次 executeNode runtimeContext 注入；同一 Job 的循环/子流程/连续周期共用。
- [ ] 2.2 确认并覆盖所有实际执行入口：正式 Job、单算子调试、工作流调试；直接构造执行器的测试/工具通过显式注入启用。
- [ ] 2.3 保持 PurePreviewExecutor 和工作流 clone-safe trial 无后台提交能力，验证纯预览不能产生延迟副作用。
- [ ] 2.4 将服务收尾放在 Job terminal event 之前；确认 Job workspace 不会在活动任务结束前清理。

## 3. ImageSaver 首个接入

- [ ] 3.1 为 ImageSaver 增加 saveMode=sync/background，默认 sync；同步模式行为和原有覆盖/唯一命名语义不变。
- [ ] 3.2 将后台模式的图像快照、编码、写入放到工作线程；返回 SUBMITTED 和 taskId，不返回 saved=true。
- [ ] 3.3 让后续算子可按 taskId 显式 wait；更新算子开发指南，说明异步文件依赖和图像所有权。
- [ ] 3.4 让 WorkflowRunner 延迟处理待完成文件的 artifact 登记、复制/哈希和预览，成功后发布 artifact.created，失败不发布。
- [ ] 3.5 保持 DetectionArchive、ResultWriter、SQLiteWriter 及所有文件读取操作的现有同步语义。

## 4. 验证与交付

- [ ] 4.1 覆盖任务提交/返回、FIFO、等待结果、异常传播、队列背压、取消、正常 drain、Job 隔离和任务事件身份。
- [ ] 4.2 覆盖 ImageSaver 同步回归、后台提交、文件最终内容、失败事件、artifact 延迟登记，以及 SQLite 等立即文件消费者仍需显式等待的行为。
- [ ] 4.3 覆盖正式 Job、单算子调试、工作流调试、纯预览和 clone-safe trial 的能力边界。
- [ ] 4.4 运行受影响的现有测试和 OpenSpec strict validation；记录固定源码 SHA、实际测试结果、未执行项。不得把静态审查或单测通过表述为真实设备验收。
- [ ] 4.5 更新算子开发指南和 Runtime 架构说明；提交/推送时按实际执行角色附对应 ChatGPT Web / Codex / dot 评论。
