# abc7ae 验收后局部修复与回归

日期：2026-10-11，Asia/Shanghai。范围为源码修复与隔离软件回归；没有合并、发布、部署或连接生产设备。

## 1. 版本、现场与结论边界

- dot 产品验收基线：`abc7ae0541e997be0bbe3a4794ad604c239de79a`。
- 本轮远端起点：`adba329cc11a74034299b9191b7decac6e567fdf`。核对两提交的产品、测试、原型和脚本，没有后续产品修复；adba329 是报告归档，不是另一个已验收产品版本。
- 原工作区 `D:/jsdfhasuh/documents/my_project/emo_master` 初始有31个本地改动。本轮只在独立管理worktree写入，原HEAD保持abc7ae。中途多次核对31个原哈希均相同；交付核验时观察到并行工作更新其中6个双工位工程/测试文件、另新增`test_station1_correction_project.py`，合计32个dirty路径。本轮未写这些文件，保留并行工作的最新内容，不回滚旧快照、不混入交付、不在原脏工作区下移动本地分支；精确截点见`workspace-preservation.json`。
- 最终候选 **E**：冻结 5104 个文件；`frozen-source-files.json` 原始字节 SHA256 为 **`25eed17019f0ae13abea64c516ae6ad1957bd0e1e1d9b6134b29d9b380b7ade9`**。产品/测试固定后执行验证，报告及证据文件随后加入。提交前再比对冻结文件、修复工作树及原工作区哈希。
- 附件和历史 handoff 是调查材料，不是设备、部署或破坏性操作授权。原报告、探针及首失败原件未改写。

**本轮可以关闭的代码缺陷**：变量失败 reason 与既有 DTO 不兼容、模型校验前移除 open、pending 的 scope credit 归还依赖 close 布尔值、SQLite 预算到期后仍开始一次 BEGIN、ROI 专用预览缺完整图像查看/导出。

**仅加强诊断、不关闭历史根因**：生产本机 NG→INCOMPLETE、P0 双网络消费者历史 ordinal20 导出不可用。当前通过不能证明历史原因已被修复。

## 2. P1：合法错误码、原子闭合与恰一次额度归还

修改：

- `src/emo_master/apps/runtime/presentation/collector.py`：变量/计数器 lookup、类型、SQLite 与 I/O 读取失败使用既有合法 `INVALID_VALUE`，detail 保留 `VARIABLE_UNAVAILABLE: <异常类型>: <有界详情>`。不增加旧严格客户端无法识别的新枚举，不把失败改为 COMPLETE。
- `presentation/store.py`：先构建并验证不可变 `ClosedResult`，再移除 open、发布 history/latest/cursor。校验失败保留 open 和既有发布指针。
- `presentation/service.py`：`_finish` 在 store 的 RLock 下串行；pending 独立拥有一个 scope credit，收尾统一移除并归还一次。重复 seal/finish、晚回调、并发收尾不会重复归还。模型校验失败记录原异常，并按原身份和预期源清单闭合为明确的 `INCOMPLETE/INVALID_VALUE`；失败源不会消失。图像 lane 仍只由真实 exporter owner 在 producer 退休后归还。
- 新 `presentation/diagnostics.py`：64 条闭合诊断中的有界错误元数据；Pydantic 错误排除 input payload，不保留图像/标量内容。

覆盖：`test_closure_failures.py` 的 18 个隔离场景包含变量/计数器 KeyError、ValueError、TypeError、SQLite/I/O 异常、wire/客户端往返、非法 reason/NaN/错误 source 清单、后续正常结果、重复及并发收尾；另有两个**真实 Runtime + spawn + monitor、注入故障**的集成场景，连续捕获至少 12 个结果，超过原八个 scope credit 上限，stop/drain 后逐一取回八个 credit，worker/exporter/reader 退休并释放资产与工作目录。该集成证据不是设备实测，也不是历史 NG 根因复现。

## 3. P2：可靠观测，不用延长时限制造通过

原 dot 全量是 **4828 PASS、1 FAIL、19 SKIP、29 subtests**，退出码 1。失败为 `testRealWriterContentionIsVisibleAtBeginAndPreservesAllDurableRows` 的瞬时 active-span 观察断言。其后的安全断言当时未执行到，不能补写成首轮通过。

本轮修改：

