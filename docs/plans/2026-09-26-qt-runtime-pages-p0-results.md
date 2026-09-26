# Qt 运行页面 P0：决策、实测与退出审查

日期：2026-09-26。结论：**P0 未通过退出条件，停在 P0；不得进入 P1。**

此文保留第一轮历史证据。最新实现与复测见[第二轮记录](2026-09-26-qt-runtime-pages-p0-round2-results.md)：持续异步链路和旧上传已实施，但最终正常负载完整率/性能仍失败，完整CI仍有既有Qt原生崩溃；没有将第一轮失败覆盖或改判。

已实际编写并验证契约和风险原型；没有正式页面、2.2 格式启用、生产接点修改、数据库迁移或设备操作。
本记录补充主计划和 P0 任务单，不把原型通过等同于 P2/P3 实现。

## 1. 工作区和审查基线

- 用户入口 `C:/Users/jsdfhasuh/my_scripts/emo_master` 实际映射到 `D:/jsdfhasuh/documents/my_project/emo_master`。
- 初次读取：分支 `main`，HEAD `7f141ce627da39d9e174e4a79344fc468607ebef`，工作区干净。
- fetch 后远端目标分支由本地旧记录 `33fc53e` 更新至 `d96911b420380d9514393046b423993038b1a61d`。
- 使用 `git switch --track origin/agent/runtime-workflow-architecture-v1`，工作分支起始 HEAD 为 `d96911b420380d9514393046b423993038b1a61d`，恰好与交接参考一致。没有 reset、stash、main 修改或新功能分支。
- 搜索仓库和父目录未发现适用 AGENTS.md。完整阅读主计划、任务单及开发指南。R09—R11 原先只在任务单中；现已小范围补入主计划 §0、§5.3、§7.1、§8.2、B14—B16。
- `src/`、proto、既有测试完全未修改。因此默认生产执行成本、接口和数据库语义仍由原代码决定；原型不被生产入口导入。

## 2. 环境与复现

实测主机：Windows 11 专业工作站版，10.0.26200；i7-12700F，12 核/20 逻辑处理器，OS 可见内存 66,914,076 KiB。普通本地磁盘、临时目录；无 Qt 界面、无绘制/DPI 测量，未测试目标工控机。

解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10.21 x64。
沿用 PySide2 5.15.2.1；NumPy 1.26.4、OpenCV 4.10.0.84、Pydantic 2.13.5、pytest 9.1.1、mypy 2.3.1。

首次 `python scripts/ci_check.py` 在 proto drift 失败：原环境为 grpcio/grpcio-tools 1.84.0、protobuf 7.36.2、Ruff 0.16.8。
按开发指南执行 `python -m pip install -r requirements-dev.txt` 后，对齐为 grpcio/grpcio-tools 1.78.0、protobuf 6.33.6、Ruff 0.15.6；未重生成协议来掩盖漂移。ONNX Runtime 1.23.2、defusedxml 0.7.1 也按文件安装。

PowerShell，在仓库根目录（下文 python 指上述解释器）：

```powershell
$env:PYTHONPATH = "$(Resolve-Path src);$(Get-Location)"
$env:HUARAY_CAMERA_SMOKE = '0'
$env:QT_QPA_PLATFORM = 'offscreen'
python scripts/ci_check.py
python -m pytest tests/p0 -q
python -m ruff check prototypes tests/p0 scripts/p0_validate.py
python -m pytest tests/runtime tests/core tests/e2e -q
python scripts/p0_validate.py
# 仅复验故障/网络，不重复性能窗口：
python scripts/p0_validate.py --scenarios exports network --output manual_test_workspace/p0/final-risks.json
git diff --check
```

