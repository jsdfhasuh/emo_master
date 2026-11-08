# 图像导出退场补丁独立只读审查

日期：2026-11-06。基线：`cdf1250ccc41b78f79b25b87e6db197809875384`，审查对象为其上的未提交改动，精确文件指纹见末尾。仅写本报告，未修改产品代码或测试，未并行运行 spawn 或负载测试。

## 结论

**未发现阻断问题。** 当前局部修改保留 `_reap` 作为编码器读者死亡的证明：replacement 启动失败并成功回收后允许槽位继续使用；不能证明死亡时继续隔离。旧任务不会释放已排队后继的 reservation，后继各自完成 pending/credit 退场。没有新增后台或无限重启，退场记录不改写已经发布的结果。

## 代码依据

| 核对项 | 结论与具体依据 |
|---|---|
| Ready EOF / timeout 成功回收 | `exporter.py:106–132` 将启动、ready 检查和 EOF 纳入同一清理路径；异常时先 `_reap`，其成功后才记录 startupReapedNs。`_reap:206–222` 在停止、join、确认不存活以及关闭句柄后才清空 canonical process/connection。`_run:373–388` 对已清空 owner 的 replacement 失败只保留诊断，继续旧任务的正常退场，不再无条件隔离。 |
| 不能证明死亡仍隔离 | `_reap:217–218` 仍在进程存活时抛错并保留引用；restart catch 发现 process 非空就继续抛给 `396–401` 的隔离分支。`_quarantine:165–171` 保持 busy 并收走可用 credit；未证明退出的后继 reservation 不被归还。没有清空 owner 或绕过存活检查。 |
| 后继 pending / raw credit 各归属一次 | `_releaseLane:224–234` 记录 laneReleaseAttempted，删除当前任务的 pending，归还一份 credit 并设置 laneReleased。旧任务在 verified snapshot 或成功 reap 后已完成自己的释放，`390–395` 不再动其已释放 lane；仅把 active 置 false、busy 设为剩余 pending 的布尔值。因回调而入队的后继保留自己的 pendingDescriptors 和 reservation，之后由自己的 `_run` 处理。隔离分支故意不归还后继 credit。 |
| 原预算及有限重启 | `265–278` 仅让仍在原 deadline 内、且没有现存 process 的 descriptor 发起一次本地启动；启动后再次检查同一 deadline，没有续期。已过期队列项不 spawn、不 dispatch，按 not_dispatched 释放自己。`readerWasReady:249` 配合 `373` 阻止从无 reader 开始的失败本地启动在 finally 再次 eager restart。原 ready 10 秒、图像 500 ms 与 bounded reap 参数未调整；后续尝试必须由另一个新 descriptor 驱动。 |
| 已发布结果不变 | `358–361` 在结果回调前复制 diagnostic；后续 retirement 字段写入另一字典，最终再以副本传给 retirementCallback（`402–411`）。`service.py:510–525` 只查 job 身份并追加独立事件，不进入 `_exported`、结果 store 或 `_finish`，也不写 source recovery 状态。 |
| Scope credit 与 raw credit 分离 | service 的 scope credit 仍只通过 `527–534` 的 pending 对象身份检查归还；该路径在 store lock 内调用。新 retirementCallback 不归还 scope credit，也不调用 releaseSlot。`release:610–627` 仍拒绝未闭合结果以及 busy/pending/quarantined slab。 |
| 诊断接线及生命周期 | 两个 ExportPool 创建入口（`service.py:105–108, 227–230`）均安装回调，且在图像任务 admission 前完成。最终记录包含 retirementEndedNs/ownerPhase，再清 currentDiagnostic；回调异常仅记有界 errors。`service.close:675–676` 仍 join exporter owner 后才继续处置。JSONL 只增加 `presentation.export.retirement_failed` 的精确白名单（`jsonl_writer.py:394–395`），不会扩大成功结果日志。 |

## 回归覆盖审阅

`test_export_restart_retirement.py` 的确定性测试保留真实 collector、shared memory、BoundedSemaphore、ExportPool 的 submit/spawn/reap/run、encodeWorker copy/PNG、SQLite/EventStore 与 JSONL，仅控制原生进程、Pipe 行为和时钟：

- ready timeout / EOF 后可证明 replacement 死亡：检查不隔离、pending/active/busy 收束、旧任务和后继合计两次 credit 归还；后续新图像解码验证自己的像素。
- 无法证明死亡：检查进程与后继 pending 仍保留、credit 不被额外归还、新捕获被隔离挡住。
- 新 descriptor 本地启动再次失败：检查一次新 descriptor 只有一次启动、自己的 credit 正常归还、下一次新请求可恢复。
- 退场日志：检查 WARN/ERROR、最终 ownerPhase、startup 与 retirement 字段、SQLite 与磁盘 JSONL 一致；已交给结果回调的诊断副本没有后加的 retirement 字段。
- 日志回调抛错：检查不改结果、不额外持有当前已退场 raw credit，errors 数量保持有界。

另外静态对照了既有 `test_export_diagnostics.py`、`test_failed_capture_retirement.py` 及 ResultCollector 的获取/转交/本地释放路径。未发现新逻辑依赖重置信号量、批量清空后继 pending 或修改 producer 的 credit 协议。

## 验证边界

- 本审查执行了三个产品文件的 `git diff --check`，通过；没有运行 pytest、spawn、自然负载或硬件验证。实现代理正在补充的真实 spawn 原故障链应以其独立回执为准，不记为本审查已执行。
- 日志回调是同步 EventStore 发布。异常被捕获不等于任意慢日志不会延迟 owner 线程处理后继任务/退出；与设备诊断审查中记录的同步日志边界相同，当前没有新增生产锁环或现场阻塞证据，不据此要求额外框架。
- 当前修复说明的是可证明的退场恢复缺陷，不证明原始首次编码变慢、历史 93 秒或 double-start 的根因。

## 审查文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `src/emo_master/apps/runtime/presentation/exporter.py` | `e7cc3aea1e2a989211ad7aeb76f26501d076b15d5f8a8ffa9408b76d2285cf67` |
| `src/emo_master/apps/runtime/presentation/service.py` | `a594103c12b85ef83ce23453cbff1800ce237b76f1a5f98d2a3a5f8fb34e939f` |
| `src/emo_master/apps/runtime/events/jsonl_writer.py` | `02f8b8cec05ccdb47f511d60b9b7bbd0d6d0a3b59e477db25ec29dfbaf6227c6` |
| `tests/runtime/presentation/test_export_restart_retirement.py` | `64f12558ad2ab77dbfa49648e33392c54ef5ee7b99470b901337502e053581d3` |
