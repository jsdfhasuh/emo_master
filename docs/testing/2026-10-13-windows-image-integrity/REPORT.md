# Windows 图像完整性分层审计与诊断实现

日期：2026-10-13。实施计划：`docs/plans/2026-10-13-windows-image-integrity-followup-v1.md`。

## 1 结论及版本边界

本轮交付是**只读诊断、确定性回归和失败上下文补齐，不是捕获根因已修复**。

- 调查的原 ABBA 产品 SHA：`5410737a561e1f192ee9a081b3f208c9b27d9886`。
- 当前集成父版本：`c891b8a69d1d0f4ddbcb4d83ed90b54eac82d189`；相对上述版本只增加 Debian 验收档案，没有产品差异。
- 本轮提交不修改 `src/`，不调整配额、工作线程、内存、队列、deadline、预热分母或原诊断判定。
- 5410737 的 Windows ABBA 源图像、两条独立网络消费者的 receive/decode 均完整。其 FAIL 不能直接归因为捕获丢图。
- 这不关闭其他负载中的捕获不足：新读取的 PR 合并版本 CI 仍有真实 `BUDGET_EXCEEDED` 记录；旧 Windows 启动／Job 终态失败仍 OPEN。不能以本地一次通过或新专项通过取代它们。
- 原捕获诊断始终保留 `original_step_outcome=failure`、`measurement_status=INVALID`、`performance_status=NOT_ASSESSED`。本轮不重新运行 ABBA 求绿。

本文件写于候选提交形成时。最终推送 SHA 与该 SHA 的实时 Actions 结果由交付回复给出；本文件及旧运行不能证明尚未完成的新 SHA CI 通过。

## 2 P0：原证据身份与运行索引

重新下载的原 public JSON：1,039,654 bytes，未超原 1 MiB 上界。

SHA256：`5a364ea74bcb1f04a40efeff08e7f4293ab753a3e0acaf7a07b3a14e900063af`。

与原本地缓存字节相同。GitHub artifact id `11789268014`、名称 `export-credit-public-38347057040-1`，artifact ZIP 的服务端 digest 为 `ec84666a305f9364158a68eb5c3a153b418fff20cbb3f53fba9da5bd12c43b41`。此 ZIP digest 不是包内 JSON digest。本轮归档 ZIP 是重新组合的证据包，不冒称原 artifact ZIP。

