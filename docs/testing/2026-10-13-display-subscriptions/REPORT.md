# W01 五路订阅修复与 A02 显示契约

日期：2026-10-13。基线：`b27b44ca715eaf904286afe68b04f94cbfde489e`（产品同 `8c13404`）。
本报告为源码修复及 Windows 自动化验证，不是 Debian 人工复验、设备验收、包发布或部署。
GitHub Actions 在本提交推送后按精确 SHA 执行，运行结果另以对应 run/job 链接核验；
本地结果不得替代托管 Windows 结果或 PR 合并提交的结果。

## 为什么之前通过仍有问题

此前五 While 回归验证了 controller/Job/latest result，没有创建五个真实 GUI DisplaySession。
实际产品为五 Job 各建一条长流，旧 aio 把 display=2 的工作槽永久绑定长流，三路被拒绝。
Job RUNNING、Snapshot 偶得 COMPLETE 和22张正确图像，均不能证明 Subscribe 健康。
这是回归覆盖边界缺口，不是几毫秒的阈值问题。

A02 是另一个边界：consumer0 全96已接收/解码，但旧2在新3已接受后未进入live；
两个Qt窗口都接consumer0。全96显示覆盖原FAIL保留；时序支持替代推断，但旧证据
缺决策瞬间 high/generation，不能宣布历史根因已证明。详见 CONTRACT.md。

## 修复位置

| 位置 | 改动 |
|---|---|
| `grpc_server/display_subscriptions.py`、`aio_entry.py` | 订阅槽与工作槽分离；每Job2条、最多32Job；工作线程仍2；FIFO工作调度、有界背压、实际退休才归还、512条身份事件 |
| `presentation/rpc.py` | aio模式在空闲时返回内部poll标记，20ms等待移至事件循环；旧同步等待分支保留 |
| `operator_runtime/controller.py` | 全停先向所有已确认Job发停止，再等待各自原期限，防止5路串行grace累积；未知Start/失败不丢失 |
| `clients/runtime/display_session.py` | 在锁内记录实际live决策、Runtime/Job/generation/high/expiry、结果身份和源owner/SHA/不可用原因；拒绝旧结果倒灌 |
| `scripts/r3_export_credit_report.py` | 兼容旧/新raw记录；新决策包括预热全部保留，身份字典压缩、自由文本摘要；原1MiB上界和原判据不变 |
| `tests/runtime/presentation/test_display_subscriptions.py` | 五真实网络Session、每Job隔离、总Job上界、构建/next/回调/close阻塞和取消解锁 |
| `tests/runtime/presentation/test_live_decisions.py` | 5类决策闸门；双真实网络消费者、96张变化内容完整像素/owner核对、确定性替代 |
| `tests/ui/operator_view/test_five_display_sessions.py` | 五真实While Job/GUI Session，逐tab、快切、多窗共享、重连、单停、重启、全停、运行中关闭、同数据目录重新打开Runtime |
| `.github/workflows/display-session-contract.yml` | 精确push SHA的Windows原生Qt专项，always上传JUnit/图像/96原始记录/订阅退休账本 |

没有扩大捕获lane、图像预算或500ms服务端/资产期限，没有放宽550ms客户端期限，
没有删编号、删原断言或重跑择优。两处原测试的**观测点**从同步Condition等待迁至
实际快照工作/异步空闲；仍断言取消前不归还、超额拒绝、控制隔离及全部退休。

## 实际验证顺序及首失败保留

`evidence.zip` 包含下面各日志、JUnit、新截图及结构化账本；SHA见清单。组之间有重叠，不能相加。

| 记录 | 结果与解释 |
|---|---|
| `first-failure.log` / `baseline.log` | 首命令用了无pytest的默认解释器；之后沙箱临时目录权限失败。均为测试未启动，不归为产品失败 |
| `baseline-product.*` | 原两槽adapter五Session失败，active display=2；最初合成Job fixture缺scopeIds导致monitor警告。保留此警告，不把它当干净的固定SHA实机验收；原dot证据仍是正式基线 |
| `focused1.*` | 开发阶段32 passed，RPC/双端/图像按需读取 |
| `native1.*` | 12 passed / 1 failed：测试误把已有metadata当图像已就绪，实际LOADING；清理时又超时，均保留 |
| `native2.*` | 修正图像就绪检查后8 passed；此时只证明正常全停路径，不冲销直接关闭问题 |
| `close-running.*` | 运行中直接关闭1 failed：前三Job分别原5秒grace后强制退出，串行停止使后继迟收取消；没有扩大25秒窗口等待 |
| `isolated-native.*` | 修改全停派发后16 passed；原生Windows，两场景首次修复验证 |
| `regression.*` | 692 passed / 2 failed，受影响展示/Runtime UI/并行流程/退休扩大回归。两失败为旧aio测试仍等待已移除的worker Condition等待；原日志保留，迁移观测点，不删除安全断言 |
| `callback-before.*` | 审查新增取消依赖故障注入，首次1 failed：回调负责解除同步工作阻塞，却排在其后。修为沿用独立cleanup pool提前执行，仍待双方结束归还 |
| `final-native.*` | 49 passed / 1 failed：新加重开Runtime检查重复创建测试插件目录，WinError183；修为两次启动复用同一已建立测试插件目录，非产品改动 |
| `verified-native.*` | **77 passed / 1 skipped / 19 subtests passed**，原生Windows，88.60s。包含更新后的原通知断言、所有新增场景、回调退休及脱敏报告。skip为本机无symlink创建权限 |
| 静态检查 | Ruff通过；mypy 412源码文件通过；proto drift通过。首次proto工具在受限临时目录失败，提升权限后原脚本通过，未改生成文件 |

