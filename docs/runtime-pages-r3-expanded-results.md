# R3 使用能力扩展：源码验证记录

基于 `d5c7c2db488746691a2117d3e28d4bc41f253b7d`，2026-09-30。
用户已要求继续全部计划，并授权原开发分支上的远端开发、Git 同步和本地测试。
用户同时确认暂时没有实际工程样本。本批不因此把合成工程改称现场验收。

## 实现及支持边界

| 项目 | 本批源码能力 | 尚未证明 |
| --- | --- | --- |
| §5.1 组件使用 | 布尔/字符串判定输入及规范编码；已知集合字段选择；导航、显示结果详情、冻结、恢复；有限字体/卡片属性；合法空值与故障分开；带标识的离线状态模拟 | 实际工控屏幕、真实工程操作验收 |
| 窗口与图像预算 | 每窗仍预留 16 MiB，随实际屏幕/DPI变化重算边界；同一已验证图像资产别名只转一次 Qt 图片 | 整个进程 RSS 上界、任意屏幕/字体 |
| §5.2 普通运行 | 最多16个绑定 source ID/16个作用域；同一8 MiB Job原图缓冲支持单图8 MiB，或双图各4 MiB；别名复用；公开 profile，启动前协商 | 双1080p BGR不在此双图profile内；实际图像规格未取得；无隐式缩放 |
| 独立作用域 | 独立身份/开始和封闭水位；安静作用域不被其他作用域的历史周转挤掉；仍在8 MiB元数据总额度内；压力过期、旧客户端reset、迟到数据栅栏 | 跨工位产品追溯、不限作用域/历史 |
| 生命周期 | 图像 lane 独占、超时包含排队、整个缓冲隔离；原导出/读取500 ms期限和128/256 MiB预约额度保留 | 无限稳态、现场设备节拍 |
| §5.3 交付 | 原测试包/测试宿主保护保留 | 实际工程源码态验收缺失，因此未扩展通用交付、冻结包或发布 |

普通“开始运行”的两项新增 GUI 回归使用两张不同图片（原图/结果图）或两个不同调用作用域，
均为一个正常 Job；验证实际像素、别名共享、独立 invocation/resultKey、两页及协议额度。
隔离调试和测试交付仍保留其明确测试限制，不能把普通运行支持范围套入测试宿主。

## 代码接点

- 编辑：`page_designer/property_adapters.py`、`tools.py`、`editing.py`，沿用原事务与统一草稿
- 模型/显示：`core/presentation/models.py`、`ui/presentation/{renderer,hub,table}.py`
- 采集：`core/presentation/capture_limits.py`、`runtime/presentation/{normal_capture,collector,exporter,service,store,rpc}.py`
- 只读客户端：`clients/runtime/{display_session,pins,view_state}.py`
- 明确启动协商：原 `RuntimeController`、`RuntimeWorker`、`RuntimeClient`、MainWindow
- 观测工具：`scripts/r3_measure.py`、`r3_soak.py`、`r3_resources.py`

## 功能与正确性证据

以下分组数用于定位覆盖，不能相加冒充完整套件：

- 类型适配/模型116 PASS；最终组件UI42 PASS；用户路径/DPI/资产别名13 PASS
- 新普通 GUI 多来源路径2 PASS；原客户端/控制器/worker51 PASS
- 后端最终重点35 PASS，包含19项多来源、额度、迟到和旧客户端回归
- 曾出现后端 aggregate 469 PASS/1 FAIL：新的迟到快照保护错误阻止原 out-of-range cursor recovery。
  已把“已接受服务器游标”与“请求游标”分离；原失败及新栅栏用例均通过
- 独立审查曾发现安静scope被历史淘汰、旧读取者在reset后接受迟到过期值、DPI迁移预算、Qt别名重复转换，均有针对修正和测试
- 同一源码完整 `env -u PYTHONPATH QT_QPA_PLATFORM=offscreen python -m pytest -q`：
  **1394 PASS、1 SKIP，252.52秒**；唯一跳过是未授权/未启用的真实相机烟测

### Qt 间歇失败仍须单列

本批两次完整检查在原计数器对话框测试的 `processEvents` 处出现 native SIGSEGV。
不能因上述完整pytest成功就把这两次记录改判。

已定位其中一个确定机制：原独立 `FlowScene` 测试留下无父对象的scene/node循环，
不是MainWindow已修复的所属scene。强制下一次实际counter-worker线程GC时，22项前缀
复现SIGSEGV及10条跨线程停止timer警告。替换22处独立scene构造，明确GUI线程销毁后，
同一强制控制22 PASS、零该警告；原164个断言不变，并新增生命周期回归。

修正后仍记录过一次原位置aggregate崩溃；另有保留全套collection的前缀454 PASS、
带线程/销毁记录的core+designer733 PASS及上述全套pytest成功。
此时仍不能宣布全部Qt间歇问题已经解决；原始失败保留，继续针对回调/生命周期诊断。

## §7 观测工具及实际已运行范围

工具只使用固定离线输入、受控测试触发、临时数据、正式Runtime/Job及只读客户端。
不访问设备、不改生产目录/数据库；失败必须保留证据和未确认回收的目录。

`r3_measure.py` 默认保留原1080p/5Hz、96次/前8次预热、三组交替对照。
原P2/P3脚本未改。测量包装仅在受监督进程内启用，记录分段时间、全部分母、迟到、
解码/提交/原生paint、资源和缺失；trace/log均有额度，超额明确失效。
嵌套时间不可相加；仪表自身有开销，不替换历史基准结果。

已运行的只是4输入/1预热/1对照组的四臂诊断烟测：全部子进程退出0、source稳定、
无trace溢出、所有者收尾；每消费者及窗口3/3结果解码/应用/paint。
测得3个输入对应9次旧PNG编码；页面开启再增加3次overlay导出编码。
重复编码次数得到验证，不能据此认定它是全部性能回退根因。
短样本原始阈值比较仍为FAIL（捕获回退42.66%、Qt增量7.96%、采样数据年龄520.5ms）；
整体标 **NOT_ASSESSED**，未以小样本或offscreen宣称性能验收。

`r3_soak.py` 默认900秒、上限1800秒，一图/一scope、正常StartJob、两个只读session、
两个共享Qt窗口。记录进程RSS/native线程/句柄、采集额度、完整ordinal位图分母、
GUI提交/原生image paint；JSONL最多32 MiB。它是有界资源观测，不是任意时间稳态证明。
使用外部所属进程树watchdog；结束必须确认实际Job/IPC/导出所有者退休。

已运行5秒/25输入烟测：两个消费者各25/25 COMPLETE，两窗各25/25提交与paint，
无导出/监督器错误，全部所有者收尾。Linux日志含Fontconfig缓存目录警告，不能用此
环境的短烟测承诺屏幕/性能。**正式长窗口与Windows本批复测尚待执行**。

## 完成判断

功能实现、性能稳定、可交付范围、现场许可分别判断。已有源码/离线证据不能替代实际工程。
§5.3实际工程门槛未满足；旧性能FAIL保留；正式长稳态/本批Windows证据仍待取得。
因此当前不能称“整个计划完成”。

## 发布候选前的最后完整检查

同一源码再次执行 `env -u PYTHONPATH QT_QPA_PLATFORM=offscreen python scripts/ci_check.py`：
protobuf、Ruff、mypy（281源文件）、pytest全部通过；**1394 PASS、1 SKIP，253.34秒，退出0**。
这是继完整pytest成功后的另一次完整执行。前述native崩溃记录仍保留；候选可用于独立Windows验证，
并不宣布间歇Qt、长期稳定性或性能/现场验收完成。本批Windows及正式测量结果需按实际运行追加证据，
不能引用此前d5c7c2d的成功来替代新源码验证。

