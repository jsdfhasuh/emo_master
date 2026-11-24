# 计划复核记录

复核基准：OpenSpec 提交 4cd21655a413c370af8d9f3379a5d24fe49d1f6c；源码基准 main b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd。

## 复核结论

原计划的主方向正确：manifest 声明能力、PluginManifest 保存声明、OperatorExecutor 按声明注入 Job-scoped 后台任务客户端，不改算子基类或 executeNode 签名。

但原计划还有两处关键链路没有闭合，已在本次修订中补充：

1. **SQLite FILE_REFERENCE 提前校验。** Runner 在生产节点之后立即调用 captureBoundValue；该函数马上调用 SQLite toStorage，要求引用已经 saved=true 且文件存在。因此只在 SQLiteWriter 算子内部 wait 不可能生效。修订为 Runner 暂存 pending binding，在 SQLiteWriter 节点执行前 wait，成功后再调用原 captureBoundValue；失败生成 BoundValue 错误，不执行无效引用写入。
2. **ArtifactStore 和预览 writer 的线程/关闭顺序。** 原 Runner 会立即 registerPath、复制并计算 SHA-256；worker_main 的 terminal helper 当前先关闭 AsyncPreviewSnapshotWriter。修订为服务只产出完成记录，Runner 在自身执行线程 drain 并登记 artifact；Job terminal 顺序先关闭/排空后台服务、处理完成记录和产物，再关闭预览 writer，最后发布 terminal。

另补充了连续 Job 的任务状态记录上限、调试 class-only registry 的能力传递，以及现有 5000ms graceful-stop 强制终止边界，避免把线程收尾描述成无限等待。

## 仍需实施验证

- 生产 Job 的多算子共享、异步重叠和 Job 隔离。
- SQLite FILE_REFERENCE 延迟绑定的成功/失败路径。
- artifact.created、ArtifactStore 复制/校验、preview capture 与 job terminal 的严格顺序。
- 调试模式的能力传递与 drain。
- 任务记录达到上限时的显式过期行为。
- graceful stop 超时后不伪造保存成功。

本复核仅修订计划文档；没有修改 Runtime 源码，没有运行 OpenSpec CLI、pytest、GUI 或真实设备验收。


## 第二轮复核：新增的实现风险

继续沿固定源码检查 ImageSaver、PluginManifest 构造、OperatorExecutor、WorkflowRunner 和 Job 生命周期后，补充以下要求：

1. **异步写入不能直接写最终文件。** 当前 ImageSaver 直接打开最终路径写入；如果进程在写到一半时被现有 5000ms graceful-stop 机制终止，磁盘上可能留下半成品。后台模式改为任务专属临时文件，完整写入后原子发布最终文件。
2. **异步固定文件名会造成产物错配。** 多个 Job、循环周期或同一工作流的异步任务可能写同一个 fixed path；Runner 延迟处理完成记录时，文件可能已被后一个任务覆盖。首版后台模式要求 `namingMode=unique`，同步模式仍支持 fixed/unique。
3. **只限制队列任务数量不够。** 图像大小不一，有限个大图也可能占用大量内存。服务需同时限制待处理任务数和估算字节数，超单项上限明确报错；释放额度必须覆盖成功、失败和取消路径。
4. **未观察失败不能等到 Job 终止才发现。** 连续 Job 可能长期不结束。Runner 必须在节点边界、下一次 workflow run 开始处及 workflow 完成前检查未观察失败；显式 wait 已交给算子处理的失败不重复触发全局失败。
5. **任务结果记录不能淘汰仍被 Runtime 内部引用的任务。** pending artifact 和 SQLite pending binding 在处理完成前固定保留对应记录；依赖释放后才参与普通有界淘汰。
6. **单线程服务禁止任务递归 submit/wait。** 否则后台任务在唯一 worker 内等待自己排队的子任务会永久死锁。

上述限制已同步到 design、spec、tasks 和 acceptance。它们是为了保证文件正确性、内存有界和任务失败可见，不改变同步算子的默认行为。


7. **连续运行的空闲窗口也必须检查失败。** 源码中的 continuous cycle 间隔和 Dispatch consumer 的 IDLE 等待发生在 `WorkflowRunner.run()` 外；只在节点边界和 workflow 完成前 poll 仍会漏掉任务在空闲窗口内失败的情况。已要求这两个等待循环也定期调用 Runner 的统一完成/失败轮询入口。


8. **close 超时后必须防止迟到文件提交。** 仅把 worker 线程设为 daemon 仍不够：close 超时到 Job 进程真正退出之间，任务可能刚好恢复并写入最终文件。已增加 Runtime 注入的 TaskControl 和原子发布门禁；服务 abandoned 后，迟到的最终发布必须被拒绝。正常 Job drain 另设内部 30 秒上限，超时转为 E_BACKGROUND_TASK_DRAIN_TIMEOUT；用户主动 Stop 仍由原 5000ms Supervisor 宽限控制。