风险场景必须通过 `watchdog.supervised`（上述脚本或 pytest）运行；直接 `-m scenarios` 会拒绝执行。
每个场景外层 90 s 截止；Windows 进程先纳入 kill-on-close Job Object，收到 GO 后才可 spawn 子进程。
正常、异常、外层超时均清理整个受测进程树；超时另有 taskkill /T 后备。此 Windows Job Object 是测试进程容器，不是检测 Job。
所有项目、插件 manifest、SQLite、图片、输出均在 TemporaryDirectory；没有触碰现场数据。

## 3. 实际检查结果与基线归因

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 初始未对齐环境 CI | FAIL（环境基线） | proto drift，后续步骤当时 NOT_RUN |
| 对齐依赖后的基线 CI | FAIL（既有） | proto/Ruff/mypy PASS；pytest 在 Designer 原生访问冲突退出 3221225477 |
| 修改后的完整 CI | FAIL（同一既有问题） | proto/Ruff/mypy PASS；同样的 Designer 访问冲突，不能报全量通过 |
| P0 专项 | PASS | 最终33 passed，18.52 s；控制计时补充另有 final-risks 原始数据 |
| 原型额外 Ruff | PASS | prototypes、tests/p0、scripts/p0_validate.py；原 CI 不检查 prototypes，故显式补跑 |
| Runtime/core/e2e | FAIL（既有源代码用例） | 352 passed、1 failed、1 skipped，35.22 s |
| 既有失败单独复验 | FAIL | `testSuccessfulJobPromotesCurrentAndDirectUpstreamSnapshots`，1 failed，2.15 s |
| skip 核对 | SKIP | `test_icon_resources.py:73` 无符号链接权限；单文件复验 91 passed、1 skipped，不能算通过 |
| 新增功能断言失败（最终） | 无 | 早期测试辅助的长参数 ID、插件 metadata 问题已修复；最终无残留新增失败 |
| 性能门槛 | FAIL | 见 §7，保留全部样本、失败分母和原门槛 |

访问冲突栈指向 `tests/designer/test_main_window_workflow_tabs.py:300` 和 Qt 窗口/后台图标相关线程；原型加入前即存在。
Runtime 失败是 Job COMPLETED 后预览来源为空，断言缺少 `('upstream', 'loader', 'image')`；单独运行仍复现。
本次没有修改该路径，也没有为 P0 扩大修复范围。完整 pytest 被原生异常截断，未到达的测试一律 NOT_RUN，不从进度条推算通过数。

## 4. 原型与验证边界

| 文件 | 职责与证据 |
| --- | --- |
| `prototypes/runtime_pages_p0/contracts.py` | 不可变冻结、预算、开始序号、封闭/最终态、迟到栅栏、服务端 latest 和只读消费者 |
| `runner_probe.py` | 当前 WorkflowCompiler/WorkflowRunner；真实 ImageLoader、本地 PNG、嵌套集合 Source、修改原数组和集合的 Mutator |
| `isolation.py` | 每次 debug 独立临时 DB/输出根，复用真实 ProjectGlobalCounters 和 GlobalCounterOperator |
| `exporter.py` | 两个预热 spawn 单元、父持有 SharedMemory、独立 Pipe、强制回收与新 Pipe 重建 |
| `network.py` | 公共 grpc.aio API、分类准入和池；旧 proto 路径及 P0 只读展示接口 |
| `scenarios.py` / `watchdog.py` | 真实 RuntimeService/JobSupervisor/worker_main spawn、网络和故障监督、最终清理 |
| `benchmark.py` / `scripts/p0_validate.py` | 五组负载、完整样本、单调时钟、Windows 每进程资源数据和证据落盘 |
| `tests/p0/test_contracts.py` / `test_supervised.py` | 正常、乱序、超额、超时、强杀、IPC、隔离、退出断言 |