## 计数器对话框的独立所有权修正

后续精确探针证明：重复40次刷新后，原实现的40个已完成QThread仍为native-valid，
正常shutdown、删除dialog及主线程/后台GC都不回收。原lambda接收器还会在直接删除dialog后
进入已删除的控件。该缺陷可重复，但探针没有复现native SIGSEGV，不能把二者强行归因。

修正为明确QObject接收上下文和sender身份校验、GUI亲和线程的finished/deleteLater退休，
并为删除dialog后仍活跃的请求提供应用所属的停止/等待路径。保留原取消与等待语义；
不能取消的RPC仍可能延长应用退出，不以Future.cancel伪报线程已停。
修正后40个worker均在GUI线程native销毁，所有延迟投递/退出场景无回调异常或保留对象。
新增回归及重点套件10 PASS，独立审查通过。

此修正后的完整 `scripts/ci_check.py`：protobuf/Ruff/mypy PASS，pytest **1398 PASS、1 SKIP**，
256.70秒，退出0。此前已发布4d1f149在GitHub Ubuntu仍出现DeferredDelete处native崩溃；
该失败记录继续保留，后续提交的双平台CI/本机结果必须独立核对。

## ALL/NONE 与来源身份的后续实现

正常 StartJob 现可明确选择旧预览快照策略 ALL 或 NONE，默认仍为 ALL；策略在提交时冻结，
并参与请求幂等身份。NONE 关闭旧快照写入，不关闭页面采集、业务输出或运行记录。
能力协商先于启动；受限制的测试宿主明确拒绝新增 NONE，不借此扩展交付范围。

每个旧快照使用不可变 capture ID，原子发布图像及对应 Job/工程版本/nodeRun 来源。
工程/所选 Job 不匹配、来源记录被裁剪或缺失时标为历史或 UNKNOWN，不能猜为当前结果。
Designer 提供下一次运行策略选择，切换 Job/工程及读取失败时清除旧选择/像素；查看历史
需要明确选择。清理失败保留有界待删除记录并暂停准入，不允许 UUID 文件无限堆积。

独立审查覆盖幂等冲突、旧服务器、受限宿主、写入失败原子性、删除失败、来源缺失、UI旧选择。
后端重点91 PASS，含 Qt 的组合129 PASS，Operator View 2 PASS；这些分组不能相加充作全套。
新诊断只在原断言失败时附加有界内存状态，不新增 RPC、不延长业务/测试 deadline。

又定位一个确定的 Qt 测试清理缺陷：只检查直接父对象，会把菜单/下拉弹层及其祖先窗口同时
列为独立删除根。原两项布局用例可复现 native 崩溃；按完整祖先链过滤后，同组合与新增
所有权回归通过。该修正仅明确测试夹具的删除顺序，不宣称解释了所有历史 native 失败。

## 精确版本的 Windows 与性能记录

本地 Windows `f84e2aa`：完整 CI **1397 PASS、2 SKIP，374.73秒**，退出0；两个跳过分别为
本机不支持的 symlink 和未启用真实相机。`2affbcf` 的 GitHub PR CI 双平台通过，但同 SHA
push CI 的 Windows 为 **1398 PASS、1 FAIL、1 SKIP**：迟到解码回归等待解码钩子20秒未进入。
因此该精确版本 CI 结论为混合，不能称全部绿色。

本地 Windows `f84e2aa` 的12臂原1080p/5Hz/96输入测量为有效 **FAIL**：消费者正式分母
均88/88解码/应用，无丢弃；Qt窗口只提交25/27/24次、paint24/23/23次；活动输入期间数据年龄
仍达1.807–2.557秒，一组Qt p95回退26.79%。末尾等待会进一步拉高全窗口年龄，但不能用
排除末尾来掩盖活动期间失败。旧PNG正式264次、新导出88次，分段计时有包含关系，不能相加。

同机独立探针发现观测器代价：原资源读取p95约0.704ms，新增逐PID原生线程枚举p95约
160.75ms，四PID批次p95约616.28ms。它确实阻塞GUI观察，但尚不能解释全部1.5–2秒空档。
后续非阻塞采样与正常 ALL/NONE 对照需单独版本化、保留相同工作量/完整分母及原 FAIL。
正式15分钟稳态尚未完成；实际工程样本仍缺失，§5.3验收与冻结交付门槛仍未满足。

本次 ALL/NONE、来源保护与祖先清理整合后的完整 `scripts/ci_check.py`：
**1470 PASS、1 SKIP，255.70秒，退出0**，protobuf/Ruff/mypy（282源文件）通过。
此前同内容一次执行在约97%时被执行环境中断，无终态记录，保留为 INCOMPLETE。

随后修正迟到解码测试的确定性：单个已完成保留结果、先安装钩子再创建消费者；按精确
resultKey验证图像解码成功且无失败，切换Job后拒绝应用，并有不切换Job的成功应用对照。
GUI构造放在解码门释放之后，原3秒门与20秒成功等待均未延长。整个相关模块
**13 PASS，40.91秒**；最终Ruff与diff检查通过。此最终测试差异后的完整双平台CI仍待运行。

## 非阻塞观测与正常策略对照工具

`r3_measure.py --sampler-mode background` 是单独标识的观测器对照，原参数及默认
`legacy-blocking` 保留，原 P2/P3 脚本不变。原生资源读取由一个有界后台所有者执行；
GUI读取明确标注的缓存或 pending，逐PID记录真实采样起止、样本年龄和批次开销。
请求合并只计为观测器合并，不充作业务丢帧；无法取得资源数据不能假称完整。
结束前收集新的所有者资源样本并确认采样线程退出，不把旧缓存写成“收尾后读数”。

`r3_policy_measure.py` 通过正常 LoadProject/StartJob，使用明确策略及请求身份，轮换
ALL无页面、ALL双窗口、NONE双窗口三组。每组保留同一1080p/5Hz/96输入、8预热，默认三轮。
比较分别报告页面增量、策略差异、组合配置，不能把 NONE 当原 ALL 基准通过。
正式分母按精确ordinal检查，重复/越界/缺失和失败执行都影响判定；未完成的交付阶段
保持未确定，不用最后输入时间伪造完成边界。原全窗口年龄判断保留，同时列出活动输入、
最后交付、终态等待和收尾的独立观测。

已完成51项纯计量/所有权回归。正常策略4输入/1预热/1组三臂烟测：exit0，source稳定，
全部watchdog/trace与精确分母通过，**VALID / NOT_ASSESSED**。原始年龄570.0/506.6ms及
offscreen判据仍FAIL，不宣称原生性能通过。原工作量的后台采样四臂烟测也exit0、source稳定、
全部所有者退休、trace及采样角色完整、消费者和UI分母完整；同为 **VALID / NOT_ASSESSED**，
小样本捕获回退16.96%、Qt年龄518.93ms/offscreen等原始FAIL保留。

环境中断的首次后台烟测只留下RUNNING清单、没有终态，标为INCOMPLETE；随后同名目录
启动被正确拒绝，未执行负载。恢复烟测使用新目录，未复写原始trial证据。
这些烟测只证明工具接线及有界收尾，正式Windows相邻对照与15分钟稳态仍待执行。

同一原生资源枚举也在旧稳态脚本GUI循环中执行，现改为后台批次。最长1800秒/1秒间隔
及原45秒启动/收尾余量对应固定1846批上限；每批原始读数只写一次，32 MiB日志上限不变。
GUI观察行携带pending/缓存时间和年龄；未观测到某PID不能标为资源覆盖完整。
采样线程真正退休后才做一次新的收尾后本机资源读取。

