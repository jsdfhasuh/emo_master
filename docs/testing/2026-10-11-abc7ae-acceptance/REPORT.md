# abc7ae Debian 独立验收报告

验收报告 2026年10月11日

本报告覆盖精确提交 abc7ae0541e997be0bbe3a4794ad604c239de79a 的 Debian 软件验收、原生桌面操作、专项运行证据与远端 CI。自动化、原生 GUI、远端 CI 分层判定。原编号 D01–D08、O01–O20、U01–U13 延续历史含义，历史结果不作为本轮通过依据。

当前结论：不能判定整体验收通过。本地全量有一项保留的首次 FAIL；专用 ROI 预览存在完整图查看/导出入口的易用性缺口。D01–D08 与 O20 本轮原生主路径已完成，远端三个 CI run 均成功。其余原 O/U 范围有代表场景通过，也有未测分支，不能合并报告为全部 PASS。

本轮实际执行与证据收尾已完成，已包含代表小窗口及全部本轮进程正常退出的清理证据；当前门禁FAIL和未测范围继续保留。状态只使用 PASS、FAIL、SKIP、NOT_RUN；部分完成写在覆盖说明中。NOT_RUN 表示本轮尚未完成该项完整范围，不意味着没有任何已证实子项。

## 一 验收范围和证据原则

- 固定源码为 abc7ae0；Debian 13 trixie，Python 3.10.22，PySide2 5.15.2.1，Qt 5.15.2，SQLite 3.53.1。
- 软件回归使用 offscreen，真实桌面操作单独留存截图与导出物。进程、数据库和缓存文件的只读采证用于核实，不替代用户界面操作。
- 产品与测试源码、原断言、超时预算均未修改。自动化结束核验5034个受Git跟踪文件hash无变化。最终HEAD仍准确、tracked diff为空，但git status有本轮UI误导航保存的未跟踪docs.emofixture，原件与同字节证据副本均保留；不能称最终Git全clean。
- 本报告不包含实体相机、实体 PLC、机器人、生产 TCP、GPU 或现场全验收。离线 PLC 编辑器没有连接设备、解锁写入或 Apply。
- 计数不累加：全量、专项、原编号映射和 CI 有覆盖重叠。208 条原编号测试引用对应 176 个去重测试，不是新增208个测试。

证据路径以 evidence/ 为相对根目录；第十一节产品与测试源码定位以 source/ 为相对根目录。完整精确 nodeid、原始数据和身份在 case-matrix-final.json 及配套文件内。

## 二 门禁和主要结果

| 项目 | 状态 | 结果与范围 |
|---|---|---|
| 本地全量 pytest | FAIL | 4828 passed，1 failed，19 skipped，另有29 subtests passed；936.61秒，exit 1 |
| protobuf、Ruff、mypy | PASS | 均exit 0；mypy检查400个source files |
| 独立压力与退休 | PASS | 50 sessions，1049 executions，观察到50个真实spawn workers |
| 独立65秒租约 | PASS | 原65秒等待和断言未变，总过程66.64秒 |
| SQLite独立用户路径 | PASS | 10个独立进程，共20测试；每轮DB[7,13]，20个唯一Job/workflowRun |
| Actual-worker及边界专项 | PASS | 2测试/8.71秒；另30契约测试/1.95秒，不与全量累加 |
| D01–D08原生调试 | PASS | 八项主路径已测；D05缺失的最终7已补完整JSON |
| O20原生SQLite路径 | PASS | 两次正式Job与COMMITTED、保存重载、新Designer重开；DB严格[7,13] |
| 远端精确SHA关联CI | PASS | 3 runs成功，7 jobs成功、1有条件跳过；push与PR合并SHA分开 |
| 专用ROI完整图查看导出 | FAIL | 预览可工作，但用户缺查看/导出入口；缓存采证不能替代UI导出 |
| 剩余原O/U完整原生范围 | NOT_RUN | 代表场景分项已测，其余分支与平台/设备边界仍保留 |

全量的19项skip包括实体相机、明确opt-in压力/65秒租约，以及Debian不能执行的Windows路径。压力/租约独立启用通过，不改写全量的原始skip。JUnit tests=4877包含subtests，独立case_count=4848。软件专项的操作系统资源每0.2秒采样，峰值是采样峰值。

