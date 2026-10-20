# 历史 NG / INCOMPLETE：只读代码深挖附录

核查日期：2026-10-11 UTC。当前源码固定为 `abc7ae0541e997be0bbe3a4794ad604c239de79a`。正文为源码、Git 对象与已留存首失败证据的只读调查；后续另获授权做了一次第 9 节所列隔离模型/故障注入。没有重跑原测试、启动 Runtime、操作 GUI、修改源码/测试、调整原 deadline 或推送。

## 1. 可直接交给 Codex 的结论

1. **历史生产 NG 测试的根因仍未唯一确定，但发生层级已明确。** 它在 Runtime 本机 `owner.presentation.store.latest` 中直接读到 `ClosedResult.status == INCOMPLETE`，还没进入网络订阅、读图或 GUI。该对象的合法生成条件只有：`executionTerminal == COMPLETED` 且至少一个 source 非 `AVAILABLE`，或者 `executionTerminal == UNKNOWN`。旧日志没有保存 `sources`、`reasonCode` 或 `executionTerminal`，无法进一步指定缺的是 image、count 还是 judge。
2. **业务判断 false 没有被映射成执行失败或 INCOMPLETE。** `vision.compare.number` 的 false 输出仍返回 `status: ok`；collector 将合法 false 冻结成 `valueJson: "false"` 并标为 AVAILABLE。`NG` 是 indicator 的文本映射。没有证据证明历史失败因 NG 本身而停机；失败发生在验证持续 RUNNING 的后续断言之前。
3. **同一历史 Windows job 的另一项 P0 失败可以定位到服务端导出结果。** ordinal 20 的服务端 commit 已含 `image = UNAVAILABLE`、`reason = TimeoutError`、明确 task_id；两个客户端收到同一 key 的 INCOMPLETE，均没有 `read_error`。最窄的源码显式候选是旧 `AsyncExports._own` 的三个谓词：STARTED poll 超时、STARTED 消息/身份不匹配、completion poll 超时。前两个被合在同一 `TimeoutError("start deadline")` 内。**仅凭异常类名不能断言确实耗尽 500ms，也不能在这三个条件中选择一个。**
4. **两项失败不可合并归因。** P0 使用 `prototypes/runtime_pages_p0/AsyncExports + Results`；生产 NG 使用 `apps/runtime/presentation/ExportPool + ResultStore`。二者只是都有 image export 和有界 deadline，历史证据没有证明同因。
5. **1657→abc7ae 没有直接修改生产 presentation 状态/采集/导出/缓存实现。** NG 测试只补了 `result.model_dump()` 失败诊断。其他 SQLite、RPC 或 P0 批传输改动可能间接改变调度负载和复现概率，不能据“该层字节未变”断言“不可能改善”，也不能凭当前 PASS 宣布历史原因已修复。

## 2. 精确版本和证据边界

