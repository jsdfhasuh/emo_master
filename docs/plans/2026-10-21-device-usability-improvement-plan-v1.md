# 设备管理易用性与草稿操作链路实施计划

- 日期：2026-10-21。
- 版本：v1.0。
- 状态：U01—U10 已实施，C1—C6 对应软件链路及原生 Windows Qt 自动化已通过；真实桌面关键路径已走查。硬件、人工完整 UI、冻结 EXE、Linux 和跨机器验证未执行，范围见本轮记录。
- 仓库：`jsdfhasuh/emo_master`。
- 目标分支：`agent/runtime-workflow-architecture-v1`。
- 实施审查基线：`756e526c1d2c657b3ff793fed7abe904d38b8d48`，编写时已核对远端分支。
- 易用性观察基线：`4550d46e34e508cbb2774f7bcda8863d293541e6`，对应用户提供的《EmoMaster 设备易用性完整总结 4550d46 20261021》压缩包。
- 建议仓库路径：`docs/plans/2026-10-21-device-usability-improvement-plan-v1.md`。
- 技术栈：Python 3.10、PySide2 5.15.2.1、Qt QWidget；沿用 Designer、Runtime、Worker 和现有三列设备管理窗口。

当前实施状态（2026-10-21）：最终被测源码为 `236d6008651a6f1bde5daebc999f83065555940a`，
tree 为 `ecdb1063bdd6da2f85f30cfd2fe0b4b97c514d08`。独立 `git archive` 快照实际执行
61 项 CHAIN、349 项 REGRESSION，全部通过；只保留既有 2 项旧迁移参数化分支排除。
23 个受改 Python 文件 Ruff、13 个源码文件 mypy、协议一致性和差异检查通过。
完成复核时发现 §8.2 的可选 selector 默认值被草稿模式一并移除，已保留首次失败、局部修正并重跑完整批次；
缺失的必需 selector 继续保持缺失，可选 selector 恢复既有默认值和校验。
[完成复核补记](../testing/2026-10-21-device-usability-completion-audit.md)记录此次修正与最终 410 项通过；
前次 `e2a9d86` 的 409 项作为历史交付证据保留。
[实施报告](../testing/2026-10-21-device-usability-improvement.md)提供 U01—U10、C1—C6 对照、
源码提交、首次失败、桌面走查和未执行范围；[原始证据](../evidence/2026-10-21-device-usability-improvement/README.md)
绑定上述固定源码。最终交付由包含本状态与报告的文档提交标识，可用
`git log -1 --format=%H -- docs/testing/2026-10-21-device-usability-improvement.md` 精确取得；
交付回复同时给出该提交的实际 SHA 和远端核验结果。

以下各节保留计划阶段的设计、顺序和完成条件；其中“编写时”“当前基线”指原审查时点，
不替代以上实际实施状态。原附件保留原样。

## 1 目标与范围

本轮完成设备管理的正常操作闭环：用户可以先建立暂不启用的 TCP 设备、先放置待绑定节点，之后补齐配置；编辑、恢复、保存、发布的含义一致；发布时看得懂具体设备改动；导入后的副本容易辨认；补齐配置后，仍通过正式 Runtime 和 Job 入口运行。

本轮覆盖附件 U01—U10。三个实施批次只是交付顺序，不把第三批功能留作未确定事项，也不以首批完成代替整体完成。继续复用设备定义、工程草稿、统一撤销历史、配置候选和发布事务；不另建草稿数据库、设备级历史或另一套发布机制。

### 1.1 基线变化和已有修复

编写时核对的提交关系为：

`4550d46 → cdd690065bd629d0294151cb0310965aa90b9c71 → 756e526c1d2c657b3ff793fed7abe904d38b8d48`。

`cdd6900` 已补齐单次 TCP 完成日志的有界结构化预览、原始字节计数和截断提示；`756e526c` 补入验证文档及证据。相对附件基线，产品源码只改动 `apps/runtime/devices/diagnostics.py` 与 `ui/communication_panel.py`，并增加两处测试；本计划涉及的创建、编辑、发布、绑定和包文件未在这两笔提交中变化。[S01][S02]

本轮将单次日志修复列为回归保护，不重复实现。既有报告记录的 59 项 CHAIN、219 项回归及 2 项既有排除是历史结果，不计作本轮验收。开始实现时再次核对 HEAD，保留后续已完成修复和无关并行改动。

### 1.2 对原 TCP 集成计划的明确调整

| 原计划决定 | 本轮决定 | 继续保留的执行边界 |
| --- | --- | --- |
| 设备窗口不增加发布入口，关闭后从 Runtime 菜单进入 | 设备窗口增加“查看并写入 Runtime…”快捷入口，由 Coordinator 转入同一个发布对话框 | 快捷入口不额外预选或直接写入，不替用户保存磁盘或确认影响；跨工程替换沿用原规则 |
| 有设备需求的节点必须完整绑定后才能创建 | 允许保存待绑定节点，包括缺设备槽和已选设备但缺必需 selector | Runtime 编译、发布候选和完整运行包仍要求绑定完整有效 |
| 保留正文整体滚动和“应用／放弃”按钮 | 保留三列布局，改为局部滚动、固定操作条；保留有效编辑自动应用并明确恢复边界 | 工程草稿、磁盘文件和在线配置仍相互独立 |

这些是根据实际桌面走查作出的产品调整，不能记成此前违反原计划。原 v1.5 对绑定与发布入口的要求见其第 1、8、9、11 节；实施时增加新计划覆盖说明，保留历史设计及验证证据。[S03]

### 1.3 本轮不扩展的能力