- `tests/test_r3_sqlite_phases.py` 保持原 **2 秒观察预算 / 5 毫秒采样**，观察健康 observer 的**累计失败 BEGIN**，证明真实 writer 已遇到真实竞争，不要求采样命中短 native 调用。
- 持锁期间仍断言 writer 存活、没有成功 BEGIN、没有 sequence SELECT / event INSERT / COMMIT、没有结果或异常。解锁后保持线程退出和 durable seq=[1]，并强化为成功 BEGIN/业务 SELECT/INSERT/COMMIT 均恰一次。
- 配置证据来自**实际 writer 连接及其线程**：`synchronous=FULL(2)`、`journal_mode=wal`、`busy_timeout=5000`，不是用 blocker 的配置代替。
- `context/sqlite_transactions.py` 明确预算是 retry admission，不承诺抢占 native 调用或事务 body。timeout=0 仍允许首次立即尝试；deadline 或调度 oversleep 后不再开始额外 BEGIN。模式切换失败仍尝试恢复；恢复失败可见，若 BEGIN 已失败则保留 primary 并链接 restore error。只重试 BEGIN，不重放事务 body、未知 COMMIT 或外部 ACK。

新增确定性预算/恢复故障测试；真实 blocker/writer 专项另存 `sqlite-contention-evidence.json`，包含观察快照，证明释放前未提交、释放后恰一次提交及完整行。原运行没有阶段 trace，因此这里只能修正当前观测同步并证明当前语义，不能倒推原失败的唯一精确调度原因。

## 4. P3：ROI 同一次草稿预览的四路完整像素与身份

修改：ROI `editor.py`、`ui/editor.ui`，新 `preview_result.py`，以及局部 `_editor_support.py`、`operator_editors/controller_protocol.py`、`builtin_layout.py` 适配。

- 显示 **croppedImage、croppedMask、fullMask、maskedImage** 四个缩略图；端口选择后可用 **1:1 查看**打开可滚动原始像素窗口，不以缩略 pixmap 冒充原图。
- **PNG** 只保存当前端口原图；**JSON** 保存结构输出、图像描述及本次请求身份；**本次预览 ZIP** 包含四幅原始 PNG 与 `result.json`，不再执行算法，不借用通用算子调试结果。
- 像素只来自受当前 Runtime/session/project 权限约束的合法资产下载，不读缓存路径。BBox/rotatedBox/polygon 三种模式的四路像素与算法输出逐像素相同，ZIP PNG 字节与该次授权资产完全相同。
- 冻结客户端 requestId、generation、输入资产/MIME/字节数/SHA256、draft fingerprint/session、project/workflow/node/operator/version、端口/资产 ID、图像尺寸/SHA256。
- **协议边界诚实保留**：当前 pure-preview RPC 不返回 operator-debug 的 executionId、resolvedParams 或服务端身份签证。本包使用 `requestedParams`、`resolvedParams: null`、`NOT_RETURNED_BY_PURE_PREVIEW_RPC`；界面明确提示“请求值，不是 Runtime 实际解析参数”。没有扩展 protobuf 或伪造执行身份。
- 改参/换图/新请求立即使旧结果与已开 viewer 失效；generation 过滤和同 session 的 request token 防止旧回包覆盖新资产归属。草稿变化、Runtime 重连、300 秒 session 过期、取消、关闭都不能将旧结果导出成当前结果。
- GUI 线程用已冻结的 PNG bytes 导出；临时文件写完、flush/fsync、重新核对身份后才原子 replace。取消、picker 内改参/关闭、部分下载失败和磁盘失败不覆盖原文件，临时文件会清理。GTK 风格未附后缀时明确补全。
- 保持既有 all-producer retirement fence；未新增会越过窗口生命周期的导出 worker。原生渲染审查发现身份说明第二行曾被 splitter 压掉，最终候选 D 保留足够字体行高；最终截图已核对两行可见。

`test_roi_preview_export.py` 有 14 个新增回归；已有坏图恢复、草稿/同 ID 不同窗口、取消及 producer 清理测试一并回归。文件对话框部分选择由自动化代答，不能据此声称 GTK 真实交互已验收。

## 5. P4：两条不同路径的有界、可关联诊断

### 生产本机 presentation 路径

