# Codex 修复交接提示词

请在 jsdfhasuh/emo_master 的 agent/runtime-workflow-architecture-v1 分支上，先阅读本目录 9 页中文 PDF/DOCX 报告、README-8c.md、CASE-MATRIX-8c.json，并校验 SHA256SUMS.txt 后将五个证据 ZIP 解压到同一个独立目录。各 ZIP 可独立解压，共用 manifest；按内部 SHA 清单验证证据。不要把报告结论当成新 HEAD 已经通过的证明。

## 固定基线与工作范围

本轮只验收了 8c1340461d9675ab3a5f078135f17407fa027cc7，环境为 Debian 原生 Qt 桌面。包含本提示词的提交仅归档 docs，不包含产品修复。先记录当前 HEAD 与固定基线的差异；若已有相关修复，应逐项核对、补回归，不能把后续 HEAD 冒称已经桌面复验。优先处理下述两个未关闭项。保留原始证据，不改写失败或降低验收阈值；产品修复与新的测试证据须使用新提交和新目录。

## P1 / W01：五路真实 DisplaySession 与全局 display 配额冲突

先读 report/five-while-display-quota-audit.json、真实五路 observer JSONL、产品运行日志、截图 117/119/120 及视频节选10。原五入口 main-run、second-run、flow3-run、flow4-run、flow5-run 启动后，产品为各 Job 创建真实 DisplaySession；服务端 display admission 容量为2。Subscribe 在流生命周期内持有配额。真实 UI 出现 RESOURCE_EXHAUSTED，detail=display；这是显示订阅路径失败，不是根据正确 PNG 推断作业执行失败。observer 只读本地 snapshot，没有额外订阅。

固定基线定位：src/emo_master/apps/runtime/grpc_server/aio_entry.py 的容量、admission、Subscribe retirement；src/emo_master/apps/operator_runtime/main.py 的每 Job DisplaySession 创建；src/emo_master/clients/runtime/display_session.py 的 Snapshot、Subscribe、错误呈现、关闭线程。精确行号以审计 JSON 和固定基线为准。未记录逐 RPC active 计数，不能声称已直接证明每次拒绝时的槽位归属。

请评审并落实有界容量/订阅复用/活跃展示需求的架构契约，而不是盲目把2改5、无限增大或取消安全限制。保留隔离、公平、背压、错误可见性、取消与资源上界。若采用按需订阅，需明确多 Job/多窗/切 tab 的正确行为及不会让不可见任务阻塞可见需求。补充真实五 Session 集成测试，而不只复用 controller/latest-result 测试：同时启动、逐 tab 查看、快速切换、重连、单停主路其余四路继续、全部停止、关闭、重启，以及 lease/slot/thread/process 退休；记录实际 admission 与释放事件。

Snapshot 属于 control 路径，即使 Subscribe 被拒绝仍可能间歇取得正确图；Job RUNNING 和偶得 COMPLETE 图都不能关闭订阅 FAIL。22 张正确样图（5/5/5/5/2）仅证明采样图像身份与像素正确。原自动五 While PASS 没有创建五个真实 GUI DisplaySession。

## P2 / A02：保留首轮全96显示缺号并先明确 live 契约

先读 evidence/abba 全部四组原 trace/trial/ownersclosed 及 report/independent-bgr-raw-audit.json、trial0-warmup-ordinal2-timeline.json 和复算脚本。首组 trial0 预热 ordinal2：双网络 consumer received/decode 各96完整，但 consumer0 live applied 及两 Qt 窗口 commit/paint 缺2；系统确有两个独立网络 consumer；两 Qt 窗口都接 clients[0] 的 DisplayHub，并非每窗各接一个 consumer。consumer1 的 ordinal2 已 live。正式 ordinal9–96 的88条各链完整 PASS；全96显示覆盖仍不满足原验收，不能删预热、修改 deadline 或重试择优取绿。

supersession 是固定源码与时序的支持性推断（consumer0 收到 ordinal3 比 ordinal2 解码完成早3.839415ms），没有决策瞬间 high/generation 的直接记录。请补决策级观测与确定性回归以验证，不能把推断写成已证明历史根因。先明确实时 latest 合并展示是否允许跳过旧帧、在哪一层承诺逐帧完整；不要未经架构判断强迫 live 画每一帧。即使确认 latest 合并为设计行为，也须显式保留全96结果、解释可观测的合并原因，并维护可靠结果/审计数据完整性与终态正确性，不能靠变更统计口径隐藏失败。

## 证据边界与回归要求

- 原 ABBA、独立96像素 probe、16组合 native Qt gallery、真实 Designer D01–D08、NG 与五 While 分层解释；gallery 不是 Designer，也不是系统 DPI 矩阵。
- 静态同图 probe 不能证明跨 ordinal 新鲜度。增加变化内容/明确 identity 的新测试，验证 ownerResultKey、Job、generation、源 SHA 与展示内容对应，不能只比较同一静态图。
- NG40 和五 While22 的编码 SHA/完整像素验证正确，但不代表全部帧、订阅健康或历史根因已关闭。旧历史根因仍 OPEN。
- D05/D06 长暂停、D02/D07/D08 黑色真实图像、首 helper psutil 导入失败均保留；helper 失败在产品启动前，不归为产品问题。
- 119/125 原截图交付，六张含无关宿主信息的截图有排除清单；10 段无损录像是节选，原完整录像仅本地保留并提供 hash/区间证明。不要声称交付了完整录像。
- 资源退休须按 close/loop-return 与后续 final-process-check 的实际时序陈述；PID5860 是后续检查才证实消失。ffmpeg 有 SIGINT 正常结束日志和可读媒体，无独立 wait 退出码。
- 新修复应跑受影响单元/集成测试与真实 Qt 重现；保留第一次失败、原日志、命令、退出码、源码/工程 SHA、截图、视频与资源终态。不要改原 fixture 来制造通过。明确 PASS/FAIL/NOT_RUN，不以局部成功声称全量产品、SQLite/O20 或性能已通过。
- Windows CI 由你在新的修复 SHA 上执行/核验，报告精确 commit、workflow/run/job 和结果。本轮 Debian 归档没有查询或纳入新的 Windows Actions 结果，不能借旧 CI 替代。

最后交付：根因与架构取舍、最小修复 diff、新测试及其覆盖边界、原问题到新证据的映射、精确修复 SHA 和仍未验证的风险。不要无授权 merge、force push 或改写既有分支历史。

GitHub transfer note: three ZIP files exceed the connector request-body limit after base64 encoding. Their unchanged bytes are stored as .part01/.part02 files. Run `python reassemble_evidence.py` first; it verifies part and original SHA256 and refuses to overwrite different bytes. ZIP-PARTS.json records the mapping. The other two ZIPs are stored whole.
