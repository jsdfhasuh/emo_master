# 设备失败诊断补丁独立审查

日期：2026-11-06。基线：`cdf1250ccc41b78f79b25b87e6db197809875384`，审查对象为其上的未提交改动，文件指纹见末尾。仅修改本报告，未修改产品代码或测试。

## 结论

**当前版本没有发现未解决的阻断问题。** 初版 Windows 空闲断开误报已反馈根代理，修正后已独立验证。UNKNOWN、取消回执、设备所有权和不重放控制流保持原语义。保留一项有确定性辅助证据的非阻断退出边界，不能宣称日志绝不影响桥线程退场。

## 具体发现

### 已修正：Windows 空闲关闭曾被误报为失败

初版 `ipc.py` 仅排除无已解码请求时的 `EOFError`；`communication/pipe_io.py:20–32` 的 Windows overlapped 路径会以 `OSError` 报告 broken pipe，例如 109。正常 worker 在 `worker_main.py:188–194` 关闭 DeviceProxy，因此该分类会制造 `device.ipc.failed` 错误日志。

根代理已在 `ipc.py:107–111` 将 EOF、BrokenPipeError、ConnectionResetError 和 Windows 109 纳入空闲对端关闭判定，且保留 `request is None` 条件。已解码请求的发送错误仍被记录；没有吞掉其未知回执，也没有增加重试。本次为 Linux 上的 Windows 错误分支注入验证，不是原生 Windows 验收。

### 非阻断：连接已关闭后，日志仍可能延迟桥线程退出

`ipc.py:122–131` 先关闭连接，再在桥线程同步调用 `onTransportError`；`close():133–137` 仍须 join 整个线程 3 秒。`manager.py:572–575` 的回调最终进入同步 `EventStore.append`。因此，这一安排保证连接关闭不等待日志，不能保证桥线程退场不等待日志。

辅助检查使用真实 `DeviceManager.openWorker`、Pipe 和 EventStore，仅人为持有 EventStore 的顺序锁并注入一次回复发送失败：父连接已关闭，`bridge.close()` 在 **3000 ms** 后抛出 `E_DEVICE_RELEASE_PENDING`；释放顺序锁后桥线程正常退出，`device.ipc.failed` 正常进入全局事件。没有发现生产调用链新增锁环。此证据依赖传输失败与日志锁长占用叠加，未证明现场自然发生，按 AGENTS.md 记录为非阻断边界；不据此扩展日志框架或放松所有权退场门槛。

## 其余审查结果

- **锁顺序：**`executor.py:158–169` 的诊断函数不新增锁；超时判定复用已有 condition。manager 的阶段更新只操作本次调用的字典。`diagnostics.py:85–110` 在释放设备诊断锁后进入 EventStore，父桥报告回调不获取 manager/executor 锁。原有 native completion 回调在 executor condition 内发布日志，本补丁没有新增这层锁依赖。
- **回执与归属：**`executor.py:126–156` 保留取消、已完成 Future 的真实结果优先及 UNKNOWN 分支；只补时间快照及消息措辞。`manager.py:345–357, 396–406` 使用 `setdefault` 附加阶段，未改 code/outcome、future 完成、quarantine、owner 释放或重放策略。DeviceProxy 的回执转换没有改动。
- **阶段准确性：**driver 返回后才进入 frame_publish，publish 返回后才进入 frame_observer；捕获 DeviceError 时复制快照，finally 的 cleanup/finished 更新不会回写已捕获阶段。调用者等待超时的状态与阶段各有 observation 时间，表达观察事实，不足以单独归因于 native driver 卡死。IPC 的 send 阶段涵盖回复 JSON 编码与实际发送，不表示请求已成功送达。
- **实际导出链路：**`manager.openWorker` 固定 owner 的项目 scope，父桥回调进入 DeviceDiagnostics；其 `_publishRows` 发布到 `EventStore('__runtime__')`，既有 RuntimeService 接线连接全局持久化与 JSONL writer（`grpc_server/service.py:237–239`）。新测试通过真实 manager/父桥/Pipe 注入发送错误，验证 scope 不随后续 manager.scope 变化、全局事件身份以及磁盘 JSONL 中的 requestId 和 phase；不是直接构造日志行冒充父桥。`diagnostics.py:78–84` 保留有界的 deviceWait/deviceAction/deviceIpc，JSONL 的 device 事件投影不删除这些 payload。

## 本次独立验证

| 检查 | 结果 |
|---|---|
| `pytest -q tests/runtime/test_device_failure_observability.py -k 'IdlePeer or SendFailure or BridgeFailure'` | 4 passed，4 deselected，0.28 s |
| `pytest -q tests/runtime/test_device_failure_observability.py::testIdleWindowsPipeClosureIsNotReportedAsDeviceFailure` | 1 passed，0.27 s |
| 真实父桥 + EventStore 顺序锁辅助检查 | 连接先关闭；3000 ms 时 RELEASE_PENDING；解锁后退出并发布 |
| 四个设备产品文件 `git diff --check` | 通过 |