## 三 首次全量失败与限定诊断

### 原始失败保持不变

失败节点为 tests/test_r3_sqlite_phases.py::testRealWriterContentionIsVisibleAtBeginAndPreservesAllDurableRows，第493行：writer never entered measured BEGIN IMMEDIATE。原两秒watchdog内未采到active native BEGIN span。原日志、JUnit、首次错误和数据库均保留，没有修改断言、延长时限或重跑后改判。该次失败未继续执行第494–503行的释放前无提交、释放后线程退出/恰一次commit/持久行及FULL/WAL断言；finally清理仍执行。不能把事后只读DB检查说成这些原断言在首次全量中通过。

### 诊断支持观测竞态推断但未证明原始根因

单独一次诊断持有数据库写锁两秒，在锁持有期间记录631次失败的native BEGIN，其中629次小于1毫秒。389次、间隔5毫秒的采样只捕到1次active span。释放锁后另一次BEGIN和COMMIT成功，seq1持久，没有worker错误。

源码映射显示原生busy timeout为0，1–5毫秒准入等待在被观察的native execute调用之外；phase观察器只在native execute内部标记active。因此证据支持“短active span与采样存在观测竞争”的解释。原失败没有保留phase快照，不能证明该运行的精确调度或根因。

原失败DB在finally清理后也包含seq1且integrity_check正常。这是清理后的证据，不是断言瞬间快照。此诊断不抵消FAIL，也没有证明数据丢失或永久writer饥饿。

证据：automation/full/first-failure.json、automation/full/pytest.log、automation/full/junit.xml；targeted/phase-observation/report.json、targeted/original-full-failure-db-readonly.json。

## 四 八个最新关键词矩阵

| 关键词 | 软件 | 原生GUI | 已证实与保留边界 |
|---|---|---|---|
| SQLite 高并发／计数器事务／保存重载 | FAIL | NOT_RUN | 并发/重启专项与O20代表路径通过；全量BEGIN观测失败未关闭，GUI高并发全组合未测 |
| 5 路 While 并行带图像 | PASS | NOT_RUN | 真实spawn/RPC与合成图像；不是GUI多页或设备并行验收 |
| 持续图像／双网络消费者／完整覆盖 | PASS | NOT_RUN | 两消费者各40/40，覆盖100%；网络fault非致命grpc栈保留 |
| 业务 NG 不终止／图像计数同一调用 | PASS | NOT_RUN | 本轮3个COMPLETE、NG不自行终止；历史1657根因仍未确认 |
| PLC 滚轮安全 | PASS | NOT_RUN | 离线Write参数/运行页子路径通过；Read未单独原生测，完整范围未关闭 |
| 启动加载失败可恢复 | PASS | PASS | 原生无效camera项目错误后主动选择有效项目恢复；其他错误类型仅列明软件路径 |
| 调试占用／切换工程拒绝／提示前置 | PASS | PASS | 算子及流程暂停会话切工程均前置拒绝，原项目/身份保留，流程可继续7；不覆盖全部占用矩阵 |
| 小窗口／中文控件／原主路径回归 | PASS | NOT_RUN | 794×573主窗中文菜单/overflow与680×500PLC代表已测；原640/800×430和高DPI未覆盖 |

这里的PASS仅覆盖描述范围。NOT_RUN列中的部分完成结果继续留存，不能与相邻通过项合并。

### SQLite并发与事务

4个typed-variable writers、4个legacy-counter writers、4个durable-event writers各60次操作，共720次调用成功。两计数值各240，三组返回序列均精确为1–240，无重复或丢失。独立进程重开后数据仍在，完整性检查为ok。另一个连接持有未提交写事务时，已有计数器读取已提交值240，0.266毫秒，采用deferred BEGIN而非BEGIN IMMEDIATE；正常写预算保持5000毫秒。

10轮SQLite用户路径独立进程共20测试通过，20个唯一Job和workflowRun与已观察spawn PID对应。O20真实桌面路径另列在第六节。全量首次观测失败独立保留。

### 五路While与业务NG

