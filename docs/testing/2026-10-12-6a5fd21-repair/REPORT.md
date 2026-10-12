# 6a5fd21 复验后的首结果诊断与 P0 验收判定改进

日期：2026-10-12。基线：`6a5fd216180ebdec3159a7e9808147d953e32f69`。

## 结论与范围

**本轮完成可验证的诊断增强和验收脚本判定修正，不宣称 Windows 首展示超时或 P0 性能根因已经修复。** 用户原目录的未提交 UI/导航/YOLO 等改动未修改、未纳入提交；所有产品修改在独立修复 worktree 中完成。

- 本机原样首结果用例：2 PASS。没有重现远端 6a5fd21 首失败，不能因此关闭它。
- 新增和相邻专项最终：83 PASS。
- 冻结全量：**4936 PASS / 11 SKIP / 1 warning；28 passing subtests 另列；1456.86 秒**。
- Proto、Ruff、mypy 均 exit 0；mypy 403 个源文件。
- 验证前后产品、测试、脚本、原型 Python 文件指纹一致，`final-source-changed.json=[]`。
- 原完整 P0 配置两次独立记账：本机基线 15 correctness PASS、2 performance PASS / 13 FAIL；本轮严格判定运行 15 correctness PASS、0 performance PASS / 15 FAIL，**exit 1**。不能据此发布。
- 没有改变 SQLite timeout/事务、原 10 秒首结果 deadline、500ms export/read/seal 预算、5% 性能阈值、负载或窗口配置；没有改动 ROI/PNG 编码、协议或资源归还策略。

## 改动位置

### 1. 独立于正式事件和展示 mailbox 的首结果阶段证据

- `src/emo_master/apps/runtime/presentation/phase_trace.py`：可选共享区与父进程记录器、非阻塞失败快照。
- `src/emo_master/apps/runtime/presentation/collector.py`：scope begin/end、source observation、open/image/seal 发送前后及原始异常类别。
- `src/emo_master/apps/runtime/presentation/service.py`：reader 收到/获取锁、export submit/callback/adopt、seal 收到、等待 seal/缺哪些 image source、close 前后/校验失败。
- `tests/runtime/presentation/test_normal_capture.py`：原 graceful/force 首结果用例启用观察器；失败时输出 `FIRST_CAPTURE_TIMEOUT`，保留原断言、10 秒期限和原 jobFailureDetails。

默认关闭，只有 `EMO_PRESENTATION_TRACE=1` 才分配共享诊断区。每 Job 每侧最多 64 条，每条 2048 bytes，保存首批而非覆盖首失败。共享侧约 128KiB 加发布长度表，父侧编码记录最多约 128KiB（不含 Python 对象开销）。只追踪各 scope 的首 ordinal；不记录图像、参数或标量 payload。超出预算/观察器故障明确降级，不能导致 Job 接纳失败。测试还覆盖诊断分配失败、记录失败、锁不可用、真实 spawn 可见性及未发布的部分记录。

身份包含 runtime/job/scope/invocation (= workflowRunId)/resultKey/ordinal，相关阶段另有 sourceId/nodeRunId。记录 PID/TID、单调时钟、顺序号。父侧 finish 相同等待原因去重，避免 monitor 轮询淹没最初记录。迟到回调仍可以缺少已经退休的附加字段，不因诊断而再次释放或抛错。

状态快照是有界、非原子的样本，不是全局事务：扫描最多 16 个 open 和 32 个 latest；没有观察到不能证明不存在。`assertionObservedNs` 与后来状态快照时刻分开。truncated、unknown、trace_disabled 或锁不可用不得解释为业务 PASS/FAIL。某次 image AVAILABLE 与更早 results=[] 不构成同一时刻的状态矛盾。

### 2. P0 脚本不再混淆执行完成与判定通过

- `scripts/p0_validate.py`：显式打印 execution/correctness/performance；记录 `exit_code` 和 `require_performance`。
- 正确性非 PASS 时返回非零，即使监督脚本正常完成。
- 新增 `--require-performance`：原 benchmark performance 为 FAIL/NOT_RUN 时 exit 1；未选择 benchmark 时拒绝该选项。
- 默认报告模式仍支持收集完整性能失败数据后 exit 0，但明确打印 performance=FAIL；严格验收使用新选项。
- 所有原始窗口、stderr、版本/源码身份保留。`p0_exit=NOT_EVALUATED` 不变，不把软件脚本当成 Qt/设备/发布总验收。
- `tests/p0/test_validation_gate.py` 覆盖上述区分，断言仍为 96 测量 / 8 预热 / 3 轮，监督时限 432 秒，不改变单任务预算。

## 性能调查：未采纳未经证实的优化

本轮没有把产品本机展示路径和原型双网络 P0 路径混为一谈。