不扩展任意非法字段的持久化暂存、未知客户端动态接入、TCP 通配来源、旧工程迁移、跨独立 Runtime 的设备独占或新线程调度框架。真实硬件、冻结程序、跨机器部署、性能和多故障矩阵不新增为本轮门槛。现有停止、取消、一次连接失败、常规并发及合法局部发布属于应保持正常的操作。

## 2 架构与名称

### 2.1 沿用的职责

| 层次 | 名称与代码 | 本轮职责 |
| --- | --- | --- |
| 注册声明 | 设备类型注册描述符 `DeviceType` | 继续声明配置结构、能力、驱动与绑定选择器；可增加纯展示提示 |
| 工程数据 | 工程设备实例定义 `DeviceDefinition` | 保存启用状态、连接、工作参数和策略；disabled 空通道设备复用现有模型 |
| 节点引用 | `DeviceRequirement`、`DeviceBinding` | 用 slot、deviceId、selectors 表达已填绑定；缺项由声明与当前值派生 |
| 编辑与存储 | `ProjectEditSession`、`DeviceCatalogEditor` | 一个工程草稿、一次原子编辑、统一撤销／重做、保存与重开 |
| 发布 | `ProjectConfigurationDialog`、现有配置协调器 | 比较在线与草稿、选择原始变更路径、合成候选、校验、确认和显式提交 |
| 运行 | Runtime `DeviceManager` 与设备驱动 | 持有实际后端、连接和监听资源，执行准入和生命周期；UI 关闭不释放后端 |
| 观察 | 当前设备状态与诊断查询 | 展示真实在线状态和冻结归属的日志，不以草稿状态代替运行事实 |

本轮不改变“一台托管设备由 Runtime 持有对应后端”的关系，也不引入“每注册一种设备就分配一条线程”的承诺。持续监听由驱动按既有契约维护。同一普通设备的 FIFO 调度、不同设备独立调度、服务动作并发、资源独占和 Job 退出规则继续沿用。[S04][S05]

### 2.2 三类数据和两项持久状态

**表单当前输入**是尚未应用的控件内容，可能暂时无效；**工程草稿**是通过结构与已填内容校验的可编辑文档；**Runtime 在线配置**是经完整候选校验并显式提交的配置。

设备窗口固定区域分别显示：

| 信息 | 来源 | 展示规则 |
| --- | --- | --- |
| 当前输入 | 现有 formBaseline 与控件差异 | 未修改／有待应用输入／输入有误；不另建持久记录 |
| 工程文件 | `ProjectEditSession.dirty` | 已保存／有未保存修改 |
| 当前设备与 Runtime | 同一工程、同一 Runtime scope 下的有效状态与 definitionRevision | 尚未写入／与草稿一致／有配置差异／在线信息未获取或已失效 |

“已写入 Runtime，但文件尚未保存”是合法状态。发布使用当前 `session.payload()`，不能强制先 Ctrl+S，也不能顺手替用户保存。[S06][S07]

设备窗口的逐设备状态不冒充整工程比较结果。当前 `_showStates()` 对新增和删除的覆盖不足以代表全工程一致性；整工程差异继续由发布窗口的真实 `projectChanges()` 计算。读取失败或 scope 改变时显示未知／未获取，不能误报尚未发布或已一致。[S04][S07]

## 3 需求与完成条件

| 编号 | 当前正常操作障碍 | 本轮完成条件 | 主要实施节 |
| --- | --- | --- | --- |
| U01 | 新建 TCP 默认启用，未知客户端 IP 时无法先留设备 | 两个新建入口均可保存暂不启用、无通道的合法设备，后续补齐启用 | §4 |
| U02 | 创建后不知道如何保存、生效，连接按钮灰色缺少下一步 | 独立状态明确；可从设备页进入唯一的既有发布窗口并返回 | §6 |
| U03 | 发布主表是内部路径与 JSON，校验后行高膨胀 | 主表展示对象、改动和影响，保留原始选择粒度及依赖校验 | §7 |
| U04 | 自动应用与“应用／放弃”含义不清，恢复入口滚出首屏 | 自动应用规则可见；恢复当前输入与工程撤销明确；名称同步 | §5 |
| U05 | 空 IP、空相机标识、连接失败暴露原始异常 | 中文字段反馈、准确定位、可操作建议与原始详情 | §4、§9 |
| U06 | 设备未准备好时不能先搭流程 | 待设备／待通道节点可保存、重开、随流程包流转，补齐后运行 | §8 |
| U07 | 导入后同名副本难区分 | 冲突副本有可辨名称，保持新 ID、默认禁用、不覆盖和原子导入 | §10 |
| U08 | 默认暴露大量高级项、长类型 ID 和英文枚举 | 描述符驱动基本／高级分组，折叠不丢值、不漏校验 | §9 |
| U09 | 新建测试丢失实际测试边界，旧错误残留 | 共用真实测试回执；更正后清除对应过期反馈，异步结果守住 scope | §9 |
| U10 | 两个“更多”用途不清楚 | 列表操作与设备维护明确命名，删除与维护继续走原处理入口 | §5 |

## 4 TCP 设备分阶段创建

### 4.1 确定的交互

在共用 `DeviceDefinitionDialog` 增加可见的启用选择。TCP 服务提供清楚说明：“暂不启用时，可以先保存设备，稍后配置客户端通道”。原有正常创建路径仍可直接填写完整配置并启用。[S08]

1. 名称、监听地址、端口使用现有字段和合法默认值。
2. 用户选择暂不启用，`channels=[]` 时允许创建、应用、保存及重开。
3. 显示“未启用；待配置客户端通道”。不启动服务，不伪造来源 IP 或默认通道。
4. 用户启用时，没有通道则定位通道区域并提示“请添加至少一个客户端通道”。
5. 每个已经存在的通道行仍需名称、稳定 channelId 和有效 allowedIps。空 IP、重复路由、非法地址保留输入并定位到具体行列。
6. 补齐后应用到工程草稿，再显式写入 Runtime；实际启动继续由当前发布定义和启动策略决定。

