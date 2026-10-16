# SQLite 首败的代码级定位（只读取证）

核查版本：`abc7ae0541e997be0bbe3a4794ad604c239de79a`；仓库 remote 为 `https://github.com/jsdfhasuh/emo_master.git`。本报告 GitHub 链接按已验证 remote、固定 SHA 和本地已读文件行号构造。本次只读取源码、git diff、既有日志/JSON/JUnit；没有执行测试、诊断重跑、产品运行、数据库查询、产品/测试修改或 push。写入只限本取证目录。另附 `sqlite-source-evidence-hashes.json`。

## 1. 可以立即下的结论

已确认 **测试的观察门槛与当前 admission 层级存在语义不匹配**；这是首 FAIL 的最强候选解释，但原首败原因未被唯一确认：测试要求在持锁期间恰好采到一个仍在执行的 native `BEGIN IMMEDIATE` span；当前产品已把等待移到多次短 native 调用之间的 Python sleep。observer 仍忠实测量单次调用，未承诺覆盖整个 admission。因而“没有采到 active”不能推出“writer 从未尝试 BEGIN”。修复方向首先是测试的进入信号，而非延长超时、恢复长 native 阻塞或重放事务。

这是代码能确定的语义不匹配，也是已有数据支持的最强解释；原首次失败未保存阶段快照，不能证明当时精确调度轨迹，不能宣布已经确定性复现或产品事务全面无风险。

原全量保留 **1 failed / 4828 passed / 19 skipped / 29 subtests passed，936.61s，exit 1**。失败在 `tests/test_r3_sqlite_phases.py:493`；第494–503行后续安全断言没有执行。finally 的释放/等待仍执行，之后数据库里的一行不能回填为这些断言已通过。

## 2. 精确调用链及旧假设

