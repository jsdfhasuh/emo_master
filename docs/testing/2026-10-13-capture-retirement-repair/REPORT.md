# 捕获失败后的原始额度归还与 Windows 工作区回收维修

日期：2026-10-13。基底：`417f8782117ac10a0c3d714f78e4d3021c658bfd`。本报告冻结于提交前；提交后的 exact-SHA Actions 结果由交付回复和对应 run 给出。这里的待验证不是永久状态，也不是已经通过的承诺。

## 结论

本轮有产品修改，不再只是诊断：修复两条已经确定性复现的资源回收缺陷，同时补充真实 lane busy 的关联证据。没有增大捕获配额、raw slab、队列、worker 数或原500ms期限；没有删除预热编号、改原 ABBA FAIL 或将不可用源冒充 AVAILABLE。

但不能宣称所有历史捕获失败已根治。旧合并版本 `b003905d2945a05d621cb8c2bb964be798316d56`、run38356368865/Linux115160103834 的 ordinal3 真实 `BUDGET_EXCEEDED` 缺少当时持有者记录。本次证明并修复的是一条能导致无谓占用的实际路径，不伪装恢复旧现场因果。

## 1 修复了什么

### 1.1 原始 lane 归还不应等待失败回调或新进程启动

旧代码在私有快照前失败时，尽管 `_reap` 已确认编码进程退出，仍等 callback 和 replacement `_spawn` 后才归还 raw credit。未派发的排队超时也等 callback 后归还。这些操作继续拥有 staging/Job slab，但已经不再读取该次 raw；不必要的占用会拒绝后继真实 capture。

`presentation/exporter.py` 现在只依据三种证据归还：

| 证据 | 新归还边界 | 安全要求 |
| --- | --- | --- |
| 有效、同taskId、字节数匹配的 private snapshot | 保持原私有快照边界 | 不等编码或资产收养 |
| 旧编码进程已实际退出并完成 `_reap` | 在 callback/restart 前归还 | 请求取消不算退出；reap失败整slab隔离 |
| 从未向编码器派发的排队超时 | 在 callback 前归还 | 原超时仍为EXPORT_TIMEOUT |

`laneReleaseAttempted` 防止归还结果未知后重试。晚到/重复收尾不能释放后继 producer 的 reservation。active/busy 和 staging/slab assignment 保留到原收尾；raw可用不表示整个Job可释放。

### 1.2 Windows 长路径预览文件可写但不可回收

扩大回归的 `testActualFailedJobKeepsEarlierCompletedNodeAndNoFailedNodePixels` 留下真实工作区。只读检查发现成功写入的资产路径长269/270字符。private preview writer 使用 `_ioPath`，而普通 Runtime 的退出/残留清理使用未经转换的 `shutil.rmtree`，删除失败又沿原语义被延后。

`grpc_server/service.py` 的 `_removeOwnedWorkspace` 和 `_cleanupStaleWorkspaces` 改用同一 `_ioPath`。没有改变 Job 所有权准入、终态或异常处理。两个 >300字符回归验证退出和残留清理都删除目标，同时不删除活跃Job；原实际失败Job用例随后通过。

### 1.3 后续失败能够关联具体捕获调用

- `collector.py`：固定lane数的最近一次producer acquisition，带resultKey/invocation/ordinal/acquiredNs；busy/quarantine观察写入原sourceObservations，不改严格wire reason枚举或结果模型。
- `exporter.py`：最多两条pending raw描述符、active阶段、归还证据和既有64条退休环。失败快照只带当前相关Job及最近4条退休记录。
- `presentation/service.py`：失败闭合附上只读、非阻塞parent快照。不会尝试取得原始额度、调用RPC、读图或主动清理；锁忙/诊断失败标UNKNOWN，不破坏原失败。
- 最后一次producer取得不是稍后仍持有的证明；各观察时间不同，明确为非原子快照。按调用和lane关联，不能统一归成TimeoutError。

## 2 首次失败与回归账本

