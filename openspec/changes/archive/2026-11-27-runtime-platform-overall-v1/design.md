# Design：Runtime 总体架构、Operator 体验与最小日志共享（R3）

## 1. 基线与交付边界

- 仓库：`jsdfhasuh/emo_master`
- 固定基线：`48b30191884ad5ef5d96e8d26591104d16d54a58`；2026-11-27 复核远端 main 仍为此提交。
- 技术栈：Python 3.10、PySide2 / Qt Widgets，沿用现有 Runtime RPC。
- 本包为计划修订，不包含产品代码实施。实际包校验见 `validation.md`；产品测试、原生 Qt 和设备验证独立记录。

实施前对照当前 HEAD、相关 diff 和工作区修改。基线变化时只更新受影响映射，不覆盖已有工作。

## 2. Runtime 总体职责保持

| 层次 | 现有职责 | 本期处理 |
| --- | --- | --- |
| Operator / ProductionRuntime | 本机宿主、工程入口、运行控制和工具页 | 视觉、日志接线、正常操作主链 |
| RuntimeService / ProjectConfig | 正式配置、校验、提交与资源准备 | 保留 owner 和发布规则，有直接影响才定向修复/回归 |
| JobSupervisor / WorkflowRunner | Job、Worker、工作流、循环、Dispatch/Join、后台任务 | 沿用执行与并发语义，不重建调度器 |
| DeviceManager / Communication | 设备 owner、代次、允许动作、串行访问、TCP 与诊断 | 复用状态/动作，保留身份范围 |
| PresentationService / DisplaySession | 实时页面、图像、历史与观察资源 | 保留工程页面与真实 Job 来源 |
| EventStore / 日志读取与导出 | 持久化事件、JSONL 投影、查询与导出 | 复用事实来源和写入责任 |
| Designer / Web / 其他客户端 | 设计、观察或其既有管理入口 | 共享后保持职责，不扩大为全功能对齐 |

Runtime 是执行与运行资源的权威 owner。关闭观察客户端只释放自身会话；Operator 明确关闭它拥有的 Runtime 时沿用 `ProductionRuntime.close()`。不建立第二份 Job、设备或生命周期账本。

## 3. 本期范围与验证触发

以下分类只用于计划管理，不引入产品状态或新框架：

| 分类 | 判定 | 实施与证据 |
| --- | --- | --- |
| 本期必做 | 明确的视觉/日志缺口、共享迁移和日常主链 | 完成实际改动与必要验证，不用原型或源码存在替代 |
| 按实际影响触发 | 本次 diff、直接/传递调用关系或可复现缺口影响对应契约 | 记录触发原因，复用现有用例，只补覆盖空缺 |
| 保留约束 | 已有能力，未修改且无传递影响 | 保留行为，引用适用基线证据并注明本轮未重跑，不自动要求新实现或全域重验 |

原生 Operator + 软件 Runtime 的日常主链是本期必做。配置上传下载、后台任务、并发调度、TCP 内部状态、历史聚合等内部专项，按实际影响触发。查看/停止目标、工具往返、必要设备诊断作用域等本期直接使用的行为，仍在主链和日志验证中确认。

不能为了清空 NOT_RUN 扩大范围；也不能把实际受影响项标为保留约束。若发现与本期目标直接相关的正常路径缺陷，做最小修复并补必要验证；其他未变更风险保留可追踪记录。

## 4. Operator 结构和视觉边界

现有 `OperatorWindow` 使用 `QStackedWidget`：

- `businessHost` 承载工程 RuntimePages 或多工作流页面，保留业务布局与配置。
- `startupPage` 保留工程、最近日志、本机目录、远程接入、诊断与保存反馈，统一卡片和信息层级。
- `logView` 内可分别打开 Job 工作台、Runtime 系统事件和本机文件日志。Job 目录用于观察，生产页保留明确的启停入口。
- `technicalHost` 保留工程操作、启停、运行查看与设备入口的回调和真实目标。
- `DeviceManagementDialog` 保留已发布设备状态、允许动作、TCP 和诊断，不增加设备定义编辑。

保留 `toolHistory`、`_showTool()`、`backFromTool()`、返回按钮和焦点恢复，不新增全局一级导航。

### 4.1 外观

复用 `emo_panel_protocol.theme.LIGHT_THEME`，以用户现有生产页的浅色背景、白色卡片、蓝色主操作和语义状态色为视觉基准。统一字体、按钮、输入框、表格、间距、焦点和禁用态，不导入 Designer 整份 `app.qss`。

