# 36b4617 复验后的客户端预算与诊断修复

## 范围和授权

- 父提交：`36b46175caab896d337bdb21644935832ee875d1`。
- 用户于 2026-10-12 明确同意：对于只差几毫秒的时限，可以由 500 ms 放宽至 550 ms。
- 本次仅调整 P0 双消费者 **接收通知至完成图像解码和校验的总预算**，独立字段 `client_result_seconds=0.55`。不是生产 Runtime 的全局超时调整。
- 单次 asset RPC、服务端资源读取、导出、seal 仍为 500 ms；模型 p95、live age、吞吐、执行 p95 退化门槛、SQLite、捕获额度均不变。
- 原 36b4617 的 500 ms 首次失败仍有效。不能把新版标准追溯应用于旧报告，也不能把时限调整写成性能根因修复。
- 使用独立 repair worktree；不覆盖主工作区的其他 Designer/页面/YOLO 等本地修改。不合并、发布、部署或连接设备。

## 修改

### 客户端预算与失败阶段留存

`prototypes/runtime_pages_p0/contracts.py`、`continuous.py`、`scripts/p0_validate.py`：

- 将客户端总预算与 asset RPC/read 预算分开。
- 接收、传输完成、编码字节校验、解码、像素/结果校验、结束的时间戳，以及实际接收字节数、当前阶段、预算，在失败分支仍然保留。
- 缺失的阶段为 `null`，不伪造 taskId、服务端 executionId 或完成时间。
- 超时、损坏图像、RPC 错误不继续显示旧结果；记录本次 resultKey、ordinal、assetId 和错误类型。
- OpenCV 解码异常纳入单结果失败路径，不因一张坏图终止整个订阅。
- 输出中明确新版 550 ms 口径；严格 P0 的性能退出规则没有改变。

### 修复诊断包装器的真实接口不兼容

`scripts/r3_measure.py`：

当前 `WorkflowRunner._runNode` 接收第七个 `boundValues` 参数，而观测包装器的 metadata lambda 只接受六个参数。新增终态采证后，在本机三个 reduced arm 均复现：

```text
measuredJob.<locals>.<lambda>() takes 6 positional arguments but 7 were given
```

三个 Job 均 FAILED、执行次数 0；首个 node.failed 为 tick/pace，随后错误传播至 workflow.failed/job.failed。只新增诊断字段，没有先更改包装器，保留了该首次失败。

修复让阶段、predicate、metadata 包装器接受附加位置/关键字参数；原函数仍收到全部原始参数，绑定值不被丢弃，原异常不被替换。回归覆盖省略/位置/关键字传递和原算子失败，共六种组合。

该证据证明本机诊断脚本故障及其修复。原 hosted run 38211010297 未留下 worker 原因，因此不能声称已经证明其四个 arm 与本机完全同因。

### 终态证据与有界公开报告

`scripts/r3_policy_measure.py`、`scripts/r3_export_credit_report.py`、`.github/workflows/runtime-credit-diagnostics.yml`：

- 在清理临时工作负载之前，采集 Job 错误码、消息及前 16 条失败事件。每条消息最多 4096 字符，超量明确标记；采样失败不会替换原 Job 结果。
- 本地 trial 保留有界消息；公开报告独立白名单化，仅输出固定错误码/类别和消息、事件身份的哈希，不公开任意路径、原始日志、图像或参数。
- 对已知包装器错误输出固定分类 `OBSERVER_RUN_NODE_SIGNATURE_MISMATCH`；不认识的消息仍为 `UNCLASSIFIED`，旧证据为 `NOT_RECORDED`，不编造原因。
- 新增 `--output-json`，先验证原完整报告上限，再独占创建脱敏 JSON，拒绝覆盖已有文件。
- CI 在原控制失败后仍生成报告并上传**唯一精确路径的脱敏 JSON**，保留 7 天，不使用通配符上传工作目录，不忽略原失败状态。

## 验证

