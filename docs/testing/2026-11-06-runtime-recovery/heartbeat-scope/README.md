# 心跳观测与周期清理回归

## 范围与代码依据

本目录只覆盖 JobSupervisor 共享心跳观测窗口和 CyclePlanSession 异常保留两处局部修复。基线提交为 `cdf1250ccc41b78f79b25b87e6db197809875384`，使用 Python 3.10.22。源码和测试文件的 SHA-256 见 `manifest.json`。

修改的生产文件：

- `src/emo_master/apps/runtime/jobs/supervisor.py`
- `src/emo_master/apps/runtime/project_config/cycle_plan.py`

新增的测试文件：

- `tests/runtime/test_supervisor_heartbeat_observation.py`
- `tests/runtime/test_cycle_plan_scope_cleanup.py`

未新建工程夹具；周期用例使用既有 RunContext 和真实 DeviceProxy / multiprocessing Pipe。未改动工程 schema、心跳 5000ms 超时或 500ms 间隔，未连接真实设备。

## 红灯与绿灯

| 运行 | 结果 | 原始记录 |
| --- | --- | --- |
| 修改生产代码前，新回归 | 8 failed, 24 passed，6.24s | `red-focused.txt` |
| 最小修复后，相同新回归 | 32 passed，6.02s | `green-focused.txt` |
| 已有受影响回归 | 36 passed，2.92s | `green-existing-targeted.txt` |

8 个红灯包括 7 个行为失败和 1 个新增失败诊断字段的断言，并不代表 8 个独立根因。

### 心跳观测

确定性用例让读取返回原始共享值后推进观察者时钟，并让生产者继续更新。实际进程用例只启动独立心跳发布进程，每 500ms 更新共享单元，让父进程在采样后暂停 5.35 秒：

| 版本 | 父进程观测停顿 | 检查结束时实际最新样本年龄 | 结果 |
| --- | --- | --- | --- |
| 原实现 | 5350ms | 348ms | 错判 `FAILED` |
| 修复后 | 5351ms | 348ms | 保持 `RUNNING` |

修复在 `cell.read()` 之前采集本次超时判断时间，以读取完成时间验证样本不能来自未来。最后有效样本仍保留生产者时间，不给不可读、旧值、未来值或排队事件续期。14 组边界检查保留了 5000ms 恰好通过、5001ms 失效的原有约束。实际过期时，在既有 `job.failed` 事件中加入固定字段的心跳诊断（观测时间、读取完成时间、样本、最后有效样本、超时/间隔、pid）；正常检查不新增日志。

### 周期清理

真实 Pipe 在 `frameBeginWrite` 收到请求后关闭，触发 DeviceProxy 的 `E_DEVICE_OUTCOME_UNKNOWN` / `UNKNOWN`。原实现随后由 `endCycle` 对已关闭代理的调用覆盖主异常。修复后，单层和嵌套周期都保留原异常对象、原 `requestId`、动作、阶段及 outcome，清理错误追加在 `diagnostics.cycleCleanup` 中。

清理没有主异常时仍向外抛出；正常取消和清理时取消竞争保持原语义。仅处理预期的 `DeviceError` / `CancellationRequested` 清理异常。意外 `RuntimeError` 和清理阶段 `KeyboardInterrupt` 仍传播，已有主 `KeyboardInterrupt` 不会被预期设备清理失败替换。嵌套退出仍恢复外层计划/设备范围，未重放不确定写入。

## 命令

```bash
python -m pytest -q -s tests/runtime/test_supervisor_heartbeat_observation.py tests/runtime/test_cycle_plan_scope_cleanup.py
python -m pytest -q tests/runtime/test_heartbeat_liveness.py tests/runtime/test_worker_heartbeat_finalization.py tests/runtime/test_loop_cycle_configuration.py::testIterationRestoresOuterCompiledPlanAndReleasesOnException tests/runtime/test_device_ipc_cancellation.py
```

上述 `python` 是工作树同级的 `../venv/bin/python`。初始运行发现它指向的 Python 3.10 运行时缺失，使用 `uv python install 3.10` 恢复了原路径；见 `environment-restoration.txt`。生产修复开始前已记录真正的红灯测试，环境失败没有计作行为红灯。

## 边界

这是本轮两处已证实缺陷的源码和组件回归。未执行长时间完整 Runtime/Web 自然负载、CPU 压力或真实设备验收，不能由此宣称历史 93 秒失效或双任务并发失败已全部解释或解决。该目录没有提交或推送操作。

## 最终兼容性收窄与集成补充（2026-11-06）

以上红绿灯及 `manifest.json` 记录的是**初轮局部补丁**。随后根代理执行完整 Runtime 初轮，结果为 **2966 passed、7 skipped、1 failed**；唯一失败是 `tests/runtime/test_startup_cancellation.py::testCleanupCancellationCannotReplaceOriginalCycleFailure`。该既有测试第 276 行要求：正常 `CancellationRequested` 清理不得给主异常的原 diagnostics 增加内容。初轮将它与 `DeviceError` 合并处理，虽然保留了主异常对象和 UNKNOWN，却追加了 `cycleCleanup`，违反了原断言。

根代理保留原测试不变，将产品代码收窄为两个独立分支：

- 普通 `CancellationRequested` 沿用既有行为：有主异常时保留原对象及原 diagnostics；无主异常时继续抛出取消。
- 只有 `DeviceError` 清理失败才向主异常的字典 diagnostics 追加 `cycleCleanup`；原 code、outcome、requestId 及嵌套周期恢复逻辑继续保留。

根代理报告收窄后的 **33 项快速复验全部通过**：设备观察 11 项、startup cancellation 11 项、scope 11 项。截至本次文档补充，更大受影响的 76 项套件仍在收尾，未在本目录将其记为通过；最终完整 Runtime 结果由本轮证据根目录的主 README 和 integration 记录维护。本次文档更新没有运行测试。

同期 IPC 诊断补丁的回复发送计时也已恢复：先在 `encode_reply` 阶段完成序列化，再开始既有 2 秒 send 预算。独立对照的原失败记录保留于 [reply-deadline-review.json](../device-observability/reply-deadline-review.json)，恢复后的记录另存于 [reply-deadline-review-restored.json](../device-observability/reply-deadline-review-restored.json)。序列化处推进 2.25 秒后，基线与最终实现进入发送时均剩 2 秒并收到回执；同样暂停发生在实际发送阶段时，两版仍正常超时。

**源码版本归属：**本目录 `manifest.json` 和所有原始红绿记录保持不变，只说明其当时执行的初轮版本，不能当作最终源码哈希。最终源码哈希以本轮证据根目录的 `integration/runtime-suite-source-hashes.json`（从本目录为 `../integration/runtime-suite-source-hashes.json`）为准，由根代理在最终集成时维护。
