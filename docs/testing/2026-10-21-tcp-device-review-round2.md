# TCP 统一设备管理：第二轮局部审核收尾

日期：2026-10-21。范围为本轮附件指定的 R01/F09、R02/F11、R03/F10。沿用 [v1.5 计划](../plans/2026-10-20-tcp-device-integration-plan-v1.md) 的 A1—A6 结构；[上一轮报告](2026-10-21-tcp-device-closure.md) 与其原始证据全部保留。本记录不把此前的 43 passed、271 passed 或更早批次当作本轮结果。

结论：**R01、R02、R03 均已修复并通过本轮实际验证；本轮覆盖的正常工程链没有新增阻塞项。** 独立快照 A1—A6 为 47 passed，相关回归为 196 passed、2 deselected。旧迁移排除、真实硬件、人工 UI、冻结产品和其他环境限制分别保留，不将 v1.5 标为所有环境全部验收完成。

## 1. 版本、范围与结论

审核及实施起点为 `b86d0162dcde14b9c19ec19eee838dc114eeca98`，分支 `agent/runtime-workflow-architecture-v1`。实施前工作区干净；检查本地历史及远端后，基线之后没有已完成本轮修复的提交，没有回退或重复实现 F01—F08。

本轮源码提交：

| 提交 | 内容 |
| --- | --- |
| `ad1d01bba6bb4e2735afb681ff4266652dd3bfbf` | R01：在周期准入冻结全部设备身份，沿保留周期传递；新增真实 spawned Dispatch 消费者成功/失败与日志唯一持久化回归 |
| `23c0186cb4b91c56725cb667726e4c4972f0a65b` | R02、R03：选择性发布定位及本地 issues 展示，多 owner 详情/表格共用有界摘要；新增实际编辑、Qt 反馈与正式发布回归 |
| `2db1913021d764ddbe175b73a28c8623ffe9df82` | 受影响回归的夹具更新：记录模拟驱动调用时转发现有 arguments/operationContext；旧通信入口对当前 2.7 工程的拒绝码严格断言为既有 E_DEVICE_MANAGEMENT_REQUIRED |

最终被测提交为 **`2db1913021d764ddbe175b73a28c8623ffe9df82`**，tree **`e71aba961e0a9f8e0e0ea8d1357dbb6fdebb5978`**。产品 src 子树与 `23c0186` 完全一致，均为 `afcf78c82c9695be14f993d2587bf274508a4cc9`；最后一笔只修正两项测试夹具。测试从该固定提交的独立 `git archive` 快照执行；10 个改动的 Python 文件逐一通过 Git clean 后的 blob 对照。快照、实际导入路径、环境及完整 argv 见 [validation.json](../evidence/2026-10-21-tcp-device-review-round2/validation.json)。后续文档提交不再改变源码和测试；其 SHA 可用 `git log -1 --format=%H -- docs/testing/2026-10-21-tcp-device-review-round2.md` 解析，并在本地交付回执和交付答复中记录。

本轮按活动附件要求交付本地提交。此前一轮四个提交至 `b86d016` 已推送并核对远端；这不代表本轮新增提交已推送。没有合并、部署或发布。

## 2. 三项闭环及与现有工程链的衔接

