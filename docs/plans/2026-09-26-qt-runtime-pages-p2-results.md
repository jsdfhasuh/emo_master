# Qt 运行页面 P2 执行记录

起始 HEAD：`adb7b8ec1c12cddff0998323287b6cf4ae9e3a11`，分支
`agent/runtime-workflow-architecture-v1`，起始工作区 clean。按用户新授权进入 P2；
P0 原始 FAIL、200 ms / 5% / 500 ms 目标、读图完整率和 Qt 间歇崩溃继续保留。

环境：Windows、Python 3.10.21、PySide2 5.15.2.1、grpcio 1.78.0、protobuf 6.33.6。
完整依赖及系统版本见 `docs/evidence/p2/baseline/evidence.json`。

## 第一批

正式值契约、资源物化与完整编译、隔离 SQLite、Runner 路由前采集、Supervisor 接收、
业务顺序与游标分离、typed DisplayService 协议和薄适配。
真实 spawn、本地固定图像、内置 ImageLoader→Blob→Count 已读取 count=2；
准备后删除原图不影响任务，debug 增加和清零不改变同 projectId release=100。

验证命令：`python scripts/p2_validate.py --suite focused --output docs/evidence/p2/batch1-focused`。
75 passed；proto-drift、Ruff、mypy 均通过。原始日志和 dirty/受测代码摘要已记录。

基线：独立 git archive 执行 `python scripts/ci_check.py`，前三项通过，pytest 在
`test_visual_layout.py::testSidebarStaysCollapsedAcrossShowResizeAndRestore` 原生访问冲突，
退出码 3221225477。这是本轮编辑前重现的基线失败，不改断言或 skip。

开发中曾遇到测试 fixture 名冲突、长参数 ID、未设 PYTHONPATH 和新增 mypy 变量类型错误；
已修复并重新验证。后续验证全部由统一监督脚本记录原始日志，skip 不计 PASS。

首批提交 `6bf7235` 已正常推送。相关 core/runtime/e2e：431 passed、1 skipped（符号链接权限）。

## 第二批

正式模块新增共享内存冻结、两个 spawn 导出单元、资产库/有限租约、异步封闭与强杀栅栏。
专项逐步执行 85、87、89 项通过（包含重叠 P1 用例，不相加），proto/Ruff/mypy 通过。
实际验证图像 decode/摘要、同结果 count=2、可选 overlay 不自动打开、六种完成排列、
重复/跨作用域/换 Job、真实循环及重复子流程、强杀、封闭超时与迟到栅栏；
永久阻塞及损坏 IPC 各重复三次，确认导出 PID 替换和退出清理。
外层监督 300 s，单任务导出和封闭仍为 500 ms，回收确认 1 s。

第二批证据位于 `docs/evidence/p2/batch2-*`。首批证据 `.log` 被仓库通用忽略规则影响，
本批显式强制加入原日志，不修改日志内容或 JSON 内摘要。
第二批时图像 provenance 为逐帧独立 unknown，不猜测几何叠加；第三批增加下述受限可信适配。
第二批提交 `25ead9d` 已正常推送。

## 第三批：正式共享客户端与兼容入口

正式代码位于 `src/emo_master/clients/runtime/display_session.py`、
`apps/runtime/grpc_server/aio_entry.py` 和 `apps/runtime/presentation/`。
增量 protobuf DisplayService 与 RuntimeService 并存；旧 RuntimeClient 导入和默认服务入口保留。
默认设备执行路径不创建 PresentationService；显式开发入口才启用新链路。
正式 src 不 import prototypes。验证脚本仅复用旧 watchdog，不复用原型执行实现。

- 公共接口：Capabilities、Prepare、Start、ListJobs、Snapshot、Subscribe、ReadAsset、
  AcquireLease、ReleaseLease、ReleaseJob、DiscardPrepared。准备不启动；只在 Start 创建真实 Job。
  Python prepare 支持校验后的 debug/release 快照，网络 Prepare 本轮仅开放 debug，不开放 P5 发布部署。