最终验证数据见同目录 `VALIDATION.json`。专项与广泛回归存在重叠，不能相加。

- 确定性边界：500、503.2987、505.4541、550 ms 接受；550.000001 ms 拒绝。保留图像完整性、身份和旧结果失效断言。
- 初次新增及相邻测试：99 passed、1 skipped、12 subtests passed。
- 扩展 RPC/OpenCV 异常留存后，最终专项：101 passed、1 skipped、12 subtests passed，14.45 秒。
- 广泛相邻回归（完整 tests/p0、runtime/presentation、4/5 While、report/guard）：591 passed、1 skipped、26 subtests passed，346.53 秒。之后仅新增两个故障注入参数用例，未再修改可执行实现，以上最终专项覆盖新增用例。
- Proto、Ruff（src/tests）、mypy：exit 0；mypy 403 个源文件。
- reduced 4 输入/1 预热/3 arm（软件 offscreen）：包装器修复后三组 COMPLETED，各执行 4 次，其中计量 3 次。
- 首次修复后 reduced 运行因输出路径过长在临时目录清理阶段失败，日志原样保留。随后只改用较短证据目录，未放宽产品断言、未改代码规避失败。这是诊断运行环境路径问题，不是原 hosted 根因。
- 完整 ABBA：4 arm × 96 总输入（各含 8 预热，88 计量），均 COMPLETED、输入全覆盖。第 3 arm ordinal 75 的 image 为 `UNAVAILABLE/BUDGET_EXCEEDED`，捕获通道 nonblocking acquire 返回 false，两路计量图像各 87/88；整轮仍 **INVALID / NOT_ASSESSED / exit 1**。未重跑取绿，未扩大通道或等待预算。
- ABBA 的其他三组两路均为 88/88；四组 watchdog/owner retirement 通过，source_stable=true。这不是完整 ABBA 通过。
- 验证期间发现另一个非本任务发起的 `python -m pytest -q` 进程。未终止它或用户 Designer；本机并非隔离性能环境。ABBA 为观测运行，不是性能验收，不能用它解释生产性能根因。
- 本轮没有另行跑完整 pytest 或严格 96/8/3/5 P0 性能基准；完整 CI 在推送后单独判定。广泛相邻回归不是全量测试，ABBA 不是严格 P0 基准。
- 四组本地 ABBA 的公开白名单视图实际生成 854296 bytes，小于原 1 MiB 报告预算。它明确标注 offscreen、非 hosted guard/非性能验收；原文日志不进入公开包。

同目录 `evidence.zip` 包含本机首次包装器失败的脱敏分类、完整四组 ABBA 白名单视图、通过的测试控制台/静态检查输出及 SHA256 清单。原始 JUnit、完整进程日志、长路径首次清理失败与未脱敏 trial 留在本地审计目录；没有删除。

## 未关闭

1. 严格 P0 execution p95 相对退化：原完整验收的 9/15 FAIL 不因客户端总预算改变而关闭。
2. 新观测到 ordinal 75 捕获通道忙：已定位拒绝边界和同调用身份，不等于定位到持有者为何未及时释放；不按 550 ms 策略修改资源额度。
3. Windows PR merge 五路 While 的 global variable database busy：根因 OPEN，未扩大 SQLite 预算。
4. 历史首结果超时、NG→INCOMPLETE：OPEN；通过的专项不是根因证明。
5. 精确新 SHA 的 hosted Windows CI 和诊断结果：以提交推送后的 run 为准，不用 PR merge 结果代替。
6. 本轮未重新操作真实 Qt 桌面、设备、安装或部署。原 dot 的 D01–D08/ROI/O20 通过范围不自动扩展。

原始本地审计记录位于 `C:/Users/jsdfhasuh/.codex/tmp/emo-36b4617-repair-20261012`，短路径诊断记录位于同一 tmp 下 `r36-small`、`r36-abba`。不将用户附件或原始私有进程日志自动上传公共仓库。
