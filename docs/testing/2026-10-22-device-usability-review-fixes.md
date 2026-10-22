# 设备易用性审核四项修复与交付记录

日期：2026-10-22；目标分支 `agent/runtime-workflow-architecture-v1`。
依据：[设备易用性计划 v1](../plans/2026-10-21-device-usability-improvement-plan-v1.md)。
证据：[独立目录及重跑入口](../evidence/2026-10-22-device-usability-review-fixes/README.md)。

## 1. 范围、基线与源码

- 开始时本地与实际 `git ls-remote` 均为 `eefb930481eda864cf03b0725407192c41eff17a`。
- 本轮只处理连接测试回执归属、设备页共用校验、定向错误清理和 selector 默认值执行链。
- 源码／测试提交：`c41bf6f50e405f1f2c8a286372416d28d1b7cee7`。
- 源码 tree：`2790207b374f9e321899ea0412547511f052d01f`。
- 该提交只包含 7 个产品源码文件与 4 个对应测试文件；没有改 protobuf、Worker 协议、
  设备描述符、TCP 字节投影、视频采样或动态图片文件名。
- 继续遵守当前根 `AGENTS.md`。开始检查后发现用户已有 `AGENTS.md` 和
  `docs/project-json-spec.md` 的未提交修改；以 `preflight.json` 的 SHA-256 保留并核对，
  不纳入本轮提交，不覆盖用户工作。收尾时又发现非本轮编辑的
  `tests/designer/test_project_configuration_ui.py` 并行修改，记录于
  `subsequent-worktree-change.json`，同样不暂存；该未提交版本不冒充本轮快照测试输入。
  测试生成的工程继续使用现有 `WorkflowStore().toPayload()` 的 2.7 格式。

## 2. 四项关闭依据

### Q1：测试回执的设备与候选归属

**状态：已修复。** 原设备页只检查工程／Runtime scope；创建对话框也未冻结输入。
原生控件反例中，A 测试等待时切到 B，或同设备／创建表单把端口 10000 改为 10001，
旧成功和失败都覆盖了当前提示。

`device_workspace.py::testConnection/_metadataReady` 为 test 单独冻结设备 ID 和纯表单候选；
`device_wizard.py::DeviceDefinitionDialog` 冻结当次候选。
请求返回仍先处理原工程／Runtime 归属，再在读取 Future 的成功或异常之前比较候选。
当前输入无效也视为不再匹配，不调用会聚焦控件或提交草稿的校验函数。
不匹配时保留当前反馈，若仍显示“正在测试”则明确说明此前结果不适用于当前输入。
pending、定时器、刷新与后续操作照常恢复；没有把 selectedId 塞入共享 `_scope()`，
没有禁止切换或编辑，也没有新增确认流程。

测试：`test_device_workspace_usability.py::testConnectionReceiptBelongsToDeviceAndCandidate`
的设备／端口 × 成功／失败四分支，以及
`test_device_definition_feedback.py::testCreationConnectionReceiptBelongsToCandidate` 的两分支。
合法当前候选成功和失败仍能显示，由禁用空通道正向用例验证，不是无条件丢弃所有回执。

### Q2：设备详情“测试连接”共用中文校验与定位

**状态：已修复。** 基线实际显示 `connection.channels.0.allowedIps: size constraint`，
没有走已有的中文提示及单元格定位。

`DeviceWorkspace.testConnection` 现在调用现有 `validateDeviceDefinition`，并复用
`deviceValidationMessage/deviceErrorDetails/deviceErrorControl`。
保留原 type、version、enabled、description、policies 等候选字段；表单显示对应页签后再次定位。
空 allowedIps 报“通道 1：请填写至少一个允许接入的客户端 IPv4”，定位连接页第 1 行第 3 列。
校验失败没有确认或测试 RPC，工程草稿、文件字节与当前输入全部保持不变。
测试路径没有调用 `commitPending`、`sync`、save 或 Runtime 发布。
禁用且 channels=[] 的合法 Server 仍可发起端点测试；添加了非法通道行仍被原严格规则拒绝。

测试：`testDetailConnectionValidationIsChineseFocusedAndReadOnly` 和
`testDisabledEmptyDetailConnectionTestUsesCandidateWithoutApplying`（当前成功／失败两分支）。
使用真实 PySide2 表格、页签、焦点与异步 reader；RPC 为受控测试连接器，不声称真实硬件连接成功。