- 独立共享邮箱隔离展示与原事件持久化队列；每 Job 16 个固定槽，发布状态、包序号、长度及 CRC。
  原 Job 生命周期/心跳仍走原通道；部分写入被强杀不阻塞父端 Pipe 接收。
- DisplaySession 固定三个线程：长期元数据流、健康快照、单有界读图/解码单元。
  两个观察者复用同一会话，不隐式 Start/Stop；退出只关闭自己的网络连接。
  RESET_REQUIRED、代际水位和旧 Job 栅栏、有限重放及权威 latest 补取，覆盖最后通知丢失。
- 结果开始序号与封闭 latest 分开：新 OPEN 不抹去已封闭结果；封闭结果按 ordinal 单调推进。
  新 INCOMPLETE/FAILED 不回退旧 OK，晚到图片不改封闭结果。排队失败的最终结果可由健康快照重试。
- ImageLoader 1.1.0 / Blob 1.2.0 限量弱引用逐帧适配，真实输入帧决定坐标空间和 parentFrameId。
  同文件重读是新帧，未知算子/版本、过期引用为 unknown；不按相同尺寸猜来源，不开发 Qt 几何叠加。
- grpc.aio 分类额度 control=4、events=2、display=2、camera=1、asset=2、bulk=2，cleanup=13。
  阻塞构造/next/close/取消回调、文件 I/O 离开 aio loop。直到真实工作及 gRPC 完成回调结束才归额度。
  永久阻塞旧代码占住其分类额度，停服明确报未收尾；不伪称线程已被 cancel 杀死。
- 旧 UploadPreviewImage 实际上传/读回、取消、64 MiB 内容超限及 framing 限制均有真实网络验证。
  C1—C4 使用真实 spawn Job/loopback；相机角色明确为 SIMULATED-NO-DEVICE，不接设备。
- ReleaseJob 只释放已终止、IPC/导出已收尾 Job 的展示所有权；有限租约保留读图能力。
  清理覆盖旧预览工作目录和重放引用，Windows 使用与旧预览写入相同的扩展路径，能清除强杀遗留长临时文件。
  debug 站点 DB/正式输出不随客户端退出删除；PresentationService 关闭恢复 Runtime 原回调，不关闭外部 Runtime。

正式契约、资源预算和接口细节见 [P2 契约](../runtime-pages-p2-contract.md)。
源文件清单可用 `git diff --name-only adb7b8e..HEAD` 核对，证据目录为 `docs/evidence/p2/`。

## 可复现命令与证据口径

在仓库根目录、项目 Python 3.10 环境中运行（PowerShell 示例）：

```powershell
$p2Python = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
& $p2Python scripts/p2_demo.py
& $p2Python scripts/p2_validate.py --suite focused --output docs/evidence/p2/recheck-focused
& $p2Python scripts/p2_validate.py --suite regression --output docs/evidence/p2/recheck-regression
& $p2Python scripts/p2_validate.py --suite network --output docs/evidence/p2/recheck-network
& $p2Python scripts/p2_validate.py --suite ci --output docs/evidence/p2/recheck-ci
& $p2Python scripts/p2_validate.py --suite measure --output docs/evidence/p2/recheck-measure --timeout 360
```

输出目录必须未存在，保留每次原始日志。监督器设置 PYTHONPATH、QT_QPA_PLATFORM=offscreen、
HUARAY_CAMERA_SMOKE=0，Windows 进程树监督；只改变外层 watchdog，不改变单任务 500 ms 期限。
演示自动创建临时项目/数据库/输出，实际 ImageLoader→Blob→Count、一个 Job、两个独立 gRPC 客户端，
校验同 resultKey/count=2/图像 SHA 与解码像素；单方退出后另一方仍可读取。

每次 evidence.json 记录真实 HEAD、完整 dirty 状态、受测 Python/proto/examples 源码逐文件 SHA256、
起止摘要、命令、日志摘要、退出码、监督期限；未提交工作区不会冒充 clean HEAD。
第三批测试 HEAD 为 `25ead9d8a81a8d720c6d86f1f1590db594a7ea94` + 记录的 dirty 源码。
源码摘要不含报告 Markdown，因此报告收口不改变受测代码身份。测试通过数有重叠，不相加。

## 本轮失败、修复与保留证据

