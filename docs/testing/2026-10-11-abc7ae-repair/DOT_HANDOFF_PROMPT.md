# dot：固定交付 SHA 复验（不修改代码）

验收仓库 `jsdfhasuh/emo_master`，分支 `agent/runtime-workflow-architecture-v1`。**使用用户交付消息指定的40位 SHA**，不要只测试可漂移分支顶端，也不要以 PR merge SHA 通过替代该 SHA CI。

## 版本与边界

先记录 `git rev-parse HEAD`、status、实际源路径、Python/SQLite/Qt/桌面/显示缩放；保留现场未提交改动，用独立 checkout/数据目录。对照本目录 `REPORT.md`、`validation-summary.json`、`SOURCE_MANIFEST.json`、`evidence-index.json`。原产品基线 abc7ae、原验收归档 adba329 的结论只作历史参照，不搬成新 SHA 通过。

Designer 和 Runtime 均从固定 SHA 新建进程，记录 PID/启动命令/工作目录。禁止连接生产相机、PLC、机器人、生产TCP或生产数据库。只复验，不改源码/测试/deadline，不 commit/push/merge/release/deploy；不要 reset 脏工作区。开始前固定轮数，记录全部结果，首次失败原日志不覆盖；不反复重试取绿、不通过额外 sleep 或增 timeout 隐藏竞争。

## 必测关键词与断言

1. **变量捕获 / INVALID_VALUE / 模型闭合原子性 / credit恰一次**：变量及计数器读取失败有 `VARIABLE_UNAVAILABLE` detail、合法既有 reasonCode、可经 DTO/RPC/客户端解码。故障模型校验失败仍可观察同一身份 INCOMPLETE，不丢 open/pending 或错误。重复/并发 finish、晚到 callback 不双重 release。真实隔离 Runtime 连续结果超过8个后仍工作；最终取回8个scope credit、图像 lane不早退、worker/reader/exporter/资产实际退休。隔离模型和真实 Runtime 故障注入分别记账。
2. **SQLite累计BEGIN竞争 / FULL+WAL / 原预算 / 恰一次提交**：真实 blocker/writer，保留2s/5ms，累计 failed BEGIN + observer健康证明进入竞争；释放锁前成功BEGIN、业务SELECT/INSERT、COMMIT均为0。释放后成功BEGIN/业务体/COMMIT恰一次，durable seq=[1]、完整性正常；核对实际writer FULL(2)/WAL/busy_timeout5000。预算边界/restore失败、未知COMMIT/外部ACK不得自动重放。原abc7ae首失败保留，不改判。
3. **O20 SQLite [7,13] 两独立Job / 资源释放 / 保存重开**：从空白工程拖入并配置，预览建表不写库、明确确认才建表，两次不同Job/workflowRun写入[7,13]；最终生产者实际退休后正常保存重开，字段映射与唯一writeId均保持。不要直接SQL建表代替GUI步骤。与SQLite并发/变量/计数器事务专项分开。
4. **ROI四路 / maskedImage / 1:1原图 / PNG+JSON+同预览ZIP**：未Apply草稿上传本地图片；bbox/rotatedBox/polygon；四个端口可见。原始像素PNG不是缩略图；ZIP包含四幅PNG与result.json，逐像素并尽量字节核对合法下载资产。JSON frame/roi及requestId/generation/session/inputSHA/端口资产匹配同一次预览。只标requestedParams，resolvedParams=null，不伪造executionId或借用通用调试另一次结果。
5. **ROI生命周期与写盘**：坏图后有效图恢复、快速改参旧回包、同ID不同窗口、取消/过期、Runtime重连、文件选择器内改参/关闭、磁盘失败；旧结果不能覆盖/导出为新身份，失败不毁原文件、不残留临时文件。关闭等待所有producer，指向原Runtime，不误删持久资产。截图核对参数来源说明没有被压掉，GTK缺后缀保存单独验证。
6. **D01–D08 / 原图独立试运行 / step into-out-over / 断点 / 80秒暂停继续 / run-to / 完整导出**：控件真实操作，临时试验不Apply、不改正式项目；同一暂停身份/sequence保持后继续，图像上游变换与导出身份、像素对应。不要只运行pytest就宣称原生桌面PASS。
7. **5路While带图 / 持续图像 / 双网络消费者100%覆盖**：独立PID，首Job停后其余继续；双消费者各40/40、ordinal6–45全部解码、无INCOMPLETE/read_error；最终memory/staging/cache/lease/owners为0。记录实际调用身份及最终资源，而非仅窗口消失。
8. **业务NG不中止 / image-count-judge同invocation / 本机与网络分层诊断**：合法false/NG仍AVAILABLE，同resultKey、invocation和匹配workflow.completed，主动stop前RUNNING。失败先保存本机ClosedResult终态/源状态/reason、origin/iteration、job/result/taskId及export时间线，再看消费者。P0 start_wait、start_identity、completion_wait、completion_identity必须分开。一次或三次通过只写未复现，不写历史根因已关闭。

## 产出

逐项 PASS/FAIL/SKIP/NOT_RUN，软件/offscreen、真实Qt自动化、人工桌面、故障注入分开。全量与专项不累加。报告给固定SHA、首失败、精确操作/命令、PID、日志/JUnit/截图、原PNG/JSON/ZIP及SHA256、事务持久证据和所有权退休证据；无法实测的Debian13 GTK/高DPI/设备/包/部署明确NOT_RUN。

即使本轮全通过，历史生产NG与P0原因仍需新的直接证据才能关闭；本轮仅复验，不能擅自修改、提交、推送、合并、发布或部署。