Runner 复用已存在的 `previewSnapshotStore.capture`：输出校验后、路由之前冻结。
原型中替换/组合这个写入器，不给生产 Runner 加参数或改语义。值仍由真正下游修改，冻结图片与嵌套数值保持 127/7，消费者读取同一不可变快照。
根流程 1 次、A/B 同子流程各一次、Loop body 两次：共 5 个快照，leaf ordinal 为 1/1/1/2；稳定调用路径区分 A/B/body，iterationPath 保留 0/1。
受控异常生成本次 FAILED，OPEN 全收束，不沿用旧 OK。最低可信来源只适配 ImageLoader 每次输出新 token；未知 Source 不继承 token。resize/crop/Blob 可信传播尚未实现，不能做独立叠加承诺。

R09：101/102/103 六种完成排列 × 最新 COMMITTED/INCOMPLETE/FAILED = 18 组；加重复消息、跨 scope/call path、Job/generation 切换、锁定详情和 102 不完整后 101 成功。
同一规则通过真实 `GetDisplaySnapshot` 和两个 gRPC 长流消费者再次验证 18 组。消息 cursor 随提交推进，ordinal 随开始推进；晚到历史不覆盖 latest。

R10：真实子进程回送 STARTED 后永久阻塞三轮；另测损坏的 Pipe 载荷、两个槽位同时阻塞、第三任务拒绝、阻塞中 close、恢复后正常编码。
核对进程数≤2、SharedMemory 名称不可重开、计账内存归零、临时文件归零、退出无子进程；不是 Future.cancel 的推断。
C4 期间另注入导出永久阻塞：两个检测 Job 始终 RUNNING、控制继续响应。只有随后明确 StopJob 才停止检测。

R11：同 projectId 的 release=100；真实 Counter 算子在 debug 增加到 1、清零到 0，release 每次仍 100；同名 debug 文件不覆盖 release 文件。显式旧计数访问器继续可增到 101，证明未改变旧语义。
该测试证明命名空间物化规则，不宣称所有旧自定义算子任意硬编码路径都已隔离。模拟页面入口尚未实现，不以该测试冒充完整草稿调试功能。

Supervisor：C4 两个真实 spawn Job 在 leaf/mutate 已开始、未产生 workflow 终态时被 force stop；借用原 terminalCallback 收束 4 个 OPEN 根/子作用域为 INCOMPLETE，拒绝迟到值，两个进程最终均退出。
此原型只把小生命周期身份放进事件 sink；不在 sink 中编码图片或写展示文件。完整采集计划清单/独立展示 IPC 的生产接入留在 P2；不把原生命周期队列改成可丢队列。

## 5. 已选定的并发与兼容方案

选定 **同一个 RuntimeService、同一个 loopback 端点的 grpc.aio 分派 + 分类有界兼容池**。
只用 `grpc.aio.server`、公开 generic handler、生成 proto 注册及 `run_in_executor`；不依赖 gRPC 私有线程池属性。
控制处理协程直接在事件循环准入，满额立即 RESOURCE_EXHAUSTED；长流 next() 在各自池中运行，不占控制 worker。
每类准入上限等于 worker 数，待执行积压 **0**，不排无限 Future，不在控制 worker 等另一个 Future。

| 类别 | 并发额度 | P0 API / 兼容方式 |
| --- | ---: | --- |
| 控制/快照 | 4 | 旧 GetJobStatus/StartJob/StopJob 等；P0 GetDisplaySnapshot；原 StopJob 回复语义不改 |
| Job 事件 | 2 | 原 StreamJobEvents 完整调用既有 generator；含原日志收尾与取消 |
| 展示 | 2 | 每客户端一条 DisplaySession，最多两个活动观看客户端；P0 `/p0.Display/StreamDisplayUpdates` |
| 相机预览 | 1 | 原 StreamOperatorPreviewFrames 路径，测试以明确 SIMULATED 会话替换帧源 |
| 图像读取 | 2 | 旧 StreamPreviewAsset 路径，500 ms 期限；正式 GetDisplayAsset 按 assetId/64 KiB 分块，计划保留同类额度 |
| 大操作 | 2 | LoadProject、RunOperatorPreview、OpenOperatorPreviewSession；避免压占控制池 |