以下目录全部保留，不能择优删除或改成 PASS：

| 证据 | 结果与处理 |
| --- | --- |
| baseline | 修改前独立 adb7b8e archive；Qt 原生访问冲突，非新功能造成的已知基线失败 |
| batch3-first | 92 passed / 1 failed；取消测试尚未等待两个阻塞 next 均进入，补同步闸门 |
| batch3-network | 95 passed / 1 failed；第二个 Job 仍 STARTING，补等待真实 RUNNING |
| batch3-isolated | 95 passed / 1 failed；150 ms 测试租约在等待 IPC 退休期间自然过期，移到退休后获取，额度和 TTL 不变 |
| measure-first | 原事件队列阻塞及重建订阅导致缺图；改独立邮箱、长驻流，旧失败保留 |
| measure-isolated | 30 次短窗口诊断；不是 96 次原窗口验收，数据年龄仍 FAIL |
| measure-96 / final-measure | 完整三组配对均未达目标，原始 timing/consumer/resource 曲线保留 |
| final-ci | 当时源码完整 CI 1059 passed / 2 skipped；不能代表随后修改，不能抹去组合崩溃 |
| cleanup-ci / ordinal-ci | 同一 Qt visual-layout 测试原生访问冲突；仍保留为完整 CI 阻塞 |
| accepted-focused | 106 passed / 1 teardown error：Windows 长路径残留删除失败；不是通过，已修清理路径并加真实残留验证 |
| path-cleanup-focused | 修复后 106 passed，proto/Ruff/mypy 全通过，code_stable=true |

`final-measure` 曾错误地用最高“开始序号”作客户端 live 水位，导致每客户端虽收到/解码 88/88，
仅 1—2/88 进入 live。按主计划 §7.1 修为最高“已封闭序号”，六种完成排列逐步验证
latest=max(已封闭序号)，没有改 P0/P1 测试或放宽门限。新修订测量单独记录，旧分母/结果保留。

Qt 崩溃位置为 `tests/designer/test_visual_layout.py:84`
`testSidebarStaysCollapsedAcrossShowResizeAndRestore`；pytest 子进程返回 3221225477，
ci_check 外层可能归一为 4294967295。没有修改 Qt 实现、删断言或加 skip 来掩盖它。
两类 skip 分列：Windows symlink 权限不可用、HUARAY_CAMERA_SMOKE 未开启真实相机；不是 PASS。

## 验收覆盖与边界

| 项目 | 已有正式路径证据及限制 |
| --- | --- |
| B01 / C1—C4 | 真实 spawn 和 gRPC，1/2 Job、事件/展示/模拟预览同时占槽；超额 RESOURCE_EXHAUSTED；控制调用 200 ms deadline。停止受理不等同整个任务/旧预览清理完成 |
| B02—B04 | 封闭/迟到、终态栅栏、子流程开始后强杀、反复永久阻塞/损坏导出 IPC、邮箱半写与 CRC、最后通知丢失恢复；不能据此声称所有第三方阻塞算子可在线强制清理 |
| B05—B06 | 准备不启动；真实计数输出、新绑定仅下一次显式 Job 生效；不改变旧 Job/release |
| B07 / B09 | 路由前冻结嵌套值/图像，已知 ImageLoader/Blob 逐帧链及 unknown 拒绝猜测；非完整 Qt 几何渲染 |
| B08 | 单值/总值/图像预算、固定槽、有限历史/租约有校验；极端 OPEN/IPC 超额主要计拒绝诊断，整进程/全部故障组合证明仍不足 |
| B12 | 原图删除/改动、物化副本篡改、参数语义、中文空格资源根、非法路径 P1 回归；跨盘符、所有权限失败矩阵 NOT_RUN |
| R09 | 六种结果封闭排列、重复、跨 scope/Job 与新失败栅栏；客户端新封闭水位栅栏旧解码，开始水位单列 |
| R10 | 每 Job 一个独占导出槽，总计两个 spawn 编码单元；重复阻塞及 IPC 异常确认旧 PID 退出并替换，不用 Future.cancel 冒充停止 |
| R11 | 同 projectId release=100，debug 增加/清零后仍100；真实 ImageSaver 的 debug 输出不覆盖 release 文件；仅临时状态，不迁移现场 DB |
| 兼容 | 原 2.1 无页面路径、旧上传/读回与 RuntimeService 保留；新 2.2 展示通道显式启用，不切默认入口 |