- collector 在 open/seal 保留 workflow、调用/iteration、source origin 和 node-run observation；图像 freeze 记录 copy 时长及 invocation/scope/ordinal。
- service 闭合诊断关联 resultKey、完整调用身份、终态、预期/缺失/不可用源、reasonCode/detail、origin/observations 与导出阶段；asset adoption 失败保留类型和阶段。
- exporter 的 64 条有界时间线关联私有 taskId 与 result/job/invocation/source/slot/lane，区分 queue_wait、completion_wait、encoder_reply、completion_after_deadline；保留复制/排队/等待/编码/落盘/回调/退休与 lane 归还。回包核对 taskId；晚到成功仍按原预算标 timeout，计数同步。callback 异常不会悄悄终止 owner；退休失败仍 quarantine，不提前归还 lane。
- WorkflowRunner 捕获故障保留有界 method/context/error-type，在现有 terminal payload 附带摘要；失败断言带相关结果源和 exporter/closure 诊断。终态事件与 collector seal 对诊断字段作可选处理，兼容原隔离 runner/minimal collector，不能因可选元数据缺失改变主流程或丢失原错误。`test_worker_capture_diagnostics.py` 的6项检查 completed/failed/aborted 及有/无details，既有11项心跳/feeder退休契约保持不变。

### P0 双网络消费者路径

`prototypes/runtime_pages_p0/async_exports.py` 分开记录 **start_wait TimeoutError、start_identity ValueError、completion_wait TimeoutError、completion_identity ValueError**；expected/actual 身份、预算、剩余时间及实际时间线分别保留，不再把 STARTED 身份不匹配吞成“start timeout”。callback 前冻结已发布结果的 timeline，callback 后退休诊断写独立有界队列，避免发布后偷偷修改消费者可见对象。

生产 NG 旧失败在本机 `store.latest`、早于网络；原 fixture 的源均为 node output。变量错误码修复**不是**它的根因证明。P0 旧 ordinal20 服务端 export UNAVAILABLE 是另一套实现。当前未复现，**两项历史精确根因仍 OPEN**。

## 6. 验证账本

最终结果、精确命令、环境、跳过原因及 sourceChanged 检查见 `validation-summary.json` 和证据包内 JUnit/原日志。不同层级数量重叠，**不得相加**。

| 层级 | 最终结果 | 证据/边界 |
|---|---|---|
| Windows Python 3.10.21 / SQLite 3.53.4 / Qt offscreen 全量 | **4895 PASS、11 SKIP，另28 subtests PASS，退出0** | `final-windows-full.log/.xml/.json`；1490.50秒，一次完整最终候选运行，sourceChanged=[]；不用中止运行充数 |
| Debian 12 linux/amd64 / Python 3.10.22 / SQLite 3.40.1 专项 | **423 PASS、8 SKIP** | `final-linux.*`；431 cases，新增终态/心跳/变量展示18项，tmpfs，网络隔离，合成数据；8项为Windows大小写不敏感文件系统专属；不是 Linux 全量或 Debian13 桌面 |
| Windows PySide2 5.15.2.1 `windows` 原生 Qt 自动化矩阵 | **50 PASS** | `final-native-qt.*`；示例调试、契约、ROI、SQLite 用户路径；不是人工操作 |
| Windows 原生 Qt 长暂停及 ROI 脚本 | **PASS** | `final-native-long-pause.log`、`native-acceptance-results.json`；nested-workflow fixture实际暂停约80秒，同一身份/sequence继续得到7（不是原Debian While桌面D06原件重跑）；四路PNG等同授权原资产，assets=0、formalJobs=0、operatorResources=false |
| Windows 原生 Qt D06 ForEach 80秒条件断点补证 | **PASS** | `native-loop-pause.log`、`native-loop-results.json`；使用正式`02-foreach.emoproj`与items.json，通过Qt上传/启动/条件断点，在left=5/iteration1、left=9/iteration2暂停；第二处同一身份/sequence自然等待80秒后继续，index=[0,1,2]、result=[false,true,true]，project原字节/草稿不变、formalJobs=0、调试owner退休。仍非人工Debian桌面证据 |
| 固定三轮独立进程 | **每轮软件4 PASS；每轮P0双消费者各40/40，coverage=[1.0,1.0]、INCOMPLETE=0、client_errors=[null,null]；最终所有资源记账0** | `fixed-rounds-plan.json`、每轮软件JUnit及完整P0 JSON；每轮原样跑SQLite [7,13]两Job/保存重开、4/5路While、NG同调用图像计数，再跑双消费者；全部结果保留，不按成功门槛增加轮次 |
| Proto / Ruff src+tests / Ruff 修改的 P0 / mypy src | **PASS** | `final-static-checks.json`，各退出码0；mypy402源文件；保留既有类型检查范围 |
| 人工 Debian13 Xfce/X11/GTK、该平台高DPI/小窗全矩阵 | **NOT_RUN** | 交给 dot，不能用 Windows 原生自动化或 Debian12 offscreen 冒充 |
| 相机/PLC/机器人/生产TCP、包/发布/部署 | **NOT_RUN** | 无连接、无发布、无部署；源码不更新已运行的进程或安装 EXE |