各类活动客户端数最多等于本类额度；分类总额 13 个活动 RPC，不限制用户仅为 C1/C2 使用“全局两个流”。
每条显示流 latest-only，慢消费者缺口 RESET_REQUIRED 后快照重取，50 ms 健康轮询也能恢复末次丢失通知；生命周期/日志继续原持久化语义。
输入单消息上限 1 MiB；图像通道不接收任意路径。闲置 TCP 连接和跨机认证不属于此 P0 的客户端配额证明。

取消时先触发旧 context 的协作取消；如果 next() 尚未退出，仍占用所属配额直到真正退出，不能提前归还后增殖线程。
P0 停服先取消 RPC，最多等待 4 s；未归零则测试失败并交给外层监督清理，不把 `shutdown(wait=False)` 当资源释放。
永久阻塞的第三方相机 generator 不在本次“模拟帧源可协作退出”证明范围；须由既有 LivePreviewManager 的设备/超时契约单独验收。

旧同步服务入口和内嵌调用不变；P0 适配器没有正式启用。未来启用时沿用原 protobuf 包名/方法/字段。
**兼容缺口**：客户端 streaming 的 UploadPreviewImage 在此实验适配器仍 UNIMPLEMENTED；选定在 bulk 类做有界异步接收（原 64 MiB 总限额）后调用兼容适配，禁止挤入 control；实现/回归尚未做。
新展示 RPC 目前是隔离实验 JSON generic handler，正式 typed proto 及共享客户端没有实现，不修改项目 2.1 的格式。

C1—C4 实测全部通过：1/1/0、1/1/1、1/2/1、2/2/1（事件/展示/预览）。全部为真实 loopback gRPC，Job 为真实 spawn；相机为 640×480 JPEG 10 fps 模拟源，未访问设备。
测试取消后分类占用归零，并再次建立订阅。C4 超额事件/展示/预览均 RESOURCE_EXHAUSTED。
记录 GetJobStatus RTT、StartJob 回复/实际 RUNNING、StopJob 适配器受理/回复/终态分开；受理是 dispatch 时间，**不是回复**。
控制门槛固定 200 ms，原四线程空载入口的 20 次查询另列在 final-risks；不是硬实时保证，也不把 RTT 当显示延迟。

最后风险窗口：旧入口空载 GetJobStatus 最大4.33 ms；新入口 StartJob 回复35.41/45.80 ms、RUNNING就绪934.94/904.69 ms；StopJob受理0.409/0.567 ms，终态71.38/54.11 ms。C1—C4每组20次查询的全部RTT在JSON中，均低于200 ms。不能以就绪时间冒充受理时间，也不能以停机回复当作首次受理。

## 6. 固定预算、期限和回收选择

配置真相为 `contracts.BUDGET`；P2 改预算必须显式重新评审、测量，不能放宽后宣称本次目标通过。

| 数据/资源 | 固定值 | 计费与超额行为 |
| --- | ---: | --- |
| 单来源非图像 / 单结果非图像 | 256 KiB / 1 MiB | 节点保守计 64 B，字符串最多4 B/字符；预检再冻结，拒绝环/NaN/未知对象，不静默截断 |
| 集合元素 / 深度 / 值树节点 | 4096 / 12 / 16384 | 每容器元素、整来源深度/节点上限；UNAVAILABLE |
| 每结果来源 / 每 Job OPEN | 16 / 8 | 不扩大任意采集清单；超额报展示 gap，不阻塞或改变检测输出 |
| 单图 / 每结果原图 | 8 MiB / 16 MiB | uint8，1/3/4 通道；先验尺寸再预留和复制 |
| 导出同时执行 / 待处理 | 2 / 0 | 无空槽立即拒绝；后续可合并通知，不伪装完整 |
| 每 Job / Runtime 展示内存 | 128 MiB / 256 MiB | 图像按6倍原始字节预留，包含冻结、共享内存、编码/解码与 IPC；非图像按2倍来源上限预留 |
| 暂存 / 缓存 / 租约磁盘 | 64 / 256 / 64 MiB | 缓存和租约共享总盘额，租约不绕过缓存上限；以父进程生成 ID 管理 |
| 有限历史 / 活动作用域身份 | 32 / 16 | latest 独立保存，有限回看；原型拒绝新增超额身份 |
| 封闭等待 / 导出执行 / 回收 | 500 / 500 / 1000 ms | seal 从父观察执行终态计时；导出从发送任务前计时（不是 STARTED 后重起）；确认死后才能补槽 |
| 读图/解码 / 租约 | 500 ms / 30 s | 读图在可杀导出单元中；租约尚无正式实现 |
| 控制受理 | 200 ms | 无排队准入；StopJob 实际停止完成独立记录 |