### Q3：只清理已修正项，后台刷新不覆盖输入错误

**状态：已修复。** 节点缺槽报错后选中合法设备，原先 SourceEditor 已生成但“请选择设备”仍残留。
设备详情普通参数与通道表的公共摘要也未完整关联实际控件。

- `DeviceNodeDialog` 将缺设备错误关联 slot 控件，将 selector 问题关联具体 selector。
  只在对应合法选择后删除该控件自己的反馈；其他槽的提示和未解决的其他行保留。
- `DeviceWorkspace` 复用创建对话框的小型 `inputErrorControl` 机制，统一应用、切换、关闭和测试校验失败。
  字段控件清除自身旧描述后，才移除属于该字段的摘要／详情；不清除另一项或较新的操作结果。
- `TcpConnectionForm` 将通道错误绑定行／列，编辑另一列不清理仍无效的 IP 错误。
  模型级路由错误继续保留，直到合法修改或删除使路由有效；移除错误行会清除其旧反馈。
  复用现有表格信号，只在新行三个单元格填好后通知外层，避免读取半行产生 Qt stderr 异常。
- 后台状态无论读取成功或失败都更新真实在线信息，但不覆盖尚未解决的输入错误摘要与技术详情。
  没有新增持久状态账本、后台补偿或线程机制。

测试：`testMissingSlotFeedbackClearsOnlyWhenThatSlotIsSelected`、
`testDetailFieldFeedbackClearsOnlyCorrectedInputAndSurvivesState`、
`testDetailChannelFeedbackClearsOnlyEditedErrorCell`，并保留已有创建表单与后台恢复回归。

### Q4：selector 默认值从编辑、编译到冻结准入和驱动

**状态：已修复。** 这是既有扩展契约缺口，不记为“内置 TCP 主链不可用”。
基线严格校验返回 `{'channelId': 'a'}`，编译计划却保留 `{}`；正式 spawned Job 的
真实 TCP 驱动因 `operationContext.selectors['channelId']` 缺键而失败。

| 阶段 | 最终行为与边界 |
| --- | --- |
| 共享验证 | 不修改 `validateDeviceBinding` 的强度与默认语义；纯计算，无设备 I/O。 |
| Designer 草稿 | `DeviceCatalogEditor._validateNode` 使用共享函数返回值；可选默认值可进入草稿，缺失 required 即使有 default 仍保持缺失。 |
| 严格编译 | `WorkflowCompiler._devicePlans` 使用校验返回的 selectors 生成冻结计划；不修改原文档，显式值优先。 |
| 周期冻结 | 原在线候选仍原样冻结；不为补默认值导入当前 Designer 或未选草稿。 |
| Runtime 准入 | Runtime 创建 DeviceManager 时传入同一已注册算子的 requirements；manager 使用**冻结节点、槽与冻结设备定义**纯规范化预期 selectors，再与完整请求精确比较。不是规范化请求来掩盖篡改，也没有删除冻结绑定比较。 |
| 实际执行 | Worker 按编译设备计划经现有 TCP IPC 进入 manager；正式 Job 的真实 Server acquire_session 驱动收到 `channelId='a'`，正常完成。 |

`testSelectorDefaultReachesRealSpawnedJobAndFrozenAdmission` 使用临时第三方模块、真实
RuntimeService、正式 spawned Worker、设备权限、真实 localhost listener 与实际驱动方法。
记录原冻结绑定 `{}`、实际驱动 `{'channelId':'a'}`、owner／cycle、Job ID。
同一冻结周期中改成 b、发送空 map 或添加未知键，均返回 `E_DEVICE_BINDING`，驱动调用数不增加。
原在线配置与输入 payload 不被改写。

`testSelectorDefaultsAgreeBetweenDraftAndCompiledPlan` 参数化 required default、optional default、
显式值优先；`testInvalidSelectorsRetainStrictLocation` 覆盖失效默认、错误类型、失效显式值、未知键、
required 无 default，保留原 `E_PARAM_INVALID`／`E_DEVICE_BINDING` 及 workflow/node/selector 位置。
既有 optional backupChannelId 回归继续保护可选默认，局部发布隔离由 C1—C6 和原发布组件回归保护。

## 3. 首次失败和修正过程

