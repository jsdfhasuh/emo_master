# F2 / F4 尚未闭环因果的只读交叉复核

日期：2026-11-06。用途：本轮推送说明的事实核对。

## 结论

**现存证据能够锁定 F2 的设备等待超时分支、同请求随后完成，以及 F4 两个独立 watchdog 的终止分支；不能唯一锁定导致这些分支发生的底层停顿原因。** 本轮已确认并修复的局部代码缺陷、补充的失败诊断，可以分别交付；不能因此把历史 93 秒停顿或 F4 双任务启动失败写成已经闭环。

本次只读原始 JSONL、以 SQLite URI `mode=ro` / `mode=ro&immutable=1` 查询原数据库，并核对固定源码及当前诊断补丁。没有运行 pytest、Runtime、spawn、压力或长负载测试，也没有修改产品代码。相关产品文件在原证据版本 `f308c87b620461af0b48a00948f9b916b4d399d1` 与修复基线 `cdf1250ccc41b78f79b25b87e6db197809875384` 之间无差异。

以下时间均为 **2026-11-05 UTC**。E1–E7 是文末原始证据索引，S1–S7 是源码索引。

## 1. 时间和序列的解释边界

原数据库的 `jobEvents.timestampMs` 对 worker 心跳保留的是 worker 生成事件时写入的时间。`timestamp` 文本由父侧 `SqliteStore.appendJobEvent()` 内的 `_utcNow()` 生成，位于获取 SQLite connection、`BEGIN` 和 INSERT/commit 之前。因此它不是 commit 完成时间。两者相减能显示事件到达持久化调用前的延迟，不能直接称为“这一条 SQL 写入耗时”。普通批次路径同样在父侧插入时分配记录序号。〔E1；S3〕

`sequence` 由父侧 EventStore / SQLite 持久化接收时分配，正式持久化路径使用 `MAX(sequence) + 1`；它不是 worker 每次尝试发心跳时的序号。F2 consumer 的 1–3184、root 的 1–2763 完整，只证明保留的父侧记录没有号段缺口，**不能证明 producer 从未丢弃诊断心跳**。`heartbeatLoop()` 先调用独立共享单元的 `publish()`，再以 `put_nowait` 提交诊断事件，队列满时可丢事件；terminal 阶段的 `eventsStopped` / emission lock 也影响诊断事件。日志生成间隔不能替代原始共享 cell 采样。〔E1；S2、S3〕

## 2. F2：可以锁定什么

身份：root `edf8ba4b-a3bb-4e46-a513-e21a39bf301e`，consumer `469e0cdc-6a40-4717-9d38-50fddaa75cb5`；request `3c15134fe14d495d88b953f3e582ed83`，cycle `b9ecf359a59c4d2ea208f1be0adb3221`，`iterationPath=[142]`。〔E1、E2〕

| 观察 | 时间 / 数值 | 原始位置 |
| --- | --- | --- |
| camera `node.started` | 15:48:34.394 | E1 eventId 13644，root seq 2753 |
| 相同 request 已在设备 active 槽，queued=0 | 被动记录起始时间 34.482、34.549；其后 group snapshot 时间为 34.482、34.553 | E3 行 7109–7110 |
| consumer 心跳生成，但父持久化调用很晚才取得 timestamp 文本 | 生成 15:48:34.549，父侧 timestamp 15:50:08.018743，差 93469.743 ms | E1 eventId 13658，seq 3179 |
| consumer 下一条保留心跳 | 生成 15:50:07.904；两条保留事件生成时间差 93355 ms | E1 eventId 13660，seq 3180 |
| root 相邻保留心跳 | 15:48:34.470 → 15:50:07.905，差 93435 ms | E1 eventId 13657、13659，seq 2754–2755 |
| camera `node.failed / E_DEVICE_OPERATION_TIMEOUT` | 15:50:07.995；从节点开始到此事件相差 93601 ms | E1 eventId 13661，root seq 2756 |
| 同 request / cycle 的 `device.action.completed` | 15:50:08.019 | E2 原 Runtime JSONL 行 11958 |
| root FAILED；consumer COMPLETED | 15:50:08.077；15:50:09.711 | E1 eventId 13667、13671 |
| 首个保留的全执行资源退休样本 | 15:50:09.938056，ownedJobs / processes 均空 | E3 行 7124 |
| 后续 UI Stop 请求完成 | 15:51:20.023，elapsed=154 ms，估算请求开始 15:51:19.869 | E4 gateway 行 1843 |