空通道禁用设备本来是合法 `DeviceDefinition`，也允许作为禁用配置同步到 Runtime。不要新增“禁用设备必须先配齐通道才能同步”的限制。缺少通道的禁用配置与填写了坏通道行的配置不同：本轮只放行前者。[S09]

### 4.2 实施细节

- `device_wizard.py::DeviceDefinitionDialog.definition()` 把 enabled 显式传入共用定义校验，不依赖 `DeviceDefinition.enabled=True` 的默认值。保留初始值和已有 metadata，不因加控件重置策略。
- 设备管理和节点向导中的“新建设备”继续使用同一对话框与校验函数。
- `tcp_device_form.py` 保留 `setSchema/getValues/validationMessage/focusField/setFieldEnabled` 契约；错误定位扩展到通道行及 allowedIps 单元格。
- `core/project/communication.py` 的路由和启用规则原则上无需修改；Runtime 的 disabled 准入保护继续复用。
- 测试连接继续先校验候选定义。选择禁用且无通道的合法候选后，可按既有端点测试能力检查本机 bind；不为测试而临时启用设备，不让测试应用草稿通道。
- 暂存半填表格到磁盘不在本轮范围；用户可以补全、删除未完成行或恢复当前输入，退出路径必须可见。

监听地址决定本机绑定端点，allowedIps 决定来源到 channelId 的映射。保持稳定通道 ID、跨通道来源唯一和同通道同时连接规则，不把空 IP 改成通配符。[S09][S10]

## 5 编辑、恢复、撤销与窗口布局

### 5.1 采用一种明确的编辑模型

继续保留切换设备、切换分类／搜索、关闭窗口时自动应用有效编辑的行为。一次有效编辑仍是一次 `session.transaction()`。非法输入留在原控件，不应用、不覆盖已保存草稿。[S04][S06][S11]

固定说明：

> 切换设备或关闭窗口时，有效修改会保留到工程草稿。保存工程文件和写入 Runtime 需分别执行。

固定操作条使用以下名称：

| 操作 | 精确含义 |
| --- | --- |
| 立即应用到草稿 | 立即执行现有 commitPending／sync，不保存文件、不写在线 |
| 恢复当前输入 | 放弃本次尚未应用的控件输入，恢复当前工程草稿中的值 |
| 撤销工程修改／重做工程修改 | 调用统一工程历史，作用于上一次工程事务，不伪装为当前设备专属历史 |
| 查看并写入 Runtime… | 转入 §6 的统一发布窗口 |
| 保留草稿并关闭 | 校验并保留有效编辑后关闭观察窗口 |

“恢复当前输入”在无效输入时必须可见可用。工程撤销保留现有先同步的规则：有效当前输入先进入历史，再撤销该事务；无效当前输入先修正或恢复。沿用现有运行中历史操作限制，不新增强制停止全部 Job 的流程，不覆盖文本控件自身的编辑撤销。

历史恢复完成后，统一刷新 `original`、`formBaseline` 和控件，避免随后切换或关闭把撤销前的旧值重新写回。自动应用成功同步左树、中表、详情标题；修复附件图 27 的改名后列表仍显示旧名问题。[S04][S06][S12]

### 5.2 保留三列，改为局部滚动

- 顶部新建／导入／导出、搜索和简短状态固定；左树、中表使用自身滚动。
- 右侧继续保留基本信息、连接、工作参数、策略，以及节点引用、在线状态、诊断日志。
- 参数正文与信息正文分别滚动；参数操作条和关闭／发布入口固定可见。
- 调整 QWidget、QSplitter 和 QScrollArea 的 sizePolicy/minimumSize，使常用 1200×760 窗口下能直接找到恢复与发布入口；不新建另一套设备工作区。
- 延续当前两秒刷新与 scope 检查。刷新仅更新在线区域，不重建正在编辑的参数控件，不抢焦点、不重置滚动。

### 5.3 菜单归属

列表级“更多”改为“设备列表操作”，设备详情级“更多”改为“设备维护”。所选设备删除可提供明确入口，但必须调用既有 `deleteDevice()`，保留只改草稿的说明、引用保护和确认。暂停设备、恢复准入、恢复会话继续使用原语义和 availableControls，不能合并成含义不清的“重启”。

## 6 从设备管理进入现有 Runtime 发布流程

### 6.1 窗口转换必须由 Coordinator 编排

`DeviceWorkspace` 为主窗口所属的 `WindowModal` 对话框，`configureRuntime()` 也创建主窗口所属对话框。设备窗口仍显示时直接叠加 `exec_()` 会造成模态归属和焦点问题，因此采用以下顺序。[S04][S12]

1. 用户点击“查看并写入 Runtime…”，在设备窗口仍可见时执行现有同步和输入校验。
2. 无效输入：留在设备窗口，展开分组、选中页签、滚动并聚焦错误字段；不打开发布页。
3. 有效输入：记录工程／Runtime scope、选中 deviceId 和必要的视图位置，通过现有关闭流程完成设备窗口观察清理。
4. 设备窗口不再显示后，再排队调用既有 `PageCoordinator.configureRuntime()`；发布窗口仍归主窗口。
5. 快捷入口仅进入比较／校验，不调用写入 RPC，不额外替同工程变更预选条目或“停止任务／重连”，不跳过影响确认。跨工程替换沿用原有全量候选、stopAffected 默认值和显式确认，不在本轮改动该规则。
6. 取消、校验失败或完成后关闭发布窗口，若原工程和 Runtime scope 仍有效，则恢复设备窗口、选中设备并刷新状态；scope 已变化则不重新拉起旧设备窗口。
7. 已有测试／控制请求未完成时暂不可转换，完成后恢复；不能假定 `DeviceWorkspace.done()` 已检查 pending。转换过程中暂时禁用重复入口，不能重复打开发布窗口。

