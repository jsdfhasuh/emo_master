# heartbeat / scope 独立只读复核

## 结论

**本次定点复核通过，未发现阻断集成的问题。** 不需要为这两处修复扩大产品改动或增加超时。

复核基于 `cdf1250ccc41b78f79b25b87e6db197809875384` 的当前工作树补丁，覆盖 `supervisor.py`、`cycle_plan.py` 和对应两组新增测试。复核时四个文件的 SHA-256 均与本目录 `manifest.json` 一致。未修改这些产品源码或测试文件。

## 心跳判断与时钟

`JobSupervisor.checkHeartbeat()` 在 `HeartbeatCell.read()` 前记录 `observation`，用它判断本次样本是否已过期；读取后的 `current` 仅作为样本不能来自未来的上界。最后有效时间依然只在 `lastHeartbeat < sample <= current` 时更新为生产者的样本值。没有把观察者的读取完成时间写回最后心跳，也没有改变既有超时、间隔或首次观察的规则。

这意味着生产者若在读后停顿期间真正停止，本次检查可能暂缓判定；**下一次获得执行机会的检查仍面对原来的生产者时间并正常超时**。该修复不承诺监督线程被暂停时仍能按墙上时间及时执行，也没有重置存活期限。新增测试中的不可读、旧值、未来值和排队心跳反例均与此约束一致。

`EventBridge._run()` 仍在调用 `checkHeartbeat()` 之前检查 `process.is_alive()`。已退出进程进入排空事件、`processExited()` 和资源退休路径，不依赖心跳年龄；读取基准变化不会使这条退出分支失效。

日志的时钟域也清楚：

| 字段 / 路径 | 实际时钟 | 复核结果 |
| --- | --- | --- |
| 共享单元的 observation/current/sample/lastGood | monotonic | `clock: "monotonic"`，没有与 epoch 时间比较 |
| 无共享单元的兼容心跳 | wall | `clock: "wall"`，`sampleMs: null`，继续使用 `nowMs()` |
| `job.failed.timestampMs` | wall / epoch | 仍由 `EventStore.append()` 生成，没有把 monotonic 时间作为事件时间 |

证据位置：`supervisor.py` 的 `checkHeartbeat()`、`event_bridge.py` 的 `_run()`、`heartbeat.py` 的非阻塞 `read()`，以及 `event_store.py` 的 `append()`。

## 周期清理与异常保留

`CyclePlanSession.scope()` 仅新增处理清理阶段的预期 `DeviceError`，沿用已有 `CancellationRequested` 处理。存在主异常时，原对象继续传播，不会由已关闭代理引发的 `E_DEVICE_CANCELLED` 覆盖原来的 `E_DEVICE_OUTCOME_UNKNOWN` / `UNKNOWN`。原 `requestId`、动作和阶段保留；拥有字典 diagnostics 的主异常会获得独立的 `cycleCleanup` 条目，嵌套周期按内到外保留各次清理失败。

以下边界没有被吞掉：

- **没有主异常：** 清理 `DeviceError` 或 `CancellationRequested` 原样抛出，不能伪装成成功退出周期。
- **正常取消：** 已观察到取消时继续跳过 endCycle，并传播原取消；在 cleanup 入口发生取消竞争时，无主异常仍传播取消，有主 UNKNOWN 时保留原 UNKNOWN。
- **意外清理故障：** `RuntimeError`、清理阶段 `KeyboardInterrupt` 不在新增捕获范围内，仍然传播，并保留主异常上下文。
- **嵌套周期：** 外层 document、compiledProject、revision、deviceScope 和 deviceIdentities 的恢复顺序未变。

跳过或失败的周期清理仍依赖已有 owner retirement。只读追踪确认 `DeviceManager.retire()` 在 native operation 尚未退出时仍抛出 `E_DEVICE_RELEASE_PENDING`，只有退休完成才调用配置的 `retireOwner()` 清除周期；本补丁没有移除该检查，也没有把它改成成功。它不等于主动重放未知设备操作。

## 本次实际执行