| 项目 | 最终行为及修改位置 | 新增直接验证 |
| --- | --- | --- |
| R01 / F09 | `DeviceManager.cycleIdentitySnapshot()` 在既有 Manager 锁下捕获全部后端身份；`ConfigurationCoordinator.beginCycle()` 在获取配置快照时将身份保存到现有周期租约。普通应答与 `retainCycle()` 共同通过 `_cycleResult()` 返回副本，普通 IPC 不再在准入后单独查询当前代次。`project_config/rpc.py` 注入捕获方法。已有 Dispatch invocation 与 `CyclePlanSession.install()` 无需绕行，直接得到冻结身份 | `testConsumerDeviceLogsKeepRetainedCycleIdentityAndPersistOnce[False/True]`：生产者和两个消费者为真实独立进程；消费者绑定生产者不用的第二台模拟相机，真实捕获成功；标准 WARN `node.log`、错误 `node.failed` 进入正确设备索引；逐项核对 scope/deviceId/generation、时间/级别/data/phase、owner/cycle/plan、workflow/node run 与 iteration；运行结束回收帧、周期和 Worker；实际 recover 换代后历史日志仍用原代次；关闭 writer 后检查原始 JSONL 每个事件只写一次 |
| R02 / F11 | `core/project/configuration.py::requiredProjectChanges()` 在已有绑定校验调用点补入 project/workflow/node/slot 上下文，复用 `locatedValidationError` 保留 `E_DEVICE_BINDING`、设备及通道信息。`ProjectConfigurationDialog._errorDetails()` 同时展示本地 `WorkflowCompileError.issues` 和既有 RPC diagnostics；不解析错误字符串 | `testSelectiveChannelRemovalLocatesOldBindingAndUiCanPublishAfterSelection`：实际 `commitNode` 从 a 改绑 c，再 `put` 删除 a，完整草稿编译通过；只选通道配置在 RPC 前拒绝，现有 Qt 弹窗详情明确包含 main/acquire/deviceBindings.tcp.selectors.channelId；草稿、勾选项及 Runtime 文档不变；补选改绑后经现有校验及写入正式 COMMITTED，在线通道与节点均为 c |
| R03 / F10 | `device_workspace.py::_activeOwnersText()` 以 `activeOwners` 为准，表格与 `showDiagnostics()` 共用摘要。无活动显示 `-`，单个显示该 owner，多个显示前四个及总数；每个显示值最多 80 字符。保留 HTML 转义、驱动状态和刷新。没有修改服务并发或 Runtime 的 `activeOwner=None` 契约 | `testDeviceDetailShowsAllActiveOwners[owners0/owners1/owners2]`：检查右侧实际 QTextBrowser 文本，包含 0/1/2 个 owner；多 owner 不再显示“当前任务 -”；包含 `<` 的 owner 以普通文本显示；64 个 owner 的输出保持有界 |

身份捕获覆盖所有已装载设备，因此不遗漏消费者独有设备。正常运行中，消费者 Worker 的设备权限由既有启动链建立；`PreparedDevices` 和 `recover()` 继续拒绝替换仍被 owner 使用的后端。保留周期共用该准入身份，不在设备日志投影或查询时倒填当前 generation。显式子周期按原机制捕获新快照，继承周期使用原租约，嵌套安装和恢复行为保持原有语义。

日志继续使用原节点事件和 `DeviceDiagnostics.projectEvent(... write=False)`，没有新增持久化通道、日志数据库或查询时归属猜测。原有缓存、分页和截断限制保持。设备动作和配置校验仍走原契约；候选校验不进行设备 I/O。正常停止 Job 不关闭共享 Listener、COMMITTED 后连接失败不回滚提交、草稿保存不隐式发布等行为由连续链及相关回归实际覆盖。

## 3. 本轮执行与证据层级

环境为 Windows `10.0.26200`，`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python **3.10.21**，PySide2 **5.15.2.1**，Qt **5.15.2**；`QT_QPA_PLATFORM=windows`，`PYTHONIOENCODING=utf-8`。PYTHONPATH 指向固定源码快照的 src 和根目录，`EMO_TEST_SOURCE_SHA` 固定为被测源码 SHA。临时工程与测试数据位于系统临时目录，没有使用现场工程、真实设备或凭据。

可复用入口：

```powershell
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  docs/evidence/2026-10-21-tcp-device-review-round2/run_validation.py `
  D:/jsdfhasuh/documents/my_project/emo_master
```

入口导出执行时 HEAD；本次实际固定版本以 `validation.json` 为准。原始命令、退出码、用时、工作目录、导入路径和源码 blob/SHA-256 全部保留。脚本中的 CHAIN 与 REGRESSION 列出完整文件集合，不以测试名称推定已覆盖场景。

本轮直接反例修复后为 **6 passed，9.73s，exit 0**，见 [fixed-counterexamples.log](../evidence/2026-10-21-tcp-device-review-round2/fixed-counterexamples.log)。这是提交前工作区批次，最终独立快照再次覆盖这些断言。

