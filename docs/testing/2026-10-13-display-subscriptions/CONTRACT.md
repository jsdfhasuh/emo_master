# W01 有界订阅与 A02 实时显示契约

## 基线和不改判原则

产品基线 `8c1340461d9675ab3a5f078135f17407fa027cc7`；证据归档
`b27b44ca715eaf904286afe68b04f94cbfde489e` 仅增加 docs，产品源码相同。
本轮先核验该归档的 16 个 Git blob、3 个重组 ZIP 原 SHA 和 452 个包内文件。
Windows 检出文本的 CRLF 不等于 Git blob 损坏：第一次直接校验检出文本在
CASE-MATRIX 失败，随后按提交原字节校验全部通过；未修改清单。

原 W01 五路订阅 FAIL 和原 A02 trial0 全 96 显示覆盖 FAIL 均保留。
原 trial0 consumer0 ordinal2 已解码但未 live，两窗口都接 consumer0 的 hub；
consumer1 已 live。后继 ordinal3 提前 3.839415ms 到达只支持 supersession 推断，
没有历史决策时 high/generation，不能用新增测试倒推为历史根因已证实。

## W01：连接和工作分别拥有资源

旧实现把每条 Subscribe 流长期占用一个 display 工作槽。两个同步工作线程
因等待变更被订阅独占，而 OperatorWindow 每个 Job 都需要一个真实 Session。
五 Job controller/latest 测试没有覆盖这条链路。

新实现不增加工作线程：display executor 仍为 **2**。Subscribe 的同步构建、
单次 next 和 close 在这两个工作线程执行，经 FIFO semaphore 调度。
取消回调沿用现有 cleanup executor，先发起再等在途工作，防止回调负责解锁
构建/next 时却排在其后形成循环等待；所有回调完成前仍不归还订阅额度。
原 20ms 等待移到 asyncio，不占工作线程。每条流最多一个在途工作，没有每帧
无界任务队列；yield 仍受 gRPC 下游背压。慢连接只保留自己的下一条消息。

订阅容器与工作容器分开：**每 Job 最多两个独立网络消费者，最多 32 个 Job，
合计最多 64 条流**。32 对齐现有 ResultStore 的 32 个 latest 地址上界，不是提高
执行 Job 上限，也不意味着 32 Job 的性能验收通过。多 scope 仍共享原 latest
缓存及 expiry 契约。单个 Job 不能抢占其他 Job 的订阅名额；总 Job 数超限明确
RESOURCE_EXHAUSTED，不取消安全上界，不把上限无限随 maxConcurrentJobs 扩张。
最多 64 条在途流/工作等待者，最多两个实际工作；每条流不累积回复队列，当前/
在途快照受现有消息大小上限约束，不新增长期结果/图像缓存。此为有限资源上界，非低内存承诺。

同 Job 的本地多窗口继续共享 DisplayHub/DisplaySession；切 tab 不建新连接。
五个后台 Job 仍保持元数据订阅，隐藏页的图像读取仍按原 imageDemand 控制。
没有采用隐藏任务占位、可见任务被迫逐个抢槽的按需订阅方案。不同网络进程
的第三个同 Job 订阅仍会明确被拒绝；调用方应共享会话或关闭不再需要的消费者。

取消不等于退休。只有在途同步工作、取消回调、迭代器 close 和 gRPC 响应生命周期
都结束，才能归还订阅名额。同步工作不能被 Python 强杀；被故障注入阻塞时继续
占用工作额度，控制 RPC 隔离仍保留。close 超时必须暴露，不能伪造已释放。

Windows 首次“运行中直接关闭”还暴露了原 `stopAll` 的串行停止等待：前三 Job
各等到原 5 秒 grace，后继 Job 之后才收到停止，整窗 25 秒观察期限被耗尽。
本轮改为先向所有已确认 Job 请求停止，再按各 Job 原 deadline 等待真实退休；
未增加 grace、窗口观察期限或强杀许可。未知 Start 和独立失败仍逐 Job 保留，
不能因某一路停止失败阻止其他路收到停止。正常全停后关闭与运行中直接关闭分别测试。