Operator 样式限定在自有控件、objectName 或明确作用域。工程自定义 QSS、RuntimePages 局部样式和共享通信控件的 Designer 默认外观不被覆盖。

`technicalHost` 可能留在根布局，也可能移入 `RuntimePages.diagnostics`；验证这两个实际挂载路径，不假设固定父级。优先在 Operator 设备对话框根节点设置通信区域样式；确实修改共享类行为时才追加对应 Designer 回归。

StartupPage 保留现有 920px 响应式切换，只有原生画面证明不合适时才改断点。长路径可读、操作可到达。

### 4.2 原生视觉验证

默认 1060×800 逻辑窗口采集起始/设置、生产、日志和设备的代表视图；独立设备窗口或目标机器尺寸不同时记录实际尺寸。

900×700 窄窗口至少验证 StartupPage；日志或其他页面的响应式/溢出布局实际变化时追加该页面。现场使用明确的不同 DPI 时补该实际缩放。不要求固定“四视图×两尺寸”或所有分辨率/DPI 组合。

原生 Qt 截图与实际点击都要有；返回、焦点和主要按钮并入日常主链，不为每个控件分别造测试。HTML 原型不替代原生验收。

## 5. 启动、工程与日常主链

### 5.1 状态来自现有权威读源

分别表达进程/服务、工程加载、轻量启动准入、Job、设备和 Presentation 状态，在相关页面使用真实字段，不用一个“已连接”推断全部成功。

`ProductionRuntime.status()` 在已有刷新路径读取目标工作流的轻量准入结果。现有 `ListWorkflows` 的 `can_start` 和 `start_block_reason` 由 `startAvailability(runtime, workflowId)` 产生。Operator 将两项结果与已有的加载、忙碌及会话状态一起用于开始按钮和状态说明。当另一个工作流占满并发名额时，目标工作流应显示暂不可开始及 `E_MAX_CONCURRENT_JOBS` 对应的可读原因。该 Job 释放名额后，原页面随正常刷新恢复可开始。用同一软件 Runtime 核对上述两次状态变化。

“可开始”指可以请求启动，完整预检仍由 `StartJob` 执行并报告结果。复用现有读模型和刷新节奏，不增加 readiness 缓存、健康检查服务、后台探测循环或全设备连接门槛。

### 5.2 工程恢复与 autoStart

记忆或显式加载工程后，已有的加载完成回调读取正式配置中的 `production.autoStart`。开启时对启动目标使用与手动按钮相同的轻量准入结果。若受阻，显示原因并结束这次自动启动判断。之后名额释放只恢复按钮可用，用户可手动开始。StartupPage 沿用工程内的自动运行配置。

记忆工程恢复和本机“更新工程”都以加载完成后的正式配置为准。本机从已有工程选择 `.vxpkg`，走 `updateProject → installPackage → loaded`。更新成功后，界面显示新配置，已有产出保留。对一个启动工作流，更新后的 `autoStart=false` 应保持就绪且新增 0 个 Job，`true` 在具备启动条件时应沿原 `StartJob` 路径新增恰好 1 个 Job。复用 `testWindowUpdatePreservesOutputsAndUsesNewAutoStartPolicy` 的两个参数，核对更新入口及实际 Job 数。仅加载旧工程或重启恢复的结果不能覆盖此入口。远端提交和被动配置同步保留原有职责，不触发本机加载完成后的自动启动。

数据目录继续区分当前有效目录、来源和下次启动目录。监听显示真实状态、端口及已有错误；更广的监听/网络专项按实际影响触发。

### 5.3 工程、Job 与资源

选择/加载、更新、启停、查看已有运行和返回仍调用现有方法。正式配置候选、revision/instance、资源传输和显式提交规则保持，Operator 不增加草稿编辑。

观察目标和控制/停止目标独立。同工作流多个 Job 并存时明确真实身份，不静默选择最新 Job。区分停止请求受理、Job 终态与资源实际退休，从原读模型展示，不建立新生命周期状态。

同一工程的同一工作流准备两个 Job A、B 和两个可区分的页面。在 A 打开第二页，切换查看 B 的第一页，再返回 A，应恢复 A 的第二页及其结果来源。记录每次的 `jobId` 和 `pageId`。在现有页面保存与恢复路径上按实际观察的 Job 区分状态，继续校验 Runtime、工程和页面配置适用范围。选中 B 的日志只切换观察，回到生产页仍按明确的控制目标停止。