- 历史 source head：`1657a65e5700b963000090964bd1b54227e1d550`。
- 历史 PR CI 实际 checkout：`db2014401017a04c832f69fa0fa4d208bccbbe53`。原 checkout 记录见留存 CI 审查与完整 job 日志；本次 GitHub compare 返回 base/merge_base 均为 1657、ahead 1、`files: []`，支持该合成 merge 与 1657 文件内容相同。保留响应：[merge 比较原记录](historical-evidence/merge-compare-1657-db201.json)。[精确 merge](https://github.com/jsdfhasuh/emo_master/commit/db2014401017a04c832f69fa0fa4d208bccbbe53)。
- 两项首失败均在 [PR Windows job 114380859609](https://github.com/jsdfhasuh/emo_master/actions/runs/38109143323/job/114380859609)，该 job 原计数 `2 failed, 4800 passed, 5 skipped, 29 subtests passed`。这里“两项”指两个测试失败，不是声称做过两次 NG 重现。
- 当前 HEAD：`abc7ae0541e997be0bbe3a4794ad604c239de79a`。[23 个相关文件的双版本 SHA256](ng-code-sha256.json)同时核对了 working tree 与当前 Git blob；[相关 diff](ng-relevant-1657-to-abc7ae.diff)保留未删节补丁。
- 源码 tracked diff 为空；Git 整体并非全 clean，保留 `?? docs.emofixture`。该文件由父任务本轮 GTK 保存误导航形成，未删除；与本次只读诊断无关。
- 下文 `@abc` 链接均展开为精确当前 SHA permalink；历史改变过的 P0 分支另给 `@old` 的精确 1657 链接。没有用可漂移的 master 行号作结论依据。

### 独立可核对的历史原件副本

以下前四份历史原件（完整 job 日志、完整 P0 JSON、原 CI 审查、原 CI 机器摘要）按原字节复制；仅这四份的原路径、文件字节数、SHA256、copy_equal 记录在 [NG-HISTORICAL-PROVENANCE.json](historical-evidence/NG-HISTORICAL-PROVENANCE.json)。未执行任何日志内命令或旧脚本。

- [完整 Windows 首失败 job 日志](historical-evidence/job-114380859609-pr-windows-first-failure.log)
- [完整 P0_CONTINUOUS JSON](historical-evidence/pr-windows-continuous-first-failure.json)
- [原 CI 审查](historical-evidence/READ_ONLY_CI_REVIEW_1657a65.md)、[原 CI 机器摘要](historical-evidence/ci-summary-1657a65.json)
以下是另外生成的派生材料，不属于上面四份 copy_equal 原件记录：

- 日志/JSON 派生摘录：[NG 原断言的带原行号摘录](historical-evidence/ng-assertion-extract.txt)、[P0 ordinal 20 聚焦摘录](historical-evidence/p0-ordinal20-extract.json)
- Git 对象派生文件（git show 精确 1657 blob，并非历史日志原件）：[旧 AsyncExports 原文件](historical-evidence/p0-async-exports-1657a65.py.txt)、[旧 NG 测试原文件](historical-evidence/production-boundaries-1657a65.py.txt)

## 3. 两个历史首失败分别证明什么

### 3.1 生产 NG 测试：只能确定服务端已闭合为 INCOMPLETE

精确测试：[testBusinessNgDoesNotStopAndSharesInvocationWithImageAndCount，1657:241–270](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/tests/runtime/test_production_boundaries.py#L241-L270)。原日志物理行 552–583：

- 建立 `count → judge`，比较参数 `operator=gt, rightValue=3.0`；false 显示文字为 NG。
- `waitFor(any(latest.resultOrdinal >= 3))` 返回后，取 `next(iter(owner.presentation.store.latest.values()))`。
- 原第 261 行断言 `assert result.status == "COMPLETE"`，实际 `INCOMPLETE`。
- **未保存**：该 result 的 key/精确 ordinal/invocationId、executionTerminal、各 source 状态与 reason、exporter errors/stats、presentationErrors、全部 job 状态。后续 `count==2 / judge==false`、同 invocation workflow.completed、job RUNNING 的断言没有执行到。

fixture 源码期望的三个 source 为 image/count/judge；image 来自 `blob.overlay`，count 来自 `count.count`，judge 来自 `judge.result`。基础输入为本地 120×160×3 uint8 合成图，两个白块；continuous 默认周期 100ms。见 [sampleProject:12–38](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/examples/runtime_pages_p2.py#L12-L38)、[productionProject:10–20](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/tests/runtime/production_fixture.py#L10-L20)。这给候选排序提供上下文，**不能替代失败对象的实际 source 值**。普通图像体积远小于 8MiB，并不排除“image lane busy”也使用 BUDGET_EXCEEDED 的分支。

### 3.2 P0 网络双消费者：失败源在服务端导出，客户端没有尝试读这个失效图

原始 JSON 中可核对：

| 字段 | 首失败值 |
|---|---|
| server key / ordinal | `710600a5-1b09-4680-ac22-acf826f6234c` / 20 |
| exporter task_id | `1055159d-9f71-43c7-915b-8785b96b95fc` |
| server status / image | `INCOMPLETE` / `["UNAVAILABLE", {"status":"UNAVAILABLE","reason":"TimeoutError",...}]` |
| scope_end_ns / commit_ns | 952181950700 / 952235998700 |
| commit 距 scope_end | 54.048ms，非 500ms seal 缺图兜底的时间特征 |
| client 1 / client 2 | 同 key、ordinal 20、INCOMPLETE、decoded=false；均无 read_error |
| coverage / incomplete / client_errors | `[0.975, 0.975]` / 1 / `[null, null]` |
| generated / sealed / final / rejected | 45 / 45 / 45 / 0 |
| scope 20 execution / lateness | 961.4255ms / 2455.7927ms |
| measured achieved_hz / execution_p95 | 2.5443699629792866Hz / 809.329885ms |
| cleanup used / owners | memory、staging、cache、lease 均 0；owners=0 |

服务端 commit 数据由 [continuous.window:137–141](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/continuous.py#L137-L141)直接从 Snapshot 取出，不是客户端推测。客户端仅在 `status == COMMITTED && image == VALID` 时调用 `_decode`；读失败会额外产生 `row.status=UNAVAILABLE` 与 `read_error`，再将客户端 snapshot 设 INCOMPLETE，见 [NetworkConsumer._receive:71–99](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/continuous.py#L71-L99)。ordinal 20 不符合读图条件；故“这个结果因客户端读图 500ms 超时而变 INCOMPLETE”与原记录不符。

54.048ms 是闭合相对 scope_end 的间隔，不是导出耗时。导出 timer 从 `submit` 的复制前起算，早于 scope_end，且 callback 之前有退休/清理、可能等待 Pipeline 锁；不能用这 54ms 排除导出超时。整体调度慢是观测事实，不是某一超时分支或 CPU 争用根因的证明。

## 4. 生产路径：从业务判断到结果、资产与查询

### 4.1 单次 invocation 的调用链与身份

1. [worker_main._runContinuous:133–151](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/jobs/worker_main.py#L133-L151) 每周期建立新的 RunContext，运行 workflow；周期延迟减去当次运行耗时。
2. [WorkflowRunner.run:99–125](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/workflow/runner.py#L99-L125) 在 begin 建立 capture，执行后 terminal=COMPLETED，finally 执行 collector.end；取消/异常使用不同 terminal。
3. [ResultCollector.begin:45–77](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L45-L77) 按 workflow/callPath 匹配 scope，生成 resultKey UUID、单调 ordinal、invocationId=workflowRunId，并保留 runtimeInstanceId/jobId/executionRevision/capturePlanRevision。open/待发送 seal 共最多 8 或 credit 不可得时增加 rejected，**不直接造一个 INCOMPLETE 对象**。
4. [WorkflowRunner:206–220](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/workflow/runner.py#L206-L220) 在 `_validateNodeOutputs` 后、下游路由前调用 collector.observe/output；[collector.output:79–113](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L79-L113) 仅关联同一 workflowRunId 以及指定 node/port。图像 alias 通过 imageSourceId 回指同一次冻结导出。
5. `display.open / display.image / display.seal` 进入专用共享内存 mailbox，不是 SQLite EventStore：[worker_main._collector:178–182](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/jobs/worker_main.py#L178-L182)、[SharedMailbox:14–58](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/mailbox.py#L14-L58)。普通 node/workflow 事件另由 runner.publish 发出：[runner.publish:399–411](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/workflow/runner.py#L399-L411)。因此共享 event 的 SQLite 改动不是直接改写该 ClosedResult.status 的代码路径；间接时序影响仍未排除。
6. [PresentationService._consume:253–279](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L253-L279) 建立 pending，检查 image descriptor 属于 job/slot/lane/source，再 submit；seal 带 worker 侧 sealedAt、terminal、所有 source 清单。
7. `ExportPool.submit → _run → service._exported → assets.adopt → service._finish → ResultStore.close`，具体异常桥接见下一节。
8. [ResultStore.close:88–115](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/store.py#L88-L115) 生成冻结 DTO 后入 history/latest；latest 仅接受更高 ordinal，晚回调不能把已经闭合的旧结果修回 COMPLETE。

### 4.2 NG false 只是合法业务数据

[NumberCompareOperator.executeNode:75–96](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/number_compare/operator.py#L75-L96) 无论比较布尔结果 true/false 都返回 `status="ok"`。`freezeValue` 接受 bool 并 JSON 编码：[values.py:21–65](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/core/presentation/values.py#L21-L65)；成功后 [collector.py:106–111](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L106-L111) 写 `state="AVAILABLE"`。

UI 在 [renderer.py:630–633](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/ui/presentation/renderer.py#L630-L633) 用 `json.dumps(value)` 查询 indicatorStates 并显示 NG 文案，不回写 result.status。NG 文本与 INCOMPLETE 可以在同一结果显示层并存，例如 judge AVAILABLE=false 而 image UNAVAILABLE；但旧 NG 首失败日志没有 source 清单，尚不能认定它就是这种组合。

### 4.3 唯一合法的生产 INCOMPLETE 生成规则

[store.py:93–103](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/store.py#L93-L103) 与 [ClosedResult.checkManifest:91–107](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/core/presentation/results.py#L91-L107) 独立执行同一约束：

| executionTerminal | sources | 结果 status |
|---|---|---|
| COMPLETED | 全部 AVAILABLE，包括合法 false | COMPLETE |
| COMPLETED | 至少一项 UNAVAILABLE | INCOMPLETE |
| UNKNOWN | 任意合法 source 清单 | INCOMPLETE |
| FAILED / CANCELLED | 任意合法 source 清单 | FAILED / CANCELLED |

遗漏/重复 sourceId、错 asset ownerResultKey、状态与 terminal 不符等触发 DTO 校验错误，并非另一种合法的 INCOMPLETE 规则。独立的 [supervisor._stopFailure:395–402](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/jobs/supervisor.py#L395-L402) 所返回 `E_JOB_RETIREMENT_INCOMPLETE` 是 Job 资源退休错误码，不是 ClosedResult 状态，不能混入本分析。

## 5. 所有 collector 不可用分支及导出/缓存桥接

表内规则取当前 abc；这些生产文件与历史 1657 字节相同。只有 terminal=COMPLETED 或 UNKNOWN 时，它们才按上表产生 INCOMPLETE。

| 位置 / 函数 | 条件 | 产生的 source / 行为 |
|---|---|---|
| [collector.output:91–93](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L91-L93) | 指定 port 不在 outputs | OPTIONAL_ABSENT |
| [collector.output:95–113](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L95-L113) | projectField / freezeValue / image 抛出捕获范围内 ValueError、TypeError、KeyError、IndexError | INVALID_VALUE；异常 detail 未保存。单 source >256KiB 等 freezeValue 错误也走此类 |
| [collector.output:106–111](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L106-L111) | 累计标量 JSON >1MiB | BUDGET_EXCEEDED |
| [collector.image:117–121](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L117-L121) | 非 ndarray、非 uint8、维数/通道数不合或空图 | INVALID_VALUE |
| 同上 | 单图 >8MiB 或本 result rawBytes 累计 >16MiB | BUDGET_EXCEEDED |
| [collector.image:123–138](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L123-L138) | 图像超过分配 source lane 容量 | BUDGET_EXCEEDED，明确 no implicit resize |
| 同上 | slab 已 quarantine，或 acquire 后二次检查变 quarantine | BUDGET_EXCEEDED，等待真正 owner 退休 |
| [collector.image:133–159](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L133-L159) | 无可用匹配 lane / 非阻塞 credit 获取失败 | BUDGET_EXCEEDED，detail=`source image lane is busy; capture never waits` |
| [collector.event:161–168](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L161-L168) | 指定 node 被 skipped / failed | BRANCH_SKIPPED / NODE_FAILED |
| [collector.end:197–212](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L197-L212) | global_variable/global_counter 读取、冻结或总字节超限，捕获 ValueError、AttributeError、KeyError | 尝试生成 VARIABLE_UNAVAILABLE，见下方 schema 边界；此 NG fixture 没有这两类 source |
| [collector.end:213–217](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L213-L217) | 预期 source 从未写入 values | 按 terminal 补 SOURCE_MISSING / NODE_FAILED / EXECUTION_CANCELLED |
| [runner._capture:413–424](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/workflow/runner.py#L413-L424) | collector 任意其他 Exception，例如复制/IPC异常 | 仅增加 captureErrors/rejected 并吞掉；之后可能被 end 补 SOURCE_MISSING，原异常未入此对象 |

**枚举边界：** collector 的 `VARIABLE_UNAVAILABLE` 不在 [ClosedSource.reasonCode Literal:54–62](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/core/presentation/results.py#L54-L62) 中。只读代码确认该分支与 DTO 枚举不一致；随后第 9 节的一次隔离补证进一步确认 ValidationError、open 先 pop、结果未发布和模拟 credit 未归还。不把它列成可成功存入 ResultStore 的额外 reason，也不把它当本 NG fixture 的原因。

**IPC 分支：** `display.open` 发送失败会移除该 open 并归还 credit；`display.image` 未成功转交则 finally 释放 lane。seal 遇 queue.Full 时保留 pendingSeals，后续 capture boundary 再尝试；其他不明确发送异常不重发，交 parent fence。分别见 [collector:70–77](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L70-L77)、[152–184](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L152-L184)。mailbox 的序列/长度/CRC 错误会 ValueError；父 reader 捕获 EOFError/OSError/ValueError 后 `_fence(job, UNKNOWN)`，只对尚未收到 seal 的 pending 填 IPC_ERROR：[mailbox:39–58](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/mailbox.py#L39-L58)、[service:226–243](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L226-L243)、[345–354](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L345-L354)。已收到 seal 的 pending 不走该 fence，继续由 _finish 处理。

### 5.1 导出、deadline 与 lane 释放：三个最值得补 trace 的生产候选

**候选 A：捕获时 lane 尚未释放，source 直接 BUDGET_EXCEEDED。**

- 每个 image job 分配自己的 slot；根据 imageLaneBySource 配置 1 或 2 lanes：[service._attach:194–218](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L194-L218)。这个单 image source fixture 正常为 1 lane。
- collector 成功 acquire 后复制到共享 slab，descriptor 带 frozenAt、key、sourceId、jobId、shape：[collector.image:133–155](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L133-L155)。占用持续到 exporter 真正归还 credit。
- [ExportPool._run:218–236](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/exporter.py#L218-L236) **先 callback**（可能发布前一 result），后删除 staging/必要时重新 spawn exporter，最后 remove pending 并 release lane。因此“前一 result 已经闭合”不等于“下一 cycle 一定拿得到 lane”。100ms continuous 节奏遇到较慢导出或调度，有代码可达的 busy 分支；没有历史 lane trace，尚未证明命中。

**候选 B：两套 500ms deadline / callback 与 finalizer 的次序。**

- exporter timer 从父进程 `submit` 计时：[exporter.submit:148–153](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/exporter.py#L148-L153)。排队也消耗时间。
- `_run` 有三个 EXPORT_TIMEOUT 点：出队时已过期；发给 encoder 后 poll 无回复；收到 ok 回复但检查已过期：[exporter:189–207](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/exporter.py#L189-L207)。它们不是业务 NG 判断。
- assembler timer 从 worker 的 `sealedAt + .5` 计时：[service:275–279](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L275-L279)。在 pendingImage 尚无 callback 且该 deadline 到时，`_finish` 自行生成 EXPORT_TIMEOUT：[296–314](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L296-L314)。monitor 每约 20ms 尝试：[322–334](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L322-L334)。
- 这两个起点不同。IPC backlog 可让 seal 到父进程时 deadline 已近或已过；encoder 即便仍在自己的 submit deadline 内，missing callback 也可能先被 assembler 关闭。反过来，若 callback 已存在，_finish 不再因 seal deadline 过期单独拒绝该 available 值。最终表现依赖实际时序，历史没有采到它。
- `_finish` 删除 pending 后，后到 callback 立即返回，不能回补：[service._exported:281–294](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L281-L294)。冻结结果这一规则明确，但是否是该次原因未证实。

**候选 C：EXPORT_FAILED / IPC UNKNOWN / 其他 source 捕获失败。**

- encoder 读取共享 slab、PNG 编码、写 staging，失败回复 `{ok:false,error:异常类名}`：[encodeWorker:18–37](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/exporter.py#L18-L37)。父端丢弃 reply.error，统一抛 `ValueError("invalid export reply")`；大多数异常最后以默认 EXPORT_FAILED 回调：[exporter:184–219](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/exporter.py#L184-L219)。
- adoption 要求 staging 有效、单 encoded image≤8MiB、总 cache≤256MiB；读/hash/rename 也可失败：[AssetStore.adopt:34–51](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/assets.py#L34-L51)。service 捕获 OSError/ValueError，统一变 EXPORT_FAILED，未保存底层 detail：[service._exported:286–294](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L286-L294)。
- descriptor job/slot/lane/source 不匹配或 exporter.submit 校验失败可通过 reader 的 ValueError 进入 UNKNOWN fence。原 capture 异常也可能导致 count/judge/image 的 SOURCE_MISSING；没有失败 source 清单，不能排除后两者。

**排序说明：** 小图、连续 100ms、共享 lane 异步导出，使 A/B 是高价值调查入口；C 与 scalar/missing/UNKNOWN 仍保留。这是代码可达候选排序，不是概率估计、历史重现或根因证明。

### 5.2 缓存、网络读取与 GUI：不会倒改本机 ClosedResult.status

- `[history | latest | events | pending]` 联合保留资产 key：[service._retain:317–320](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L317-L320)。缓存不足可在 **adopt 阶段**导致 EXPORT_FAILED；闭合后 metadata trim/asset eviction 不会把 frozen ClosedResult 改成 INCOMPLETE：[store._trim:49–70](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/store.py#L49-L70)、[assets._collect:20–32](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/assets.py#L20-L32)。
- `ResultStore.snapshot` 选择已保存结果/增量/过期元数据；RPC `wireResult` 原样复制 status/terminal/source reason：[store:124–139](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/store.py#L124-L139)、[rpc:13–39](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/rpc.py#L13-L39)、[126–146](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/rpc.py#L126-L146)。
- asset read 错 job、读取并发 quota、500ms deadline、SHA/size 不符，变成 RPC 错误，不修改 ResultStore：[assets.read:58–80](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/assets.py#L58-L80)、[rpc._asset/ReadAsset:148–164](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/rpc.py#L148-L164)。
- 正式 DisplaySession decodeResult 保留 wire.status 并验证 DTO：[display_session:22–47](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/clients/runtime/display_session.py#L22-L47)。读图失败写到 `failures[sourceId]` / imageStates，仍与原 result 一起保留；客户端图像预算裁剪也不改 result.status：[593–603](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/clients/runtime/display_session.py#L593-L603)、[625–679](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/clients/runtime/display_session.py#L625-L679)。
- GUI 展示 source reason、client failure、未请求/加载中的图像状态和独立业务 false 映射：[renderer._value:507–545](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/ui/presentation/renderer.py#L507-L545)、[630–645](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/ui/presentation/renderer.py#L630-L645)。因此不能把用户看到“无图”“NG”“INCOMPLETE”不加区分地归成同一状态转换。

## 6. P0 路径的直接 INCOMPLETE 条件与最窄候选

P0 状态命名是成功 `COMMITTED`，生产是成功 `COMPLETE`，不要混用。

### 6.1 直接设置点

- [Results.begin:181–195](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/contracts.py#L181-L195)：open scope quota 耗尽且已有该 identity ordinal 时，造一个包含 gap/UNAVAILABLE 的 INCOMPLETE 健康快照；该次历史 rejected=0 且 image 内容为 TimeoutError，不符合此特征。
- [Results._commit:225–244](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/contracts.py#L225-L244)：seal 后若 expected source 缺失且未到 deadline 不提交；否则 **missing 或 force 或已有 UNAVAILABLE** 优先成 INCOMPLETE，然后才判 pending.failed→FAILED；正常为 COMMITTED。`terminate` 可 force 闭合：[253–260](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/contracts.py#L253-L260)。
- [NetworkConsumer._receive:84–94](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/continuous.py#L84-L94)：仅客户端读 VALID asset 失败时，将本地 snapshot 设 INCOMPLETE，row 另标 UNAVAILABLE+read_error。历史 ordinal20 服务端已 INCOMPLETE，无这个特征。

pipeline.publish 在 workflow.started 建 source `image/items`；metadata 预算失败会写明 `metadata budget`；capture 在 load 冻结 image，source 节点冻结 items；提交被 Unavailable 拒绝会保存明确字符串：[Pipeline:61–115](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/pipeline.py#L61-L115)。历史 image 是带 task_id 的导出 outcome 字典，不是上述 rejection 字符串，也不是缺源 fallback `deadline`。

### 6.2 与原 task_id 匹配的导出调用链

`Pipeline.capture(load)` → `AsyncExports.submit`（复制前记 submitted、复制后记 copied、建立 task_id）→ owner `_own` → callback `Pipeline.export_done` → `Results.value(image, (outcome.status, outcome))` → `_commit` → 服务端 Snapshot → 两个消费者。

定位：[旧 submit:30–54](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L30-L54)、[旧 _own:67–102](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L67-L102)、[Pipeline.export_done:117–123](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/pipeline.py#L117-L123)。BUDGET 中 export/seal/read 均为 0.5s，但各自起点与用途不同：[contracts:15–39](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/contracts.py#L15-L39)。

旧 `_own` 的精确显式候选：

| 行号 | 谓词 | 抛出的异常 |
|---|---|---|
| [84–85](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L84-L85) | `not pipe.poll(remaining())` | TimeoutError("start deadline") |
| 同两行 | poll 有数据，但 `pipe.recv() != ("STARTED", task)` | **同一个** TimeoutError("start deadline")，即便未到 deadline |
| [89–90](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L89-L90) | STARTED 成功后，completion poll 无数据 | TimeoutError("running export deadline") |

旧 except 只留下 `type(error).__name__`：[100–102](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L100-L102)。所以三者无法分开，也无法凭这个字符串彻底排除底层操作传播的同类型异常。DONE 身份错误在 [91–93](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L91-L93) 则显式是 ValueError，不应和 STARTED 错误混为一谈。adopt 预算错误一般是 Unavailable，同样没有原证据指向它。

`_retire`、共享内存/文件清理、资源释放在 callback 前；Pipeline 的 capture/export_done 使用同一 lock。这能影响报出的 commit 时间，但旧失败没有 phase 时间戳或 started/reaped 明细，不能从 commit 距 seal 54ms 倒推是哪个 poll 分支。参见 [旧 finally:103–125](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/async_exports.py#L103-L125)、[Pipeline.capture/export_done:96–123](https://github.com/jsdfhasuh/emo_master/blob/1657a65e5700b963000090964bd1b54227e1d550/prototypes/runtime_pages_p0/pipeline.py#L96-L123)。cleanup 最终归零支持该场景最终资源回收完成，不证明过程中没有等待/性能问题，也不证明生产 NG 路径相同。

## 7. abc7ae 做了什么，没证明什么

- P0 exporter 增加 `detail/stage/budget_ms/elapsed_ms/copy_ms/queue_ms` 的失败 outcome：[当前 async_exports:77–111](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/prototypes/runtime_pages_p0/async_exports.py#L77-L111)。这对未来失败的定位有用；历史记录无法补出这些值。STARTED mismatch 仍与 start deadline 共用同一 raise，所以新增 `stage=start` 也不能单独区分那两个谓词。
- P0 读资产改成最多四个 64KiB wire chunks 一批，减少跨 executor dispatch；保留 read deadline/cancel/charge 语义：[resources:159–202](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/prototypes/runtime_pages_p0/resources.py#L159-L202)、[network:156–181](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/prototypes/runtime_pages_p0/network.py#L156-L181)。可影响整体负载；没有证据证明它解决旧 server-side TimeoutError，更未证明它修改生产 NG 状态路径。
- NG 第 261 行仅加断言消息 `result.model_dump()`：[当前测试:255–270](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/tests/runtime/test_production_boundaries.py#L255-L270)。这是补诊断，不是生产逻辑变更。
- presentation、runner、worker_main、比较算子、正式客户端、renderer、NG fixture 在核对范围内字节未变。当前其他测试/fixture PASS 只能说明那些既定场景本轮结果；不能反向补齐历史失败 source，也不能关闭两个历史未明根因。

## 8. 最小补证与修复调查建议（均未实施）

### 8.1 第一优先级：一次失败留下足够归因字段

保持现有 watchdog/deadline/断言与 first-fail；不采用延长超时或 retry-until-pass。

1. 生产 NG：保存 **该失败 ClosedResult 完整 model_dump**，包含 identity、executionTerminal、expectedSourceIds、每 source.state/reasonCode/reason/valueJson/image.ownerResultKey、timing。abc 测试新增消息已覆盖这一步；旧结果无法恢复。
2. 同一个 key/invocation 保存所有 job 的终态/完整 worker error、presentationErrors、exporter.errors/stats、collector captureErrors/rejected；不要只留最后一个工作正常的 job。
3. 对每个 image task/slot/lane 保存有界 phase 记录：frozenAt、worker seal、mailbox 接收、parent submit/deadline、dequeue、encoder start/reply、adopt start/end、callback start/end、finalize 时 export 是否存在、lane credit 释放、必要时 quarantine/reap/spawn。明确每个 clock 域，不能混入远端时钟差。
4. P0 保留 task_id 关联的 STARTED/DONE 是否收到及期望/实际身份、每次 poll 开始/结束、当时 remaining；记录 `_own` 当前新增 stage/detail 与 bounded started/reaped。这才可在三个显式候选中选定一个。

### 8.2 已证实的可观测性缺口，适合最小修改调查

- P0 STARTED identity mismatch 应与 start poll timeout 分开错误分类；先确认协议消息为何不匹配，不能把它继续统称性能超时。
- 生产 `encodeWorker` 的 reply.error 被父端 `invalid export reply` 吞没；asset adopt 的 OSError/ValueError 也被统一 EXPORT_FAILED。建议保留有限长度、带 key/source/phase 的原异常，不扩大无界日志。
- `runner._capture` 只计数，不能回溯哪个 method/source 抛了异常；建议有界记录最后若干原始原因，与 source manifest 对齐。
- `ExportPool._run:203–204` 的“收到 ok 但已过 deadline”分支没有增加 `stats["timed_out"]`，而另外两个 timeout 分支增加了。不能把 timed_out==0 当作从未发生 EXPORT_TIMEOUT 的证据。建议修正/明确指标含义，另加 phase 标签。[确切分支](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/exporter.py#L189-L207)
- 只有 trace 确认是 lane-release 或双 deadline 时，才考虑最小所有权/计时语义修复：必须保证 encoder 真正停止读 slab、adoption/retirement 所有权完成后才释放，不得为了让断言通过提前放行、跳过 quarantine 或丢掉失败源。

### 8.3 停止条件与验收边界

当前只读调查到此已能给出“两个历史失败各在哪一层、哪些分支可达、哪些归因被排除、缺哪几项原 trace”。**仍不能给出生产 NG 的唯一 reason；P0 也不能在三个显式谓词中作唯一选择。** 若原 CI 没有额外 artifact 保存这些字段，历史根因无法靠静态代码再压缩；后续需要授权的最小补诊断验证，而不是继续把新的 PASS 当历史解释。本附录不授权实施修复，不更改原验收结论。

## 9. 追加：获授权的一次 VARIABLE_UNAVAILABLE 隔离补证

详见 [独立补证报告](probes/VARIABLE_UNAVAILABLE.md)、[首次运行 JSON](probes/variable-unavailable-first-run.json)、[完整首日志](probes/variable-unavailable-first-run.log)与[原样辅助脚本](probes/variable_unavailable_probe.py)。

在既有 Python 3.10.22 venv 中，仅运行一次最小内存 fixture：真实 collector.end 在注入变量 KeyError 后发出 VARIABLE_UNAVAILABLE；真实 ClosedSource/ClosedResult 校验失败；ResultStore.close 已先 pop open，未发布 history/latest。隔离调用真实 service._consume/_finish 后，首异常留下 pending/未归还 credit；同一对象模拟下一次 _finish 删除 pending 但仍未归还 credit。独立合法 INVALID_VALUE 对照正常 INCOMPLETE 并归还 credit。没有构造 Runtime、启动 worker/后台线程/网络、访问数据库或设备。

这是新确认的独立模型/闭合所有权缺口，不是历史 NG 的解释，也不是 UI/真实多 worker 验收。历史 NG 没有 global source；它的 sources/terminal 缺证与 P0 的三个 TimeoutError 谓词歧义均保持未解。没有实施修复。