`server.active['display']` 继续表示流数；`displaySubscriptions.workActive/workPeak`
表示实际工作数。512 条有界 admission 事件记录单调序号、时间、订阅 ID、
Runtime/Job、各 Job 占槽数、拒绝原因及最终退休。环形记录需及时导出，不能当永久审计库。

## A02：latest 显示不是逐帧录像

1. **源结果**：每次 ClosedResult 的完整源清单、AVAILABLE/不可用原因、调用身份、
   终态和 ownerResultKey 保持原义。显示合并不得改变 COMPLETE/INCOMPLETE、业务 NG，
   不得伪造缺失图像。ResultStore/AssetStore 仍是有界留存，不是无限历史数据库。
2. **传输/解码**：Subscribe 保留原 bounded replay/reset 语义，不是可靠无限消息队列。
   在明确采集窗口内要求全 96 received/decoded 时，必须逐项核对 1–96 及原始
   source/hash/identity，不能因是预热而删除。需要永久逐帧留档的业务必须采用显式
   持久化结果/审计输出及容量规划，不能把 GUI 或有限 replay 当可靠档案。
3. **live 模型**：同 Runtime/Job/generation/scope 内，已经接受更高 ordinal 的
   ClosedResult 后，较旧图像即使解码成功也不得覆盖新结果；新终态为 FAILED 或
   INCOMPLETE 也不恢复旧 COMPLETE 图。允许合并旧帧，但必须记录决策原因。
4. **Qt commit/paint**：GUI 有界采样最新快照，多个 live 更新可能被合并，窗口隐藏
   或事件循环繁忙也不承诺逐帧绘制。两窗共享一个 hub 不是两次独立网络交付。
   想要逐帧回放需独立产品能力，不能强迫 live 堆积所有帧造成无界延迟。

`DisplaySession.records` 在锁内记录决策时 task/current generation、Runtime/Job、
scope/invocation、high ordinal/resultKey、expiry、源 owner/SHA、结果终态和原因。
决策为 APPLIED、SUPERSEDED、GENERATION_CHANGED、JOB_CHANGED、RUNTIME_CHANGED 或
SCOPE_EXPIRED；在解码前已过期的排队任务也有明确记录。原 applied_to_live、时间和
缺号字段不删除；128 条上界不变，超过上界须按现有 retention 规则声明不完整。

**契约澄清不改写验收**：原“全96都被画出”要求仍为 FAIL；新增 deterministic
supersession 测试故意要求 consumer0 仅 ordinal2 未 live，但仍要求双端完整96张
变化图像及像素/owner/哈希验证。该测试验证最新结果防倒灌契约，绝不是把原96
显示缺失重新计成96通过。Windows ABBA仍保留原判据，不改分母/期限。

## 本轮验证边界

- 原失败、新测试首次失败/修正分别归档，不以重跑挑绿。
- 五个真实 GUI Session：同时启动、逐 tab、快速切页、共享窗口、重连、单停、
  主路重启、全停和关闭；检查订阅、线程、exporter、Job 资源及 admission/retirement。
- 注入构建/next/取消回调/close 阻塞，证明不提前归还；同 Job 超额与跨 Job 公平。
- 双网络96张变化内容，ordinal2解码完成被闸门延后，ordinal3已接受后释放；检查每个
  source owner、完整像素、两个消费者96原始行和明确合并原因。
- 原生 Qt 自动化、offscreen 回归、故障注入、人工桌面与 Actions 分列。
- 不宣称 SQLite/O20、D01–D08 本轮人工复验、系统 DPI、真实设备或历史根因关闭。
- 无合并、发布、部署；Windows 精确修复 SHA 的 CI 由 Codex 跟踪。