共享工作区中途出现其他任务对debugger的改动，后续在独立detached检出验证，仅携入本轮文件。
未混入或覆盖该并行任务、本地工程、旧测试和原验收包。

### 可直接核对的结果

- 五路正常生命周期：7次admitted（5初始+重连+主路重启）对应7次retired，0 rejected。
- 运行中直接关闭：5次admitted对应5次retired，0 rejected。
- 两场景工作并发峰值均2；最终所有RPC active=0、Session线程全停、5个exporter进程均不存活，Runtime真实关闭。
- 新96变化内容：两个真实网络消费者各96张完整像素核对；故意闸门延迟consumer0的2，3先被接受；原始96行全部保留，consumer0仅2为SUPERSEDED、其余APPLIED，consumer1全部APPLIED。
- 这份确定性注入不是原1080p/5Hz ABBA性能复验。原ABBA的全96缺号不重新判PASS；Windows既有捕获诊断工作流仍采用原负载/期限/分母。

## 未关闭和未验证

- 旧A02全96显示覆盖仍FAIL；旧时序根因仍缺历史决策直证。本次完成latest合并契约与未来可观测性，不提供永久逐帧录像/无限审计库。
- 新Debian真人桌面复验由dot执行；本轮Windows为原生Qt脚本/QTest，不冒充人工操作。未录制新桌面视频，交付截图和结构化证据。
- D01–D08本轮人工复验、SQLite/O20、生产设备、系统DPI矩阵、完整产品性能、冻结包均不在本地结论内。
- 32Job/64流为安全容器上界，不是该规模吞吐/内存性能合格证。超过上界仍明确拒绝；控制、资产、执行资源隔离不放宽。
- 不合并PR、不发布、不部署、不连接生产设备。

## 精确 SHA Windows Actions 与诊断报告后续修复

产品修复 SHA：`d3f5c929f8eac1322d82e5b8511ae2986bb00478`。
此节后的报告脚本修复不改变产品源码；前述 SOURCE_SHA256.json / evidence.zip
仍标识原本地验证，不把它们改写成后续脚本的新验证。

- [原生 Windows 专项 38344397274](https://github.com/jsdfhasuh/emo_master/actions/runs/38344397274)，
  job `115122318901`：**17 passed in 41.83s**，成功，源身份为上述 SHA。
- [冻结包只验证、不发布 38344404435](https://github.com/jsdfhasuh/emo_master/actions/runs/38344404435)：
  成功；准备阶段解析 PR 的精确源码 SHA，而非拿合并提交替代。它不等于生产部署或设备验收。
- [捕获诊断首次失败 38344397289](https://github.com/jsdfhasuh/emo_master/actions/runs/38344397289)：
  四组原始摘要依次为 AVAILABLE 96；AVAILABLE 94 / BUDGET_EXCEEDED 2；
  AVAILABLE 87 / BUDGET_EXCEEDED 7 / EXPORT_TIMEOUT 2；AVAILABLE 96。
  原 measurement=INVALID / performance=NOT_ASSESSED，不能判捕获性能通过。
  报告解析又报 `base_trace_file_denominator`，未写 artifact；该托管运行的原始 trace
  没有留存，无法确定当时是缺失还是额外 trace，也不能据此断言进程替换是历史根因。
  已保留可取得的完整失败日志，不以新运行替代。

后续局部修复仅限诊断：trace 数不等于5时，保留原计数/角色失败条件并判 INVALID，
但继续输出所有有界可读 trace 摘要、96编号、capture/parent记录和其余ABBA组。
仍检查文件角色对应，保留20文件/组、64输入文件、256MiB读取及1MiB输出上界。
不可安全解析时也独占写入脱敏拒绝报告；已有首份文件不覆盖。没有改 workload、
measurement判据、分母、配额或期限。它修复证据丢失，不宣称修复额度拒绝。

确定性回归首跑：缺/多trace两子案例和拒绝artifact案例失败；修复后报告及hosted guard
回归 **40 passed / 1 skipped / 35 subtests passed**，20.98s；skip为无symlink创建权限。
Ruff和diff检查通过。`actions-followup-evidence.zip`保存这次首失败、回归及托管日志/产物。
同一产品源码的既有专项通过不得替代后续 HEAD 的精确 CI 状态；最终结果以新 run 链接报告。
