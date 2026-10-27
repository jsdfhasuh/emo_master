# Runtime 校验反馈与当前连接展示验证记录

日期：2026-10-20。复审发现的已提交后连接恢复失败漏报已定点修正，当前固定源码的 44 文件受影响回归为 **538 passed in 315.13s，0 失败 / 错误 / 跳过**，Proto / Ruff / 差异检查通过，见 §7。前序 **529 passed in 298.68s** 属于修正前快照，不与此次结果相加。本文不代表正在运行的 EXE 已更新，也不代表真实桌面、冻结产品或现场设备验收。

关联计划：[Runtime 校验反馈与当前连接展示改进计划](../plans/2026-10-20-runtime-validation-feedback-connection-state-plan.md)。

## 1. 源码与工作区边界

- 用户工作区：`C:/Users/jsdfhasuh/my_scripts/emo_master`，本机解析后的同一检出路径为 `D:/jsdfhasuh/documents/my_project/emo_master`。
- 核对 HEAD：`ef5af5066599bc57a5182067e31775f8798e8107`。实际验证对象为该提交加既有及本次未提交修改，不能只用 HEAD 冒充最终源码快照。
- 前序实施前差异和哈希：[baseline.json](../evidence/2026-10-20-runtime-validation-feedback/baseline.json)。前序固定回归的文件清单、命令、开始 / 结束源码 SHA-256 及检查返回码：[software-verification.json](../evidence/2026-10-20-runtime-validation-feedback/software-verification.json)。复审修正的独立证据见 §7。
- Python 3.10.21 / PySide2 5.15.2.1，`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。Qt 使用 `offscreen`，所有服务、工程、图片和设备状态均为临时或模拟对象；真实 gRPC 仅连接测试自行创建的本机隔离服务。
- 没有改动现场工程、凭据、相机 / PLC 地址，没有构建、替换 EXE、提交、推送或部署；前序菜单 / 首次写入改动及证据保留。
- 前序实施前记录的 29 个无关脏文件均保持原哈希，其中包括 `project_entry_dialog.py` 和 `test_startup_project_entry.py`；详见 [preservation.json](../evidence/2026-10-20-runtime-validation-feedback/preservation.json)。前序软件证据核对了开始与结束源码哈希一致。
- 前序交付时完成 [逐项核对](../evidence/2026-10-20-runtime-validation-feedback/completion-audit.json)：12 项验收要求映射到实际通过的用例，65 个源码 / 测试文件与当时测试快照一致，44 个测试文件均执行；计划、指南、验证记录和 7 张渲染图均存在。该记录未覆盖后续发现的嵌套连接失败漏报，修正与最新验证另见 §7。真实桌面、冻结程序、真实远端主机和设备仍单列 NOT_RUN。

## 2. 实际实现

| 接点 | 本轮行为 |
|---|---|
| `ui/project_configuration.py` | 点击即显示忙碌，成功有图标及“尚未写入”文字；本地错误、RPC 异常、正常返回 FAILED 均弹窗；消费 future 后再通知，阻止模态重入和重复提交 |
| 同窗口的请求结果 | 同一写入请求的相同失败 / 警告只通知一次；未知结果保留原请求供查询，不重发；COMMITTED 后的发布、清理、导出及连接恢复失败弹窗保留已提交事实；正常等待和取消不弹错 |
| 同窗口的草稿升级 | 明确的“升级草稿并重新校验”，复用 `migrateProjectPayload(enableDevices=True)`；独立候选通过结构检查后，一次事务应用并刷新既有画布 / 标签 / 编辑器；可撤销，不清空历史、不保存、不自动写入 |
| 同窗口的失效处理 | 选择、撤销 / 重做或草稿改变后清除旧结论；连接代次改变后显示具体说明与关闭出口，而不是按钮无声返回；空闲检查读取已有工程身份和 checkpoint，不周期性重新校验整个工程 |
| `ui/runtime_connection.py` | 当前连接与候选目标分区；一次异步读取当前身份和可获得的版本；候选先读取再明确切换；失败弹窗可重试，中文取消；关闭后的候选由原 future 清理，不更新或切换新上下文 |
| `services/runtime_client.py`、`page_designer/chrome.py` | 复用同一无副作用连接描述，按真实传输对象 / 所有权识别内嵌与 gRPC；不凭地址是否为空、是否回环或是否有页面端点猜测，不为展示启动服务 |
| `project_config/rpc.py`、`coordinator.py`、`core/project/runtime_directory.py` | 复用现有 ValidationIssue 保留缺失算子的流程 / 节点 / 字段位置；同步异常及异步 FAILED 回执保留可获得的 issues，沿用既有 `result_json` 字段，无新 RPC 或协议字段 |

本轮没有改造新建工程的默认 schema，没有自动迁移旧相机 / PLC 节点，没有重写资源准备、设备串行访问、配置事务或连接切换生命周期。菜单仍为“文件 / 运行 / 编辑 / 视图 / Runtime / 设备管理”，工具栏仍只有工作区切换。

## 3. 前序软件检查与失败证据

| 执行 | 结果与适用范围 |
|---|---|
| 实施前正常操作反例 | `3 failed`：无立即忙碌反馈、本地选择错误无弹窗、没有当前连接分区；见 `baseline-counterexamples.log/xml` |
| 第一轮聚焦复测 | `3 passed in 1.95s`；仅覆盖最初三个反例，见 `feedback-first.log/xml` |
| 扩展过程反例 | `feedback-expanded-2.log/xml` 保留 legacy Open 会先加载 Runtime 的不成立测试前置；`feedback-expanded-3.log/xml` 保留 `4 failed, 31 passed, 2 errors`，包括诊断位置丢失、替换工程资源根目录的正常拒绝及测试确认框补丁干扰清理 |
| 聚焦受影响复测 | `77 passed in 73.74s`；见 `focused-1.log/xml`。属于后续补测前的快照，不代替最终回归 |
| 中间受影响合并回归 | `523 passed in 312.81s`，再补充选择失效 / 连接切换等场景后 `528 passed in 302.73s`；见 `regression-before-final-feedback.*`、`regression-before-identity-check.*`。都不覆盖最后的轻量身份检查调整，不与最终数量相加 |
| **前序固定源码回归** | **529 passed in 298.68s，0 失败 / 错误 / 跳过**；44 个测试文件，见 [acceptance-regression.log](../evidence/2026-10-20-runtime-validation-feedback/acceptance-regression.log) 与同名 XML，原 393 / 中间 523 / 528 项不与之相加；不覆盖 §7 修正 |
| 前序静态与协议检查 | Proto 同步、作用域 Ruff、`git diff --check` 均 exit 0；开始与结束源码哈希一致，记录于 `software-verification.json` |
| 前序控件渲染 | 当时源码的 `render_offscreen.py` exit 0，7 张控件图已检查；两个证据脚本的 Ruff 检查通过。渲染初次清理已销毁窗口的脚本错误保留于 `render-1.log`，修正脚本清理后复测见 `render-final.log` |

以上日志均在 [本轮独立证据目录](../evidence/2026-10-20-runtime-validation-feedback/)；没有覆盖前序证据目录。

前置与反例的处理没有弱化正常业务断言：旧格式场景只在测试打开时隔离旧 LoadProject API，以保留截图中“草稿已存在但 Runtime 为空”的前置；升级、校验、写入与查询仍调用实际隔离 Runtime。不同在线工程的未绑定相对文件由既有资源根目录规则明确拒绝，测试检查没有强行首次安装或覆盖。缺失算子的位置丢失由源码修正，不用删断言绕过。测试中的无二次确认断言只作用于被测动作，窗口清理恢复原确认处理。

### 模态执行环境限制

当前 Windows `QT_QPA_PLATFORM=offscreen` 下，独立的 `QMessageBox.exec_()` 最小例子也以 `0xC0000005` 崩溃；添加系统字体及 Fusion 样式后仍复现。证据为 `modal-isolated.log`、`modal-minimal.log`、`modal-minimal-font.log`、`modal-minimal-fusion.log`。最小例子不经过本轮的窗口、任务或错误处理逻辑。

自动化因此按计划使用可控的 QMessageBox 模态执行器，检查实际窗口对象、标题、正文、详情、真实按钮点击、通知次数、禁用状态和事件循环重入；没有让异常弹窗被全局静默吞掉。Qt 控件渲染与按钮行为通过不等于真实 Windows 桌面模态循环通过；后者为 **NOT_RUN**，不能写成用户当前产品已验收。

## 4. 逐项验收映射

下表为软件层面的实际覆盖；最终结论须与固定源码回归结果一致，不外推真实远程主机或硬件效果。测试名主要位于 `tests/designer/test_runtime_validation_feedback.py`。

| ID | 直接证据 |
|---|---|
| VF-01 | `testValidationHasImmediateBusyAndPassedNotWrittenFeedback` 检查立即忙碌、重复点击不提交、通过文字 / 样式、草稿与磁盘不变；既有首次写入菜单测试检查实际 Runtime 校验不启动 Job |
| VF-02 | `testLegacyValidationOffersUpgradeWithoutMutatingDraft`，内嵌 / loopback；版本说明、错误弹窗、升级按钮、关闭后历史 / 草稿 / 磁盘不变 |
| VF-03 | `testExplicitUpgradeIsOneUndoableDraftChangeAndNeverWrites`，2.1 / 2.5 × 两类传输；一次历史项、原资源及流程保留、来源身份、画布同步、撤销 / 重做、无保存或写入 |
| VF-04 | `testUpgradeKeepsDraftOnCompilerFailureAndPreservesLocations`、`testUpgradedDraftResourceFailureCanBeFixedAndRevalidated`、`testMigrationStructureFailureLeavesNoHalfUpgrade`、`testUpgradeRejectsUnknownVersionsAndIllegalFields`；异步回执另由 Runtime 的 `testInitialFailedReceiptRetainsCompilerIssueLocations` 验证 |
| VF-05 | `testPrewriteSuccessHasOnlyExistingImpactConfirmation`、`testUpgradeActionFromPopupDoesNotResumePrewrite`；前者检查只有原写入确认、一次校验一次写入；后者通过真实弹窗按钮升级后无自动写入、无第二次确认 |
| VF-06 | `testLocalSelectionErrorIsVisibleAndRecoverable`、`testValidationFailuresPopupEachNewOperationAndRestoreControls`、`testReceiptNotificationsRespectActualStateAndDeduplicate`、`testValidationErrorModalCannotReenterCommandOrPoll`；RPC 异常和正常 FAILED 分别验证，主动新操作可再次通知 |
| VF-07 | 既有 `testLostFirstWriteReplyQueriesTheSameReceiptWithoutReplaying`、准备中查询 / 取消测试和资源上传取消测试；回执通知测试检查 COMMITTED 后异常、WAITING、CANCELLED，未知结果保留 requestId 且不重放；§7 新增 `testCommitted*` 覆盖嵌套连接失败及直接反例 |
| RC-01 | `testCurrentRuntimeDescriptionReadsActualTransportWithoutProvisioning`，内嵌 / loopback；立即类型、真实实例、空工程、无新端点、无 Job；`testCurrentInfoFailureDoesNotEraseSuccessfulIdentityOrClaimConnected` 区分读取失败与版本不可用 |
| RC-02 | 上述两类真实传输测试，`testOpeningCurrentConnectionWithExistingPageEndpointDoesNotProvisionAgain` 检查已有真实页面端点，`testRemoteTargetMetadataUsesIndependentTypeAndShowsCurrentVersions` 使用模拟远端元数据；没有访问真实远端主机 |
| RC-03 | `testCandidateFailureDoesNotOverwriteCurrentAreaAndCanRetry`、`testRealConnectionDialogSwitchUpdatesChromeAndRetainsEmbeddedOwner`，以及既有候选先探测再切换 / 保留旧 owner 测试；实际本机 gRPC 切换后摘要一致，原内嵌 owner 不被关闭 |
| RC-04 | `testLateCandidateIsReleasedWithoutSwitchOrPopup`（关闭 / Runtime / 工程变化）、`testClosingDuringCurrentInfoReadIgnoresLateReplyAndKeepsOwner`；配置窗口另由 `testLateValidationCannotNotifyOrContinueWritingInNewContext` 覆盖 |
| REG-01 | 原 37 文件回归清单全部保留，追加本轮反馈测试及直接影响的 runtime directory / globals / device resources / project models / edit session / device catalog 共 44 文件；菜单、运行停止、首次写入、查询取消、资源和设备窗口断言继续执行 |

另有 `testSelectionChangeImmediatelyInvalidatesVisiblePassedVerdict`、`testReconnectedRuntimeExplainsExpiredDialogInsteadOfSilentButtons` 和 `testIdleContextChecksUseExistingIdentityWithoutWholeDraftValidation`，防止通过结论误留、重连后无声按钮或空闲定时器反复验证整个工程。

## 5. 可复现命令

仓库根目录 PowerShell；仅使用临时工程和隔离服务：

```powershell
$p = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
& $p docs/evidence/2026-10-20-runtime-validation-feedback/connection-failure-fix/verify_followup.py
```

复审修正入口复用前序验证脚本的 44 文件清单，输出改到 `connection-failure-fix/`，不覆盖前序 529 项及渲染证据；依次执行 pytest、Proto 同步检查、作用域 Ruff 和 `git diff --check`，在前后分别计算实际源码哈希。脚本不会启动产品部署、访问现场设备或修改用户工程。

## 6. 控件渲染与人工边界

前序已渲染并检查 7 张控件图，使用 Windows 微软雅黑字体，记录于 `render-summary.json`；不将它们作为 §7 新增连接失败弹窗的渲染证据：

- [2.1 草稿与明确升级入口](../evidence/2026-10-20-runtime-validation-feedback/legacy-format.png)
- [格式错误弹窗控件](../evidence/2026-10-20-runtime-validation-feedback/popup-1.png)
- [升级后校验通过且尚未写入](../evidence/2026-10-20-runtime-validation-feedback/validation-passed.png)
- [当前本机内嵌连接](../evidence/2026-10-20-runtime-validation-feedback/current-embedded-runtime.png)
- [候选失败且当前连接保留](../evidence/2026-10-20-runtime-validation-feedback/candidate-failed-current-retained.png)
- [候选失败弹窗控件](../evidence/2026-10-20-runtime-validation-feedback/popup-2.png)
- [主界面 Runtime 摘要](../evidence/2026-10-20-runtime-validation-feedback/main-runtime-summary.png)

这些是 Qt offscreen 控件渲染，不包含操作系统标题栏或真实桌面模态交互，不是 150% / 200% DPI、冻结程序、真实远程服务或相机 / PLC 验收。原生桌面可继续按计划无设备人工走查；不要求先解决已接受暂缓的 F01 风险，也不额外增加性能或现场节拍门槛。

使用源码启动的程序需正常重启以加载修改；冻结 EXE 需要另行构建。本轮没有停止或替换用户正在运行的程序。

## 7. 复审修正：已提交后的连接恢复失败提示

复审发现 `DeviceTransition.finish()` 把设备重连 / 通信监听恢复失败记录为 `connections[目标] = "FAILED: 原因"`，而窗口只将顶层错误字段纳入弹窗，导致已提交后的连接失败仍只列在状态文本中。这是正常在线更新的失败出口，不属于已暂缓的 F01 组合风险。

本次产品代码仅在 `ProjectConfigurationDialog.poll()` 增加四行：提取 `connections` 的明确失败条目，合入既有警告详情与去重键。保留“配置已提交，但后续操作异常”和“不要重复写入”说明；不修改 Runtime 的实际重连、写入、取消、资源策略或菜单。等待 / 成功 / 已替代 / 重启后未知不误报为失败；后续查询出现新失败时仍通知，其他设备成功进展不会重复通知相同失败。

新增测试复用 `setupDialog` 和现有 QMessageBox 记录夹具，通过实际“查询写入结果”按钮验证：

- `testCommittedConnectionFailureWarnsWithoutRewriting`：设备、通信两种失败；保留提交事实与原回执；同结果去重、无关成功变化去重、新请求正常通知、按钮恢复、不写入、不改草稿或磁盘。
- `testCommittedConnectionFailureReportedWhenLaterQueryResolvesPending`：先返回等待，再查询失败；新增另一连接失败正常提示，重复查询不重复提示。
- `testCommittedNonfailedConnectionDoesNotWarn`：PENDING / CONNECTED / LISTENING / SUPERSEDED / UNKNOWN_AFTER_RESTART 五种非明确失败不弹错。
- `testCommittedConnectionAndTopLevelErrorsShareOneWarning`：连接失败和导出 / 观察持久化错误合并一次通知，详情完整。

独立证据目录：[connection-failure-fix](../evidence/2026-10-20-runtime-validation-feedback/connection-failure-fix/)。

| 执行 | 结果 |
|---|---|
| 修正前新增测试 | **4 failed, 5 passed, 47 deselected in 9.33s**，保留于 `baseline-counterexamples.log/xml`；三个漏弹窗及一个混合详情漏字段反例 |
| 定点修正后复测 | **9 passed, 47 deselected in 9.17s**，见 `focused.log/xml`；未选中的旧测试不记为通过 |
| 44 文件受影响回归 | **538 passed in 315.13s，0 失败 / 错误 / 跳过**，见 `acceptance-regression.log/xml`；沿用前序清单，不与历史 529 项、审查时的 102 项或本轮定向 9 项相加 |
| 静态与协议检查 | Proto、作用域 Ruff、`git diff --check` 均 exit 0；补充检查本轮验证脚本并再次检查最终差异 |
| 固定源码 | `software-verification.json` 记录 65 个源码 / 测试文件的执行前后 SHA-256，一致；仅产品窗口和新增测试两个文件相对前序源码快照变化 |

本次基线记录 98 个既有修改 / 未跟踪文件，只有窗口源码、测试及三份相关文档共 5 个文件变化，其余 93 个保持原哈希；详情见本子目录 `baseline.json` 与 `preservation.json`。前序证据未覆盖，无关的 `project_entry_dialog.py` 和 `test_startup_project_entry.py` 保持不变。

可见弹窗补充检查仍受本机 offscreen 环境限制：独立 QMessageBox（不经过工程或 Runtime）在 `show()` 处同样退出 `3221225477 / 0xC0000005`，见 `modal-visible-isolated.log` 和 `render-summary.json`。未显示前的中间渲染有文本裁切，保留 `connection-failure-popup-unshown.png`，不计为视觉验收通过；未因该环境问题放宽业务测试断言或修改产品弹窗行为。

本轮新增测试为模拟回执的 Qt offscreen 控件测试，沿用可控的 QMessageBox 执行器；不声称真实设备重连失败或原生 Windows 模态循环已验收。没有访问真实设备、构建、替换运行中程序、提交或发布。