### 已确认的因果层次

1. **该错误来自执行期限等待分支。** 原消息 `operation deadline expired; driver still owns device` 对应 `DeviceExecutor.wait()` 的 `activeExpired` 分支：operation 处于 EXECUTING，执行 deadline 已过，取消请求后 Future 仍未完成，因此返回 `UNKNOWN`。这是调用者当时没有可确认结果，不是 `E_DEVICE_WAIT_TIMEOUT` 的排队失效。相同 request 在停顿前已被观察为 active，进一步说明不能把整段现象解释为请求尚未获准进入设备执行槽。默认执行预算为 5 秒；原设备 policies 为空、模拟曝光配置为 1 秒。〔E1、E2、E3；S1〕
2. **错误消息不能定位到 native driver。** 该 operation 的 `perform()` 包含连接、`driver.execute()`、frame publication、frame observer 和清理。节点开始时间不是执行 deadline 的精确起点，节点失败事件也不是 waiter 判定时刻。即使停顿前已观察到 active，仍没有同 request 的分阶段时钟，无法把 93.601 秒分配到等待观察、驱动、图像发布、观察回调、IPC 交付或后续错误发布。〔S1、S2〕
3. **晚完成属于原请求，没有改写失败业务结果。** 同 request/cycle 的 completed 回执在错误后记录，consumer 业务表仍为 142 条、source index 为 0–141。原代码先完成 Future，再执行 completed 日志回调，因此这条完成日志自身的慢写不能直接让同一个已完成 Future 继续保持 pending；但完成日志的时间也不是精确 driver-return 时间，24 ms 的日志差值不能当作原生执行剩余时长。模拟驱动的曝光循环可在恢复时先越过循环结束条件，再返回图像，说明 UNKNOWN 后晚完成在机制上相容；这没有解释为何现场出现长停顿。〔E1、E2、E6；S1〕
4. **后续 UI Stop 不是先发生失败的触发命令。** 失败及资源退休都早于 gateway 中该 Stop。这里并不否认超时分支自行设置 operation cancellation；不能把内部取消与随后用户 Stop 混为一件事。〔E1、E3、E4；S1〕

### 仍不能作出的归因

同期 DELETE view 用时 93471 ms、POST view 用时 94761 ms，均返回 200；被动记录 7110→7111 的起始单调时钟相隔 93528.003161 ms。这说明同窗口有多条路径出现长延迟，**没有证明同一个原因，也没有证明整个主机或进程暂停了相同长度**。被动 snapshot 在多个步骤取得多个锁，只有开始时间，没有各步骤完成时间。〔E2、E3、E4〕

POST view 的单次 unary RPC 有默认 5 秒预算，但 HTTP 操作还有 target lock、线程池等先行等待，且没有整体 deadline；DELETE view 包含本地订阅任务取消后的 `gather`，并不等同于一次 Runtime unary RPC。单靠一次慢 HTTP 或一把候选 Runtime 锁，不能把全部 93 秒链路锁定。〔S6〕

现存材料也不能把原因确定为“SQLite 慢”“GIL/CPU 饥饿”“驱动卡住”或“设备/Dispatch 队列堵住”。15:24 的环境压力记录早于该窗口，且当时命名空间看不到 Runtime/Web PID，不能据此认定环境根因。此前描述过的“真实 EventBridge + 慢 SQLite + 健康 producer”对照，其 `deep-*` 原始产物当前已丢失；**本报告不把它纳入可复核的确定排除清单，本轮也没有重跑。**〔E7；S1–S3〕

## 3. F4：两个 watchdog 判定，仍缺判定输入