正式支持的绑定为指定调用作用域内 node_output/workflow_output（包括实际计数节点输出）。
跨作用域混合来源、global_counter/runtime_status 来源暂在 prepare 明确拒绝；不静默取最后值。
同一 Runtime 最多保留两个展示 Job、八个准备记录，终态需显式 release/discard；不是完整历史系统。

共享内存 16 MiB、两导出预留 96 MiB、每 Job 元数据/OPEN/邮箱/历史/重放预留 64 MiB、
读取预留 16 MiB；两 Job 账本 256 MiB，每 Job 112 MiB。图像8 MiB/来源、16 MiB/结果，
cache256 MiB，租约16个/30s/64 MiB，staging实际≤16 MiB（原上限64 MiB）。
导出/封闭/读图各500 ms，导出强杀回收确认1s；源码及报告均未提高原目标。
账本包含独立邮箱等新增组件，但不是整进程 RSS 上界；网络、旧预览和库基线不能忽略。

## 阶段结论

本批正式最小链路已实现；受支持范围的真实功能/一致性/旧接口集成有可重复证据。
不把这等同 P2 所有 B 项退出、P0 验收转 PASS、长期性能稳定性通过或允许现场发布。
完整负载测量和最终回归结果另列下节；已有 P0 原始报告/evidence 没有修改。
尚需：5 Hz 全分母性能/数据年龄达标、完整资源稳态与极端预算证明、Qt 组合崩溃定位、
跨盘符/权限矩阵。Qt 双页/可见覆盖率、冻结包、目标工控机、真实硬件均 NOT_RUN，属于后续阶段。
本轮停止于 P2，未开发拖拽编辑器，未进入 P3/P4，未创建发布包或现场部署。

## 最终固定负载测量

`accepted-measure/evidence.json`：code_stable=true，受测源码摘要
`9cecd6f6e08abb628fa423c3594138daf0432ee870a419d999610980e926a024`；
完整日志 SHA256 `94648c46008d92b12986ab323a828ea625f7b9c8cbd188bc3a5c6a209ae3c851`。
`accepted-measure-summary.json` 仅是原始日志派生索引，不能替代六个窗口的全部原始数据。

固定种子20260926、1920×1080×3本地图、5 Hz调度、每窗96次、预热8次，三组交替顺序
baseline→two-clients / two-clients→baseline / baseline→two-clients；旧预览成本保留。
每个窗口持续同一个真实 Job、连续业务序号；客户端长驻，不每轮重建/close/清零。
perf_counter_ns 记录真实 scope 起止、导出封闭、接收/解码/模型应用；数据年龄一直采到 Job 终态观察，
包含尾部事件处理时间。性能测量期间没有并行运行回归/CI，也没有改源码。

| 配对 | 执行 P95 基线/启用 ms | 回退 | 实际 Hz 基线/启用 | 两消费者接收/解码/应用 | 模型 P95 ms（A/B） | 最大数据年龄 ms | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 258.818 / 493.466 | +90.66% | 4.140 / 3.659 | 各88/88，全三项完整 | 178.917 / 174.066 | 10250.314 | FAIL |
| 1 | 240.211 / 426.445 | +77.53% | 4.701 / 3.828 | 各88/88，全三项完整 | 154.533 / 163.064 | 9287.400 | FAIL |
| 2 | 237.047 / 413.900 | +74.61% | 4.743 / 4.059 | 各88/88，全三项完整 | 145.510 / 154.872 | 6473.786 | FAIL |