最终独立快照验证结果如下；所有行均来自本轮实际执行，完整命令和文件集合保存在 validation.json。

| 批次 | 实际命令与范围 | 结果 |
| --- | --- | --- |
| A1—A6 与相关补充 | `python -m pytest -q -rA --basetemp=... --junitxml=...`，5 个文件：TCP 连续链、前轮闭环契约/反馈及本轮 Qt 用例 | **47 passed，74.42s，exit 0**；[chain.log](../evidence/2026-10-21-tcp-device-review-round2/chain.log) / chain.xml |
| 受影响回归 | 同参数；Dispatch、普通 Job、Loop、周期/owner、日志、首次写入、配置发布/错误反馈、设备窗口及配置依赖，共 12 文件；命令明确 deselect 两项旧迁移分支 | **196 passed，2 deselected，108.49s，exit 0**；[regression.log](../evidence/2026-10-21-tcp-device-review-round2/regression.log) / regression.xml |
| Ruff / mypy / 协议 / 差异 | 受改 10 个 Python 文件 Ruff、受改 7 个源码文件 mypy、`scripts/gen_proto.py --check`、`git diff --check b86d016 2db1913` | 全部 **exit 0**；ruff.log、mypy.log、proto.log、diff-check.log。mypy 使用项目现有配置；未标注类型的函数体仍按项目默认规则处理 |

A1—A6 本轮重新执行：A1 创建、保存重开、首次校验/写入及另存为；A2 同工程模拟相机→处理/判定→脚本 SLMP PLC；A3 三类 TCP、内置/第三方算子正式 Job、16 MiB、单次事务；A4 控制/Stop/恢复/选择性更新与 COMMITTED 后连接反馈；A5 同工程设备包、流程包、运行包在新目录导入后实际运行；A6 第三方服务、状态日志、窗口关闭重开及管理动作预算/取消。实际导入目录、Job 与回执在本轮 [acceptance-results.json](../evidence/2026-10-21-tcp-device-review-round2/acceptance/acceptance-results.json) 中，与源码 SHA 关联；保留副本不改原绝对路径。

验证层级：新增 R01 是真实 Runtime/spawned Job 配合模拟设备，不是仅组件注入身份。R02 是原生 Windows Qt 控件自动化连接本地 Runtime，弹窗返回由测试控制，不能等同人工 UI。R03 是原生 Qt 右侧详情实际渲染文本断言，状态来自测试快照；Runtime 多 owner 准确性另由本轮已有服务并发测试复跑验证。

## 4. 首次失败与修正记录

| 记录 | 实际结果与解释 |
| --- | --- |
| first-counterexamples.log / XML | **4 failed, 2 passed，10.47s**。R01 两种真实消费者分支均在设备 scope 为空失败；R03 多 owner 分支显示 `-`。R02 夹具首次写入漏 Runtime 身份，尚未触及产品缺口，单列夹具问题 |
| r02-baseline.log / XML | **1 failed，2.05s**。补入 Runtime 身份后，通道夹具仍未通过首次配置；后改为互不重叠的合法回环 IP，并增加完整状态断言。本批次不作为 R02 产品反例 |
| r02-baseline-corrected.log / XML | **1 failed，2.27s**。源码仍为 b86d016；合法完整编辑序列已通过编译，部分发布正确被拒绝，但实际弹窗详情只有 `device server: selectors.channelId='a' does not exist`，没有 main/acquire/字段位置 |
| fixed-counterexamples.log / XML | **6 passed，9.73s**。保留故意业务失败的子进程 traceback；这是预期失败事件验证，不将它误记为未捕获产品缺陷 |
| snapshot-first-environment.log | 初次快照环境检查因 8.3 短路径与长路径字符串比较失败。输出已显示正确 Python、Qt 与快照导入路径；修正验证脚本为 `os.path.samefile()`，不改变产品源码或测试断言。最终快照的 environment.log 检查通过 |
| snapshot-first-chain.log / XML | `23c0186` 第一次独立快照 **47 passed，73.60s**。该批次及工程、Dispatch 原始资料保留在 snapshot-first-* 中；不混入最终提交结果 |
| snapshot-first-regression.log / XML | `23c0186` 第一次独立快照 **4 failed, 194 passed，118.07s**。失败涉及两个过期测试夹具及旧工程升级的两个参数化分支，下面逐项说明 |
| baseline-confirmation.log / XML / JSON | 将原始 b86d016 导出到另一个独立快照，对上述四项执行 **4 failed，17.03s**；错误与第一次快照一致，确认均非本轮产品回归 |
| configuration-fixtures-fixed.log / XML | 修正驱动记录函数以转发现有 API 参数；严格更新当前 2.7 工程旧通信拒绝码后，**2 passed，3.04s**。保留业务结果、计划更新、不重启后端和在线配置不被旧入口改变等原断言 |