未重跑根代理已执行的完整设备定向套件，未运行长 Runtime/Web 负载、原生 Windows、Qt UI 或硬件验收；以上 5 项是本审查独立运行的定向用例，不代替后续集成结果。历史 93 秒与 double-start 原因仍未由本补丁解释。

## 审查文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `src/emo_master/apps/runtime/devices/executor.py` | `0ecec180af3d98a653eee7b8ea1d2ece6ab852f797a54883f4c751b55954c6bc` |
| `src/emo_master/apps/runtime/devices/ipc.py` | `c6982348dcee32f04cb795c3c96cd844deeefeb1bec57c0e04ae32ae1f5f43b9` |
| `src/emo_master/apps/runtime/devices/manager.py` | `b7d8415b734175ec965642036966a191551266a120c8695d626ded76bc9ddda8` |
| `src/emo_master/apps/runtime/devices/diagnostics.py` | `d9769dee111170f06d3c8ce765243fe9dc879824c1b01c2cca8ce1fffa351ad0` |
| `tests/runtime/test_device_failure_observability.py` | `b1be8ed3bbd952eae1a2a7db41121e50bd57aa0b7e6c72e4d33a67c66449ef47` |

## 最终发送预算恢复与兼容性补充（2026-11-06）

### 回复序列化不得提前消耗发送预算

根代理最终 diff 复核发现，上文所审版本把 send deadline 的计算放到了 `json.dumps(reply).encode()` 之前。基线调用按参数求值顺序先序列化、最后才计算 `time.monotonic() + IPC_TIMEOUT`；提前计算会把序列化或该处的调度暂停计入原 2 秒发送预算。**上文关于 IPC send 阶段涵盖编码的描述属于初轮版本，不适用于最终版本。**

独立脚本 [reply-deadline-review.py](reply-deadline-review.py) 分别加载固定基线与工作树中未修改方法体的 `DeviceBridge.run`，使用真实 Pipe 和真实 `pipe_io.sendBytes`，只控制模块局部时钟及 JSON 序列化暂停，没有 sleep 或 spawn。

| 控制场景 | 初轮补丁对照 | 恢复边界后的对照 |
|---|---|---|
| 无暂停 | 基线与补丁均收到回执 | 基线与最终实现均收到回执 |
| 序列化处推进 2.25 秒 | 基线进入发送时剩 2.00 秒并成功；补丁剩 −0.25 秒，TimeoutError / EOF | 两版均剩 2.00 秒并成功 |
| 实际发送阶段推进 2.25 秒 | 两版均 TimeoutError / EOF | 两版仍均 TimeoutError / EOF |

各场景 `execute` 都恰好一次，没有重放。原失败对照 [reply-deadline-review.json](reply-deadline-review.json) 保留；修正后以 `--expect restored` 执行的 [reply-deadline-review-restored.json](reply-deadline-review-restored.json) 另存，退出码 0、耗时 0.28 秒。最终实现先记录 `encode_reply` 并完成序列化，再记录 send 起点和原 2 秒 deadline。根代理另补正式的三参数回归 `testReplySendBudgetStartsAfterSerialization`，没有扩大超时常量或增加重试。

### 周期取消兼容性与整合状态

完整 Runtime 初轮由根代理执行，结果为 **2966 passed、7 skipped、1 failed**。唯一失败是 `tests/runtime/test_startup_cancellation.py::testCleanupCancellationCannotReplaceOriginalCycleFailure` 第 276 行：普通 `CancellationRequested` 清理不得增改主异常 diagnostics。根代理保持原测试不变，将 scope 收窄为：普通取消保留原行为，仅 `DeviceError` 清理追加 `cycleCleanup`。只读复核确认原 UNKNOWN、requestId 和异常对象仍被保留。

根代理报告后续 **33 项快速复验全部通过**（设备观察 11、startup cancellation 11、scope 11）。本次文档补充时更大受影响 76 项仍在收尾，最终完整 Runtime 结果由根代理维护，不在此提前记为通过。本次只更新文档，没有再次运行测试；上文独立 5 项与同步日志辅助检查仍是各自当时的记录。

**最终源码归属：**上方源码 SHA-256 表与初始 manifest/红绿回执均属于其初轮补丁或当时的审查快照，保持原样，不作为最终源码清单。最终源码哈希以本轮证据根目录的 `integration/runtime-suite-source-hashes.json`（从本目录为 `../integration/runtime-suite-source-hashes.json`）为准。root 主 README、设备 README 与最终整套结果由根代理维护。