关闭设备观察窗口不能 disconnect/dispose 驱动，不能打断已有 TCP listener 或 Job。无需为窗口转换建立后台任务平台。

### 6.2 状态与反馈

- 新建后即时提示“已加入工程草稿，尚未写入 Runtime”；禁用设备另说明暂不启用。
- 连接按钮旁显示真实不可用原因。尚未发布引导到快捷入口，禁用状态引导到启用配置，在线信息失效引导刷新。
- 区分持续状态、最后操作结果和字段错误。普通状态刷新不覆盖未解决的输入错误。
- “在线状态已读取；工程草稿未改变”改为单纯的刷新结果，不长期冒充工程当前是否修改。
- 已有 COMMITTED 后连接失败继续显示“配置已提交、设备连接未成功”；不得把它当未提交重新初始化或自动重放。

## 7 发布界面展示设备改动

### 7.1 展示规则

保留每条现有 `projectChanges()` 差异对应的原始 path 和选择单元，只增加展示摘要。[S07][S13]

| 主视图 | 示例 |
| --- | --- |
| 对象名称与类型 | `loopbackserver · TCP 常驻服务` |
| 变更动作 | 新增设备／修改参数／删除设备／变更绑定 |
| 字段及摘要 | `名称：模拟相机 → camera2`、`监听端口：10000 → 10002` |
| 生效方式与影响 | 使用当前校验结果，显示空闲后生效、影响设备／流程／Job 等 |

新增对象取草稿名称，删除对象取在线名称，改名同时展示旧名与新名。注册元数据不可用时退回现有名称、字段键和类型 ID，不能因无法美化而阻断比较。

完整 JSON、内部路径、deviceId 放详情；主表限制行高并省略长文本。校验后更新影响摘要，不再让 JSON 换行把整张主表撑高。首次写入的 `_showInitial()` 与后续 `_showSnapshot()` 都要可读；流程、资源、页面等非设备变更继续正常展示。

可增加“全选本次新增”按钮，选择范围必须是主表中明确展示的新增项。不要把同一设备的一个参数选择扩成整设备覆盖。TCP 通道数组维持现有差异粒度，不制造协议不识别的伪子路径。

### 7.2 提交语义保持

- 原 path 继续保存在 Qt data role，不能从显示文本反推。
- 保留 `requiredProjectChanges()`、`selectedCandidate()`、依赖补选和确认。
- 校验后修改选择或替换策略，继续使旧校验结果失效。
- 同一设备只发布工作参数时，未选择的连接草稿不得被带入。
- 设备删除与节点改绑必须形成合法候选，保留 workflow/node/slot/selector 定位。
- Designer 中另有待绑定节点时，合法的无关选中发布继续允许；一旦选择该节点使候选不完整，Runtime 编译必须拒绝，在线版本保持不变。

原则上不修改 `core/project/configuration.py` 的差异与事务协议，本轮主要在现有窗口增加展示适配。

## 8 待绑定节点草稿

### 8.1 确定的数据表达

节点绑定窗口保留严格“完成”动作，增加“保存待绑定节点”。创建与重新编辑都使用相同规则；取消仍不提交任何节点、新设备或资源变更。[S08][S11]

| 用户当前进度 | 草稿表达 | 画布提示 |
| --- | --- | --- |
| 尚未选择 camera 设备 | 不写 camera slot，不生成空 deviceId | 待选择设备：camera |
| 已选 TCP Server，尚无 channelId | 保留该 slot 的 deviceId，selectors 不写 channelId | 待选择通道：tcp.channelId |
| 部分 slot 已完成 | 保留完整 slot，其余缺项按上两种表达 | 列出尚缺 slot 或 selector |
| 全部绑定完整有效 | 使用现有 DeviceBinding | 不显示待绑定提示 |

这能保证在节点向导中新建 disabled、无通道 Server 后，节点保留该 deviceId，新设备仍被引用；现有 `newDefinitions` 的 used 过滤与原子提交可以沿用，不会因“稍后绑定”丢掉刚创建的 Server，也无需放开保存任意未引用临时设备的保护。

待绑定提示从当前设备需求和真实绑定派生，复用画布 `summaryLines` 等展示，不保存 `isIncomplete/ready` 等第二套状态。注册元数据尚未获取时显示“绑定状态待检查”，不凭空认定完整。

### 8.2 只放行缺项，继续检查已经填写的内容

| Designer 草稿允许 | Designer 草稿仍需拒绝或要求显式修正 |
| --- | --- |
| 缺必需 slot | 未声明的 slot |
| 已选合法设备，缺必需 selector | 不存在的 deviceId、类型／能力不匹配 |
| disabled Server 无通道 | 已填写但不存在、类型错误的 channelId／第三方 selector |
| 未绑定节点保留合法动作参数和图位置 | 无设备槽却遗留该槽的 overrides/valueBindings |
| 已合法完成的其他 slot | 该设备槽中不存在或类型不兼容的变量、输入来源及无效资源引用 |

