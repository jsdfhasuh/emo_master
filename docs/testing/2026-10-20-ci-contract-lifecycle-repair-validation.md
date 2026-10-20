# CI 契约与生命周期修复验证（2026-10-20）

基线为 `704d098aab99496247b84d9f1e5719365c0543dc`；原分支 `agent/runtime-workflow-architecture-v1`。用户扩大本轮范围为排查并修正 CI 失败，包含已有失败。未修改 workflow、跳过策略、原测试 deadline、SQLite FULL durability 或生产平台限制。

## 首轮证据

- [精确 push CI 39344352186](https://github.com/jsdfhasuh/emo_master/actions/runs/39344352186)：Linux6259通过/12失败/11errors/23跳过；Windows6280通过/16失败/5跳过，均42subtests。
- [PR merge CI 39344359733](https://github.com/jsdfhasuh/emo_master/actions/runs/39344359733)：实际checkout `8decf65a802db3bd19a28787ff1dbad404c07ed9`；Linux同上，Windows6278通过/18失败/5跳过。PR合并验证不冒充精确push。
- [Frozen Windows 39344360337](https://github.com/jsdfhasuh/emo_master/actions/runs/39344360337)：显式源checkout704d098，成功；11tests＋2subtests。该有限自测不是完整生产验收。
- 原日志均为attempt1并已保留。Linux23个失败/错误签名与旧精确8a65f4ce hosted baseline一致；Windows精确push的multi_capture10秒等待无已确认旧签名。没有重跑CI取绿。

## 最小修复及契约

1. **接口节点双击误移动**：原生Qt默认双击重开press，模态回调前的viewport resize可合成held-button move，节点x70→75。立即释放grab并消费已处理事件；仍延迟打开模态并保留对象有效性/所属scene检查。保留编辑、取消、拖动及后续点击行为。
2. **部分consumer启动失败的共享快照寿命**：不再提前移除未启动producer的组映射，避免仍活跃consumer的project.json被删除。组映射消失必须晚于持久化、回调和所有进程/队列ownership退场。
3. **退场异常恢复分阶段**：独立review发现第一候选的workspace-only repair会误清除未完成dispatch的receipt。最终修正区分callback与dispatch；未知event写入结果保留incomplete ownership、不重放；已知workspace repair成功后才首次执行未开始的dispatch。首红保留。
4. **资源写准入**：资源journal/transfer的8处隐式事务沿用现有有界BEGIN IMMEDIATE准入，不重放SQL body/fsync/commit。健康状态未变的LOOKUP保持只读；确有变更时，获得准入后重新读取authority和文件健康。
5. **IMV测试平台契约**：测试专用驱动在DeviceManager加载前注入，且仅复制该测试manager的type表；生产Windows-only声明和gate不变。新增生产拒绝及模拟隔离断言，软件模拟不等于实机测试。
6. **TCP端口退出契约**：确定性证明原失败仅有TIME_WAIT、inode0，所有原descriptor/线程/worker已关闭。按生产POSIX reuse/Windows exclusive策略验证真正bind/listen/connect/收字节，仍有live-listener负对照；不修改生产socket策略。
7. **工具栏、资源与SQLite能力断言**：按实际空间验证标签收缩/恢复，增加28px字体窄宽往返；资源恢复断言稳定分类及禁止启动状态；精确能力集合包含新增模拟相机，并保持版本门槛/依赖验证。

8. **全局变量原生接收生命周期**：定向探针首次证明，QObject 父对象删除对话框后，原 Python bound-method finished 连接仍进入原 `@Slot _finished` 并访问已删除的 QComboBox。沿用全局计数器已有的原生 QObject 接收上下文，并在清空 active worker 前核对 sender 身份。普通刷新/写入队列、scope、取消与应用所属 worker 生命周期不变。该确定缺陷与下述原生崩溃的精确因果尚未证明。

9. **连续运行的退场边界**：受控真实进程回归证明，终态事件已经可读，但该 Job 的 EventBridge 尚未退场时，原 follow 流会先结束，使 Designer 过早显示可再次运行。follow 流仍立即交付终态事件，流收尾改为通过既有 Supervisor 等待接口只等待本 Job 的 bridge；不持锁 join，不等待无关 Job，取消或断连可打断观察等待。非 follow 历史查询不等待。已失败的 retirement receipt 不被修复、清除或重放，LoadProject 对仍持资源的 Job 继续拒绝。

10. **工作流包原子性测试的跨秒契约**：`WorkflowStore.toPayload()` 生成保存候选时派生当前 `updatedAt`，不是修改已存储状态。受控相邻秒复现了原完整 payload 比较失败，同时整个 store 状态未变。改为深拷贝全部 store 字段验证导入失败与预览无修改，并在原三种不兼容参数中精确验证跨秒序列化只有候选 `updatedAt` 变化。没有修改产品时间戳语义或忽略未知字段。

## 验证分层

- 本机Python3.10.22、PySide2/Qt5.15.2.1，Linux云环境。
- canvas修复初版109项相关通过；最终类型兼容增量独立50项通过。此前独立98项＋19额外Qt probe对应初版，不能合并为最终全量计数。
- tests-only契约最终独立159项通过，无新skip/xfail；工具栏125%/150%/200%自动化各16项通过。
- 最终Runtime修复独立151项＋5个补充probe通过。早期相关结果及review首红单独保留，不与重叠集合累加为唯一总数。
- 首次完整check停于canvas新表达式的mypy类型错误，未进入pytest；改用实际 `self.scene()` 后mypy496通过，原失败保留。
- 22 文件固定候选（清单 SHA256 `aee9ed80c2b4d1f893ed25fc4d8668ba6edbca8258dcdc3aec35e55f8d5cc233`）完整 check 首次：proto/Ruff/mypy 通过，pytest 约 18% 原生 SIGSEGV（-11），栈在 `theme.fitWindowToScreen → widget.resize`，由全局变量对话框等待事件触发。首日志保留，不能记完整通过。
- 崩溃定位：两份全局变量相关文件 22 项通过；Designer 前序至故障文件的 638 项通过（227.01 秒）。均未改源码，未复现不能作为修复结论。保留完整测试收集上下文、仅执行至故障文件的诊断前序共 1226 项通过（260.78 秒）；外部 trace 记录 424 次 fit 调用全部返回，没有复现。

- 全局变量接收生命周期修复：4 项新增父对象删除回归及既有变量测试 22 项通过；独立相邻检查 30 项通过。相同 40 刷新外部探针修复后首次在第 5 次出现原生 SIGSEGV，独立一次执行通过。两次结果均保留，不能据后一次通过宣布崩溃解决。未改 theme、GC 策略或测试 fixture；两个隔离 legacy/owned window 的后台 GC 对照未证明错误线程销毁。

- 后续隔离对照：old/fixed × profiler on/off 的四个 400 次刷新首轮中，fixed/profiler off 在第 318 次出现 SIGSEGV，其余完成；这排除了 profiler 是必要条件，但不能据单次样本判断修复导致崩溃。安装版本的默认 `destroyed` 信号实测传入零参数，未支持临时 QObject 参数包装假设。移除构造补丁、销毁观察器和 profiler 的 bare old/fixed 各 400 次首轮均完成，仅说明这些简化流程没有复现。所有首失败仍保留。

- 原生栈取证边界：官方 Debian 调试依赖仅下载至独立云端目录；环境拒绝 ptrace，且无法读取 `core_pattern` 以确认 core 的落点和处理方式。因此未改变内核、安全设置或 core 软限，未生成或上传内存转储。该限制使上述 SIGSEGV 的精确原生归因仍未完成。

- 为检查先前中断后尚未执行的范围，24 文件冻结候选（清单 `778d62db15db13973d54c41c307ddea6cb3e88c2dc79cf61b01882e642ad7570`）执行剩余套件：5084 通过、23 既有跳过、42 subtests 通过、1226 前序明确 deselected，1218.03 秒，exit 0。执行前后源码指纹全部一致。它是分段检查，不拼成一次完整 CI 通过。

- 24 文件候选不加载诊断插件的完整 `scripts/ci_check.py` 首次执行在环境恢复后仅留下约 52% 的日志，没有末尾 summary 或退出码；记为环境中断，不能记作通过。

- 恢复环境后，逐一核对 24 个文件仍与冻结清单相同，另用独立临时目录执行完整 `scripts/ci_check.py`。proto、Ruff、mypy 通过；pytest 终态为 6308 通过、2 失败、23 既有跳过、42 subtests 通过，1586.29 秒，exit 1。本次没有出现 SIGSEGV，不能因此宣称先前原生崩溃根因已解决。源码执行前后指纹完全相同。

- 该轮两处首红：图像查看器的连续两次真实运行，首 Job 已 COMPLETED、UI 已解除 active 后，第二次同步仍被未退场 Job 所有权保护拦截；工作流包拒绝不兼容设备映射后，完整 payload 比较在 project 字段出现差异。原始日志和临时工程保留，随后定向修正与验证另行记录，不通过重跑旧候选取绿。

- 最终集成静态独立审查已核对全部 24 文件与跨模块调用链，未发现新的具体缺陷；候选 Python 3.10 parse/compile、Ruff、diff-check 通过，31 个生产 gate、CI 配置与支持文件与基线一致。该审查未执行 Qt 或大套件，不覆盖上述新增首红，也不等于完整 CI 放行。

- 连续运行增量的受控原代码首次反例为 1 failed；修复后最终相关检查 165 passed，26.31 秒，覆盖原生 Qt 两次运行、真实进程退场屏障、取消、非 follow、无关 Job、异常 receipt、supervisor/finalization/dispatch/gRPC/RuntimeWorker。Ruff、mypy、diff-check 通过，三文件执行前后指纹相同。

- 工作流包增量：21 项状态测试通过；相关选择 60 通过、9 项 UI 明确未选取。独立复核另行执行 21 项纯状态测试通过、2 项 GUI 未执行，5 项实际 mutation 反例均被完整状态断言捕获，包含 stored updatedAt、未知字段与嵌套布局。两组结果不累加为唯一测试总数。

## 真实桌面

真实Designer＋embedded Runtime，dot云端DISPLAY=:0，1364×1024，100%缩放。实际通过拖动、输入/输出接口双击、取消、不变确认、编辑确认、撤销、保存和480/640/1200窗口往返；UI保存的JSON证实位置未变。10张截图及UI工程已保留。该GUI进程加载于最终mypy类型兼容增量之前，因此是对应canvas行为的人工证据，不冒充最终所有源码的桌面验收。

本轮工具无法把窗口关闭快捷键交给Linux窗口管理器，正常UI关闭标为NOT_RUN；验证自有PID且无Job后做SIGTERM清理，不算UI关闭通过。未连接真实相机/PLC，无生产硬件或Windows桌面人工验证。

## 尚须精确Windows证据

Windows multi_capture/300joins原等待失败仍需新精确SHA的普通CI确认。Linux profile证明事件逐条FULL提交数量大，但不足以证明Windows原始时序根因；本轮未引入批量事务、削弱durability、扩大deadline或把单次不再复现说成根因解决。

本提交的源码清单和本机验证索引随相邻sha256文件保存；完整首失败日志、独立review、截图与临时工程不进入Git仓库。

## 最终候选整体复验（2026-10-20 03:43 UTC）

- 独立审查首红指出 follow 观察者取消后仍可能无界等候 Supervisor 的退休回调持锁。最终只对带 cancellation 的快照取锁采用可取消、有 deadline 的短间隔等待；既有无 cancellation 调用保留原语义。没有停止 Job、释放其资源、重放退休效果或移动回调。首红日志保留。
- 恢复后使用独立云端 checkout，从相同 704d098 基线逐文件复制全部候选改动。27 个源码/测试文件加本文共 28 项在完整执行前后 SHA-256 一致。外置独立取消锁、旧 API 与真实 loopback gRPC 探针 3 passed；其首次收集因外置脚本 PYTHONPATH 不含测试包而失败，修正运行路径后执行，不作为产品失败或删除证据。
- 最终候选完整 `scripts/ci_check.py`：proto-drift、Ruff、mypy（496 source files）全部通过；pytest **6317 passed、23 skipped、42 subtests passed，1595.86 秒，exit 0**。没有新 skip/xfail，没有修改 workflow、CI 脚本、deadline 或 durability。本次是该最终候选第一次完整执行，不是把分段结果累加，也不是重跑同一失败候选取绿。
- 本次未出现原先偶发原生 SIGSEGV，但原生根因尚未得到精确归因，不能把本次通过表述为已证明该崩溃永远消除。Windows 与冻结程序须看推送后精确源码 SHA 的远端结果；本机 Linux 通过不替代它们或真实设备验收。