稳态采样/收尾/资源重点13项通过；5秒、每秒采样烟测exit0、source稳定：两个session各
25/25 COMPLETE、两窗各25/25唯一提交与paint，无业务丢弃/读取错误。六个原生批次覆盖
所有者、Job与两个exporter，采样错误/溢出/合并为0；线程退休后读数明确非缓存。
证据日志37,274字节。该短窗口仍不是15分钟稳态、原生Windows或现场验收。

后台采样、策略对照与稳态修正整合后的完整 `scripts/ci_check.py`：
**1523 PASS、1 SKIP，258.19秒，退出0**；protobuf/Ruff/mypy通过，新增脚本另行Ruff通过。
前一功能提交 `4f587e7715aa3911605a539d6e33a708c8ae91b7` 的GitHub Ubuntu push/PR检查
分别1471 PASS/1 SKIP（286.89/283.99秒），Windows当时仍在执行；不据此宣称双平台通过。

## 后续操作审查与监督器回归

精确GitHub结果继续保留：`4f587e7` 的PR双平台通过，push Windows为1469 PASS/2 FAIL/1 SKIP；
`0a71a6c` 的push双平台1523 PASS/1 SKIP，PR Windows为1521 PASS/2 FAIL/1 SKIP。
它们都不是“所有检查绿色”。实际用户Windows的`4f587e7`完整检查1470 PASS/2 SKIP，
400.5秒；与GitHub各自列示，不用一处成功替代另一处失败。

操作审查发现原生拖入控件/来源与移动控件绕过未提交属性同步，同一控件按下还会重载
表单，丢失有效或无效输入。修正使用原coordinator.sync并避免同一选择重复重载。
六个反例在原版全部失败；修正后页面设计完整43项通过，包含原生拖放、修正重试、历史步数、
保存重开，独立审查通过。

另一个CI用例直接写入活动client.cursor来模拟错误游标，可能被在途普通回复覆盖而根本
没有发出错误请求。现在只在一个真实Snapshot RPC中注入错误游标，并追踪该确切回复的
真实reset_required及其自身接受导致的reset增量，随后验证图像/数值恢复且仍只有一个Job。
原15秒等待及0.5秒RPC期限不变，相关网络模块10项通过，独立审查通过。

监督器问题已有确定的机制复现：事件桥一次处理一个事件，持有监督器锁同步持久化后，
先检查心跳再读取下一条。把持久化模拟延迟到5001ms，原5000ms监督器会杀掉进程，
即便下一条心跳已经排队。CI中记录的逐事件消费延迟与此一致，但不能断言其具体磁盘原因。

新增每Job一个固定大小的生产者心跳时间单元，使用同机单调时钟与非阻塞锁；正式心跳
事件、顺序和持久化不变，旧排队事件不能续期，锁持有者死亡/读失败依旧到期。
启动基准保留在process.start返回后。单元随实际进程/桥所有权退休，旧直接调用保持兼容。

独立审查还用真实spawn复现了首个候选在队列feeder收尾时停止心跳的回归，已修正且未发布
该候选：终态入队时在局部锁下停止新正式心跳，原心跳线程只维持时间单元直到现有队列
close/join_thread完成，再停止线程；仅管理了时间单元的正式Job使用该收尾路径。
不提前发布终态、不改变期限、不丢事件。38项重点测试通过，真实spawn在6000模拟ms的
未读终态积压中仍保持健康，排空后9条事件按序持久化、COMPLETED、退出0且全部退休；
真正停止发心跳仍在5001ms失败。独立复核通过。此修正不声称解决SQLite吞吐或原10秒终态等待FAIL。

补充保留验收：1000次真实Qt切页、30次浮动窗口创建/原生销毁及资源计数、5页50控件/
1080p像素功能；真实正常Job两作用域总览分别锁定旧GUI NG，即使后台已有新OK，身份、像素、
标量和lease退休匹配；模拟及10次真实共享观察窗开关不隐式Start/Stop、访问设备或改计数。
三项重点27.24秒通过；加强最终native销毁后的A18单项23.19秒通过，独立审查通过。
它们不是完整0/1/2观看端原生性能矩阵。隐藏Qt转换与持续PNG解码是不同事实：当前默认
共享会话仍持续解码，A18的零图像需求解码抑制尚为独立契约/实现缺口，不能用转换数替代证明。

上述心跳/编辑/验收整合的第一次完整检查：protobuf、Ruff、mypy通过，但pytest在既有
`testActiveNormalObserverDisconnectStopRetireAndExplicitRestart`第二次明确Run的GUI事件等待中
发生native SIGSEGV（pytest -11，外层退出245）。它在新增A18/B11用例执行之前发生；
不能由重点测试成功推导此组合通过，也不因历史出现过Qt崩溃而自动归为基线。
失败日志保留，继续做该具体顺序的生命周期隔离；该次整合没有以全套通过名义发布。

随后独立探针证明已结束RuntimeWorker/StopWorker的native对象仍被无父对象的信号lambda
保留，且controller.close之后仍投递更新；另有页面重复关闭时访问已删除hub timer的反例。
改为应用所属的明确QObject接收/线程所有者、GUI排队slot、当前worker身份检查和真实退休。
原生上下文删除/关闭会切断UI投递；删除前等待实际线程结束。取消失败后仍可重试关闭，
通过弱生命周期引用只清除匹配的worker指针，不触发UI或额外Stop升级。
具体反例修正及最终52项重点通过；首版所有权修正完整CI为1567 PASS/1 SKIP（316.55秒），
最终重试关闭修正之后的组合检查另计。该证据仍不宣称解释了所有历史SIGSEGV。

零图像需求现有明确本地模式：GUI所属会话选择imageDemand，按可见实时页面合并来源需求；
默认/无界面消费者仍保留连续解码及原回归行为。隐藏来源不继续ReadAsset/PNG解码，
元数据仍前进；恢复显示只通过原有8槽队列/单解码线程读取同一Job已采集的最新结果。
已有同结果图像复用，缺图显示独立加载状态而非旧图。详情仍锁定GUI已显示的结果；缺少的
冻结图像经同一解码器、原lease/TTL及16 MiB pin预算取得，分配前按有效PNG尺寸预约额度。
别名、代际、过期、取消与销毁均有有界清理；原生窗口销毁不会遗留需求或破坏其他窗口。
最终63项客户端/Qt/既有回归通过（130.61秒），Ruff/mypy及独立静态审查通过；整合验证另计。

Windows精确`0a71a6c`结果保留：本机完整CI1522 PASS/2 SKIP（402.36秒）；相邻旧/后台
观测12臂及正常策略9臂均为VALID/FAIL。后台观测改善GUI延迟但仍有完整性/年龄失败。
NONE组实际约5Hz、完整88结果/窗口分母，仍有模型p95约210.86–225.45ms、GUIp95约
235.32–248.49ms；不能把吞吐改善79–82%改称显示延迟通过。

900秒小图稳态实际FAIL：4500/4500 COMPLETE到达且客户端零丢弃/读取失败，GUI提交4500、
paint4499，但原945秒任务终态等待上限到达，949.98秒退出1。尾段所有者CPU仍约一核工作，
Job/exporter几乎空闲；精确最后事件、任务状态/错误及退出码未记录，不能补猜。
采样189批退休且无错误/溢出，外层进程树最终关闭，但缺失正常终态/退休资源读数。
不再盲目重复15分钟；先分离正式事件通道、SQLite/GC和终态资产提升的成本。