五个不同PID在首停边界均RUNNING，完成body轮次为6/6/5/4/4；停止首Job后其余四个仍RUNNING。最终退休轮次6/10/12/15/18，各Job按预期ABORTED/E_CANCELLED。共61个COMPLETE和5个主动CANCELLED，没有INCOMPLETE/FAILED或exporter failure；关闭后worker、image reader、export线程、队列和资产均退休。这些轮次不作为性能吞吐数据。

业务NG场景三个结果均COMPLETE，count=2、judge=false与可用图像属于同一result identity和invocation；每次都有匹配的workflow.completed事件，主动stop前Job仍RUNNING。本轮无无法解释的INCOMPLETE，仍不能关闭历史1657生产NG→INCOMPLETE的精确阶段和根因问题。P0 AsyncExports阶段诊断与生产PresentationService主要来源码不能视为等价诊断能力。

### 持续图像和双消费者

45轮包括5轮warmup；两个loopback消费者分别收到并解码测量区间6–45全部40/40图像，原始/编码完整性通过，覆盖率均100%，INCOMPLETE=0、客户端错误=0。最终memory/staging/cache/lease与owners记账均0。

15个网络兼容、取消、中断与清理场景断言通过。Pipeline两Job导出饱和、slow active-reader取消生命周期、原read deadline与running-export close通过，最终children=0。中间快照仍持有的cache/metadata不可与最终关闭状态混用。

network-faults进程有一次非致命grpc asyncio PollerCompletionQueue回调BlockingIOError errno11栈。断言和退出码通过，但stderr不干净；根因及生产影响未证实。完整stderr保留。

## 五 D01至D08原生调试矩阵

| 原编号 | 本轮原生结果 | 主要证据 |
|---|---|---|
| D01 PASS | number独立试运行临时99，不请求after或上游；不Apply，正式重开仍7 | 009、014；d01-trial99.emodebug.zip |
| D02 PASS | 原图独立threshold127，无blur；完整320×240 mask逐像素差异0 | 068–072；export-pixel-verification.json |
| D03 PASS | 单击进入/跳出/跳过依序seq1–6，两次调用输出7；仅宣称实测seq1–6主链 | 021–032 |
| D04 PASS | 启动前普通断点，两次调用node.before暂停且nodeRunId不同，最终7 | 034–040 |
| D05 PASS | params["value"] == 7、命中起点2，第二次调用暂停；补充会话最终JSON确为7.0 | 042–048及145–147；d05-final7.emodebug.zip |
| D06 PASS | left5/iteration1、left9/iteration2；自然暂停82.923秒后继续，index[0,1,2]、result[false,true,true] | 056–063；D06-pause与结果JSON |
| D07 PASS | run-to process/threshold停在node.before；输入完整图等于GaussianBlur3×3，非原图 | 076–079；d07-blur-input导出 |
| D08 PASS | number试99、图像试threshold200不改主暂停身份；主流程仍7/threshold127，完整PNG逐像素一致 | 024–030、081–085；trial200/main127导出 |

D05最初记录仅有最终SUCCEEDED而未选最终值，该记录保留。随后补齐的是缺失证据，不是失败重跑改判。只读检查d05-final7包确认outputs/value=7.0，main workflowRunId为bb1c00a8-aafa-46e7-a842-82f9c5501e97，文件SHA256与元数据一致。

D02/D07/D08四份完整PNG均为320×240且像素差异0。输入/试运行/主结果身份分别保留，不能用缩略图或其他会话图像替代。

## 六 新增原生编辑持久化和预览证据

### O20两次提交与新进程重开

使用历史工程副本、Number重置7以及本次全新空业务表，原生运行两次正式Job。结果分别7、13，两次COMMITTED且Job、workflowRun、writeId均不同；worker PID为3162、3177。数据库严格只有[7,13]。

保存后切换工程并重开，13以及Number.value映射到“数量”/INTEGER保留。旧Designer、两个worker和resource_tracker正常全部退出后，新Designer3195（launcher3194）重新打开同工程，仍显示13.0，没有第三次Job或新增业务行。本项不宣称从零通过UI创建该工程或建表。证据：ui/projects/o20-native-final-summary.json及截图134–144。

### 从零创建与参数校验

独立代表路径通过UI创建空白工程、拖入number，独立调试1.2345后Apply，定义number输出result、连接端口、自动布局，正式运行输出1.2345；保存重开保留，GTK自动追加.emoproj。