四项既有失败的分类：连续周期用例的记录函数遗漏 `arguments`/`operationContext`，实际调用在进入驱动前因夹具 TypeError 失败；gRPC 用例已正确拒绝旧通信写入，但断言仍期待统一设备化以前的错误码。两项夹具已更新并纳入最终回归。另两个参数化分支验证旧 2.1→2.6 草稿升级，期待旧式 `main/load/imagePath` 文案，实际返回现有结构化 main/load/params.imagePath；本轮明确不承担旧工程迁移，故最终命令仅对这两个分支使用可见的 `--deselect`，不改原测试、不标记为通过，也不作为三个新缺陷未闭环的依据。

未删除首次失败、夹具问题或环境检查失败；历史文件不重写。本轮原始文件统一使用 `.gitattributes` 的 `-text -whitespace` 规则保存精确字节，证据清单见 [SHA256SUMS.txt](../evidence/2026-10-21-tcp-device-review-round2/SHA256SUMS.txt)。清单不自包含。

## 5. 未运行项、限制与复验

| 项目 | 状态及原因 |
| --- | --- |
| 真实相机/PLC/TCP 设备 | **NOT_RUN**。本轮使用临时工程、模拟设备和本机回环，没有真实硬件联调授权或条件 |
| 人工 UI 完整走查 | **NOT_RUN**。运行原生 Qt 自动化及受控弹窗，尚未做操作人员人工验收 |
| 冻结 EXE/安装包 | **NOT_RUN**。本轮未构建产品；现有 EXE 不会自动加载这些源码修复 |
| Linux、跨机器 | **NOT_RUN**。沿用前轮无可用 Linux 用户环境/独立远程测试环境的限制，本轮没有重跑环境库存；前轮库存输出仍是历史证据，不冒充本轮检查 |
| 深层 Windows 资源 rename | 保留前轮已观察限制。本轮使用正常系统临时目录，没有扩大为第四项路径改造，也没有证明任意深度工程路径均可用 |
| 旧工程迁移、全仓/压力/强杀矩阵 | 不在本轮范围；没有把未执行项计为通过 |

用户复验：

1. 用本轮源码启动 Designer，打开正常 2.7 临时工程；保存、另存为和显式 Runtime 写入保持各自入口。
2. TCP Server 建立 a/c 两个通道，节点 main/acquire 的 tcp 槽先绑定 a；发布后改绑 c，再删除 a。完整草稿应可校验。
3. 在发布窗口只选通道数组：应拒绝并在详情定位 main/acquire/deviceBindings.tcp.selectors.channelId，草稿与选择保留。补选该节点改绑后校验、写入，确认在线配置两处一致。
4. 让 Dispatch 消费者调用其绑定设备；在设备日志查看正常节点日志及一次普通业务失败，核对 Job/周期/节点/代次归属；生产者与消费者退出后资源正常释放。
5. 两个 Job 正常共享同一服务设备，比较中间表与右侧详情，均应显示两个任务；退出后恢复空闲 `-`，监听和驱动状态仍准确。
6. 沿既有正常工程链运行/停止、查看状态日志，再导出并在新目录导入运行。真实硬件、冻结产品和人工 UI 结果另记，不用软件通过替代。