`DeviceCatalogEditor.commitNode/_validateNode` 增加明确的 Designer 草稿完整性选项；严格路径默认保持。共享 `validateDeviceBinding()` 如需扩展，采用默认严格的“允许缺少 selector”关键字，草稿调用仅放宽缺键，仍检查所有已经提供的键和值。必需 selector 缺失时保留缺失状态，不用默认值代填；可选 selector 的既有缺省规则保持。Compiler、Runtime 装载／发布、完整运行包继续使用严格默认值。本节的来源校验作用于设备槽，不顺带收紧普通算子表单的其他草稿规则。[S11][S14][S15]

`DeviceCatalogEditor.put()` 对引用节点复核时也要使用草稿规则，否则修改某个合法设备，会因为同一节点的另一个 slot 尚未配置而被阻止。与此同时，删除已引用通道或引入错误来源仍需拒绝并定位，不能以草稿模式忽略。

### 8.3 防止丢失选择和误触发旧迁移

- 原 deviceId 或 channelId 已失效时，显示“不可用：原 ID”占位，保留原值。不能自动选第一项，也不能转成空值后被“稍后绑定”吞掉。
- 真正未填写的 selector 提供明确的空选择；用户主动选择后才写入真实值。
- 用户明确解除已有绑定时，说明该 slot 的参数来源处理；需要删除的 overrides/valueBindings 与解除操作在同一事务完成，不静默删除其他 slot 或资源。
- 未选设备时显示“选择设备后可配置继承与覆盖”；不复制虚假的实例默认值。选定设备后继续复用 `SourceEditor`，明确的 `0`、`False`、变量和输入来源保持原语义。
- `main_window.py` 不能继续用 `migrateLegacy=not node.deviceBindings` 判定旧节点。新待绑定节点重开属于当前编辑路径；旧迁移仅保留已有显式入口或真实旧格式判定。
- 新建节点、引用的新设备、资源计划仍在一次 `ProjectEditSession.transaction()` 中提交；正常取消和校验失败完整回退。

### 8.4 保存、包、发布与运行

现有 `WorkflowNode.deviceBindings`、`DeviceBinding.selectors` 和 `ProjectEditSession` 已能表达上述草稿，不升级工程 schema，不新建存储格式。[S06][S16]

| 链路 | 本轮要求 |
| --- | --- |
| 保存／重开／另存工程 | 待绑定状态、已填引用、位置、端口、参数来源保持，重新打开不进入旧迁移 |
| 流程包作为编辑模板 | 允许缺 slot／缺 selector，按实际 bindings 收集设备依赖并重映射；导入后重新派生待配置提示 |
| 流程包复用现有设备 | 沿用现有兼容判断；无法严格确认兼容的待配置依赖可选择新建禁用副本，不自动匹配或补通道 |
| 完整 Runtime 包 | 保持可执行校验；含待绑定节点时拒绝并定位，补齐后再构建 |
| 首次写入 | 整份候选必须完整，缺项拒绝且不产生连接／监听 |
| 后续选中发布 | 校验在线加所选变更合成的候选，不把整个未选草稿误带入 |
| 正式运行 | 不能绕过编译执行不完整候选；已有合法在线工程继续按现有明确运行入口使用 |

不要因为本地存在未绑定草稿就全局禁止运行在线版本或禁止无关合法局部发布。也不要把流程模板可保存误写成完整运行包已可部署。[S13][S15][S17][S18]

## 9 通用表单、错误与测试反馈

### 9.1 用描述符控制基本与高级显示

保留连接／工作参数／策略页签，在设备配置表单中支持字段级 `xAdvanced: true` 展示提示；未声明的第三方字段默认可见。复用 `title/description/xUnit/xExample/xOptionLabels`，不增加 DeviceType 顶层接口或驱动方法。[S14][S19]

| 类型 | 默认可见的主要信息 | 可折叠的主要信息 |
| --- | --- | --- |
| TCP Server | 监听地址、端口、通道、常用等待和发送参数、启动方式 | 报文容量、队列／发送时限等较少修改项 |
| TCP 单次客户端／接收 | 端点、分帧、常用超时、编码；fixedLength 对应长度；接收的 ACK 设置 | 容量上限、尾随数据、半关闭等细节 |
| SLMP PLC | 主机、端口、通信格式 | 网络号、PC 号、模块 I/O、站号、监视定时器及恢复时限 |
| 模拟相机／IMV | 标识、常用采集／曝光／增益／触发项 | 容量、较少修改的算法和流检查项；沿用既有条件显示 |

中文枚举保存原始值，例如“按换行分帧（newline）”；不修改 enum、required、默认值或参数范围。展示“可使用默认值”，突出确需用户补填的字段，避免把所有有默认值的字段都表现为必须逐项重新决定。

设备类型选择以 displayName 为主，deviceTypeId 存 item data／详情。同名类型用版本或短 ID 区分；移除依赖 `labels.index(label)` 的显示名唯一假设。

### 9.2 折叠不得影响值或校验

1. 折叠只改变显示，不 disable 控件；现有 `validationMessage()` 会跳过 disabled 控件。
2. 保留同一份 schema、控件字典和 `getValues()`；不拆成会相互覆盖的基本／高级数据，不在开关时重建控件。
3. `xAdvanced` 不等同于 `xHidden`，保持后者对未提供字段的既有省略语义。
4. `_updateDependentFields()` 分开计算业务适用性与高级区域展开状态。折叠不可见不能被用于判断业务 enabled。
5. 校验错误先展开高级区、切换外层页签、滚动到字段，最后聚焦真实输入控件；通道错误定位单元格。
6. 高级分组只在设备配置显示模式启用。`SourceEditor` 复制字段生成常量覆写表单时保持直接可见，不能每个值都多套高级开关。

内置描述符新增展示元数据后，继续使用现有描述符版本／摘要依赖规则生成本轮包；不绕过设备依赖校验。历史包兼容不作为本轮新增工作。