1.2345与17位1.2345678901234567、科学计数法1.2345e-10有输入/应用/持久化观察；nan拒绝、字段变红、文本保留，修正后恢复。InRange保存lower=[60,40,20]；非法“[1,”报行1列4且保留文本、文件hash不变；{}被类型拒绝、[]被三项语义拒绝，修正后保存重开保持。没有直接读取live undo stack，此处不宣称完整撤销栈已证明。证据：截图101–133和InRange完整性JSON。

### Fresh Runtime的ROI与Histogram预览

全新Designer3285的schema2.4专用ROI/Histogram使用本地图像，完成坏图恢复、未Apply临时参数预览、关闭后正式参数与工程文件不变。ROI前、ROI后、Histogram后数据库jobs=0、jobEvents=0。零行不是“未调用LoadProject”或直接检查live loadedDocument的证明；fresh进程、源码路径与原有自动化提供分层支持，该限制保留。

只读留存ROI原图及四份完整输出资产，像素全部一致。实际留存的是临时X=32、Y=16、width=32、height=24阶段；X16与X32的小裁剪碰巧相同，必须用fullMask空间边界和mtime确认X32，不能冒充最初X16预览。关闭ROI编辑器后五个旧asset均不存在。Histogram可见bins16与pixelCount=76800，原图320×240匹配；临时将bins改为32会改变图形，关闭discard后保存基线仍16（截图171）；没有取出序列化live逐bin值，因此不宣称逐bin数值全核验。

### ROI专用预览的易用性缺口

专用ROI PreviewLabel保留完整pixmap用于缩放，却缺完整图查看/导出操作、右键保存菜单和双击完整查看。该缺口由原生观察和对应源码界面定义确认，归为易用性FAIL。缓存PNG的只读留存属于采证，不是用户通过界面完成导出。通用算子调试的threshold/CLAHE完整结果导出已实际通过，不能概括为整个产品无导出能力。ROI的准入和参数页“算子调试”入口由源码支持，可另开会话、重新上传输入并执行后逐端口导出，但本轮未亲自完成ROI这条替代GUI链，因此为NOT_RUN；它不是导出刚才pure-preview同一次结果。通用图像显示也会缩放，导出后需外部查看，不能称自带1:1查看器。专用预览与D02/D07/D08通用调试完整导出是不同入口，结果分开记录。

证据：ui/fresh-preview-final-summary.json、ui/roi-native-preview-assets/pixel-verification.json、ui/roi-after-close-asset-retirement.json、ui/roi-preview-export-boundary.json；截图162–170。

### CLAHE失败历史与损坏输入集恢复

CLAHE clipLimit=0准备阶段报E_PARAM_INVALID，界面说明本次未启动执行，旧SUCCEEDED结果仍保留且跨自然poll历史身份不变。历史执行5c31fdccf6d549a2a7ed2d9c82cd9fac的完整PNG元数据为clipLimit2，320×240，参考像素差异0。修正为2后一次执行成功，新身份前缀cf6834b0；完整身份记录在矩阵。开始有一次坐标未命中执行按钮，真正成功的单次点击才产生新执行，不把坐标偏差说成产品重复Execute。

流程及算子两条路径均保存正常命名输入集，再加载损坏包；错误跨poll保留，旧输入未被替换，加载正常输入集恢复。这里只关闭这些代表场景，不扩成U08所有类型/来源/原子失败矩阵。证据：截图156–158、175–191；ui/clahe-history-verification/verification.json。

## 七 原O与U历史编号分层映射

仅复用历史范围和exact nodeid，从本轮全量JUnit重新取结果。软件PASS仅限列出的Linux/offscreen路径；O15/O16/O18不能因此声称本地Windows路径执行。O02–O13原尺寸为640/800×430，已测离线Write 680×500不是原尺寸矩阵。

### O01至O20

| 编号 | 原范围 | 软件 | 原生GUI及覆盖 |
|---|---|---|---|

| O01 | 默认缓存/模拟相机编辑器 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O02 | PLC小窗 未连接 / 高级项关 / 640×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O03 | PLC小窗 未连接 / 高级项关 / 800×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O04 | PLC小窗 未连接 / 高级项开 / 640×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O05 | PLC小窗 未连接 / 高级项开 / 800×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O06 | PLC小窗 已连接 / 高级项关 / 640×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O07 | PLC小窗 已连接 / 高级项关 / 800×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O08 | PLC小窗 已连接 / 高级项开 / 640×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O09 | PLC小窗 已连接 / 高级项开 / 800×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O10 | PLC小窗 写入态 / 高级项关 / 640×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O11 | PLC小窗 写入态 / 高级项关 / 800×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O12 | PLC小窗 写入态 / 高级项开 / 640×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O13 | PLC小窗 写入态 / 高级项开 / 800×430 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O14 | 长工作流名 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O15 | Linux pidfd / Windows进程句柄 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O16 | Linux pidfd / Windows进程句柄 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O17 | 模拟PLC确定性accept | PASS 1路径 | NOT_RUN；原范围未完成 |
| O18 | Linux Python / Windows batch入口 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O19 | Job资源退休 | PASS 1路径 | NOT_RUN；原范围未完成 |
| O20 | 默认缓存+双Job SQLite | PASS 1路径 | PASS；两Job、保存重载、新进程重开，保留fixture边界 |

### U01至U13

| 编号 | 原范围 | 软件 | 原生GUI及覆盖 |
|---|---|---|---|

| U01 | 原子编辑、变量迁移取消/回滚、无效草稿恢复 | PASS 5路径 | NOT_RUN；部分完成：无效JSON等代表编辑恢复已测；迁移/回滚未全测 |
| U02 | 读轮询不丢点击、ACK/暂停序号、历史身份 | PASS 19路径 | NOT_RUN；部分完成：CLAHE历史与pause身份已测；ACK矩阵未全测 |
| U03 | Blob条件输出与required/optional/nullable预检 | PASS 4路径 | NOT_RUN；原范围未完成 |
| U04 | Switch字符串语义、真假快捷键、预览/真实下游 | PASS 16路径 | NOT_RUN；原范围未完成 |
| U05 | 启动前断点应用/确认/未知ACK与混合版本拒绝 | PASS 12路径 | NOT_RUN；部分完成：普通/条件断点已测；拒绝/未知ACK/混版未全测 |
| U06 | 视口本地保存、切换恢复、低缩放与导航 | PASS 2路径 | NOT_RUN；原范围未完成 |
| U07 | 编辑器新建与Save/Discard/Cancel、活动任务保护 | PASS 5路径 | NOT_RUN；部分完成：新建/保存重开和调试占用已测；决策矩阵未全测 |
| U08 | 命名输入集、完整数据/来源/原子失败及恢复 | PASS 42路径 | NOT_RUN；部分完成：流程/算子损坏包恢复已测；类型/来源未全测 |
| U09 | 完整PNG/JSON、历史调用身份、取消/过期原子导出 | PASS 30路径 | NOT_RUN；部分完成：代表PNG/JSON身份已测；取消/过期未全测 |
| U10 | 保存后缀/重复后缀确认与原文件保护 | PASS 13路径 | NOT_RUN；部分完成：GTK自动后缀已测；重复后缀/覆盖未全测 |
| U11 | 整数0/-1/1/9鼠标/键盘跨poll并执行 | PASS 8路径 | NOT_RUN；原范围未完成 |
| U12 | 五类安全拒绝中文引导、无Worker/Job/设备动作 | PASS 17路径 | NOT_RUN；原范围未完成 |
| U13 | 纯导航不产生dirty/重排，真实编辑可撤销 | PASS 6路径 | NOT_RUN；原范围未完成 |

上述O/U路径和D测试重叠；不能与全量总数累加。原生NOT_RUN不引用历史PASS或FAIL，也不把offscreen测试抬升为真实桌面通过。完整nodeid与当前JUnit对应记录在JSON矩阵中。

## 八 远端CI与实际checkout身份

| Run | 事件与实际源码 | 终态与测试结果 |
|---|---|---|
| 38124940288 | push；abc7ae0541e997be0bbe3a4794ad604c239de79a | success；Ubuntu4829/19/29，Windows4843/5/29 |
| 38124943996 | pull_request；29154d04f29b1d4ae50c659fcc30531c8f9000fb | success；Ubuntu4829/19/29，Windows4843/5/29 |
| 38124944740 | 冻结Windows包；源码abc7ae0 | success；prepare/build/verify成功，vision_train条件跳过 |

数字依次为passed/skipped/subtests。PR普通CI的实际checkout是merge SHA，不能用API head_sha替代checkout日志。所有run attempt=1；未重跑CI或改动仓库。Legacy commit status为空的pending不覆盖Actions成功结论。

冻结包prepare/build/verify明确使用abc7ae0源码，packager是d5f3617a7265c545c793366f93e3a114212d8c59。合同测试11 passed、2 subtests；provenance source_commit匹配。安装器仅核验hash，未执行安装；实体相机、GPU、现场验收、Designer主编辑、OperatorView像素保真均不在此验证范围。

Portable SHA256为afc9d8f2a535977937a21dbea3b558892977aaa4499315974082b649a5d41dc2；Setup SHA256为937de5e97243e3f64a86dd3cc09b26fb45ffb0e236e11cbbcce08204519baec2。原始API、7份完整job日志及精确身份在ci/。运行链接：

- Push CI：https://github.com/jsdfhasuh/emo_master/actions/runs/38124940288
- PR CI：https://github.com/jsdfhasuh/emo_master/actions/runs/38124943996
- Frozen Windows：https://github.com/jsdfhasuh/emo_master/actions/runs/38124944740

远端成功与本地Debian首次FAIL并存，不能互相覆盖。

## 九 当前问题和待审核边界

| 项目 | 状态 | 处理原则 |
|---|---|---|
| 本地SQLite BEGIN观测测试 | FAIL | 保留首次失败；诊断为观测竞态推断，不作根因定论 |
| ROI专用预览完整图查看/导出 | FAIL | 易用性问题；预览像素正确不等于用户导出路径通过 |
| Network-faults非致命grpc栈 | PASS | 断言通过但stderr告警保留，不推定生产影响 |
| 历史1657业务NG问题 | NOT_RUN | 精确根因未确认，当前未复现不等于历史关闭 |
| 剩余原O/U与硬件平台范围 | NOT_RUN | 部分完成按分项说明；不得整体提升PASS |
| 最终桌面退出与源码终检 | PASS | 3285/3330正常退休，桌面直接/proc复查本轮相关进程为空；tracked diff为空，保留一个UI生成未跟踪素材 |

正常UI关闭后，Designer3285及tracker3330于10:04:36.442558 UTC全部消失，时间线processes=[]。10:08:44.300637 UTC通过实际桌面terminal再次复查，PID namespace与原observer同为pid:[4026532208]，sameNamespace=true，本轮相关processes=[]，observerAlive=false。早先10:05:46的CUA REPL /proc处于不同namespace，原记录保留但不用作桌面退休证明。最终精确HEAD不变，5034文件tracked摘要齐全、tracked diff为空；git status仅?? docs.emofixture，这是本轮GTK对话框误导航后生成的输入集素材，原件和同字节证据副本保留，不是tracked源码修改。

代表主窗client尺寸794×573、PLC约680×500已测。环境仅配置QT_SCALE_FACTOR=1，实际DPR未测；125/150/200%原生缩放均NOT_RUN。环境没有阻碍，本轮代表覆盖并未穷尽全部高DPI、async、重连和输入来源组合。

因此本报告不作上线、现场验收或全部通过声明。不建议以远端CI成功替代本地FAIL处理，也不建议把已测代表编辑器推广到完整历史矩阵。

## 十 证据索引与复核方法

- case-matrix-final.json：41原编号、8关键词、精确nodeid、分层状态、身份、证据路径与覆盖边界。
- automation/REPORT.md、automation/acceptance-summary.json、automation/full/junit.xml、full/first-failure.json：本地全量、首次失败、skip和独立专项。
- targeted/TARGETED_ACCEPTANCE.md、targeted/summary.json、targeted/mixed-sqlite/、targeted/p0-*、targeted/phase-observation/：并发、图像、NG、诊断与资源证据。
- ci/findings-zh.md、ci/runs-final.json、ci/checks-final.json及job日志：精确commit/merge SHA区分和终态。
- ui/observations.jsonl与编号截图：原生操作事实。ui/export-pixel-verification.json和原导出包：完整PNG/JSON身份与像素。
- ui/projects/o20-native-final-summary.json：两Job、COMMITTED、保存和新进程重开链路。
- ui/fresh-preview-final-summary.json、ui/roi-native-preview-assets/、ui/roi-preview-export-boundary.json：fresh预览、实际asset像素和用户入口限制。
- ui/clahe-history-verification/verification.json：保留成功历史的元数据、PNG、参考像素和范围。
- report/d05-export-readonly-verification.json、report/ui-observations-review-snapshot.jsonl：本版缺口补证与固定观察快照。

可独立先核对目标SHA、各门禁退出码，再核对原编号的软件/原生两列，最后检查“未测”和“部分完成”说明；不要仅统计PASS单元格作最终结论。

## 十一 供Codex继续复核的精确定位

详细交接见targeted/CODEX_HANDOFF.md。以下是本报告的最小定位与后续验收标准，未在本次实施修复。

### 本地全量失败定位

- 原测试：tests/test_r3_sqlite_phases.py:466–509，testRealWriterContentionIsVisibleAtBeginAndPreservesAllDurableRows；486–493的两秒/5毫秒轮询要求active列表含begin_immediate，第493行首次失败。
- 原产品边界：src/emo_master/apps/runtime/context/sqlite_transactions.py:15–35，beginImmediate(connection)。native busy_timeout暂置0；仅BEGIN重试；准入sleep 1–5毫秒；退出恢复原timeout。不能回放SQL body/commit或副作用。
- 观察器：scripts/r3_sqlite_phases.py，SqlitePhases._begin(65)、_finish(85)、_invoke(126)、snapshot(187)，_Connection.execute(252)。核对瞬时active与累积entered/failed/returned的语义。
- 继续复核时，应区分“从未进入native BEGIN”和“进入但active span短于采样间隔”。可用现有累积计数/阶段证据建立稳定不变量，但必须先解释与原测试意图的对应；不能只延长watchdog或删掉断言。
- 原严格持久化断言应保留：占锁期间returned=0且无commit；释放后thread退出、result=[1]、无failures、commit returned=1、数据库[(1,)]、synchronous=2和journal_mode=wal。不得以当前补充诊断替代原全量FAIL。
- 证据：automation/full/first-failure.json与pytest.log；targeted/phase-observation/report.json；targeted/original-full-failure-db-readonly.json。原失败的精确调度仍未被直接记录。

### ROI专用预览易用性定位

- 视图：src/emo_master/apps/designer/ui/widgets.py:75–104，PreviewLabel.setPixmap保留_sourcePixmap，_rescale只向QLabel显示缩放图，未定义context menu/export/double-click viewer。
- 调用：src/emo_master/plugins/builtins/roi/editor.py:185、285–297，RoiEditorController.handlePreviewResult下载完整asset后调用setImageLabel；onClose/dispose沿PurePreviewControllerBase清理。
- 装配：src/emo_master/apps/designer/operator_editors/builtin_layout.py:_replaceLabel；plugins/builtins/_editor_support.py:setImageLabel、PurePreviewControllerBase；plugins/builtins/roi/ui/editor.ui的croppedImage/croppedMask/fullMask标签。
- 可复核的后续目标：在专用预览入口提供完整图查看/导出并保留明确身份、关闭生命周期和取消/过期保护；或明确且可发现地引导至已有通用调试导出。不能用复制缓存文件冒充用户工作流。
- 对照测试：tests/designer/test_draft_pure_preview_ui.py及test_draft_pure_preview_context.py的fresh Runtime、本地图像、无正式Job、未Apply草稿和关闭清理断言；新增入口应继续保持这些边界。
- 原生复查：fresh schema2.4 ROI，坏图后恢复、X16改X32未Apply；从专用预览打开完整图/导出原分辨率PNG，逐像素核对fullMask边界和四个输出，关闭后所有资产退休。通用threshold/CLAHE导出仍应回归通过。
- 证据：ui/163–165截图、ui/roi-preview-export-boundary.json、ui/roi-native-preview-assets/pixel-verification.json、ui/roi-after-close-asset-retirement.json。

上述是定位与复核建议，本次验收未实施修复、推送或更改仓库。历史1657 NG问题继续保持未确认，不从这两个发现推导其根因。