新增独立`r3_sqlite_event_probe.py`仅写新诊断目录，比较原连接方式、额外保活连接和复用写连接，
保留每事件事务、busy timeout和全部PRAGMA。默认每臂600事件或20秒，必须另有外层watchdog；
记录阶段直方图、GC/连接、原生资源、代码摘要与逐条序号完整性，不输出性能PASS。
六项安全/完整性回归通过（0.62秒）。正式Windows容量结论仍待取得。

需求模式与最终Qt/心跳修正组合的完整检查为4 FAIL、1599 PASS、1 SKIP（408.25秒），
protobuf/Ruff/mypy通过，未发生native崩溃。三个GUI用例在元数据已到但图像仍LOADING时
过早断言图像/转换基线；另一个旧解码用例重排已缓存同结果，未再进入解码钩子。
后续将以实际图像提交和首次真实解码同步修正测试，不改变原期限、像素/身份断言；
此条记录保留该次FAIL，重点修正结果及下一次整合检查另计。

独立SQLite诊断代码已以`adc5b645e741ea39a7e90c3badc67ff71799ad17`正常快进发布；
该提交只含脚本及六项测试，未包含上述仍在整合验证的生产代码。

四个同步用例模块修正后28项通过（40.60秒），独立复核未发现弱化断言；完整组合重跑另计。

本地无损PNG参数诊断27种图像×6组，源像素/摘要及解码像素全部不变。精确R3结果图
默认编码/生产decodePng的p95为47.80/11.61ms；compression=0为73.57/16.18ms，
大小约6.233MB几乎不变，并使12个原本可接纳的近限额图像超限。其余参数也未改善该结果图。
因此保留现有默认编码，不把参数猜测当优化；这是Linux独立编解码诊断，不替代Windows
传输/GUI端到端或500ms完整导出期限验证。固定输入的Blob结果恰有0个保留目标，结果图
与输入逐字节一致；该事实仅说明此合成基准，不代表实际工程内容。

Windows精确`adc5b64`SQLite独立三臂均退出0且代码树干净：各600条连续唯一序号，
完整性检查通过、来源稳定、无错误，清理后连接存活数0。原方式2.973秒/201.83事件每秒，
保活连接0.878秒/683.31，复用写连接0.326秒/1842.05；CPU分别1.906/0.719/0.141秒。
全部保留WAL、synchronous=2、autocheckpoint=1000、page_size=4096；没有GC回调。
journal阶段总计832/280/3.5ms，commit937/317/274ms，连接及WAL生命周期成本明显。
这些合成小事件容量不是实际工作流的因果证明；下一步用原正式事件路径的短程前沿/
终态阶段诊断和保活连接相邻对照，尚未据此改生产持久化所有权或更改历史稳态FAIL。

最终需求/心跳/Qt所有权/编辑/保留验收组合：完整CI为1603 PASS、1 SKIP（404.46秒），
protobuf/Ruff/mypy通过，退出0，无native Qt崩溃；运行前后全部Python来源摘要相同。
此前4项FAIL及历史native失败仍保留，不因该次通过宣称所有长期/现场问题解决。
独立诊断脚本草稿未纳入该测试树，性能/长稳态FAIL和实际工程/交付门槛仍待处理。

新增可选Job诊断（--job-diagnostics）在原终态超时/异常清理之前记录有界前沿、阶段、
线程栈和worker入队/feeder收尾；不丢事件、不取生产锁查询、不改变期限。保活连接只在
显式--sqlite-keeper诊断臂启用，原默认保持不变。诊断回调失败不能替换原操作结果/异常。
26项重点通过；相邻10秒Linux真实spawn基线/保活两臂均完成50/50结果、正式序号1336，
无诊断错误，代码来源稳定、正常清理。SQLite append累计墙钟795/640ms，线程CPU500/391ms；
终态回调2.68/6.37ms，feeder join0.231/0.185ms。保活连接在Runtime关闭后显式关闭。
这些只验证短程诊断可用，不证明Windows900秒失败原因，不改判既有性能或稳态FAIL。

Windows精确8298f48本机完整检查为1601 PASS、1 FAIL、2 SKIP（529.8秒），无Qt崩溃；
失败是A18把全进程原生线程从81降至80当作泄漏。GitHub同SHA Windows另保留启动RPC
2秒期限、任务终态期限及独立消费者元数据到达顺序失败；两次Ubuntu均1603 PASS/1 SKIP，
因此该SHA不能称整体通过。

A18改为原生线程ID/Python Thread对象的子集检查：允许旧背景线程退出，仍拒绝任何新留存
线程，包含“总数下降但混入新线程”的回归。Qt数量、1000次导航/30次窗口、显式
自有线程/文件身份与退出断言保留；可选线程清单失败时明确报错，默认采样输出/计数路径不变。
相关8项通过（20.70秒）。独立消费者测试在原15秒期限内等两个确切ordinal-3快照，
保留像素/解码断言并验证GUI无需求且总资产读取恰为3；真实RPC单项通过（4.96秒）。

新增--asset-split-trace为显式诊断，不改变既有策略臂、96/8/88分母、stub、期限、编码或
认证metadata。有界且仅含身份的逐调用轨迹分离服务器分派/锁/文件/哈希/序列化及客户端
反序列化/哈希/解码；观测错误不能更改真实操作，关联替换或缺阶段均不称COMPLETE。
68项相关测试通过。真实三臂短程烟测（6输入/2预热）为VALID、split COMPLETE，
两个采集臂各12次准确配对读取/168段，全部4个正式结果到达，所有者退休。原失败比较
布尔值保留，性能为NOT_ASSESSED；烟测只早于最后的关联替换判定/测试收紧，原摘要保留。
这些轨迹为嵌套区间，不相加p95，不把RPC剩余时间称纯网络或GIL原因。

Windows精确99a07d2相邻60秒基线/保活诊断均完成：正式序号7934、300/300结果与GUI/native
绘制，无丢弃/读取/诊断/退出错误；终态62.453/62.359秒，正常资源退休。
append墙钟50.859/15.771秒、线程CPU30.797/11.516秒，表明保活减少约69%正式写入成本；
该长度没有形成积压，也尚未跨过10000条内存保留边界。原900秒FAIL仍未解决，
下一步以更长但有界的相邻诊断核查前沿，再验证明确拥有连接的优化。

后续99a07d2 Windows还观察到其他所有者退出使句柄2168降至1608。A18允许进程总句柄
降低，但禁止超出预热上界；自有文件句柄身份/有效性/退休、Qt对象准确数量和原生销毁、
线程身份子集仍严格验证。总句柄下降不能证明所有句柄逐个来源，故不称穷尽泄漏证明。
新增回归验证句柄下降不能掩盖Qt对象增长或自有资源状态变化；最终相关9项通过（21.13秒）。

Windows精确99a07d2的180秒相邻诊断仍均完成：序号23768相同，终态182.562/182.359秒，
append墙钟155.513/46.932秒、线程CPU91.531/34.016秒，全部900结果和所有者正常退休。
10000事件前后SQLite平均分别约6.599→6.514ms、2.001→1.960ms，没有观察到突增；
非SQLite EventStore平均变化很小。该窗口未复现原900秒失败，不宣称证明其全部原因。

基于两组实际成本证据，Runtime现明确拥有一个空闲SQLite连接，使每事件连接关闭不再
反复触发最后连接的WAL收尾。仍为原独立写连接、每事件事务/commit、WAL、
synchronous=2和busy timeout；不复用writer、不丢事件、不改期限或采集分母。
普通独立SqliteStore不隐式持有它；Runtime在其他所有者初始化完成后取得，在Job、
JSONL writer、维护线程退休后归还。未成功关闭不清空所有权或释放Runtime数据锁。
取得失败的连接在配置前即被登记；关闭失败时保留未就绪句柄和原异常原因，重试先回收
旧句柄才另行分配。未宣称穷尽解决原普通每调用连接的所有历史异常路径。