root `f8e0840c-17c9-4c2a-b446-ee7b7063ed69`（pid 661880）与 consumer `f6ab6a28-3eeb-4eed-8a8d-06d64515afbe`（pid 661877）。〔E1〕

| 事件 | 时间 | E1 eventId / sequence |
| --- | --- | --- |
| root `dispatch.group.started` | 15:59:50.430 | 16849 / root 2 |
| consumer `process.terminated` | 15:59:56.367 | 16850 / consumer 1 |
| consumer `job.failed / E_HEARTBEAT_TIMEOUT` | 15:59:56.396 | 16851 / consumer 2 |
| root `process.terminated` | 15:59:56.410 | 16852 / root 3 |
| root `job.failed / E_HEARTBEAT_TIMEOUT` | 15:59:56.415 | 16853 / root 4 |
| `dispatch.group.retired` | 15:59:56.435 | 16855 / root 6 |

两条 `process.terminated` 在 SQLite 保留，Runtime JSONL 没有投影它们；不能只查 JSONL。两 Job 的 `startedAt` 均为 null，完整保留序列为 root 1–6、consumer 1–2，没有 worker `job.process.started`、`job.started` 或 `process.heartbeat`。〔E1、E2〕

**19 ms 是两条 job.failed 的事件时间差；两条 process.terminated 记录相差 43 ms。二者都不是最后心跳间隔或实际死亡时刻。** 从 group.started 到两条失败分别为 5966 / 5985 ms；jobs 约 6044 / 6012 ms 的 duration 从 acceptedAt 计时，不能拿它代替 watchdog 的样本年龄。〔E1；S2〕

可以进一步锁定：

- 两个 `process.terminated / E_HEARTBEAT_TIMEOUT` 都来自各自的 `checkHeartbeat()` 终止分支，表示监督器各自观察到进程仍活着并执行了终止路径。`RunGroup.workerRetired()` 不会把普通同伴退场改写为这个错误码，故不是“一个任务普通结束，另一个被自动连带标成相同失败”。〔S2、S5〕
- 两次 watchdog 的 cell 读取、比较、终止和事件写入由同一个 supervisor lock 串行保护。若另一 worker 的共享 cell 始终健康更新，单个观察者的一次读后暂停不能独自满足后一个任务的超时条件；这个局部反例不能代替 F4 全链路解释。没有当时两次读取值，仍不能认定两个判定究竟是真实失联、读取不可用或观测时序问题。〔S2〕
- group.started 只证明父进程完成组提交和 `startJob()` 调用，不能证明子进程已完成 spawn 导入并进入 `runJobProcess()`。`runJobProcess()` 还需执行入口导入/代理构造、提交 job.process.started，然后启动 heartbeat 线程。诊断事件缺失不能单独定位卡在哪一步，更不能等同于共享 cell 从未更新。〔S2〕

F4 被动样本 8651、8652 起始时间相隔 4789.263754 ms，前者记录两个 PID alive/STARTING，后者无 ownedJobs。其多阶段读锁和缺少结束时间，同样不足以证明系统暂停、具体锁等待或 worker 停顿长度。两条原 job.failed payload 均为 `{}`，没有共享 cell、lastGood 或原始判定时钟。〔E1、E3〕

## 4. 新诊断能消除哪些歧义