### 9.3 中文校验和原始详情

复用 `WorkflowCompileError.issues[].fieldPath`、Pydantic `errors()[].loc` 与 schema title，生成设备表单的字段反馈：

- 空客户端 IP：“通道 1：请填写至少一个允许接入的客户端 IPv4”。
- 空 IMV 标识：“请填写 SDK 设备唯一标识，或通过设备发现选择”。
- 已知连接拒绝：“无法连接到目标地址和端口，请检查设备端服务与端口设置”；原始异常放详情。
- 未知插件错误：“设备配置校验未通过，请查看详情”，不猜测原因。

保留错误 code 和技术详情供排障。字段变更只清除对应过期错误；选择设备后清除该 slot 的“请选择设备”提示，不清掉仍有效的其他问题。

通用 `SchemaParamForm._valueError()` 有意允许资源／变量的未完成字符串草稿，因此不能全局新增 minLength/pattern 阻断。设备完整性通过现有设备校验适配或明确的设备表单严格模式实现，普通算子表单语义保持。[S19]

### 9.4 共用实际测试结果

新建窗口 `setupReady()` 与设备详情共用返回的 `result.message`，保留既有后端测试行为，不重新实现网络测试：[S08][S20]

| 类型／操作 | 展示的能力边界 |
| --- | --- |
| TCP Server／receive_once 端点测试 | 本地监听端点可用；未验证外部客户端；Server 测试不应用草稿通道 |
| TCP client_once 连接测试 | 远端连接可用；未发送业务报文 |
| 相机／PLC 测试 | 按已有结果显示检查范围，不把连接成功称作采集或 PLC 读写成功 |
| 正式 Job 业务动作 | 依据实际动作结果显示业务完成，与连接测试分开 |

测试前说明也应准确，不能把本地 bind 一律写成“连接远端设备”。新建页没有完整结果时使用明确的通用兜底。所有异步反馈继续检查工程、Runtime 和设备 scope，旧请求不能覆盖新对象的提示。

## 10 导入副本辨识与包一致性

设备包的既有校验、资源安装、ID 重映射和事务回滚全部复用；在同一导入事务中给新设备分配可辨识显示名。[S21]

命名规则：无冲突保留原名；冲突时依次使用“camera2（导入副本）”“camera2（导入副本 2）”。名称集合包含既有设备和本批已分配名称；遵守 160 字符限制，为后缀预留长度。不修改原始包对象，不改变 deviceTypeId、deviceId 映射、现场字段、资源目标和默认禁用行为。

流程包中选择“新建禁用副本”的路径可复用同一命名函数；选择复用已有设备时不得改名。在同一新工程部署完整运行包时保留包内名称，不把完整工程安装改成逐设备加副本后缀。[S17][S18]

导入完成后选中新条目，显示本次新增名称与待填写字段的友好标题。删除测试副本继续遵守引用保护；一次工程撤销对应整个导入事务，不产生半套设备和资源。

## 11 代码落点与修改边界

| 文件或区域 | 主要修改 | 明确保留 |
| --- | --- | --- |
| `apps/designer/ui/device_wizard.py` | 启用控件、待绑定完成动作、失效值占位、共用反馈、类型选择数据 | 取消原子性、参数来源、测试 RPC、严格完成入口 |
| `apps/designer/ui/tcp_device_form.py` | 通道行列错误定位、中文缺项反馈 | 共用表单契约与核心 TCP 校验 |
| `apps/designer/ui/device_workspace.py` | 固定布局、恢复与历史入口、发布意图、状态提示、名称同步、菜单 | 设备 controls、scope、观察生命周期、引用保护 |
| `apps/designer/page_designer/coordinator.py` | 模态窗口转换与返回、共用历史刷新 | 现有 session、configureRuntime、sync、history |
| `apps/designer/ui/project_configuration.py` | 首次／后续发布摘要、详情区、有限行高 | 原始 path、候选、依赖、校验失效、提交结果 |
| `apps/designer/state/device_catalog.py` | Designer 草稿缺项校验、引用节点复核 | 一次事务、已填字段合法性、身份与资源保护 |
| `core/plugin/devices.py` | 如需要，默认严格的缺 selector 草稿选项 | 描述符协议、类型能力校验、Runtime 严格默认 |
| `apps/designer/ui/main_window.py` 与现有画布视图模型 | 待绑定标记、当前节点重开与旧迁移分流 | nodeId、端口、位置、连线及运行入口 |
| `apps/designer/ui/param_form.py` 与内置 `device_types/*.json` | 设备显示模式的高级折叠、枚举文案、错误展开 | 完整取值、业务条件、通用草稿校验默认 |
| `apps/designer/state/device_package.py`、必要的 `workflow_package.py` 路径 | 副本名；确认待绑定模板重映射 | 新 ID、默认禁用、资源安装与回滚 |
| `ui/communication_panel.py::TcpChannelEditor` | 仅必要的通道单元格定位 | 新基线的 DeviceDiagnosticsView／recordDetails 日志修复 |
| Compiler、Runtime 配置、运行包模块 | 原则上复用，补相关边界回归 | 完整绑定校验、在线事务、资源生命周期、JSONL 单写 |

若实现中需要新增函数，优先在上述现有模块抽取小型展示或校验辅助，不创建第二套设备工作区、发布服务、草稿框架或错误协议。

## 12 实施顺序

### 批次一 创建与编辑恢复

完成 U01、U04、U05、U09 的共用反馈及 U10：新建暂不启用、固定操作条、局部滚动、统一应用／恢复／历史、名称同步和测试结果文案。先确定与后续高级分组共用的字段定位接口。

### 批次二 发布引导与可读性