独立复核通过；39项持久化/计数/日志及故障注入检查通过（1.13秒）。最终完整CI为
1651 PASS、1 SKIP（399.18秒），protobuf/Ruff/mypy通过、退出0，无Qt崩溃，
Python来源前后摘要相同。默认Runtime路径（不加诊断保活开关）的10秒真实烟测完成
50/50结果、GUI/native绘制及序号1336，11.898秒终态，资源/诊断所有者退休、来源稳定、
无丢弃/读取/诊断错误。它仍不是Windows长稳态或原性能基准通过；下一步需该版本实测。

另保留aa3cf65 Windows PR的P0“read deadline”失败及后续未关闭reader导致的清理链。
检查发现该故障探针用sleep(0.51)推断严格>0.5秒期限已过；CPython3.10在Windows的
[monotonic实现](https://github.com/python/cpython/blob/3.10/Python/pytime.c)使用GetTickCount64，
不能以另一等待原语假定其粗粒度时钟已经越过边界。真实Assets.read的确定性粗时钟测试
证明恰在0.5秒时仍合法返回下一块。原CI未记录实际时钟读数，所以仍不补称该次根因已完全证明。

仅修改P0故障探针：等其所属时钟严格越过原期限后再检查，并在任何断言/读取异常时关闭
自己拥有的generator。生产500ms期限、读取逻辑及传输均未修改。四项边界/清理回归和
两项真实监督进程网络故障场景共6项通过（6.25秒），Ruff/diff检查通过，独立复核通过。
A18的新增原生线程及空循环前句柄增加仍在Windows作探针归因，不用放宽容差掩盖。

1955d02 GitHub Windows仍有正式终态等待及A18全进程资源变动失败，不能因Ubuntu
1651项通过或实机短程完成而称整体通过。为定位余下CI等待，既有失败诊断现在仅在
失败时记录所属bridge/维护/writer的有界Python调用位置，及空闲SQLite连接所有权状态。
不读取locals/载荷/完整路径、不取Runtime锁，不新增RPC、等待或放宽期限；16KiB
总预算保留。先核对登记Thread对象再取frame，并复查存活，防止已退出线程ID复用
被误标成旧所有者。六项边界/隐私/预算及真实阻塞线程测试通过，独立复核发现的ID复用
问题已补回归。尚待下一次实际失败栈确认SQLite、资产提升或队列阶段，未预先归因。


Windows精确1955d02默认Runtime路径的180秒、900秒离线实测均正常完成（182.390/
902.391秒），不加诊断保活开关；900秒120×160、5Hz全部4500结果/GUI/native绘制
到齐且无丢弃/读取错误，序号118776、所有者退休，峰值commit约2.905GB。这为该有界
长测补上终态证据；原900秒FAIL保留，尚不代表1080p性能或真实项目验收通过。
同期完整Windows CI为1649 PASS、1 FAIL、2 SKIP，唯一A18最终全进程句柄2171→2175。
3301f3f的GitHub push双平台通过，PR Windows仍仅A18最终句柄1869→1873失败；自有
线程/文件及Qt对象退休检查通过，但余下句柄来源仍未知，不放宽容差或宣称无泄漏。

针对5cc3501的Windows PR诊断盲点，P5探针现在等全部当前页绑定图像真实非空且
result key匹配后才验收，不再以metadata到达推断像素已展示；原40秒期限不变。
新增两项实际Qt图像就绪回归，失败时输出各8KiB以内的子进程输出及view.json尾部。
原失败没有该JSON证据，因此不补称原错误根因已证实。双图像失败断言也附上已有Job
原因诊断，断言不变。SQLite有界微探针仍限1秒，验证实际完成1至5次的精确持久序号、
连接关闭和时限标记；不再把“5次必须在1秒内”误当无负载保障的正确性要求。
五项文件变更独立复核通过；相关28项通过（24.28秒）。最终完整cloud CI为1660 PASS、
1 SKIP（406.93秒），proto drift/Ruff/mypy均通过、退出0，Python来源前后摘要完全相同。


最终独立计划复核另外发现两处真实编辑缺陷，已作窄范围修正：未知但合法的自定义输出
类型不再中断整个页面编辑器，而在不可绑定/不可拖动的行显示具体原因，其他输出仍可用；
目录调用路径深度与既有模型32层边界一致。旧HEAD可复现未知类型异常及17–32层遗漏，
四项新增目录回归加原五项共9项通过（1.17秒），没有增加任何运行时类型适配。

编辑器顶部切页、键盘、侧栏和预览返回现经过同一页面身份边界：合法未应用字段先提交
给原页面，非法字段阻止切换并保留；画布、侧栏及命令目标一致，避免显示B却修改A。
只改变编辑器所属renderer子类，独立运行页行为不变。独立复核还发现详情动作先pin后
切页的边界，现于取得pin前验证表单；保留冻结结果时不重建画布，回编辑再应用外观。
真实DisplaySession/DisplayHub回归证明非法输入不改pin/lease/页面/历史，合法输入只改A，
即使新结果到达仍保留原显示结果身份/像素。最终导航/草稿/属性/工作区/真实正常运行及
renderer兼容检查56项通过（11.86秒），Ruff/diff检查通过，独立复核通过。完整最终CI为1681 PASS、1 SKIP（423.35秒），proto drift/Ruff/mypy通过、退出0，
Python来源前后摘要相同。该版本仍需Windows精确复测；A18全进程资源来源仍待归因。


A18后续Windows原生入口归因发现一个退休后仍在WINMM.dll中的非Python线程；仅入口
模块不能说明所有句柄来源。随后两个不导入产品代码的新进程进行QApplication对照：
共同初始化216句柄/23线程，1秒无timer为217/23，16ms QTimer为223/24（61次回调）；
停止并原生删除timer、销毁QApplication后差异仍在。所有查询句柄均成功关闭。
[Qt5.15.2实现](https://github.com/qt/qtbase/blob/v5.15.2/src/corelib/kernel/qeventdispatcher_win.cpp)
确认小于20ms使用timeSetEvent、注销timeKillEvent。这证明相同16ms平台timer的首次
使用成本，不证明所有历史A18残留来源或允许排除未知线程。

A18因此只在测量基线前显式启动同样16ms QTimer，等一次真实timeout（原生2秒看门狗），
再停止/删除两个timer及事件循环并逐个验证native失效。原1000切页、30浮窗、Qt对象、
自有线程/文件、全进程句柄无增长和线程身份子集断言均未改变，没有数值容差/线程豁免。
独立复核通过，Linux相关4项通过（23.92秒），Ruff通过。此修正必须以Windows精确版本
A18与完整CI实际结果判断，不能凭Linux通过标记Windows资源问题已解决。

该平台预热修正后的完整cloud CI：1681 PASS、1 SKIP（424.60秒），proto drift/Ruff/mypy
通过、退出0，Python来源前后摘要相同。Windows精确验证仍待执行，不以此清除旧FAIL。


## 2026-10-01：继续定位残留问题的有界诊断

41cfa4a最终GitHub Ubuntu双组1681/1通过；Windows PR仅A18新原生线程失败，push还包含
真实停止用例终态期限失败。Windows本机A18为周期检查句柄483>467，线程数25不变，
最终版本本机完整CI未跑。仅Qt16ms首次使用已被独立对照归因，余下句柄/线程不能豁免。

重新读取停止失败原日志：停止后约7秒内seq6至15继续推进，最后栈在commit，不能据此
声称一次commit卡住10秒。事件时间来自监督器append入口，不是worker产生时间；
日志缺最终失败观察时钟。新增显式pytest插件仅观察该原用例，保留原10秒期限和所有
SQL、连接、事务、commit/rollback/close调用；没有改变生产持久化或队列策略：

`python -m pytest -q -p scripts.r3_sqlite_stop_diagnostics --sqlite-stop-diagnostics NEW.json tests/runtime/test_runtime_job_lifecycle_integration.py::testRealSpawnConcurrencyStopsAndCleanup`

输出目录须已存在、文件须不存在。记录有界SQL阶段计数、wall/当前线程CPU、活动调用、
慢尾部及淘汰数、排空前沿和所属线程栈；各子快照非原子，不按thread_id单独推断Job。
周期及等待入口/正常返回只存内存，实际失败或清理/最终阶段才落盘，避免给原等待起点
增加额外磁盘排空时间。硬杀可能丢最后内存尾部，明确作为限制。原用例失败不会被探针
改成通过；观测错误或来源变动将诊断标无效。未新增原生VFS/锁/flush追踪，所以commit
耗时仍不能独自证明具体存储原因。Linux无探针原用例通过3.23秒，带最终探针通过2.87秒，
71次commit共1.558ms/max0.039ms，仍未复现Windows延迟；探针capture共8.137ms wall，
最大1.973ms，3次capture落盘均在清理/最终阶段。44项定向检查通过（0.66秒）。

性能先复用Windows已有aa3分段原始文件，而非先增加一轮负载。新离线分析器：

`python scripts/r3_asset_split_analyze.py --input EXISTING_AA3_ROOT --output NEW_DIRECTORY`

纯标准库，不导入Runtime/Qt、不运行检测，不改原始文件；检查输入SHA256、同次call ID、
身份、时钟、阶段数量/包含关系和完整分母后才分解相邻时间边界。缺失/冲突/负间隔均
保留，不相减P95，不把RPC差值称纯网络或GIL耗时。消费者与channel不猜测关联。
未观测的导出完成至结果封闭/订阅轮询阶段仍列缺口。25项纯JSON回归通过（0.09秒）。
上述工具只增加定位证据，不把原性能、A18或停止FAIL改判；Windows下一轮原路径结果待测。

该诊断工具批次最终完整CI：1736 PASS、1 SKIP（405.93秒），proto drift/Ruff/mypy通过、
退出0，Python来源前后摘要完全相同。生产src没有变更；Windows诊断及原问题修复仍待证据。


Windows后续A18类型归因将一次性16句柄定位为Event，发生在页面构建附近且后续循环
不继续增长。官方Qt5.15.2的QMutexPrivate在首次竞争时从静态freelist整批创建16对象，
Windows每对象创建一个Event，归还池不关闭该Event。实际PySide5.15.2.1/Qt5.15.2纯Qt
新进程对照验证：非递归QMutex已锁定后tryLock(0)保持205句柄/21Event；tryLock(1)
首次变为221/37，精确+16Event，随后20次及对象销毁均稳定。此对照不导入产品代码。

因此A18基线前增加且只增加同一有界竞争路径：非递归QMutex.lock→tryLock(1)返回false，
finally unlock并原生销毁、验证失效；不增数值容差、不排除任何线程/句柄，不增加页面
循环次数来预热到通过。原1000/30和最终全部严格断言保留。Linux相关4项通过20.36秒。
这解释一次性+16Event，不等于A18已经通过：本机预触发对照中间循环不再增长，但最终
仍有约+3净句柄（Semaphore+4、Section−2及一个类型未取得），继续保留FAIL并独立归因。
官方源码：[QMutex池](https://github.com/qt/qtbase/blob/v5.15.2/src/corelib/thread/qmutex.cpp#L705)、
[整批分配](https://github.com/qt/qtbase/blob/v5.15.2/src/corelib/tools/qfreelist_p.h#L168)、
[Windows Event](https://github.com/qt/qtbase/blob/v5.15.2/src/corelib/thread/qmutex_win.cpp)。

QMutex基线修正经独立复核；完整cloud CI为1736 PASS、1 SKIP（403.90秒），
proto drift/Ruff/mypy通过、退出0，Python来源前后摘要相同，最终Windows严格检查仍待验证。


后续纯Python Windows夹具对照确认：创建时File+1/Semaphore+6，join/close但仍保留
引用后留下4个Semaphore，删除所有夹具引用后回到原基线。两个Event、Thread._started
Event及已关闭BufferedRandom的锁由Python对象寿命持有；before快照中的Thread强引用
解释了仅清session/owners/hub/window后剩余的最后一个。单独gc不能释放强引用。

A18现先完成全部原native关闭/Qt销毁、线程身份和fstat失败断言，再删除所有测试夹具
引用（包括before，不改变原baseline），weakref额外确认fixture、Thread和buffer即时
销毁；不调用gc.collect，不等待、不重试、不加容差，然后执行原最终严格计数及身份
检查。该11行修正独立复核通过，focused4项通过21.55秒；在独立e5e9ea0 worktree中只含
该修正的完整CI为1736 PASS、1 SKIP（412.12秒），proto drift/Ruff/mypy通过、退出0，
源码摘要前后相同。后续workflow/性能markers未混入这次验证。

另一个PSS未识别句柄经类型查询确定为EtwRegistration；纯Qt可见主窗口下第二窗口
首次勾选也出现一次，随后30次及窗口退休稳定。无产品PSS对照排除采样本身残留。
其provider/注册所有者尚未识别；不按类型豁免、不宣称ETW已销毁。严格总数通过仅是
原有有界验收结果，不能等同逐个全进程资源已归零。Windows正式版本复测仍待执行。

## 2026-10-01：8608283 的精确结果与继续诊断

8608283 本机 Windows 原 A18 通过（26.15秒），原完整 CI 为1735 PASS、2 SKIP
（600.19秒），静态检查通过、源码干净。完整套件独立8GiB容器峰值4.05GiB，系统
可用物理内存最低34.58GiB；性能实验原4GiB边界不变。GitHub 同提交结果混合：PR
Windows 为1736 PASS、1 SKIP；push Windows 为1735 PASS、1 FAIL、1 SKIP，仍是
A18 新原生线程身份断言（IDs 2700、7768、8188），不是已解释的夹具4个Semaphore。
两组Ubuntu均通过。不因本机或PR通过而清除push失败。

诊断对象寿命检查另发现原探针恢复实例方法时存回已绑定方法，会形成实例自身循环。
现按原始 `__dict__` 属性来源恢复：原有自有值逐一还原，继承方法删除临时覆盖；外部
替换不覆盖，恢复失败保留重试记录。Runtime.close探针使用相同路径。禁用循环GC的
弱引用回归覆盖Store、Owner和实际pytest hook；这修复诊断污染，不修改生产SQLite
事务/耐久性。此前带探针的时序仍是观测结果，不能证明测试后对象寿命未被探针影响。

独立 `Windows stop diagnostics (not acceptance)` workflow 保持原pytest顺序及默认CI，
仅显式加载有界停止观察器。原目标失败仍令步骤失败，缺失/无效停止证据也失败；没有
新凭据、上传artifact或新日志目的地。A18原线程身份断言失败后才另启最多5秒helper，
只读其父进程所属最多16个新线程起始模块，输出最多32KiB，原AssertionError原样保留。
模块名不证明创建者或保留原因，线程ID可在快照后复用；无输出不代表通过。实际Windows
原生调用与未复现的停止延迟仍待该环境证据，不以诊断PASS代替无探针验收。

性能工具新增默认关闭的RPC观测点：同一服务peer的匿名令牌、序列化后的下一次既有
事件循环回调、RPC完成回调。回调时钟在诊断锁前采样；弱引用不保留trace，失效或异常
仍排空有界pending项，仅在原loop已关闭后取消观测handle。它们不改变RPC参数、通道
选项或期限。`r3_rpc_marker_control.py` 固定off/on/on/off、原1080p/5Hz、96输入/8预热、
两消费者/两窗口，逐臂核对输入和资产摘要；保留原延迟/龄期失败，不扣除假定探针成本。
相同peer加客户端RPC区间重叠不能证明服务端RPC或HTTP/2流同时活动；完成回调也不是
客户端接收时刻。原45–58ms序列化后区间尚不能归因网络或GIL。

产品完成度也需明确纠正：普通Designer内设计、绑定、同Job分页预览可用，但普通工程
独立只读运行壳/用户弹窗入口、真正实时Job状态尚未打通。当前OperatorView仅接受受限
test delivery host；连接健康状态不等于Job状态。两源各4MiB，双1080p彩色不在当前边界。
§5.3真实项目/冻结交付门槛解释了通用交付仍延期，不能把上述软件缺口全部归因缺样本。
真实项目暂缺、原性能FAIL及这些软件缺口均保留，不能宣称整个R3或现场需求已完成。

上述诊断增量独立复核通过，最终完整cloud CI为1811 PASS、1 SKIP（408.19秒），
proto drift/Ruff/mypy通过、退出0。最终marker真实RPC烟测6输入/2预热通过4.33秒，
原进程树看门狗及owner退休通过、源码稳定、12调用关联完整、4结果成对完整；性能
标记NOT_ASSESSED。首次临时分析调用参数拼写错误未生成汇总，原文件保留；修正调用
后在新目录重做此小烟测，未改被测源码。完整Windows ABBA及诊断workflow仍待执行。

## 1c8d9c：Windows 缺图与下一段诊断

本机无探针完整CI为1810 PASS、2 SKIP（623.85秒），静态通过、源码干净。GitHub PR
两平台均1811/1通过；push Ubuntu通过，但Windows为1810 PASS、1 FAIL、1 SKIP，
独立诊断同样失败，均为A18导航循环中的新native thread身份。失败后只读查询成功：
两个新线程起始于ntdll.dll+0x75bc0，创建约在失败前248ms，查询在失败后约62ms；这
不是创建者或泄漏根因证明。停止目标本轮通过，STOPPING到ABORTED/E_CANCELLED为
641ms，seq5到13；68次commit共2.781秒、最大219ms，未复现原10秒超时。
这里SQL/outer观察器使用monotonic_ns；官方CPython3.10.11 Windows实现为
[GetTickCount64](https://github.com/python/cpython/blob/v3.10.11/Python/pytime.c)，记录存在
粗粒度量化，不能将0ms理解为零成本，也不做精细等待/CPU归因。新credit阶段采用
perf_counter_ns，不改变原产品时钟或期限。

本机RPC marker ABBA四臂全部完成并退休，但仅两臂图像覆盖完整；另两臂190/192读取、
174/176正式读取，明确INVALID/INCOMPLETE/NOT_ASSESSED，不计算探针成本扣除。
缺口同时发生在on和off臂。离线核对：ordinal39/69的图像源为UNAVAILABLE/
BUDGET_EXCEEDED，计数可用、无客户端read_failed；前一图38/68解码完成时已收到更高
序号，因此未应用旧帧，后来的无图状态被正确展示。前帧PNG已写完约140ms后，新图仍
遭采集预算拒绝，不能解释成PNG尚未完成。原始Windows文件保留本机，未上传。

新增默认关闭的export-credit探针只包装现有父进程操作，记录Pipe reply、原callback、
assets.adopt、result finish/retain及成功返回的semaphore.release边界。原self、store、
RLock和semaphore对象不换，不多acquire/release，不改变child消息、SQL、quota或deadline。
真实spawn回归确认child仍取得原生SemLock方法。恢复失败保留重试记录，不依赖循环GC。
callback到adopt包含锁等待和Python前置工作；探针本身也会扰动持锁时间，不能称纯锁
等待或把observer_cost从延迟中相减。新控制入口：

`python scripts/r3_export_credit_control.py --qt-platform windows --output NEW_DIRECTORY`

固定off/on/on/off、96/8、原1080p/5Hz、两消费者与两窗口，RPC passive markers全关。
它保留严格全帧覆盖门槛，少图仍INVALID；已准入export的生命周期记录完整，不等于
输入全覆盖。每臂额外写credit-boundaries.json供原样核对，失败/重复边界为null，负值
不截断；源拒绝单列，不伪造export记录。6输入/2预热真实烟测通过4.31秒，68记录、
6/6准入与AVAILABLE export、无丢记录/错误，source稳定、owner退休；不作为性能验收。

另修正两个原测试文件中7个RuntimeService fixture遗漏close：仅加finally，原断言不变、
关闭异常继续失败。修正前单独两测试通过却留下4条writer/maintenance线程；修正后两
文件8项通过7.65秒，pytest.main返回即无新增活线程，不等待或GC。注入原断言失败的
7条路径均关闭一次、所属线程退休、原异常保留；关闭本身失败也不吞。此已证测试资源
缺陷独立成立，不能据此宣称ntdll线程增长已解决。生产src与A18断言均未修改。

该批最终完整cloud CI为1834 PASS、1 SKIP（411.42秒），proto drift/Ruff/mypy通过、
退出0，全部Python来源前后摘要相同。此前一次执行会话在94%后失联，没有终态或退出码，
保留为INCOMPLETE；确认原session不可用后才恢复这次完整验证，没有把截断日志当通过。
新credit Windows对照仍待执行，原1c8d9c混合CI和ABBA缺图失败保留。

## d986fe1：已定位到adopt持有credit期间，尚未归因其内部慢段

本机d986无探针完整CI为1833 PASS、2 SKIP（643秒），静态通过、源码干净。GitHub
PR两平台和push Windows均1834/1通过；push Ubuntu为1833 PASS、1 FAIL、1 SKIP，
唯一失败是双正常Job图像结果非COMPLETE。原all断言未显示具体源原因，不能猜成超时；
现只补逐Job的原COMPLETE断言与已有jobFailureDetails。云端单独检查1 PASS/1.36秒
仅表示未复现，不覆盖该push失败。本轮无A18新增线程，也不能证明此前ntdll根因已解。

本机四臂credit对照仍INVALID/NOT_ASSESSED：off0、on2、off3各96图，on1仅93图，
缺58/63/69。三源均明确“source image lane is busy; capture never waits”。它们的前帧
57/62/68在拒绝前113.64/116.93/133.20ms已收到reply；adopt持续138.79/129.14/
171.61ms，原release分别在拒绝后26.74/13.92/39.97ms返回。拒绝时adopt尚未结束；
不是reply未到，也不是PNG尚未编码完。尚未直接记录拒绝调用的slot/lane数值，更未
拆分adopt内部，因此不归因磁盘、GIL或探针本身。所有owner退休，原始证据保留本机。

下一诊断版维持原控制命令、96/8、1080p/5Hz、off/on/on/off，生产src不变：

- adopt细分_collect、stat/open、hash创建、stream进入/退出、replace和hexdigest；
  原64KiB read与对应update逐次原样执行，只按每结果固定聚合次数、字节、sum/max。
  read/update首尾跨度包含彼此交错工作，不能当纯read/hash耗时；只比较各原调用的聚合。
- worker只转发原free.acquire(False)一次，记录其bool返回和当时实际配置的Job/result/
  source/slot/lane/capacity/offset/raw_bytes，不另acquire/release/读取semaphore值。
  False证明本次未取得credit，不伪造前驱owner。原生方法和属性恢复保持相同身份。
- producer只在原作业体结束、探针恢复后落盘capture-credit-PID.json，512行/1MiB；
  parent仍有界。原始身份/阶段、细聚合、时间合法性和producer/export lane交叉核验
  独立于summary；空数据、缺细阶段、错Job/lane、倒序或伪造时间均不得标有效。

门控真实Exporter/Collector用例在observer off/on均证明：adopt阻塞期间下一采集被busy
拒绝，原release后再采集成功，先前资产hash/像素保持；未修改原.5秒deadline。最终
6输入/2预热烟测4.37秒通过，parent134行、6/6adopt细阶段完整，producer6个原生尝试
身份/lane相符、无错误或丢行、owner退休和source稳定。独立窄复核通过；这些只验证
诊断工具和受控因果，不替代待执行的Windows细分对照或原性能验收。

此版探针最终云端完整检查为1897 PASS、1 SKIP（416.52秒），proto drift、Ruff、
mypy通过，退出0、全部检查来源前后稳定。后续Windows细分改用独立GitHub标准runner，
先在云端实现与排查，收敛后再集中本机最终验收；现有本机失败证据继续保留。

独立`runtime-credit-diagnostics.yml`在标准Windows runner执行一次上述四臂，原普通CI
不变。入口`r3_hosted_credit_guard.py`先核验当前controller及继承子进程的4GiB聚合
Job内存限制和当前进程BelowNormal优先级；设置失败即INVALID，不启动负载。外层
不设自包含进程的kill-on-close，原各臂90秒/kill-on-close仍负责退休，workflow上限
20分钟。PeakJobMemory不等于“从未触及内存限制”。

`r3_export_credit_report.py`只读取该次受控合成证据，将consumer/UI原始覆盖与计数、
实际采样角色、四臂输入/资产hash、原生credit尝试和adopt阶段交叉核验。完整报告最多
1MiB，分块输出至同一job日志，以字节数/SHA和终标记验证完整性，不上传artifact。
原控制非零或INVALID保持，观察完整不等于性能通过；缺失/伪造原始数据另标INVALID。
新增32项纯数据/假WinAPI用例通过（云端整合3.10秒），尚不等于Windows原生guard验证。
最终整合完整云端CI为1929 PASS、1 SKIP、27个subtest通过（419.94秒），全部静态
检查通过、退出0；Python检查来源和workflow前后摘要一致。Windows原生资源readback
及细分对照仍待该提交的GitHub诊断执行，不能以此覆盖历史性能FAIL。

## 62b715e：远端Windows证据与有界IPC恢复缺口

该头普通CI已经终态：push/PR Ubuntu均1929 PASS、1 SKIP、27 subtest通过；
push Windows为1928 PASS、1 FAIL、1 SKIP，PR Windows为1927 PASS、2 FAIL、
1 SKIP，均27 subtest通过。两Windows都在A18原严格检查新增2个native线程；PR还
出现正常Job终态超时，失败快照bridge位于SQLite commit，但event sequence继续到17、
heartbeat仍在，不能解释为某一次commit全程停滞。原断言和持久化保证没有放宽。
见[push CI](https://github.com/jsdfhasuh/emo_master/actions/runs/36820841252)及
[PR CI](https://github.com/jsdfhasuh/emo_master/actions/runs/36820844973)。

[独立Windows credit诊断](https://github.com/jsdfhasuh/emo_master/actions/runs/36820843603)
保留INVALID/NOT_ASSESSED，报告观察也为INVALID。原生聚合4GiB/BelowNormal设置、
读回和关闭通过，峰值Job commit为924282880字节；四Job完成并退休。完整886086字节
报告按内容SHA与分块终标记重组。两个on臂各96次真实acquire成功、96个export，adopt
全96样本（含预热）的p95约9.1/10.5ms、max11.3/13.0ms；没有复现本机129–172ms
慢段，不能据此归因磁盘、GIL，或覆盖本机失败。

首off臂实际执行96个detect、正式88个全成功，但消费者只观察81个ClosedResult，
为ordinal7、8、18–96；8在终态附近以双源IPC_ERROR出现，detail确为该常量。
其他三个臂都观察96个result，但有GUI漏帧。首臂StartJob reply到观察循环5.525秒，
其他臂约0.26秒；脚本在Job启动后才构造客户端和窗口，此处属于需要区分的冷启动因素，
不能直接认定某个Qt调用或GIL是阻塞源。晚完成图像有的只在下一metadata前保留约
1–2ms，16ms轮询没有取到；另一些在下一metadata到达后才解码，原防陈旧逻辑拒绝应用。

不改生产的云端门控复现已经得到相同关键签名：原16槽mailbox的第16包为8.open，
8.seal遇Full，9–17无scope credit，因此96个成功workflow只调用87次image capture。
排空后返回7个credit，后续恢复，8保留到原terminal fence并得到双源IPC_ERROR。
该复现导出80个PNG，不能冒充解释了远端全部77个export或81个观察结果。公开admission
接受的8scope测量/准备路径还证明全部8个credit可在丢seal后失去后续恢复；正常StartJob
不启用measure，不能把这一全耗尽结论套到正常单scope。即时预算拒绝是原契约允许的；
需要修复的是已接受scope的有限封口/恢复所有权及可选timing在失败后的有界性。

修复将已结束scope移入有限pending seal，保留原sources、terminal、sealedAt和
scopeEndedNs；仅Full允许在后续begin/end非阻塞重试。成功发出后删除，其他异常
按有歧义的发送处理、不重发且沿原诊断传播。parent仍唯一归还credit，无后续边界
时仍由原terminal/force-kill fence兜底。可选timing先全部移除，seal优先，真实timing
丢失沿原有界错误路径计一次，纯seal重试不伪造新拒绝。提前UNKNOWN fence归还共享
credit时，显式本地open+pending<=8上限仍生效；旧实现的8增长到16反例已先验证。
原16槽、图像预算、.5秒期限及晚旧结果保护不变。18个新恢复场景连同相邻回归73项
通过31.10秒，不能据此宣称无损采集；原门控场景仍有9次立即拒绝。

另补4处测试RuntimeService finally close（image_pipeline一处、MVP smoke三处）。
修前4项测试虽通过，pytest返回即仍有4个open/ready idle连接和8条writer/maintenance
线程；修后同4项通过且立即仅MainThread。12条失败路径验证正文/原断言/close异常
继续失败，AST确认15个原断言与66个原调用不变。此为确证测试owner遗漏，仍不等于
A18 native+2的根因证明。

一次新增“pytest返回后owner清点”启动器因缺少spawn主入口保护而递归执行，已强制
停止并标INVALID，不能将其失败标记归产品或称正常owner退休通过。补标准主入口保护
后的单个真实spawn用例通过，返回即仅MainThread；后续完整检查同时记录产品来源与
外部启动器的前后hash，清点只在全部pytest返回后执行，不在用例期间插入探针。
修正后最终完整云端pytest为1947 PASS、1 SKIP、27 subtest通过（424.53秒），
原proto/Ruff/mypy命令全通过、退出0、产品和两个启动器hash前后一致。pytest返回的
即时清点仅MainThread、0个活RuntimeService；没有GC、sleep或诊断补close。该结果
只闭合Python测试owner验证，Windows native线程及性能仍待精确提交的远端检查。