图像导出选择：2 个预热 spawn 子进程 + 每子进程独立 Pipe；父拥有 SharedMemory 与 UUID 暂存路径；子负责编码、原子写入、读回校验/解码。
超时/IPC 异常先降级，terminate/join，必要时 kill/join；无法回收的槽位保持隔离，不无限重建。补槽是独立 READY 握手，不藏进原检测耗时。新槽使用全新 Pipe；检测 Runtime/Job 不因此终止。
文件/缓冲在 finally 由所有者清理，退出也覆盖正在运行的阻塞任务。P0 成功文件仅验证后即删除，没有假装实现资产租约或生产资源接管。

**预算证明限制**：树、图像、OPEN、导出槽和 exporter 全局 ledger 已执行边界断言；Collector 和 Exporter 尚未由同一个 Runtime 全局 ledger 统筹。缓存/租约磁盘、长时间历史淘汰、跨 Job 总资源的正式实现和饱和回归仍 NOT_RUN。
这些预算已经选定，但未完整证明所有生产资源路径服从它们，故不能以“配置已填写”判定 P0 全过。

## 7. 测量负载和不能通过的性能项

负载固定：seed=20260926 的本地 1920×1080 uint8 BGR PNG，5 Hz 调度，五组各30次。
当前 ImageLoader→Source→Mutator，保留原 PreviewSnapshotWriter 同步编码/写盘开销；新采集是额外 Tee，不移除旧开销来取得漂亮数字。
0/1/2 只读模型消费者；无 Qt 页和50控件，故不计算 Qt 可见率。新采集零观看者仍执行冻结/封闭/导出。

时钟统一 `perf_counter_ns`；每个 spawn 导出器 READY 时间落在父发送前/接收后括号内，Windows 同机单调计时验证成功。
scopeEnd 取真实 Runner workflow 终态回调，在随后导出等待之前。延迟从 scopeEnd 到资源完成、只读模型就绪；不从图片 ready 重新计时。
每组保留全部30条样本、执行耗时、调度迟到、scopeEnd/commit、完整或不完整、每消费者完整数量。
计数型资源记录 ledger 峰值/归零、文件/进程归零；Windows GetProcessMemoryInfo/GetProcessTimes/GetProcessHandleCount 记录父和两个导出进程 CPU/RSS/句柄。

第二窗口（证据 `measurements.json`，首窗口亦保留，未覆盖失败）：

| 组 | Runner P95 ms | 相对原基线 | 模型资源就绪 P95 ms | 实际吞吐 Hz | 最大 live 数据年龄 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 原功能 | 176.42 | — | — | 5.06 | — |
| 新版无 presentation（相同生产代码） | 188.43 | +6.80% | — | 5.05 | — |
| 启用采集、零观看者 | 174.19 | -1.27% | 90.34 | 4.37 | 不适用 |
| 一个消费者 | 172.37 | -2.30% | 96.87 | 4.32 | 345.83 |
| 两个消费者 | 360.91 | +104.57% | 169.25 | 3.39 | 812.24 |