本机基线中 original 和 new-no-presentation 都调用同一 disabled 路径（enabled=False、viewers=0）；后者相对前者三轮差异约 +125.79%、+88.83%、+99.44%。这是控制条件本身明显不稳定的证据，不能据此认定新路径产生这些开销。系统已有用户 Designer 进程，本轮没有擅自终止它，也没有与本任务其他 pytest 套件并发运行 P0。

独立核算全部原始 rows 的 p95、相对同轮 baseline 的比值、吞吐/调度/模型延迟/live age 门槛，结果与原 verdict 一致：

| 测量 | 正确性 | 性能 | 各门槛失败窗口数（可重叠，不能相加） |
|---|---|---|---|
| 基线 | 15 PASS | 2 PASS / 13 FAIL | execution 回归 12；吞吐 7；调度迟到 13；model 0；live age 6 |
| 严格判定运行 | 15 PASS | 0 PASS / 15 FAIL | execution 回归 10；吞吐 5；调度迟到 15；model 0；live age 6 |

两次带捕获窗口均核对 cleanup used=0 / owners=0 / jobs={}。这些通过项不抵消性能 FAIL。第二次不是“重跑变绿”，是新严格判定开关的完整真实运行验证；它仍失败且返回 1。两次分别保留，不能声称前后性能改善。

32 测量 + 8 预热的单独阶段 profiling 是诊断，不是验收：本次 AsyncExports.submit wall p95 约 4.81ms、Assets.adopt 8.66ms、Pipeline.publish 0.065ms、legacy PreviewSnapshotWriter.capture 80.65ms。它们有嵌套、重叠和不同样本数，不能相加或替代完整 P0 指标。Windows thread CPU 采样粒度有限，不能把 0ns 解读为没有 CPU 成本。

离线 1080p 无损 PNG 候选测量：随机图默认编码 median 35.48ms，level 0 为 56.67ms，显式 RLE/level 1 为 82.06ms；所有候选解码像素一致，但候选未更快且 level 0 的平坦图体积显著增大。**未采用这些参数，没有修改 exporter 来赌性能。** 证据保留在 codec-probe.json 与 pipeline-profile.json。

## 首次结果和开发过程保留

- dot 原 Windows exact push 首失败日志及结构化诊断保留在 evidence.zip 的 dot-original/。StopJob 当时尚未执行；SQLite commit 栈不是锁持有者或耗时根因证明。
- 新诊断测试在实现前因新模块不存在 collection error；这是能力缺失，不冒充历史 CI 的功能复现。
- 实现初稿专项 5 FAIL / 45 PASS：4 个为迟到回调只有 key、诊断却读取必填 jobId/sourceId；1 个为新增测试错误访问 fixture。修正后 52 PASS，后续扩展最终 83 PASS。第一份失败日志未删除。
- P0 新门禁测试实现前 5 FAIL / 1 PASS；实现后通过，未删断言。

## 全量 warning 与验收边界

全量有 1 条 `PytestUnhandledThreadExceptionWarning`，定位于既有 `tests/test_r3_sqlite_event_probe.py::testDiagnosticRefusesExistingOutputWithoutChangingIt`：本轮驱动设置了 PYTHONIOENCODING=utf-8，但该测试 subprocess 的 text=True 使用本机默认 GBK 解码，拒绝已有输出时的 stderr 引发 UnicodeDecodeError。返回码和文件保留断言通过，不能把这一 warning 隐去。后续在统一 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` 下单独核验该测试文件，启用该线程警告为 error，**6 PASS，9.44 秒**，见 utf8-probe.log。未修改产品或测试源码来压制 warning，原全量记录仍为 1 warning。

本轮是 Windows 软件自动化与故障注入，不是 dot 的 Debian 真实桌面复验；没有重做实体 Windows CUA、DPI、安装/卸载、真实设备、完整空白建图，也没有更新用户已启动的 Designer 或安装包。全量中的 11 SKIP 保持原始记录，专项和全量计数不可相加。

## 未关闭项与下一步

1. Windows 6a5fd21 首展示超时根因仍 OPEN。下一次精确提交首失败要用新增阶段证据定位，不能凭本机通过或不同 checkout CI 成功关闭。
2. P0 性能仍 FAIL。需要可控主机的原完整配置与进一步可归因 profiling；不能增大 deadline、移除 legacy preview、降低图像质量或放宽门槛。
3. 历史本机 NG→INCOMPLETE、网络 ordinal20、Windows While busy 仍 OPEN；本轮未宣称修复。
4. ROI 边界数学契约、此前 NOT_RUN 的平台/设备/安装与真实桌面覆盖仍按 dot 报告保留。

下一轮 dot 提示见同目录 DOT_HANDOFF.md；本轮未合并、发布、部署或连接生产设备。

## 证据

`evidence.zip` 保存初始/最终日志、JUnit、完整 P0 原始 JSON、独立核算、源码前后指纹、门禁反例、编码/阶段候选调查及原 Windows 首失败。`EVIDENCE_SHA256.txt` 是归档校验；归档内 MANIFEST.json 提供逐文件哈希。
