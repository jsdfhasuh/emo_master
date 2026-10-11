# EmoMaster abc7ae：代码定位与最小补证附录

目标提交：`abc7ae0541e997be0bbe3a4794ad604c239de79a`。2026-10-11。

这是冻结验收报告之后，依据“尽量查阅代码，定位到具体位置”的追加诊断。它不改写此前全量首 FAIL、不把当前 PASS 回填成历史通过。未修改产品源码、仓库测试或原断言；未提交、推送、部署；未连接实际设备。唯一追加运行是独立证据目录内的一次有界模型/资源记账模拟，明确不是原生 UI 或历史 NG 重现。

## 最值得先处理的四项

### 1. 新确认：变量捕获错误码与结果模型不一致，破坏闭合记账

- 位置：`presentation/collector.py:197–212` 产生 `VARIABLE_UNAVAILABLE`；`core/presentation/results.py:54–62` 的 reasonCode 不接受它。
- 进一步位置：`presentation/store.py:88–103` 在构造/验证 ClosedResult 前已移除 open；`presentation/service.py:_finish` 仅在 close 返回成功时归还捕获 credit。
- 一次隔离补证确认：真实 collector 在注入 KeyError 后发此错误码，真实模型抛 ValidationError；真实 store 已丢 open，但没有 history/latest。首轮 `_consume/_finish` 异常后 pending 保留、credit 未还；模拟后续一次 `_finish` 会删除 pending 但仍不归还 credit。独立合法 reason 对照可正常生成 INCOMPLETE 并归还 credit。
- 运行仅 Python 主线程、内存对象和一个受计数观察的真实 BoundedSemaphore；无 Runtime 构造、Worker、数据库、网络或设备。脚本 exit 0 表示预期缺口被观测到，不是产品验收 PASS。
- 最小建议：统一生产方/消费方合法 reason 契约；结果模型校验先于破坏性状态变更，并为失败闭合定义明确、恰一次的 ownership/credit 收尾。不要仅吞掉异常或把非法结果伪装成 COMPLETE。真实 reader/monitor 线程的影响仍需后续集成补证。
- 它不是历史 NG 根因：那份 fixture 的三个来源都是 node_output，不走 global_variable/global_counter 错误分支。

详细证据：`NG_INCOMPLETE.md` 的枚举补证部分；`probes/variable_unavailable_probe.py`、首轮 JSON、日志和退出码。

### 2. SQLite 首 FAIL：确认观察语义不匹配，原运行唯一根因仍未证明

- 位置：`tests/test_r3_sqlite_phases.py:489–493` 只轮询 native BEGIN 的瞬时 active；`sqlite_transactions.py:15–35` 已变为短 BEGIN 尝试之间的 Python sleep；`scripts/r3_sqlite_phases.py` 不观察这段外部等待。
- 既有诊断：631次失败 BEGIN 中629次小于1ms，389次采样仅一次抓到 active。这支持漏采解释，但不是原首败的完整 trace 或确定性复现。
- 最小建议：保留原2秒/5ms预算，用累计 entered/failed 与 observer 健康表达“已进入准入”；继续保留锁释放前无成功 BEGIN/body/commit、释放后恰一次提交和持久行等不变量。不要加 sleep 或重跑至通过。
- 额外边界：原 synchronous 断言检查 blocker 而非 writer；deadline oversleep、restore异常覆盖等只列为契约/覆盖待补，不假称已经发现对应生产事故。

详细定位与精确 SHA 行链接：`SQLITE.md`。

### 3. 历史 NG 与 P0 双消费者失败：两条路径，不能合并归因

- 生产 NG：旧 Windows 测试从本机 `presentation.store.latest` 读到 INCOMPLETE，在网络/GUI之前已经产生。历史未保存 sources、reasonCode、executionTerminal 或精确结果身份，尚不能指定缺少 image、count 还是 judge；后续实际业务值及 RUNNING 断言当次未执行。
- 生产状态规则：COMPLETED 且任一 source 不可用，或 UNKNOWN，才生成 INCOMPLETE；合法业务 false 本身仍为 AVAILABLE，NG只是显示文本。
- P0：ordinal20服务端已含 image UNAVAILABLE / TimeoutError，两个客户端只是收到同一状态且无 read_error。旧 `_own` 的最窄三个候选为 STARTED等待超时、STARTED消息/身份不匹配、completion等待超时；旧错误仅保留异常类，无法三选一。不是已经证实客户端读取超时，也不能仅凭TimeoutError认定500ms预算耗尽。
- 1657→abc7ae生产 presentation层代码未直接修改；其他改动可能间接影响调度，不证明“不可能改善”，也不证明历史已修好。
- 下一步：按附录最小字段保存完整失败对象、各source来源原因、阶段时间线和同一task/Job/invocation身份；保留原预算与所有失败，不自动重放外部动作。

详细调用链、所有直接状态/不可用分支、历史完整日志/JSON和来源哈希：`NG_INCOMPLETE.md`、`historical-evidence/`。

### 4. ROI 专用预览：局部UI/结果适配缺口，完整图像能力已存在

- 位置：`_editor_support.py:_pollResults` 将资产信息降成端口→ID；`roi/editor.py:handlePreviewResult` 只下载显示，丢弃结构化输出，未保留完整可导出身份。
- 算法返回四个图像，专用页显示三个，maskedImage未展示；底层完整PNG生成及受session/projectKey约束的下载已有。
- 最小原始PNG保存通常可局部补UI+不可变请求/结果记录+受限下载适配，无需重写ROI算法。若要完整审计身份ZIP，当前pure-preview回复缺resolvedParams等元数据，不能用rawParams或伪造operator-debug execution ID冒充。
- 通用ROI调试路线源码可达，但需另开会话、重新选输入并执行、逐端口导出，不能导出刚才pure-preview同一次结果；本轮未亲自完成ROI替代路线整段GUI，其他算子完整导出通过不能代替它。
- 新操作必须继续遵守generation、固定Runtime/session、指纹、租约、过期取消和所有producer实际退休后清理等边界。

详细函数/行号/协议/生命周期：`ROI_EXPORT.md`。

## 阅读与审查边界

三个专题均含固定提交的 GitHub 行链接。SQLite与ROI已互相交叉审查；NG核心历史归因和三个P0谓词也经独立代码对照。审查修正了过度确定的首败措辞、ROI证据来源和历史原件/派生材料归属。没有通过改源码让任何失败“变绿”。

本附录与原冻结验收ZIP分开保存；原报告中的D01–D08、O20、全量和CI计数不因这里的单次诊断而重复相加。
