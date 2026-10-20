# Windows 捕获诊断的启动就绪修复

基线：`01d54e0dde6ccdaab664fb7683d1097c72dbe22c`。本次只改诊断脚本和回归，
保留基线中“有界私有副本确认后归还共享原图额度”的产品实现。

## 已确认的失败与修复范围

精确基线的 [Windows 诊断 38258234312](https://github.com/jsdfhasuh/emo_master/actions/runs/38258234312)
失败。首组 first detect 为 232533025600ns，observation start 为 234294477200ns，
相差约 1.76 秒。旧脚本先 StartJob，随后才创建消费者、窗口并处理 Qt 事件；
它无法保证观察到全部启动输入，还将原生窗口初始化负载叠加到已运行的检测上。

该组源图实际观察到 94 张 AVAILABLE，ordinal 2 为 BUDGET_EXCEEDED，ordinal 1
没有源观测；不能用 completed=95 推断 ordinal 1 的终态。正式窗口绘制仅 74/88。
后三组均源图 96/96、两个消费者解码/live 88/88、两个窗口绘制 88/88。
原始公共 JSON、首次 job 日志及独立审计原样保留在本次证据包中。
同 SHA 的常规 push CI、PR CI、Windows 冻结包检查均已成功，不能混同为编译失败。

新诊断版本为 `export_credit_lifetime_abba_v3`，启动协议为
`subscribers-and-qt-before-input-v1`：

- 仅 credit-control 的 none_qt 子进程启用 `--startup-barrier`；其他原有测量默认不变。
- 在测试专用 Pace 首次建立原始调度时钟之前等待；真实 Job 和心跳已经运行。
  Gate 使用 spawn 可传递的事件及两个时间戳，不传递 Qt 对象或父进程线程锁。
- 两个实际服务端订阅已进入、两个客户端身份/连接/兼容性正确且尚无结果或错误、
  两个可见窗口已发生原生 paint，才放行第一张。等待固定最多 10 秒并支持取消。
  启动等待不计入或重置任何图像导出/读取期限。
- 原来 seed、1080p 随机图、5Hz 调度、96 输入/8 warmup、off/on/on/off、两个消费者、
  两个窗口、原始分母与完整性断言全部保留。没有预跑输入、清空记录或补帧。
- 报告独立核对 Job 身份、就绪时间顺序、第一帧真实执行时间和 worker 原始 gate span。
  `startup_complete=true` 本身不能替代证据。缺少/篡改证据仍失败。

新版本控制的是“消费者与窗口就绪后开始输入”，不能据此改判历史 v2 冷启动通过，
也不能据此声称产品冷启动行为或持续调度性能已经修复。第一张空窗口的 paint 仅证明
启动就绪；每张结果图的 Qt 绘制仍由原来的独立记录验证。

服务器导出、资产 RPC 仍为 500ms；客户端接收/解码仍为 550ms。捕获容量、编码预留、
资源回收、report 1MiB 上限及 watchdog 都没有扩大。没有修改生产设备或依赖。

## 验证及首次结果

定向最终回归：142 PASS / 1 SKIP / 33 subtests，18.36 秒。覆盖首次 Pace 等待、
取消和超时不执行算子、重复调用不重复等待、包装恢复、实际 Qt paint、订阅去重与
错误身份、未就绪客户端/窗口拒绝放行、证据缺失/篡改不继承成功结论。
跳过项为 Windows 未授权 symlink 创建；不是功能通过。
Ruff（src、tests 和修改脚本）通过，git diff --check 通过。

保留测试开发首次失败：第一组为 141 PASS / 1 SKIP / 1 ERROR / 32 subtests，
因新增测试误用仓库未安装的 qtbot fixture；改用已有 retainedQtApplication。
第二组为 138 PASS / 1 SKIP / 4 FAIL / 32 subtests，因新增原始 gate span 的合成
elapsed_ms 未匹配时间戳；修正测试数据，保留生产报告的时钟一致性断言。
短流程第一次命令遗漏 child 所需输出目录，尚未启动工作负载即失败；原日志保留。
补齐目录后真实 Windows Qt/spawn 短流程 4 次源图、双消费、双窗口均完整。

独立执行一次完整 Windows 原生 Qt v3 ABBA，没有重跑直到变绿：

| 组 | 源图 AVAILABLE | 两消费者正式解码/live | 两窗口正式提交/绘制 | 启动与回收 |
|---|---:|---|---|---|
| 0 off | 96/96 | 各 88/88 | 各 81/81（分母 88） | 完整 |
| 1 on | 96/96 | 各 88/88 | 各 88/88 | 完整 |
| 2 on | 96/96 | 各 88/88 | 各 87/86（分母 88） | 完整 |
| 3 off | 96/96 | 各 88/88 | 各 88/88 | 完整 |

四组捕获无拒绝、导出 completed=96 / timed_out=0 / failed=0；两个 on 组各 96 次
snapshot→额度释放→最终回复→callback 顺序成立。两个消费者各记录全部 96 个输入，
正式 88 个全部解码并应用。四组就绪顺序、原始诊断及 owner retirement 核对通过。
arm 0 后段发生明显调度延迟，最大约 923ms；arm 2 最大调度延迟约 15.6ms，仍有窗口
覆盖缺口。不能把所有绘制缺口都归为同一个原因。

整体保持 `INVALID / NOT_ASSESSED`，没有把图像捕获完整写成桌面全通过。
这次本地结果只证明捕获/消费与启动控制，并未解决持续 Qt 逐帧绘制的所有缺口。
完整原始 JSON、各次失败日志、源码指纹与独立审计见 evidence.zip / VALIDATION.json。
本地 source identity 是基线加本次修改；精确提交的 hosted 结果在推送后单独核验。

## 未关闭与验收边界

本机 Qt 持续负载下逐帧绘制缺口继续保留；历史 NG→INCOMPLETE、SQLite 历史问题、
P0 相对性能及人工桌面验收不由这项脚本修复关闭。脚本化 native Qt、单元故障注入、
dot 人工桌面操作必须分别记账。Windows Actions 由 Codex 跟进精确 SHA，
不再要求 dot 代为调试 CI。正常推送原分支，不合并、发布或部署。
