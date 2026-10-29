# 页面展示异常接入现有运行日志

日期：2026-10-29。源码基线：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`，叠加已有未提交工作及本轮局部修改。前一轮提示改进的记录保留在 `../2026-10-29-preview-budget-messages/`。

## 原因与范围

原实现通过独立的展示结果通道返回 `UNAVAILABLE/reasonCode/reason`，完整诊断只留在有限内存队列；没有写入普通 EventStore，JSONL 也没有展示事件白名单。仅修改提示不能解决用户在“运行 → 打开日志”中查不到问题的情况。

本轮复用现有 EventStore、SQLite 事件历史、JSONL writer 和 Designer LogDialog，不创建第二套日志窗口。覆盖页面结果的容量、忙碌、导出、通信、数据异常和恢复，以及预览操作失败；不宣称已复现内网截图的具体根因。

## 行为

| 情况 | 等级 |
| --- | --- |
| 首次预览通道忙 | INFO |
| 持续/反复忙碌的汇总 | WARN |
| 图片/数据超限、导出超时 | WARN |
| 通道异常后尚未释放、导出失败、通信异常、展示数据异常/来源缺失 | ERROR |
| 恢复可用 | INFO |
| 正常缺图、跳过、取消 | 不新增异常日志 |

- 使用 `presentation.source.unavailable` / `presentation.source.recovered` 事件；业务节点失败沿用已有 `node.failed`，避免按页面绑定重复告警。
- 同任务、结果范围和数据源的同类异常首次记录，重复每 30 秒合并计数；不以原始字节数或结果编号作为无限增长的去重键。
- 通道忙与正常帧快速交替不会逐帧打印“恢复”；收到持续约 1 秒的正常结果后记录恢复，任务结束时补充已有实际恢复或尚未报告的次数。
- 状态跟随结果序号，较旧结果晚到不能把较新故障误判为恢复。该日志记录当前来源状态变化及合并次数，不作为逐帧历史归档。
- 日志包含工程/任务/工作流/节点、原始原因、结果身份、导出阶段与本次/累计次数，不写图像像素或业务输出值。
- logger 留在父进程，不放入传给 spawn worker 的配置；日志写入异常保留在现有诊断队列，不阻止结果及采集额度释放。
- Runtime 的最后一帧展示结果可能晚于 `job.completed`。事件流在已有 worker 退休等待后，有界等待该任务展示收尾，并等待新增展示日志的 JSONL 写入游标，再完成最终补读。
- 新 Runtime 通过已有能力列表提供 `presentation_logs`。Designer 对仅观察的 producer/consumer 使用独立的只读事件订阅，在 GUI 线程注入现有 `appendRuntimeEvent()`；普通 Run 已登记任务保留原订阅。停止查看不停止任务，重连沿用序号避免重复历史。外部观察不伪造本地工程会话身份。
- 日志订阅仅对临时断连/超时进行最多 3 次连续重试，收到事件后重置计数；权限/参数等明确错误直接停止。耗尽时记录 WARN 并说明重新查看可重连，主动停止查看不报错。

## 验证

环境：Python 3.10 / PySide2，解释器 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。

- Runtime 来源日志、结果收尾、终态流和共享文案：**54 passed in 4.89s**。见 `runtime-focused.log/xml`。
- 多来源采集、真实 spawned 导出/收尾集成、JSONL、终态资源回归：**43 passed in 51.24s**。
- 终态补读/JSONL 最新展示日志游标专项及既有相关测试：**24 passed in 11.51s**。与上述批次存在重叠，不累加总数。
- `QT_QPA_PLATFORM=windows` 下，页面观察日志和现有任务查看：**23 passed in 12.72s**。见 `designer-logs.log/xml`。覆盖实际 loopback gRPC → PreviewController → MainWindow → 原 LogDialog、双任务、取消、续读、完整详情及避免重复订阅。
- 原生日志窗口截图 `designer-log-window.png` 明确标注模拟数据，已检查 INFO/WARN/ERROR 三条记录及原始字节数详情。截图单测另行通过 1 项，与上述 23 项重叠。截图使用 `EMO_PRESENTATION_LOG_ARTIFACTS` 环境变量从新增 UI 测试生成。
- 共享提示提取后的 Windows 原生 renderer、组件、加载连续性与预算提示回归：**73 passed in 2.74s**，见 `renderer-regression.log/xml`。
- 日志观察器有界重试补充后的新测试文件：**9 passed in 6.69s**，覆盖一次断连恢复、临时连续失败有界、永久失败立即停止；见 `designer-retry.log/xml`。其中 4 项与前述 Designer 批次重叠。
- 本轮相关源码/测试 Ruff 与定点 `git diff --check` 通过。

## 保留失败与边界

- 终态流首轮测试有 2 个 teardown errors：测试替换的 `waitForRetirement` 未在 Runtime.close 前恢复，导致清理 `KeyError`；测试清理已修正，业务断言未弱化。
- 原生扩展回归 `test_preview.py`、`test_dispatch_queue_viewing.py`、`test_dispatch_live_page.py` 得到 **8 passed、2 failed**。两项 `testNormalRunEnablesSharedPageCapture[ALL/NONE]` 在 `window.startJob → _syncRuntimeProjectBeforeRun` 阶段触发配置确认，违反夹具 `unchanged published project must run directly` 断言，未进入预览或新增日志观察器。本轮未改动已有配置工作以绕过断言，失败保留。
- 未执行全量测试、冻结 EXE 验证、内网部署或真实相机/PLC 验收。没有提交、推送或发布。本轮接入需配套更新 Designer 和 Runtime；旧 Runtime 不会凭空产生新增日志事件。