| 批次 | 实际结果 | 解释 |
| --- | --- | --- |
| first-failure | 17 failed、3 passed、2 teardown errors | 产品未修改；含 QSpinBox 使用错误 API、夹具声明不一致、错误码期望过窄与失败后未恢复表单的收尾问题。 |
| first-counterexamples-corrected-fixtures | 13 failed、7 passed | 只修夹具及收尾，产品仍是审查基线；四项直接缺口全部复现。 |
| fixed-targeted-1 | 20 passed | 原始输出仍有半行表格的 Qt 信号异常；不当作最终无异常通过。 |
| fixed-targeted-2 | 20 passed | 局部修正半行通知后，无上述异常。 |
| fixed-targeted-3 | 1 failed、20 passed | 新增正向按钮断言暴露测试连接器初始化顺序错误；不放松断言，改为重算真实控件支持状态。 |
| fixed-targeted-4 | 22 passed | 增加当前成功／失败对照后全通过，正式 Job 默认值与冻结拒绝均通过。 |

所有批次均保留原始日志、JUnit XML、命令及退出码；不删除失败、不新增跳过。
开发中的 Ruff 发现测试局部变量与 fixture 名冲突，改名后通过；未改产品接口来迎合测试。

## 4. 固定源码快照验证

环境：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python **3.10.21**、
PySide2 **5.15.2.1**、Qt **5.15.2**，`Windows-10-10.0.26200-SP0`。
显式设置 `QT_QPA_PLATFORM=windows`、`PYTHONIOENCODING=utf-8`，不是 offscreen。
工程全部位于临时目录，使用模拟相机／PLC、第三方模拟设备和 localhost TCP。

```powershell
$env:QT_QPA_PLATFORM='windows'
$env:PYTHONIOENCODING='utf-8'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  docs/evidence/2026-10-22-device-usability-review-fixes/run_validation.py `
  --repo . --source-sha c41bf6f50e405f1f2c8a286372416d28d1b7cee7 `
  --output-dir docs/evidence/2026-10-22-device-usability-review-fixes/final-source
```

实际导入路径：
`C:/Users/JSDFHA~1/AppData/Local/Temp/emo-f-x3pf58k1/source/src/emo_master/__init__.py`。
入口使用 `git archive` 导出固定提交，并以 `samefile` 确认不是当前工作区／已安装包。
11 个源码／测试文件均比较导出 blob 与提交 blob；完整 argv、cwd、时间、退出码、
环境和 tree 位于 `final-source/validation.json`。

| 最终固定源码命令 | 实际结果 | 退出码 |
| --- | --- | --- |
| 环境／导入路径检查 | Python 3.10.21、PySide2 5.15.2.1、Qt 5.15.2，导入独立 source/src | 0 |
| `python -m pytest -q -rA ...` CHAIN 六个文件 | **62 passed**，JUnit 90.753 秒 | 0 |
| `python -m pytest -q -rA ...` REGRESSION 28 个文件 | **370 passed, 2 deselected**，JUnit 131.154 秒 | 0 |
| `python -m ruff check` 11 个受改 Python 文件 | All checks passed | 0 |
| `python -m mypy` 7 个受改源码文件 | Success: no issues found in 7 source files；保留既有 untyped 注记 | 0 |
| `python scripts/gen_proto.py --check` | 一致，无协议修改 | 0 |
| `git diff --check eefb930... c41bf6f...` | 无差异格式错误 | 0 |

本轮固定源码合计 **432 passed、0 failed、0 errors、0 skipped、2 deselected**。
这是本轮 XML／日志的实际统计，不是历史数字之和；各开发定向批次也不重复计入该合计。
最终批次退出码全部为 0。原有 runner 与新入口分别有 SHA-256，48 项实际产物有独立记录。
入口脚本另行 Ruff 检查，结果保留 `entry-point-check.json`／`.log`。

仅沿用已有 `testUpgradedDraftResourceFailureCanBeFixedAndRevalidated` 的两个旧迁移参数化分支排除；
没有新 skip／deselect。历史设备 **410** 和媒体 **181** 均不记作本轮结果，更不相加。

### 验收映射