使用现有 Python 3.10.22 环境，只运行三个独立小反例和新增 scope 测试：**14 passed in 0.41s**。原始输出见 `review-counterexamples.txt`，独立反例源码见 `review_counterexamples.py`。

| 独立反例 | 验证结果 |
| --- | --- |
| 样本 900ms，读前 1000ms，读后暂停推进至 6350ms，生产者期间不再发布 | 本次暂缓；下一次保持 900ms 原样本并产生 `E_HEARTBEAT_TIMEOUT`；事件时间仍为 epoch |
| 新鲜共享心跳，进程对象报告退出码 23 | 实际 EventBridge / Supervisor 先走 processExited，产生 `E_WORKER_CRASHED` 并完成退休；不执行心跳检查 |
| 无共享单元，wall 心跳年龄 5001ms | 正常超时；不读取 monotonic；payload 标记 wall 且 sample 为 null |

第二个反例使用测试进程替身提供 `is_alive=False` 和 exit code，执行实际 bridge / supervisor 逻辑；它不是一次新的 OS 进程崩溃验收。scope 的 11 个用例使用真实 DeviceProxy 和 multiprocessing Pipe，覆盖单层/嵌套 EOF、原 UNKNOWN 回执、显式清理错误、正常取消及取消竞争。

可复现命令（仓库根目录）：

```bash
PYTHONPATH=src:. /workspace/scratch/4e0e3d24442d/review-f308/venv/bin/python -m pytest -q -s docs/testing/2026-11-06-runtime-recovery/heartbeat-scope/review_counterexamples.py tests/runtime/test_cycle_plan_scope_cleanup.py
```

本次未重跑作者已记录的 32 项新增回归或 36 项已有回归全套；阅读了其红灯/绿灯记录并核对源码摘要。没有运行长时间 Runtime/Web 负载、自然 CPU 停顿、人工 UI 或真实设备验收。上述结论限于本轮两处补丁及其直接边界，不将其扩展为历史所有异常均已解释。

## 最终兼容性收窄复核补充（2026-11-06）

本补充修正上文对初轮 `CancellationRequested` 兼容性的判断。根代理随后执行完整 Runtime 初轮，得到 **2966 passed、7 skipped、1 failed**；唯一失败为 `tests/runtime/test_startup_cancellation.py::testCleanupCancellationCannotReplaceOriginalCycleFailure`。原测试第 276 行严格要求主异常的 diagnostics 保持 `{'original': True}`。初轮合并异常分支为正常取消追加了 `cycleCleanup`，因此不完全等价于既有取消行为；此前定向复核通过不能替代该完整套件断言。

原测试没有修改。对根代理收窄后的代码再次只读复核，确认 `except CancellationRequested` 已恢复独立分支，有主异常时不增改 diagnostics，无主异常时继续传播；新增的清理诊断仅在 `except DeviceError` 中追加。保留原 UNKNOWN、requestId、异常对象及嵌套恢复的修复仍然有效，没有扩大意外异常的捕获范围。

根代理报告收窄后的快速复验为 **33 passed**（设备观察 11、startup cancellation 11、scope 11）。本次补充时更大受影响 76 项仍在收尾；最终完整 Runtime 结果由根代理维护，本报告不提前记为通过。本次仅更新文档，没有重跑测试；上文 `14 passed`、初轮红绿记录及其来源均保留。

IPC 的相关计时兼容性也已单独闭环：回复序列化记录为 `encode_reply`，完成后才启用原 2 秒 send 预算。原对照脚本以 `--expect restored` 复跑，退出码 0、耗时 0.28 秒；序列化暂停不再提前耗尽发送预算，真实发送阶段暂停仍然超时，每次请求只执行一次。见 [原失败对照](../device-observability/reply-deadline-review.json) 与 [独立 restored 回执](../device-observability/reply-deadline-review-restored.json)，两份记录均保留。

**版本边界：**上文 SHA-256 与本目录 `manifest.json` 的一致性描述针对初轮补丁，不代表这次收窄后的最终源码。初始 manifest 及原始红绿、复核记录不覆写；最终源码哈希以本轮证据根目录的 `integration/runtime-suite-source-hashes.json`（相对路径 `../integration/runtime-suite-source-hashes.json`）为准。