| 新字段 | 下次可据此判断 | 仍不能由单条记录判断 |
| --- | --- | --- |
| `deviceWait` | 对 F2 这类串行设备路径，用 submitted/started/deadline/observed 的 monotonic 时间及 state 重算排队、执行期限和调用者观察的迟到程度；关联原 requestId | 谁阻止 waiter 获得执行机会；错误判定后在 IPC/worker 发布又等了多久；这些字段不是线程栈 |
| `deviceAction` | 分辨被观察的最后阶段：queued/connect/driver/frame_publish/frame_observer/return/cleanup/finished；已有 driverReturned/framePublished 标记时可确认相应边界已跨过 | “当前停在 driver”不等于硬件故障；可能是该线程未获调度。它是错误时的阶段快照，不是完整阶段轨迹；与 deviceWait 各有观测时间，不应当作同一原子采样 |
| `deviceIpc` | 父桥实际关闭后记录 poll/receive/encode_reply/send 阶段、具体传输异常与短消息、phase start/deadline/observed；已解码请求时保留 request/cycle 身份，补上 worker 单见 EOF 时缺失的父端原因 | encode_reply 与 send 分开，原有 2 秒发送预算在编码结束后起算；进入 send 仍不等于成功交付。请求尚未解码时身份可能只有 owner。正常空闲退出/明确取消不报此错误，缺记录不能反推传输从未出问题 |
| `heartbeat` | 用 pid、clock、observation/current/sample/lastGood、timeout/interval 复算 watchdog 条件，分离旧样本、未来样本、None、读窗口延迟，并确认 monotonic 或 legacy wall 路径 | `sampleMs=None` 尚不能区分抢锁失败与底层读异常；lastGood 可能是启动基准或已接受 pulse；没有启动阶段/栈便不能解释 worker 为什么未推进 |

**`heartbeat.seen` 仅表示父侧消费过 `process.heartbeat` 诊断事件，不表示 cell 曾发布或曾采纳样本。`seen=false` 不能单独证明 worker 没启动。** 设备新增阶段时间使用 monotonic；心跳字段则按 `heartbeat.clock` 区分 monotonic 与 legacy wall 路径。将 monotonic 与事件 epoch 时间对齐时，应使用同机、同窗口采集的 wall/monotonic 配对，不能直接相减。〔S1、S2、S4；本目录 `device-observability/README.md`〕

新增诊断不会回填原事故缺失的输入，也没有新增调度/锁等待观测。它们可以定位下次超时所处的阶段和读取事实，但不能提前承诺一定揭示底层停顿原因。

## 5. Dot 最少需要额外保留什么

限定同一个 Runtime、一次原失败操作窗口，保留以下两组材料即可开始下一轮归因，不把重跑已通过的完整可见验收表当作这些材料的替代：

1. **完整身份和原始失败窗口。** 固定实际源码 SHA、runtimeInstanceId、group/Job/PID、request/cycle/iteration；保留从启动/最后正常进展到失败、晚回执和退休的 Runtime 原始 JSONL、SQLite、gateway 请求以及父/子进程 stderr。包含 WARN/INFO、全局 `__runtime__` 设备事件及上述四类 payload，不只导出 ERROR 或截图。F4 的 process.terminated 只能在本包 SQLite 中看到，正说明完整事件源有必要。〔E1–E4〕
2. **一份对应这些真实 PID 的短窗口观测。** 必须在能看到 Runtime 与两个 worker 的命名空间内采集，并使用配对 wall/monotonic 时间：
   - F4 保存父 process.start 返回、子入口/heartbeat 线程开始或首次 cell 发布的阶段边界，以及终止前 parent watchdog/bridge 和两个 worker/heartbeat 线程的栈或等待位置。必要时补 `cell.read` 返回 None 的原因，不能只保留布尔 seen。
   - F2 保存 device waiter、device I/O/观察回调、相关 EventBridge 的等待位置；若 view 请求再次同窗变慢，保留 gateway 对应 asyncio task 的当前 await 阶段。线程栈之外，配合前后两个同 PID 的 CPU/调度状态样本，才能区分代码等待与没有获得执行机会。
   - 现有被动 snapshot 增加开始/完成或各阶段采样时间，或由不等待同一组 Runtime 锁的旁路观察补齐；否则下一次记录空档仍不能说明空档本身的原因。采集应覆盖 watchdog terminate 前，事后已退出 PID 无法补栈。

这些是当前缺失的时间/等待关系，不是新增根因断言。没有证据支持现在全面放宽超时、把 FAILED 改成成功或自动重放 UNKNOWN 操作。

## 6. 可用于推送说明的事实表述