保留循环、并发上限、Dispatch/Join、后台任务、正常取消和设备串行访问。只有本次改变相关调用/契约或发现直接缺口时，追加这些内部专项的现有测试。

### 5.4 设备、结果与历史

设备目录来自当前工程发布定义与 Runtime 注册能力；动作由 Runtime 声明和当前允许条件决定。保留 generation、projectLoadGeneration、owner 和资源忙碌等真实约束，界面展示实际结果与正常出口。

工程 Presentation 继续决定生产页面。当前/历史查看使用真实 Runtime/project/Job、capture 与 revision，不把部分结果描述成完整历史，不因查看而新建 Job。关闭观察会话不误停生产。

设备当前状态、近期诊断缓存和持久化 Job 事件属于不同数据。历史事件下钻携带 Runtime、工程加载代次和设备身份；缓存范围不匹配时明确说明，原 Job 事件仍可查看。

## 6. 日志共享：按实际依赖抽取

### 6.1 来源与责任

| 来源 | 当前事实源 | 本期处理 |
| --- | --- | --- |
| Runtime Job 事件 | EventStore、QueryRuntimeLogs、现有读取驱动 | 接入共享 Job 工作台 |
| Runtime 系统事件 | QueryRuntimeLogs、既有系统分区与 Job 归属判定 | 在 Operator 日志页提供独立入口，读取无真实 Job 归属的事件 |
| operational JSONL | runtime.operationalLogWriter、runtime-*.jsonl | 保留活动跟随、轮转、历史固定文件 |
| application JSONL | application writer、runtime_app-*.jsonl | 保留既有 fallback 与历史枚举 |
| 近期设备诊断 | GetDeviceDiagnostics、DeviceManager 缓存 | 带作用域下钻，不冒充持久化历史 |
| Designer 本机日志 | Designer 本机来源 | 继续留在 Designer，不混入 Operator |

现有 EventStore 持久化及 JSONL 投影保留。UI 不新增写入者、事件存储或导出器。

### 6.2 真实源码与共享读取面

完整 `RuntimeClient` 位于 `apps/designer/services/runtime_client.py`。日志查询还依赖 `apps/designer/page_designer/runtime_logs.py::eventPayload`；`clients/runtime/calls.py` 只提供取消/调用期限基础，并非完整客户端。

先检查工作台真实 import/call 依赖，只共享必要部分：

| 需要 | 优先复用的现有能力 |
| --- | --- |
| 任务目录、运行中与历史任务 | ListRuns、ListJobHistory |
| 实时/历史事件、范围导出读取 | QueryRuntimeLogs、现有 RuntimeGlobalLogs 读取语义 |
| 算子参数/结果说明 | ListOperators 的真实 ID、版本和声明 |
| 设备诊断 | GetDeviceDiagnostics |
| Runtime 身份、RunState | 优先沿用 Operator 现有宿主读取与状态 |

此表是已有读取能力范围，不要求新增 RPC。其他读取仅在实际依赖证明必要时补入，不能据此迁移完整 Designer 客户端。

共享实际需要的事件归一化、JobEventWindow、执行树/轮次/耗时、筛选、Qt 详情/JSON/事件视图与导出接线。非 Qt 逻辑与 Qt 组件放入项目合适的中立位置，路径由实际依赖决定；不为凑分层强行拆类或创建通用框架。

Designer 保留完整 RuntimeClient，调整必要 import、回调和薄适配，保留其原目录中的合法停止动作、日志读取、详情、导出、布局与调试行为。Operator 提供自身事件转换、operatorLookup、目录与查询接线。Operator 日志目录只观察，不显示或接入 Designer 的“停止任务”动作；操作员从既有生产控制入口显式选择停止对象。Operator 和共享模块不得导入 `apps.designer`。

不迁移无关工程编辑、发布、预览或调试服务，不建立通用 SDK、插件层或独立数据协议。**工作台所需依赖解除、Operator 接通且 Designer 行为保留后，停止扩大抽取。**

### 6.3 每个宿主的读取驱动

每个宿主工作台复用一个实时日志读取驱动，宿主内部多个视图不重复订阅、合并或落盘。Designer 与 Operator 可以独立观察同一 Runtime；不建立跨进程全局订阅单例或共享后台代理。