1. [testRealWriterContentionIsVisibleAtBeginAndPreservesAllDurableRows:466–509](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/tests/test_r3_sqlite_phases.py#L466-L509)：主线程 blocker 持 `BEGIN IMMEDIATE`；另起 writer 调 `appendJobEvent`；第486–493行维持原2秒/5ms轮询，只检查 `report["active"]`。
2. [SqliteStore.appendJobEvent:245–290](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/context/sqlite_store.py#L245-L290)：连接→`beginImmediate`→MAX(sequence)+1→INSERT→可选retention DELETE→显式commit→返回sequence。
3. [beginImmediate:15–35](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/context/sqlite_transactions.py#L15-L35)：读取原busy_timeout，算一次deadline，native busy_timeout设0；BEGIN busy/locked后在原调用外sleep 1–5ms；只循环BEGIN；finally恢复原timeout。
4. [observer._Connection.execute/_call:233–260](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/scripts/r3_sqlite_phases.py#L233-L260)→[SqlitePhases._invoke:126–164](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/scripts/r3_sqlite_phases.py#L126-L164)：只包被转发的 `connection.execute`，没有包外层helper与sleep。
5. [_begin/_finish:65–117](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/scripts/r3_sqlite_phases.py#L65-L117)：entered和active写入同一lock；返回/异常后failed或returned增加、active移除/恢复也在同一lock。[snapshot:187–206](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/scripts/r3_sqlite_phases.py#L187-L206)用同一lock复制累计和active。因此短调用完成后可留累计证据，active不再存在，这是正常语义。

与父提交比对：旧 `appendJobEvent` 在sequence未指定分支直接native BEGIN；当前改为所有event先进入共享helper。原生等待可能跨许多5ms采样点，改后多数等待发生在observer看不到的sleep里。旧测试隐含“进入admission后会在native调用内足够久”的时序假设；该假设从来不是稳定同步协议。observer不是admission级追踪器，不能把active=[]理解为没有争锁。

累计计数是该store所有被包装调用的合计，不是每Job事务数，不能在繁忙生产场景仅凭thread_id归属某event。本测试probe在initialize和blocker之后安装且仅一个writer，因而累计进入信号在此隔离场景可用。observer的instrumentation失败会disable；应同时检查enabled/errors，不能把禁用后的空active当成功。

## 3. 已有证据：支持与限制

读取 `evidence/targeted/phase-observation/report.json`，未重跑：持锁2秒、5ms采样，共389次；仅1次（约694.907ms）采到active BEGIN。当时entered=221、failed=220、returned=0，说明同锁计数能表达“一个正在调用”。释放前累计entered=failed=631、returned=0，其中629次≤1ms、最大1.791ms，累计调用墙钟约19.997ms。释放后第632次成功，commit entered=returned=1；线程结束、无异常、sequence=1持久化。

该样本并没有复现零次命中，不能估算稳定失败概率，也不能把约20ms/2s直接当独立采样概率。它支持短窗口/调用外等待事实。无原首FAIL trace，无法排除当时初始化、线程调度或其他阶段耗时；没有证据把原因归给特定CPU、磁盘、SQLite版本或永久饥饿。

`original-full-failure-db-readonly.json`已有记录：finally清理后sequence=1、integrity=ok、WAL、synchronous=2。不能把清理后状态当失败瞬间状态。

额外观察：原测试第502行查询的是 **blocker连接** 的 `PRAGMA synchronous`，不是被测writer；第503行WAL检查也不能替代writer所有连接配置证据。[`_configureConnection:568–570`](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/context/sqlite_store.py#L568-L570)明确设置的是busy_timeout=5000及journal_mode=WAL，没有在这里显式设synchronous。因此这条测试本身不能单独证明writer的FULL同步等级；这不是发现当前writer实际被降级。

## 4. 产品事务边界审查

### 4.1 仅重试准入：代码可证，边界有限

helper `try/except`仅环绕BEGIN；只捕获OperationalError且字符串含locked/busy，非busy错误直接上抛。没有包含MAX/INSERT、transform、UPDATE或COMMIT。`_beginWrite`只把准入busy映射成E_VARIABLE_BUSY，不包transaction body。[global_variables:53–59,158–188](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/context/global_variables.py#L158-L188)。字符串分类是兼容性实现，未使用sqlite_errorcode，不能据此声称所有SQLite扩展错误语义已精确覆盖。

### 4.2 COMMIT未知：不重放不等于保证没写入

显式event commit在helper循环外。已有全量JUnit中 [testDurableEventWritesDoNotReplayTheirBodyOrAnUncertainCommit:83–132](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/tests/runtime/test_sqlite_write_admission.py#L83-L132) 的None/before/after三参数全部PASS：先注入一次BEGIN busy，INSERT仍只一次，commit调用一次、close一次；commit前报错无行，原生commit后抛错保留一行。支持不重放及不把错误等同未持久化。

[`EventStore.append:46–77`](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/events/event_store.py#L46-L77)直接调用一次持久化append，返回后才更新内存sequence，本段未见重试。若commit已落盘但返回错误，持久化与内存/调用者认知仍可能暂时不同，这是未知提交结果应保留的边界；本检查不是所有上层调用者/外部客户端的exactly-once证明。

### 4.3 计数原子性：同一writer事务，非跨操作总原子性

`ProjectGlobalVariables._mutate:158–164`先取得BEGIN，`_mutateInTransaction:166–177`在同一connection读取值/revision、比较expectedRevision、transform、验证、UPDATE revision+1。该层没有将读改写拆到两个事务，transform失败不重放。

[`counterOperation:238–270`](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/context/global_variables.py#L238-L270)的legacy写入把 `_counterReady(create=True)` 和 `_mutateInTransaction`合在一个store.connection；缺失counter的definition/value初始化与首增量同一commit。已有counter的只读路径BEGIN读取，缺失后另开writer事务并重新校验。`_mutateInTransaction`自身不验证调用者已处于写事务，是内部调用前提，不可随意复用成无锁入口。

这支持每次计数调用的事务边界，不证明“计数＋事件＋外部设备副作用”共同原子。event append与counter mutation是不同操作/连接。

### 4.4 rollback与close

[`SqliteStore.connection:543–556`](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/context/sqlite_store.py#L543-L556)把body放在native connection context内，finally显式close；observer.__exit__:278–279原样转发native context。body异常经过该退出路径，已有回滚注入支持普通异常时不留下半建counter。不要将文件后面的 `_rollbackQuietly:581`误认为本路径的回滚实现，它不在这些调用链中。

对rollback/close本身失败、进程kill、I/O故障、断电等，既有证据不足。close或context退出异常可能覆盖先前异常；本报告没有触发或证实这些故障。

### 4.5 deadline和restore：明确源码边界，不与首FAIL混因

确定行为：deadline只计算一次，sleep最长为剩余预算，不因每次retry刷新5秒。timeout=0仍进行一次立即BEGIN。循环顶部没有deadline检查，只有失败后查remaining，所以若sleep发生调度超时，下一次BEGIN仍会开始；若该次成功就返回。这是确定控制流，但目前没有硬实时截止的外部契约或相关现场trace，**不能直接宣判产品FAIL**。其模块说明“total admission budget”至少需要澄清是限制重试等待还是禁止deadline后的新尝试；原生执行与线程调度不可抢占，即使加顶部检查也不能保证严格墙钟返回期限。连接创建/初始PRAGMA在helper之前，事务body/commit在helper之后，更不属于整个append的端到端5秒上限。

既有 `testAdmissionCannotExtendTheOriginalTotalBudget:50–59`用fake clock且只由sleep推进时钟，证明累计sleep为10ms、5次BEGIN，不证明真实墙钟上界或oversleep后行为。最小补证应是模拟oversleep/最后尝试成功并记录每次BEGIN起止与deadline，先明确所需契约，不应只调整轮询等待让测试变绿。

恢复路径：finally会尝试恢复原busy_timeout，正常成功/异常路径已有测试；但是设置0在try之前（第21–22行），如果这一设置调用抛错，不进入finally。恢复调用本身若抛错（第35行），可覆盖原BEGIN错误，或使已成功BEGIN表现为helper失败；外围connection context将处理异常退出，不会因此执行后续body。本例没有restore失败证据。可补失败注入验证原异常保留、rollback/close和无body执行；不要为了保留原异常而静默吞掉配置恢复失败并继续写。

## 5. 已有JUnit覆盖清单（读取原全量，未新运行）

以下对应testcase无failure/error/skipped：

- `tests.runtime.test_sqlite_write_admission`全部8项：BEGIN重试/timeout恢复；fake-clock预算；0/10非busy或零预算；变量busy码；event commit None/before/after。
- `tests.runtime.test_variable_write_admission`全部9项：空初始化、真实初始化争锁、transform恰好一次、真实30ms争锁后无事务且timeout恢复、body异常不重放、legacy错误码等。
- `tests.runtime.test_counter_transaction_boundaries`全部6项：[32–94](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/tests/runtime/test_counter_transaction_boundaries.py#L32-L94)，含已有counter只读不请求writer、WAL已提交值、首建/已有increment、插入value失败后definition/value均回滚、typed名称冲突。
- `testConcurrentIncrementsShareStateAndLegacyAdapter`、`testGlobalCounterConcurrentConnectionsDoNotLoseIncrements`、`testGlobalCounterPersistsAcrossConcurrentJobsAndRuntimeRestart`均PASS。

PASS为这些有界测试提供支持，不推导全面并发公平性、任意崩溃/断电耐久性或多步外部副作用exactly-once。既有mixed-sqlite独立720调用证据同理，不能覆盖原FAIL。

## 6. 最小建议修复/补证（均未实施）

1. 在原测试保留2秒/5ms预算，用同一个snapshot的累计 `begin_immediate.entered > 0` 或已失败尝试 `failed > 0`作为“writer已进入准入”信号，同时要求probe enabled、errors为空。不要要求恰好采到active；若要验证“仍在admission”，在此持锁隔离场景结合writer存活、failures为空及未成功BEGIN/未进入body，而不是把累计entered本身当持续活跃。
2. 持锁阶段保留returned=0、commit entered=0，并补sequence_select/event_insert entered=0；释放后保留线程退出/result=[1]/无错误、commit恰好一次、持久行。该机制能观察短尝试，不需把helper伪装成一个长native span。
3. 如果产品诊断必须展示整个“等待写锁”，添加独立命名的admission级阶段/计时，明确包括retry及sleep。不要改变begin_immediate原有native-call含义或将累计尝试数称事务数。最小首败修复无需改observer。
4. 单独补writer连接的同步配置证据；保留blocker读取WAL/行数据用途。不要把blocker synchronous当writer配置。
5. 若另获执行/改动授权，再做固定数量、独立目录的目标测试及完整失败trace，记录入场/锁释放/尝试/提交时间顺序。为deadline oversleep、restore错误、rollback/close错误、未知commit各建明确故障模型；保留所有失败，不重跑到通过。
6. 不因本次定位关闭历史1657 NG→INCOMPLETE：这里缺少那次source/phase关联，也没有证明两者同因。