本轮修复已确认的局部恢复缺陷，并新增设备等待、执行阶段、父桥传输和心跳判定诊断。原 F2 可确认执行等待超时后同一请求晚完成，原 F4 可确认两个独立 watchdog 分别终止任务；造成 93 秒停顿及双任务启动失败的底层触发因素仍缺现场判定输入和线程/调度观测，暂未唯一归因。本次交付不将这两个历史问题标记为根因闭环，也不改变 UNKNOWN 不重放及设备/进程所有权边界。

## 证据和源码索引

原证据根目录：`../evidence/emo_master_PR11_f308c87`（相对修复工作树），原报告固定源码为 f308c87b。现有派生诊断用于定位，关键事件已回查原 SQLite/JSONL。

- **E1**：原包 `runtime/runtime-snapshot.sqlite3`，`jobs`、`jobEvents`；本报告给出具体 eventId、sequence 和 Job。
- **E2**：原包 `raw-logs/runtime/runtime-20261105-151407-591614-0001.jsonl`；同请求 completed 为行 11958，F4 JSONL 为行 14703–14708。派生索引：`../diagnostics/device/f2-device-evidence.json`、`../diagnostics/heartbeat/heartbeat-evidence.json`。
- **E3**：原包 `raw-logs/passive-state.jsonl`，行 7109–7112、7124、8651–8652；采样实现 `support-scripts/serve_runtime.py`。
- **E4**：原包 `raw-logs/gateway.jsonl`，行 1810–1811 的慢 view 请求及行 1843 的后续 Stop；原包 `验收报告.md` 的 F2/F4。
- **E5**：`../diagnostics/ROOT_CAUSE_REVIEW.txt`，前次事实和归因边界；不把其中或会话描述但原产物已缺失的试验当成本次新证据。
- **E6**：`../diagnostics/device/expired-wait-late-completion-counterexample.json`，明确标为受控时钟机制反例，非历史 93 秒重放。
- **E7**：原包 `evidence/environment-pressure-observation.json`，15:24 的采样、Runtime/Web PID 不可见和命名空间限制。
- **S1**：[DeviceExecutor](../../../src/emo_master/apps/runtime/devices/executor.py) 的 `_run()` / `wait()`；[DeviceManager](../../../src/emo_master/apps/runtime/devices/manager.py) 的 `execute()` / completed callback；[SimulatedCamera](../../../src/emo_master/apps/runtime/devices/simulated.py) 的 `execute()`。
- **S2**：[JobSupervisor](../../../src/emo_master/apps/runtime/jobs/supervisor.py) 的 `startJob()` / `_startRunGroup()` / `checkHeartbeat()`；[EventBridge](../../../src/emo_master/apps/runtime/jobs/event_bridge.py) 的 `_run()`；[worker_main](../../../src/emo_master/apps/runtime/jobs/worker_main.py) 的 `runJobProcess()` / `heartbeatLoop()`；[HeartbeatCell](../../../src/emo_master/apps/runtime/jobs/heartbeat.py)。
- **S3**：[EventStore](../../../src/emo_master/apps/runtime/events/event_store.py) 的 `append()` / `appendBatch()`；[SqliteStore](../../../src/emo_master/apps/runtime/context/sqlite_store.py) 的 `appendJobEvent()` / `appendJobEvents()`。
- **S4**：[设备 IPC](../../../src/emo_master/apps/runtime/devices/ipc.py) 的 `DeviceBridge.run()`；[设备诊断投影](../../../src/emo_master/apps/runtime/devices/diagnostics.py) 的 `append()`。
- **S5**：[Dispatch scheduler](../../../src/emo_master/apps/runtime/frame_dispatch/scheduler.py) 的 `snapshot()` / `workerRetired()`。
- **S6**：[Web app](../../../web_panel/backend/emo_web_panel/app.py) 的 `new_view()` / `delete_view()`；[Views](../../../web_panel/backend/emo_web_panel/services.py) 的 `remove()`；[GrpcRuntimeClient](../../../web_panel/backend/emo_web_panel/runtime_client.py) 的 unary timeout。
- **S7**：本目录 [heartbeat/scope 独立复核](heartbeat-scope/review.md)、[设备诊断复核](device-observability/review.md)，用于当前补丁语义和验证边界，不代替原事故观测。
