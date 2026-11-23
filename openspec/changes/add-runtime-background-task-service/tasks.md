# 实施任务

## 1. 注册能力声明

- [ ] 1.1 在 PluginManifest 增加可选 runtimeCapabilities；缺省为空，首版识别 backgroundTasks。
- [ ] 1.2 在 validateManifestFields() 校验能力列表类型、唯一性和已支持值；未知值或格式错误按 manifest 校验失败处理。
- [ ] 1.3 通过现有 PluginDescriptor.manifest 保留声明；不增加 OperatorMeta 字段、不增加算子基类或必需方法。
- [ ] 1.4 ImageSaver manifest 声明 backgroundTasks；需要 wait 的下游算子也声明该能力，其他现有算子保持不声明。

## 2. 后台任务服务

- [ ] 2.1 新增 Job-scoped BackgroundTaskService、节点级 BackgroundTaskClient 和任务引用；首版一条 FIFO 工作线程、有界队列。
- [ ] 2.2 实现 submit、wait、status、任务结果、完成/失败记录和提交时运行身份冻结；不复用已关闭的 OperatorLogger。
- [ ] 2.3 队列满时实施可取消背压；终态记录有界保留，淘汰后 wait 返回明确的 E_BACKGROUND_TASK_EXPIRED；保留独立的未观察失败摘要，不能因记录淘汰而漏报失败。
- [ ] 2.4 实现正常 drain、取消待执行任务、等待当前任务收尾和幂等 close；未观察失败影响 Job 终态，原始工作流错误优先。

## 3. Runtime 与 Runner 接入

- [ ] 3.1 在 Job worker 创建服务，并沿 WorkflowRunner → OperatorExecutor → 每次 executeNode runtimeContext 注入；OperatorExecutor 仅对声明 backgroundTasks 的算子注入。
- [ ] 3.2 让 BackgroundTaskService 提供完成记录队列；WorkflowRunner 在节点边界和收尾阶段于 Runtime 执行线程处理任务事件及延迟产物，不从后台线程直接操作 ArtifactStore、ResultCollector 或预览对象。
- [ ] 3.3 确认并覆盖正式 Job、单算子调试、工作流调试；调试能力必须来自 trustedEntries() 的已校验 manifest，不能信任客户端自报。
- [ ] 3.4 单算子调试在输出冻结前 drain 并解析最终任务状态；工作流调试在最终快照/会话关闭前 drain，暂停期间允许后台任务继续运行。
- [ ] 3.5 保持 PurePreviewExecutor 和工作流 clone-safe trial 无后台提交能力，验证纯预览不能产生延迟副作用。
- [ ] 3.6 调整 Job terminal 顺序：后台服务先 drain/cancel，Runner 处理剩余完成记录和产物，再关闭 AsyncPreviewSnapshotWriter，最后发布 terminal event；保留现有 5000ms graceful stop 超时语义。

## 4. ImageSaver 与下游依赖

- [ ] 4.1 为 ImageSaver 增加 saveMode=sync/background，默认 sync；同步模式行为和原有覆盖/唯一命名语义不变。
- [ ] 4.2 将后台模式的图像快照、编码、写入放到工作线程；返回 SUBMITTED 和 taskId，不返回 saved=true。
- [ ] 4.3 让普通下游算子可按 taskId 显式 wait；更新算子开发指南，说明 manifest 能力声明、异步文件依赖和图像所有权。
- [ ] 4.4 让 Runner 识别 pending artifact，任务成功后才 registerPath、计算校验值、发布 artifact.created 和预览；失败不发布成功产物。
- [ ] 4.5 为 SQLite FILE_REFERENCE 增加延迟绑定：生产节点先保存 pending binding，在 SQLiteWriter 执行前 wait，成功后再调用现有 captureBoundValue/toStorage；失败生成 BoundValue 错误，不改变 SQLiteWriter 事务逻辑。
- [ ] 4.6 保持 DetectionArchive、ResultWriter、SQLiteWriter 的同步执行语义及所有文件读取操作不变。

## 5. 验证与交付

- [ ] 5.1 覆盖 manifest 缺省、声明、重复值、未知值、非字符串值和 descriptor 能力保留；未声明算子不得获得客户端。
- [ ] 5.2 覆盖任务提交/返回、FIFO、等待结果、异常传播、队列背压、记录淘汰、取消、正常 drain、Job 隔离和任务事件身份。
- [ ] 5.3 覆盖 Runner 完成记录 drain、ArtifactStore 延迟登记、预览 writer 关闭顺序、Job terminal 顺序和 graceful stop 超时边界。
- [ ] 5.4 覆盖 ImageSaver 同步回归、后台提交、源图快照、最终文件内容、失败事件、artifact 延迟登记，以及 SQLite FILE_REFERENCE 的成功等待和失败路径。
- [ ] 5.5 覆盖正式 Job、单算子调试、工作流调试、纯预览和 clone-safe trial 的能力边界。
- [ ] 5.6 运行受影响的现有测试和 OpenSpec strict validation；记录固定源码 SHA、实际测试结果、未执行项。不得把静态审查或单测通过表述为真实设备验收。
- [ ] 5.7 更新算子开发指南和 Runtime 架构说明；提交/推送时按实际执行角色附对应 ChatGPT Web / Codex / dot 评论。