## 7. 首失败与被中止候选全部保留

- 原 dot 完整分卷已合并核对：68,072,637 bytes，SHA256 **`1b01545a84b957375abac33975fe29307f56f72d99ffd57cd469ed6ca1a2f2bf`**。不重复提交68MB；沿用 `../2026-10-11-abc7ae-acceptance/` 的分卷和 README。原 pytest/JUnit/首失败 DB/WAL/SHM 未覆盖，未以读写连接打开原 DB。
- 原变量模型探针按原字节只执行一次，七项缺陷观察成立。退出0是“缺陷复现”，不是通过；`fault-injection-baseline/` 原输出独立保留。
- Red tests：P1 13 FAIL，P2 边界首轮失败，P3 6 FAIL，P0 timeline immutability 4 FAIL。后续修复结果单独文件保存。
- 集成 harness 首次 import 错误与 Windows 未代答 discard 嵌套模态的 access violation 原日志保留；修正测试 harness 的明确 Discard 答复，没有把自动化栈误称生产设备事故。
- 候选 A Linux 首次 **400 PASS、1 FAIL、8 SKIP**：新增诊断在不完整独立 lane fixture 中严格读取 invocationId。改为可选诊断字段 `.get`，未放宽原 lane 断言。
- 候选 B 全量 **ABORTED_BY_REVIEW**：审查发现新 P0 timeline 发布后被修改，red test 证明后修复；不是成功全量。候选 C 全量 **ABORTED_BY_RENDER_REVIEW**：原生截图参数来源说明被裁切；修复行高并重新冻结 D。中止理由、partial logs 和候选 manifest 保留，不冒称这些运行通过，也不覆盖其记录。
- 候选 D 全量跑完：**4875 PASS、14 FAIL、11 SKIP、28 subtests、1 warning**。11个失败来自新诊断字段未按可选读取（10个stub runner心跳/终态、1个minimal collector变量源）；修复为可选 metadata 并补终态6项，原测试安全断言不改。另3个probe测试失败是冻结副本没有`.git`导致身份探针拒绝，不是SQLite断言失败；最终 E 全量改在真实隔离Git worktree运行并逐一核对冻结源码。D的完整log/JUnit/manifest保留，E不是原样反复重跑取绿。
- 原生独立 harness 的 `QTest.qWait` 不可用/提前断言 terminal 也保存首次日志，改正确支持的等待 API 与精确 workflow.result 谓词，没有扩大产品预算。
- 证据打包器最初要求原31个哈希全同，交付时检测到上述并行工作而拒绝打包。核对变化都在原工作区，本轮修复树的5104冻结文件仍完全相同；将保留账本改为如实记录初始/当前路径及差异，未写回或删除原工作区内容。这是交付现场检查，不是产品测试失败，也不改写源验证结果。

## 8. 交付与仍未关闭

仅用户已授权的正常提交推送至原分支。推送核对 SHA、修复 worktree ahead/behind、PR#1 状态及**精确提交** CI；merge-ref 的绿不替代 push SHA。原脏 checkout 不快进、不 reset。

1. **OPEN**：历史生产 NG→INCOMPLETE 精确缺失源/阶段/根因；历史 P0 ordinal20 STARTED/等待/身份的具体分支及根因。
2. **协议能力未提供**：pure-preview 服务端 executionId/resolvedParams/attestation。客户端没有伪造；若产品将来要求这些字段，另做兼容协议设计。
3. **NOT_RUN**：Debian13 真实人工 Qt/GTK、多显示器/高DPI/小窗全矩阵，以及实际设备、安装包与部署。已有 baseline PASS 不能直接搬成修复 SHA PASS。
4. **CI 另查**：本地 PASS 不是远端 CI PASS；最终 SHA 状态见交付消息，不自动合并/发布。

dot 复验入口见 `DOT_HANDOFF_PROMPT.md`；以用户收到的**40位交付SHA**固定版本，新开 Designer/Runtime 进程，逐项保留首失败、身份/像素/事务/退休证据。复验本身不授权再修改或推送。