测试均使用干净归档417f878加具名候选文件，未注入当前工作区的operator_debug、连线、PLC及迁移等并行修改。

| 运行 | 结果 | 含义 |
| --- | --- | --- |
| 修复前capture归还门控 | **7 failed** | 6个纯受控callback/restart场景及1个真实spawn/reap场景，旧实现稳定不能及时取到额度；不是原历史CI的重放 |
| 第一版修复定向回归 | **45 passed** | 包含上述7项及已有归还/生命周期测试；后续扩展不与此相加 |
| sandbox扩大回归 | **77 passed / 10 failed** | 9项临时目录拒绝访问；另1项正常编码期间后继结果失败，原因未证实。保留原日志，没有当作全部环境问题 |
| 第一次Windows原生扩大回归 | **243 passed / 2 failed**，360.41s | 五路/全96/NG等通过；1项adoption期间后继导出无结果、1项失败Job目录未删。后者随后定位长路径并修复；前者原因仍未证实 |
| 长路径专用修复前回归 | **2 failed** | 退出和残留清理稳定留下通过extended path创建的 >300字符文件 |
| 最终Windows原生专项及两个原入口 | **75 passed**，92.05s | 71项Windows专项+两个长路径测试+NG同调用图像计数+原失败Job清理。包含14个新增capture测试；这些范围重叠，不能叠加报全量 |
| Ruff / mypy / protobuf drift | **PASS / 412源文件PASS / PASS** | 干净隔离候选；不是当前dirty工作区全量验收 |

最终75项覆盖：失败后的真实后继collector图像/PNG逐像素、未知reap/release的隔离、重复收尾、500ms原期限、原始容量拒绝、编码/收养期间不覆盖旧像素、source与wire失败保真、双消费者全96变化图像含预热、五真实DisplaySession生命周期/运行中关闭、NG图像计数和资源回收。Qt为native automated，不是人工桌面或生产设备验收。

**没有反复重跑整套扩大回归求绿。** 扩大首次失败保留；长路径因果由两个修复前确定性测试和相应产品修改建立。最终不同范围的通过不能反向证明两次间歇导出失败的根因已修复。对原断言仅增加exportDiagnostic失败上下文，没有降低成功条件。

## 3 精确提交的远端验证

- 提交前核对PR#1仍OPEN/DRAFT，远端head=417f878；最终分支SHA由正常push核对。
- Windows/Linux exact push CI：本报告冻结时待运行。
- Windows native workflow：保留原19项，增加失败归还14项、lane lifetime9项、export predicates29项，预期71项。不能借旧19项结果验收新候选。
- 原ABBA捕获诊断不重新派发求绿，旧5410737 FAIL/INVALID/NOT_ASSESSED保留。
- PR merge CI与exact push SHA分开；no-release包验证不等于发布/安装/部署。

## 4 仍未关闭与未运行

1. 历史b003905真实source lane busy的精确持有者因果仍OPEN；本次修复不代表任意输入速度和任意主机负载下全帧可靠归档。
2. 本地扩大回归两次后继导出缺失的历史精确原因NOT_PROVEN；已补失败上下文，不靠最终一次通过销项。
3. 旧Windows Job/启动/自检失败、旧GUI60/94/38和Debian关闭后ownership采样缺口维持独立账本。
4. 有限容量、非阻塞采集遇到真实占用/隔离/超时仍诚实INCOMPLETE，不借旧结果补新调用，也不丢弃缺号。
5. 本轮无新的完整Debian人工验收、生产设备、长期稳定性或性能结论；不扩为D01-D08/SQLite全套。
6. 未合并、发布、部署、force push或覆盖并行未提交修改。

## 5 证据

同目录 `EVIDENCE-INDEX.json` 和 `capture-retirement-evidence.zip` 包含旧CI首次失败、两组修复前失败、所有本地首次失败和最终回归、候选文件清单及native结构化证据。包内 `SHA256SUMS.txt` 校验每项；zip摘要在索引中。源图原始像素不进入诊断元数据。提交后Actions日志/产物由对应run取得，不假装已存在于提交前ZIP。
