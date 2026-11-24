# 验收标准

勾选项记录 Codex 已执行的实施验证，供独立验收逐项复核。实测数据见[交付记录](delivery.md)。

本清单只覆盖本次新增能力及受影响链路，不要求全算子专项、现场设备验收或极限性能测试。

## 1. 注册与能力隔离

- [x] 旧 manifest 不含 runtimeCapabilities 时正常注册，能力为空。
- [x] ImageSaver 声明 backgroundTasks 后，PluginManifest/PluginDescriptor 保留声明，RuntimeContext 能拿到客户端。
- [x] 未声明算子拿不到客户端；未知、重复、非字符串能力值被拒绝。非法输入使用参数化测试，不拆成多套测试。

## 2. 后台服务核心行为

- [x] 用可控 Event 验证 submit 不等待任务完成、FIFO、wait/status、任务成功/失败和原始异常可见。
- [x] 用一个队列测试同时覆盖任务数量/字节限制、满队列等待、取消释放额度及单任务超限。
- [x] 验证任务完成/失败保留提交时的 Job/Workflow/Node/NodeRun 身份；未 wait 的失败在下一节点或连续/Dispatch 空闲轮询时被发现。
- [x] 验证有限任务记录保留、pending artifact/binding 期间不淘汰；过期 taskId 返回 E_BACKGROUND_TASK_EXPIRED。
- [x] 验证 close 后 TaskControl.publish 拒绝迟到提交；正常 drain 超时不发布 job.completed。

## 3. Job 与 Runner 集成

- [x] 同一 Job 两个算子共享同一 worker；两个独立 Job 不共享线程或任务记录。
- [x] 验证后台任务完成后才登记 artifact、发布 artifact.created 和捕获预览；Job terminal 晚于最后一项产物处理。
- [x] 验证连续运行间隔和 Dispatch IDLE 期间能发现未观察失败。
- [x] 验证用户 Stop 沿用现有 5000ms Supervisor 规则，不增加新的停止策略。

## 4. ImageSaver 与文件依赖

- [x] 默认同步模式的原有路径、覆盖、唯一命名、返回值和错误语义不变。
- [x] 后台模式 fixed 命名明确拒绝，unique 命名成功；提交回执是 SUBMITTED，不伪造 saved=true。
- [x] 验证源图复制后再异步保存；完整写入临时文件后原子发布，失败/取消不暴露半成品最终文件。
- [x] 验证 SQLite FILE_REFERENCE 在 SQLiteWriter 执行前等待；成功后正常写入，失败时不插入无效文件引用并遵循现有 failurePolicy。
- [x] 验证普通下游算子声明能力后可 wait 并读取文件；不需要文件的下游节点可与保存任务重叠执行。

## 5. 调试与预览边界

- [x] 单算子调试和工作流调试从 Runtime 已验证 manifest 获取能力，并在结果冻结前收尾。
- [x] PurePreview 和 clone-safe trial 不获得后台提交能力。

## 6. 测试边界

只新增/调整上述直接相关的单元测试和少量 Job/Runner 集成测试。使用 Event/临时目录做确定性验证；不新增随机压力测试、长时间 soak、全 82 个算子遍历、真实 PLC/相机测试或独立性能基准。同步模式现有回归保持原范围。