完成 U02、U03：独立保存／在线状态、设备页到统一发布窗口的安全转换、首次和后续设备摘要、原 path 选择、校验与影响确认。验证合法局部发布仍不带入无关草稿。

### 批次三 待绑定流程与配置便利性

完成 U06、U07、U08：草稿缺 slot／selector、画布标记、重开与迁移分流、包重映射、副本名、高级分组。将三批连成 §13 的完整链路，统一指南与交付状态。

每批提交范围清楚；代码与最终验证证据可分提交。文档提交若声称与被测源码一致，应核对 `src/`、`tests/` 未再变化。实现者不回退其他工作区改动，不把旧报告的通过数搬作新结果。

## 13 六条正常工程链路验收

以下是六条连续场景，尽量复用同一个最小工程、模拟相机和 localhost 端点。它们是验收组织方式，不要求为每条另建 runner，也不扩成设备类型、窗口尺寸、故障组合的笛卡尔积。

### C1 分阶段 TCP 创建与表单校验

设备管理中新建禁用空通道 Server，保存并重开；确认无监听。尝试启用得到中文缺通道反馈；添加空 IP 行时准确定位；补齐 localhost 来源后应用。基本／高级切换不丢已有非默认参数，折叠区域错误能自动展开。新建页端点测试与设备页显示同一实际能力边界。

覆盖 U01、U05、U08、U09。节点向导中新建无通道 Server 的组合并入 C5，不重复建立完整场景。

### C2 编辑、恢复与统一历史

改名后切换设备，确认自动进入草稿且三处名称一致；撤销／重做恢复对应版本，随后切换或关闭不把旧控件值写回。输入空名称或非法参数，确认输入保留，固定“恢复当前输入”能退出错误状态；菜单归属明确。

覆盖 U04、U05、U10。无待应用输入时不产生空历史事务，取消不新增确认链。

### C3 从设备页发布并实际运行

无效输入点击发布快捷入口仍留原窗口并聚焦；有效输入转入唯一的发布窗口。首次写入和后续多设备差异均显示可读对象摘要，校验后主表不被 JSON 撑高；取消和完成均能返回设备管理，已有监听不因关闭观察窗口停止。

采用一次“修改后未保存文件就发布”的正常路径，确认在线更新而文件仍待保存。通过正式运行入口执行模拟相机采集和一笔 localhost TCP 业务收发，校验实际图像／业务字节与设备日志；不能仅凭连接状态判定运行成功。

覆盖 U02、U03、U05、U08、U09及正式运行链路。

### C4 有未完成草稿时的合法局部发布

已有合法在线工程上，同时修改一个设备工作参数、保留该设备合法但未发布的连接草稿，并新增一个未绑定节点。只选择合法参数差异，验证候选未包含未选连接值和未绑定节点，发布成功。再选择不完整节点，要求定位 workflow/node/slot／selector 并在提交前拒绝；草稿和在线版本均保持可恢复。

覆盖 U02、U03、U06。复用已有依赖补选及 COMMITTED 后连接失败回归，不新增第二套发布异常矩阵。

### C5 待绑定节点到补齐执行

放置模拟相机节点并保存待绑定，重开、移动及重新编辑正常，不进入旧节点迁移；补绑后保留原节点 ID、端口、连线和参数来源。

在 TCP 节点向导中新建禁用空通道 Server，保留 deviceId、缺 channelId，保存待绑定；确认设备和节点同一事务保存，关闭重开仍显示待通道。补齐设备通道、显式启用设备并选定真实 channelId，将设备启用状态与节点绑定一并纳入合法发布候选；发布成功后通过正式 Job 运行。

使用少量直接反例区分“缺项”与“错值”：失效 deviceId／channelId、错误类型或参数来源不能被“稍后绑定”自动清空；部分绑定节点的其他缺槽不能阻止合法设备编辑。取消保持原子性。

覆盖 U01、U06、U09。

### C6 包流转与独立运行

设备包连续导入两次，副本名称可区分、ID 唯一、默认禁用且不覆盖；一次撤销回退一次导入。流程模板中的待绑定节点导入后仍可继续编辑，真实绑定与资源按现有映射保留。完整 Runtime 包在缺绑定时准确拒绝，补齐后正常构建。

复用已有三类包夹具，在新目录／隔离 Runtime 中导入有效配置，补现场字段和启用状态后通过正式 Job 运行，核对实际业务结果。无引用测试副本可删除，有引用设备继续受保护。

覆盖 U07、U06以及保存、依赖、导出导入到运行的完整链路。

### 13.1 自动化与桌面证据

优先扩展现有测试：

- `tests/designer/test_device_definition_feedback.py`、`test_device_workspace_usability.py`、`test_device_workspace_sizing.py`、`test_device_workspace.py`。
- `tests/designer/test_device_catalog.py`、`test_device_project_open.py`、`test_device_finish_ui.py`。
- `tests/designer/test_project_configuration_ui.py`、`test_device_plan_completion_ui.py`。
- `tests/designer/test_device_package.py`、`test_workflow_package_devices.py`、`test_workflow_package_resources.py`。
- `tests/runtime/test_tcp_device_integration.py` 及现有配置、绑定、反馈相关回归。

新增自动化重点检查事务、值保留、缺项与错误的区别、实际 Qt 焦点、模态窗口转换、选择候选和实际运行；纯菜单改名和文字整理通过桌面走查确认，不增加只镜像实现的测试。相关功能开发完成后执行一次受影响回归；已有 A1—A6 和新基线日志用例可合并到同一次最终验证，不要求重复跑独立专项。