该原型 harness 在 Runner 返回后串行等待导出，**未达到完整5 Hz正常负载**。最大调度迟到两消费者达2819.20 ms，不能只看 P95<200 ms 就通过。
两个窗口均30/30封闭/提交、每模型消费者30/30完整，无缺失被移出分母；但这不是 Qt ≥99%可见率验证。
原目标仍为正常整组200 ms、节拍回退≤5%、持续最大年龄≤500 ms。回退和鲜度失败，保留原目标，不上调阈值。
无 presentation 与原功能实际代码相同仍有波动，表明这次开发机窗口存在显著调度/缓存噪声；不能将负回退解释为可靠加速。
首窗口见 exploratory-measurements.json：无 presentation P95365.52 ms（+103.07%）；保留该事实，不挑选最好一轮。

第二窗口父 RSS 82.68→95.05 MiB；句柄376→409，期间有导出/文件操作，未做长稳态平台化证明。完整每进程CPU/RSS/句柄在 JSON，不能把计账内存等同实际RSS。
分发/UI丢弃：模型实验采集/分发记录0；Qt UI NOT_RUN。慢流原型能明确 reset，但未完成带图网络长期拥塞覆盖率验证。

## 8. P0 退出判定与下一步条件

| P0 项 | 判定 | 退出依据/缺口 |
| --- | --- | --- |
| 最新分支/基线/开发环境与 CI 记录 | PASS（记录完成） | CI 本身 FAIL，未掩盖基线或 skip |
| R09 顺序、scope/Job/generation | PASS | 单元18排列状态组合及网络18组、重复、换会话、102/101反例 |
| 真实 Runner 根/循环/重复调用、冻结、异常封闭 | PASS（原型） | 正常/异常、本地图像、真实下游修改；两个只读消费者 |
| Supervisor 强杀与迟到栅栏 | PASS（原型） | 两个真实 spawn Job，4个OPEN作用域收束 |
| R10 导出回收和故障期间控制/Job隔离 | PASS（原型） | 实际STARTED、重复阻塞、坏Pipe、双槽饱和、阻塞close、归零 |
| R11 默认debug命名空间 | PASS（契约） | 同projectId release100不变，同名文件不覆盖；无生产迁移 |
| C1—C4正常并发、分类超额和取消 | PASS（原型） | 真gRPC/旧事件follow/模拟相机/真实Job，非内存stub |
| 资源方案与预算数值选定 | PASS（决策） | §5—6固定方案和数值，不留候选分支 |
| 全局资源边界/资产接管/完整旧接口兼容 | NOT_RUN/部分验证 | 共用全局ledger、缓存/租约、Upload适配、带图网络读期限未完成 |
| 原负载和性能目标 | FAIL | 5Hz未维持；回退与500ms鲜度失败 |
| Qt可见率/绘制、冻结包、目标工控机、硬件 | NOT_RUN（后续阶段） | 不能用模型/Windows源码原型替代 |

**不满足退出条件。**下一步仍是 P0：修复或隔离既有 Qt 原生CI崩溃/预览来源失败的基线条件；用真正异步、受总预算约束的采集/导出调度替代串行测量链路，再在同一1080p/5Hz负载下测稳定节拍、鲜度和资源平台；补全全局计账、资产读/接管与旧上传接口兼容的风险验证。不得自动开始 P1。

可迁移：开始序号与cursor分离、封闭/栅栏规则、预算常量和拒绝语义、先预留再冻结、spawn回收/父所有权规则、分类准入/取消归还规则及故障用例。
可抛弃：JSON实验RPC、Runner采集器适配、临时manifest/算子、模拟相机、同步benchmark调度和Windows采样器；不是正式UI/客户端/发布代码。

证据目录：[p0-runtime-pages](../evidence/p0-runtime-pages/)。JSON 的 execution=PASS 只表示验证脚本成功完成，不表示各性能门槛或整个P0通过。提交边界为契约/真实Runner原型、spawn网络/导出风险、文档证据三批；最终SHA以git提交和交付报告为准。