复用 cursor、progress、reset、reconnect、历史上界、增量合并、取消和错误反馈。实时跟随与历史阅读分离，保留有界窗口、选中事件和阅读位置。服务端搜索命中列表不冒充完整执行树。

### 6.4 用户可见日志能力

- Job 首页保留“运行中 + 最近已结束”，按真实身份去重；更早历史按既有分页读取。
- 选择 Job 时事件、执行树、详情和原始 JSON 同步切换；查看目标不改变停止目标。
- 易读详情、原始记录、复制和表格均来自同一选中事件；正式实现必须实际执行操作。
- 执行树、循环和子流程使用 workflowRunId、parentWorkflowRunId、nodeRunId、iterationPath 等真实身份，未知字段不猜。
- 算子语义按 Runtime 的 operatorId/version 匹配声明，保留原始 key/value，区分缺失、0、false、null、空数组和空对象。复用通用组件，不逐个算子再做一套显示器。
- 设备事件下钻到正确缓存范围，返回恢复原 Job、事件、筛选、视图和阅读位置。
- 操作员从日志工具进入“系统事件”即查询 Runtime。复用 `log_dialog.py` 的系统查询、可信来源与 Job 归属过滤及分页语义。用一条无 Job 的 Runtime 事件和一条明确属于 A 的事件核对，前者出现在系统页，后者出现在 A 的工作台。服务端还有后续记录时继续按游标读取，即使当前页因归属过滤而为空。
- 选择“当前筛选结果”时，对**选中 Job 的已加载事件**应用当前工作台的筛选和查看范围，固定这一刻匹配的事件集合。以 `JobEventWindow` 和工作台现有筛选逻辑生成快照，再用 `core/log_export.py` 写文件。树中的分组标题和普通日志表的缓存都不是这个快照的数据源。
- 选择“完整 Job”时以选中 Job、固定查询上界及服务端分页读取**仍保留可查**的全部事件。窗口未加载或被本地筛选隐藏的在界事件应被导出，已清理和从未记录的内容不在范围内。沿用现有取消与失败反馈。复用 A、B 各一条 WARN，另给 A 一条可查询但未加载的事件；按输出文件的 `eventId` 核对，筛选文件只含 A 当前窗口的匹配事件，完整文件包含 A 的未加载事件，两份文件都排除 B。
- 两类本机文件保留枚举、活动跟随、历史固定选择、级别筛选、刷新、最近日志、导出、取消和打开目录。

原型仅用于画面与部分状态讨论，模拟局限见 `reference/prototype-notes.md`；不得把固定诊断、提示式导出或模拟连接照搬为正式实现。

## 7. 多端边界

Designer 和 Operator 实际使用共享模块，两宿主定向回归是本期必做。观察客户端断开不误停 Job 可通过一个真实观察客户端的代表操作验证，不要求所有客户端各重复一次。

Web 无本次代码或传递契约影响时，不增加独立 Web 手工/专项测试门槛。修改影响 Web 使用的 API、DTO、共享客户端或生命周期时，运行对应既有定向用例并记录触发依据。既有必需 CI 保留。

Runtime open_api 草稿、Authoring MCP 与生产控制 API 的边界不变；本期不建设 Runtime MCP，不做公网部署，不追求 Web/Designer UI 完全一致。

## 8. 最小验证与完成判定

复用现有测试和夹具，只在实际行为变化、可复现缺陷或新增共享边界缺乏覆盖时补用例。不按修复项强制配一正一反。低影响文案/布局通过原生界面走查，关键身份和副作用保留有依据的直接反例。

日志阅读用有限确定性事件覆盖“一页历史→加载更早→新增事件”，直接断言上界、去重、选中和位置；不默认要求长时间 Job 或压力测试。

验收组织为日常主链、共享日志、读取边界/本机文件、两宿主回归和原生外观，具体见 `acceptance.md`。一次有效执行可映射多项要求，P9 汇总前阶段结果；仅相关源码/配置变化使证据失效时重跑。

本期必做项和实际触发项全部有与范围相符的有效证据后交付。保留约束不自动成为新测试前置；引用既有结果时写清基线与“本轮未重跑”。源码存在、模拟原型、历史通过和 OpenSpec 通过均不能替代本期产品验收。未执行或受阻如实标注，不把跳过改成 PASS。