使用实际 PySide2/Qt 平台插件确认窗口交互；补一次真实桌面关键路径走查，以少量截图记录首屏恢复入口、缺项定位、发布摘要、待绑定节点、导入副本和运行结果。若实际环境无法完成某层验证，清楚标记该层待验，不能把 offscreen、mock RPC 或源码测试说成完整桌面验收。

真实相机、PLC、冻结 EXE、跨机器和压力测试仅记录实际执行情况；没有条件时写 NOT_RUN，不新增为本次门槛。

### 13.2 既有行为保护

沿用现有用例保护普通设备 FIFO、服务后端生命周期、停止取消、参数继承、来源／selector 校验、局部发布、COMMITTED 后连接结果，以及 R01—R03 的周期身份、错误定位和多 owner 展示。保留 `cdd6900` 已修的单次 512／1025 字节结构化预览、准确截断、缓存上限和 JSONL 唯一写入；不重复实现该修复，不更改底层通信协议。

## 14 文档与交付

### 14.1 同步更新的文档

| 文档 | 更新内容 |
| --- | --- |
| 本计划 | U01—U10 的实际实施与验收状态、固定源码及最终交付 SHA |
| 原 `2026-10-20-tcp-device-integration-plan-v1.md` | 在当前状态区链接本计划，标明设备页发布入口、待绑定草稿、布局和操作文案由本计划覆盖；不改写历史证据 |
| `docs/guides/device-catalog-contract-v1.md` | 草稿与执行校验、缺 selector、参数来源和模板包边界 |
| `docs/guides/device-dispatch-online-configuration.md` | 自动应用／恢复／工程撤销、文件与在线独立、快捷发布和局部候选 |
| `docs/guides/project-tcp-service.md` | 禁用空通道草稿、来源 IP 路由、端点测试含义、补齐后发布运行 |
| 本轮测试记录 | 六条链路、原生 Qt／桌面／实际 Runtime 的证据层次与未运行范围 |

建议测试报告为 `docs/testing/2026-10-21-device-usability-improvement.md`，原始证据放独立 `docs/evidence/2026-10-21-device-usability-improvement/`。若实施日期变化，使用实际日期，不覆盖附件和已有收尾目录。

### 14.2 交付记录

交付包含实际被测源码 SHA、最终交付 SHA、U01—U10 到代码／验收的映射、首次失败及修复后的必要原始输出、最小工程与包、少量关键截图、实际测试数量与 NOT_RUN。保留首次失败，不择绿，不删除有效断言，也不把旧通过数或未执行记为本轮通过。

原计划编写阶段只做需求和代码链路审查，没有实施产品功能或运行验收。
后续实施及实际结果已补在首页状态和本轮报告，不将原审查记录改写成执行证据。

## 15 完成判定

全部 U01—U10 的行为完成，C1—C6 对应证据齐备，已有核心链路未回归，即可交付本轮。完成意味着用户能够从空白工程开始，先留合法设备／节点草稿，正常恢复编辑，明确选择在线改动，补齐配置并执行，最后通过包流转再次运行。

不以增加新的“必须提前知道所有设备信息”限制换取测试通过；不以取消完整运行校验换取草稿可保存。仍未执行的证据层次或未解决问题应单独列明，不隐藏在“全部完成”的表述中。

## 16 固定代码依据

下列代码链接固定到实施审查基线 `756e526c`。U01—U10 的操作现象来自用户提供的 `4550d46` 易用性附件，其中两份文字报告、40 张截图、实际工程和设备包互相对应；附件的 43 项原证据和 51 项完整包清单已核验。附件证明配置与连接操作，不单独证明业务运行。

| 编号 | 固定依据 |
| --- | --- |
| S01 | [4550d46 到 756e526c 的提交比较](https://github.com/jsdfhasuh/emo_master/compare/4550d46e34e508cbb2774f7bcda8863d293541e6...756e526c1d2c657b3ff793fed7abe904d38b8d48) |
| S02 | [单次 TCP 预览修复及历史验证](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/docs/testing/2026-10-21-tcp-once-log-preview.md) |
| S03 | [原 TCP 设备集成 v1.5 计划](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/docs/plans/2026-10-20-tcp-device-integration-plan-v1.md) |
| S04 | [DeviceWorkspace](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/ui/device_workspace.py) |
| S05 | [Runtime DeviceManager](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/runtime/devices/manager.py) |
| S06 | [ProjectEditSession](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/state/project_edit_session.py) |
| S07 | [ProjectConfigurationDialog](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/ui/project_configuration.py) |
| S08 | [设备定义和节点绑定向导](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/ui/device_wizard.py) |
| S09 | [TCP 配置与启用规则](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/project/communication.py) |
| S10 | [TCP 服务来源路由](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/runtime/communication/manager.py) |
| S11 | [DeviceCatalogEditor](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/state/device_catalog.py) |
| S12 | [PageCoordinator](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/page_designer/coordinator.py) |
| S13 | [配置差异与选中候选](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/project/configuration.py) |
| S14 | [设备描述符与绑定校验](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/plugin/devices.py) |
| S15 | [WorkflowCompiler](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/workflow/compiler.py) |
| S16 | [工程模型](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/project/models.py)与[设备绑定数据](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/project/devices.py) |
| S17 | [工作流包](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/state/workflow_package.py) |
| S18 | [Runtime 包](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/core/project/runtime_package.py) |
| S19 | [SchemaParamForm](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/ui/param_form.py) |
| S20 | [TCP 驱动端点测试](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/runtime/devices/tcp.py) |
| S21 | [设备包导入事务](https://github.com/jsdfhasuh/emo_master/blob/756e526c1d2c657b3ff793fed7abe904d38b8d48/src/emo_master/apps/designer/state/device_package.py) |