含预热时每个客户端均96接收/96解码、0 dropped/0 read_failed；三个启用窗口各96导出成功、
0超时/0失败。模型 P95≤200 ms 这一单项通过，而且本轮没有删除缺失/未应用分母。
**原整体性能仍 FAIL**：检测 P95回退>5%、实际未达5 Hz、数据年龄>500 ms。
启用组三次最大调度迟到6626.039 / 5587.578 / 4113.212 ms，不重设计时起点抹掉积压。
基线自己也未达5 Hz，说明本机该工作流已有瓶颈；这不能豁免新通道的实测增量回退。
未在本轮继续扩展成泛化性能优化，不能将文件 I/O、SQLite、调度或 CPU 中任一项未经剖析就认定为唯一原因。

每个启用窗口历史最多32，瞬时资产最多33（包括接管中结果），cache峰值205687383 bytes，
OPEN峰值1、采集拒绝0、单 Job固定展示账本192 MiB。父进程/Worker/两导出进程
采样 RSS 之和峰值分别442.40 /456.15 /477.90 MiB；含库基线、共享页可能重复计数及旧预览，
不能直接等同展示增量，也不能忽略而声称总内存≤256 MiB。
父进程窗口末的 RSS/句柄也有波动和增长，完整资源采样留在日志，未证明长期平台化。
**资源稳态验收 NOT_RUN / 尚无充分证明**；固定额度与故障清理专项通过不能替代它。

## 最终验证与交付判定

以下各套均使用上述 `9cecd6f6...` 受测源码，起止 code_stable=true。
提交检查仅删除 examples/runtime_pages_p2.py、scripts/p2_resources.py 各一个末尾空行；
修改前后 SHA 与 AST 完全一致性验证记录在 `final-format-review.json`，无执行语义或测试断言改动。
执行入口均为 `scripts/p2_validate.py --suite <suite> --output <目录>`，完整展开命令见各 evidence.json。

| suite / 证据目录 | 实际结果 | 分类 |
| --- | --- | --- |
| focused / path-cleanup-focused | 106 passed；proto-drift、Ruff、mypy PASS | 新功能及 P1 专项通过 |
| regression / accepted-regression | 462 passed、1 skipped（symlink权限） | 受影响 core/runtime/e2e 回归通过；skip单列 |
| network / accepted-network | 10 passed；C1—C4控制样本0—16 ms（Windows monotonic计时分辨率，非真实零耗时），每请求200 ms deadline | 真实网络及分类取消/清理通过 |
| demo / accepted-demo | 一个Job，两个客户端count=2、相同resultKey/SHA/像素，各1接收/1解码；单方退出不影响另一方 | 最小正式带图集成 PASS |
| ci / accepted-ci | proto/Ruff/mypy PASS；pytest在同基线Qt位置崩溃3221225477 | 完整CI FAIL；后续未到达的测试不能算通过，由独立回归补证但不替代全CI |
| measure / accepted-measure | 三组功能完整率和模型P95通过，原整体性能三组FAIL | 5Hz/5%/500ms性能未通过 |
| 长期稳态、Qt可见率、冻结包、目标工控机、现场设备 | NOT_RUN | 不具备验收/发布条件 |

当前新增功能测试没有剩余断言失败；开发期间新增失败与修复均保留在上表所列历史目录。
既有 Qt 组合崩溃仍未解决，完整 CI 不能标 PASS。P0 性能/完整率历史 FAIL 不重判，
本次正式通道的完整率 PASS 仅适用于记录的三个窗口和实际吞吐，不能声称已在持续实际5Hz通过。

四层结论：

1. **正式代码：已实现本轮最小通道**，三批增量交付，未接入默认生产入口。
2. **功能集成：支持范围内专项 PASS**，真实 Job + 两客户端图数一致、取消/超时/所有权测试通过；
   B08极端预算、B12跨盘符/权限等完整矩阵仍有缺口，不能声称全部P2退出条件已满足。
3. **性能稳定性：未通过**。原性能目标 FAIL；长期RSS/句柄平台化 NOT_RUN，Qt组合崩溃 FAIL。
4. **现场发布：不允许**。尚无P3/P5/P6、冻结包/工控机和设备验收，本轮不发布。

提交记录：第一批 `6bf7235`、第二批 `25ead9d` 已推送；第三批为包含本报告的
`feat(runtime): add shared display clients and classified loopback service` 提交。
最终完整SHA与推送状态在交付回复记录，避免在提交自身内循环写入其SHA。