| 范围 | 本轮复用的实际测试入口及证据层次 |
| --- | --- |
| C1—C6 | `test_device_usability_chain.py::testC1ThroughC6PendingDraftPublicationAndRun`；同一临时工程的创建、禁用空通道、待绑定保存重开、补齐启用、显式选中发布、正式 Job 与三类包流转；真实 QWidget 与 Runtime 软件链。 |
| A1—A6 | `test_tcp_device_integration.py` 六个既有 testA* 入口；保存／首次写入、模拟相机处理 PLC、三类 TCP、停止维护更新、三类包新目录运行、第三方服务／日志；真实 Job 与 localhost。 |
| R01 | `test_device_feedback_closure.py` 周期冻结身份、历史 generation、原事件投影和 JSONL 唯一持久化；没有新增日志写入。 |
| R02 | 同文件及 `test_device_usability_publication.py`、`test_project_configuration_ui.py`，选择发布定位、依赖补选和候选隔离。 |
| R03 | 设备活动快照／多 owner 有界展示与日志相关回归；没有改变活动容量或普通设备 FIFO。 |
| 本轮新增 | 四项定位反例、原生 Qt 焦点／信号／Future、selector 正式 spawned Job 及冻结篡改拒绝；最终 CHAIN／REGRESSION 均包含这些测试。 |

## 5. 未执行和限制

| 层次／范围 | 状态与原因 |
| --- | --- |
| 真实相机／PLC／外部 TCP 客户端 | NOT_RUN；无本轮硬件授权，均以模拟／localhost 验证，未访问现场工程或凭据。 |
| 人工完整桌面 UI | NOT_RUN；本轮为原生 Windows Qt 自动化，未执行新的人工全链走查；历史桌面记录保留但不冒充本轮。 |
| 冻结 EXE／安装包／发布部署 | NOT_RUN；源码交付范围，不构建、不发布；运行中的旧 EXE 不会热加载本次源码。 |
| Linux／跨机器 | NOT_RUN；本轮实际环境为 Windows，本轮没有此平台验收；不是新增交付门槛。 |
| 全仓、压力、强杀、跨 Runtime 独占、多故障矩阵、旧工程迁移 | NOT_RUN；用户明确不扩展本轮范围；仅保留既有两项迁移排除。 |
| 媒体 181 项专项 | NOT_RUN；相关已交付源码未变，不重复无关专项，不以历史数字补本轮通过数。 |

本轮没有修订为硬件完成或全产品验收；正常链路与本轮直接反例以固定源码实测为准。

## 6. 提交、范围核对与推送

源码／测试提交为 `c41bf6f50e405f1f2c8a286372416d28d1b7cee7`。
报告、计划状态、原始证据和字节保留规则在后续独立文档提交。
包含本报告的最终文档提交可精确取得：

```powershell
git log -1 --format=%H -- docs/testing/2026-10-22-device-usability-review-fixes.md
git diff --exit-code c41bf6f50e405f1f2c8a286372416d28d1b7cee7 HEAD -- src/ tests/
git ls-remote --heads origin agent/runtime-workflow-architecture-v1
```

不在文件中自引用其尚未生成的提交哈希；最终交付回复列实际文档 SHA 和远端核验结果。
只显式暂存本轮候选文件，并以 SHA-256 清单核对实际 index 字节；
保留用户文档与并行测试修改，不使用 `git add -A`、reset、clean、强推、合并或发布。
这里的源码一致性比较是**已提交的交付 tree 与固定被测提交**；
不要求丢弃并行工作区修改来伪造 clean，也不声称未提交的并行测试已经过本轮验证。

## 7. 用户复验步骤

1. 在更新源码环境打开设备页，创建禁用空通道 Server，保留草稿并保存重开；不要连接现场设备。
2. 添加通道但不填 IP，从其他页签点“测试当前连接草稿”；确认中文提示、连接页与 IP 单元格焦点，草稿和磁盘未被测试应用。
3. 只改通道名称，确认 IP 错误仍在；补合法 IP 后错误消失。节点未选设备提交后，只选择其他槽不会清掉原错误，选择对应合法设备后原提示消失。
4. 测试 A 等待时切到 B，或修改端口；待旧成功／失败返回，确认不会冒充当前输入的结果，按钮和定时器正常恢复。
5. 选中需要的设备／绑定变更显式发布，运行／停止并查看状态日志；局部候选不带入未选连接参数或待绑定节点。
6. 导出并在新临时目录导入设备包、流程包、Runtime 包，再运行；第三方 selector default 的正式链由自动化临时插件验证，不改现场驱动。