| 实际身份／运行 | job | 刷新结论 | 范围 |
| --- | --- | --- | --- |
| [5410737 捕获诊断 38347057040](https://github.com/jsdfhasuh/emo_master/actions/runs/38347057040) | 115130797996 | FAIL | observation COMPLETE，measurement INVALID；保留原负载结果 |
| [5410737 原生专项 38347060373](https://github.com/jsdfhasuh/emo_master/actions/runs/38347060373) | 115130811768 | success，17 passed | 原生 Qt 自动化，不是人工桌面 |
| [5410737 CI 38347057046](https://github.com/jsdfhasuh/emo_master/actions/runs/38347057046) | Windows 115130797999；Linux 115130797669 | 两者 success | 不替代独立捕获诊断 |
| [5410737 冻结包 38347064093](https://github.com/jsdfhasuh/emo_master/actions/runs/38347064093) | verify 115132184774 | success | no release，不是发布或部署 |
| [c891b8a push CI 38356361465](https://github.com/jsdfhasuh/emo_master/actions/runs/38356361465) | Windows 115160079248；Linux 115160079467 | Windows FAIL；Linux success | 精确分支提交；Windows inspection 第二 Job FAILED，原日志缺 errorCode |
| [PR CI 38356368865](https://github.com/jsdfhasuh/emo_master/actions/runs/38356368865) | Windows 115160103549；Linux 115160103834 | 两者 FAIL | API headSha=c891b8a，但实际 checkout **b003905d2945a05d621cb8c2bb964be798316d56**；不是精确分支提交结果 |
| [c891b8a no-release 包 38356370472](https://github.com/jsdfhasuh/emo_master/actions/runs/38356370472) | 见 run JSON | success | 包验证不能覆盖上述测试失败 |

归档包含原有 run 元数据及后续刷新结果，先前 in_progress 快照不覆盖。实际 checkout 身份由原 job 日志核对，不能只看 API 的 headSha。详细文件哈希见 `EVIDENCE-INDEX.json`。

## 3 P1：四组全编号审计

两窗口**共享 consumer0 的 hub**；consumer1 是独立网络链路，不是第二窗口独占的消费者。

所有分母均为 **96（含 1–8 预热）**；脚本另外提供正式 9–96 的 88 分母，不能用后者替代前者。重复 GUI 记录按唯一 resultKey 去重，不能补分子。

| trial / observer | 源完整 | C0 receive/decode/live | C1 receive/decode/live | W0 commit/paint | W1 commit/paint |
| --- | --- | --- | --- | --- | --- |
| 0 / off | 96 | 96/96/95 | 96/96/95 | 95/95 | 95/95 |
| 1 / on | 96 | 96/96/96 | 96/96/96 | 96/94 | 96/94 |
| 2 / on | 96 | 96/96/96 | 96/96/96 | 96/96 | 96/96 |
| 3 / off | 96 | 96/96/96 | 96/96/96 | 95/95 | 95/95 |

### 3.1 trial0 ordinal68：直接证明 live 替代

两个消费者均记录 `SUPERSEDED`，high=69。旧／新 Runtime、Job、scope、generation 一致，high key 能关联到同一结果，high receive 早于旧 decision。时间差分别约 **6.7388 ms、9.0289 ms**。源和 decode 完整，旧结果没有应用到 live，双窗因此没有该 key 的 commit/paint。

这证明本条的 live 决策，不把全 96 GUI 的原失败改成 PASS，也不外推其他编号。

### 3.2 trial1 ordinal60、94：有 commit、无 paint 记录

两条网络链路都收到、解码并应用；两个窗口都 commit，但 paint 记录不存在。61、95 的后继 commit 约在 85 ms 后。

分类：`GUI_PAINT_NOT_RECORDED`；历史精确原因 **NOT_PROVEN**。后继提交是候选解释，不足以直接证明当时 Qt 合并了重绘；不能叫源捕获丢失，也不能叫已经确认的正常 Qt coalescing。

### 3.3 trial3 ordinal38：应用到 live，但无 GUI commit 记录

两消费者均 received/decoded/APPLIED。consumer0 model38=`425182269100`、model39=`425254346300`；双窗39的 commit 分别为 `425267526600`、`425267374800`。

分类：`GUI_COMMIT_NOT_RECORDED`；历史精确原因 **NOT_PROVEN**。符合 GUI 采样 latest 的可能性，但缺少当时 hub tick 的直接读值，不能用新测试反推原现场因果。

### 3.4 导出／hash 与完整性边界

源 availability、双消费者 source/image digest 与结果身份关联来自上游完整脱敏报告。其 parent/capture/export 列表仍完整保存在原 public JSON，未因本轮分类删除。审计器没有重新解码原始 PNG，不能把摘要关联冒称新的逐像素验收。

原 ABBA 使用静态图片：相同 hash 不证明跨 ordinal 新鲜度。变化内容及逐像素检查由既有 changing-96 受控测试分别记录，不替换原负载。

## 4 P2：必要的诊断与测试改动

1. `scripts/windows_image_integrity_audit.py`：stdlib-only，只读已有 bounded public report；不启动 Runtime、重试、工作负载或清理。校验 SHA、四组顺序、source/resultKey/ordinal、双消费者 identity/digest、live decision、GUI 时间和 consumer0 拓扑。输入／输出均保持 1 MiB 上界，输出独占新建，不覆盖旧证据；损坏、重复 JSON key、非有限数、超界表或不一致身份拒绝。
2. `tests/test_windows_image_integrity_audit.py`：28 项纯数据测试，覆盖原 FAIL 不变、预热保留、supersession 正／反证、source 不可用不能借 latest 免责、第二消费者缺号不能被第一条掩盖、重复 GUI 不增加覆盖、schema／join／上界及只读输出安全。
3. `tests/ui/presentation/test_latest_sampling_contract.py`：2 项受控 Qt 时序测试。其一手动控制 hub tick，证明两窗共享同一 hub 且 between-ticks 的 view2 可由 view3 取代；其二在不处理事件时依次 commit2/3，再处理 paint，证明 commit 可以均存在而实际只 paint3，并检查当前像素。**它们不是重建历史60/94/38。**
4. `.github/workflows/display-session-contract.yml`：原生专项纳入上述 2 项；原17项不删除，候选应收集19项。原捕获诊断 workflow 未修改／未派发。
5. `tests/runtime/test_run_inspection_rpc.py`：为两次 Job 的非 COMPLETED 终态附加已有有界、只读 `jobFailureDetails`，避免未来只留下 FAILED 字符串。原成功条件和10s等待不变；这不是修复旧 Job 失败根因。

**P3 决策：没有证据证明当前 ABBA 的捕获／GUI 算法违反已确认契约，故不强行修改产品。** 下节 CI 的源不可用是真实记录，但缺少失败瞬间 lane owner 及 acquire/release 的配对证据，还不能决定是持有泄漏、吞吐不足、调度延迟还是其他原因。不能盲增配额或提前归还 owner 的信用。

## 5 新刷新的 CI 失败独立账本

| 运行／失败 | 已有事实 | 根因与下一步 |
| --- | --- | --- |
| c891b8a push Windows：`testDifferentRealJobsRetainSameInvocationImageAndCountAcrossPromotion` | 第二 Job 被接受，终态 FAILED；原断言未记录 errorCode、event tail | OPEN；本轮补只读失败上下文，保持原断言。不能推断是图像配额或简单超时 |
| b003905 PR Linux：`testBusinessNgDoesNotStopAndSharesInvocationWithImageAndCount` | ordinal3、executionTerminal=COMPLETED、count=2、judge=false；image UNAVAILABLE/BUDGET_EXCEEDED，reason 为 source lane busy；closure/export errors 空 | **真实 source incomplete**，不是 GUI 缺 paint；OPEN。需要同调用、同 lane 的实际持有及归还边界，不能用新专项成功关闭 |
| b003905 PR Windows：`testTwoExplicitDualImageJobsStayWithinRuntimeBudget` | 10s 内 Job 未终态，事件只到启动／心跳，原诊断保留 | OPEN；不能统一归为源丢图或只差几毫秒 |
| b003905 PR Windows：`testDedicatedSelfTestExercisesRealSpawnAndPagesWithoutDesigner[0]` | 自检 subprocess 返回 status=error；原日志没有完整 result.json 内容 | OPEN／取证不足；不编造其内层具体失败 |

本地在干净 c891b8a 候选上，对以上四个入口**各一次**定向复核：4 passed / 12.59s。随后只读诊断调用点及安全测试：7 passed / 3.32s。两次范围重叠，不相加；通过只说明本地本次未复现，不等于 CI 根因修复或稳定性证明。PR 合并版本与精确分支提交不能混称。

## 6 P4：本地相关回归与干预清单

基底：干净隔离检出的 c891b8a；候选 hash 记录见 archive 的 local-candidate manifest。本地 primary 中原有 operator_debug／workflow_debug／PLC／迁移等修改未注入。

- Windows **native Qt automated**：197 passed、1 skipped、39 subtests passed，79.03s。覆盖 capture lane lifetime、mailbox recovery、多路捕获、export diagnostics、closure failures、display subscriptions、live decisions、stop_all、五真实 DisplaySession、renderer 和本轮 audit／latest sampling。新增30项包含其中，不能另加。
- 五路完整生命周期：7 admitted / 7 retired、rejected=0、work_peak=2；单停其余继续、重启、全停、共享 hub、断流恢复及关闭；闭后 session/exporter、owner 和 asset/cache/reader/lease 断言通过。
- 五路运行中直接关窗：5 admitted / 5 retired、rejected=0、work_peak=2；闭后记录 job_resources 全 false，runtime_closed=true。它是 Windows 自动化，不是补齐旧 Debian 的人工关闭采样。
- 本地 changing-96 测试保留全编号、双消费者、owner/hash/像素安全断言。与上述纯数据／受控 GUI 时序分别归类。
- protobuf drift check PASS；Ruff src/tests 及新审计器 PASS；mypy 412 source files PASS。
- 首次工具失败：错误复制路径未收集测试、sandbox pytest tmp setup 拒绝、protobuf tmp 权限拒绝。原日志／摘要保留，不冒称修复前产品失败。

最终 exact-SHA CI／Windows native run 必须在推送后核对 actual checkout；旧17项通过不是新19项通过。完整本地全量没有运行，不编造全量计数。

## 7 未关闭与未运行

- 历史 capture budget 拒绝、export timeout 根因 OPEN；新 PR source incomplete 也 OPEN。
- 旧8c A02缺号以及本次Windows60/94/38的精确 GUI 原因 NOT_PROVEN。
- c891 Windows inspection Job FAILED、PR启动／自检失败 OPEN；新 CI 即使成功也不得抹去原日志。
- Debian直接关闭后ownership专门补证 NOT_RUN，非已证明泄漏；旧限定验收保留。
- 原 ABBA 新工作负载 NOT_RUN；本轮no-release冻结包 NOT_RUN（无 runtime／打包改动）。
- D01–D08、SQLite/O20、生产设备、全DPI、最大容量、长期稳定性、性能：本轮不扩展验收。
- 没有合并、发布、部署、force push或连接生产设备。

## 8 离线重算与证据使用

`windows-image-integrity-evidence.zip` 是本轮新组合包。先核对 `EVIDENCE-INDEX.json` 的 archive SHA，再验证 ZIP 内 `SHA256SUMS.txt`；包含原脱敏 public JSON、全384项审计、首次CI失败日志、运行元数据和本地回归证据。原记录未覆盖。

在独立目录解压后，从仓库根目录运行（output 必须是不存在的新路径）：

```text
python scripts/windows_image_integrity_audit.py --report <解压目录>/original/export-credit-public.json --expected-sha 5410737a561e1f192ee9a081b3f208c9b27d9886 --output <新的输出路径>/layer-audit.json
```

输出为 `AUDIT_WRITTEN_NOT_ACCEPTANCE`，不启动产品也不改变原 FAIL。逐编号身份可查 `audit/layer-audit.json`，原导出明细在 `original/export-credit-public.json`。首次本地 setup 错误的巨长 pytest 参数日志仍留 build；包内提供其hash／分类，不将4MiB环境错误混成产品日志。

不要求 dot 重跑整套 Debian。最小关键词：**只读分层审计、全96含预热、双消费者、原FAIL保留、GUI原因未证实、历史capture/CI仍OPEN、无src修改**。详见 `DOT_HANDOFF.md`。
