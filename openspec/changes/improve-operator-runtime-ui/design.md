# 设计：独立操作员端 Operator Runtime R4

## 0. 基线、范围和原则

原 Any 设计基线：`78a547270324553b8e88dea5a05258e562fcfb08`，Python 3.10 / PySide2 / Qt Widgets。沿用方案 2A 和三个可独立验证阶段。本文件为修订后的目标设计，未实施。开始前读取 `AGENTS.md`、当前工程格式规范、实际 `main` 和未归档 OpenSpec，记录固定 HEAD、工作区状态及重叠变更；已经完成的行为仅补必要验证，不重复实现。本机仓库中 `operational-page-shell` 位于未归档变更 `openspec/changes/enhance-generic-page-composition/specs/operational-page-shell/spec.md`，已核对兼容；实施前仍以当前 `PageShell`、`docs/project-json-spec.md`、现有测试为实际依据。

优先解决已复现正常路径缺陷。不为推测性极端组合增加锁、复杂状态机、重复实现或新服务。不重构 Designer 外壳或其日志工作台。本文控件名是建议，允许适配现有对象，要求行为一致而非像素/内部命名完全一致。

## 1. Phase 1：常驻宿主外壳与统一控制路径

### 1.1 布局与导航

```text
OperatorWindow (QVBoxLayout)
├── topBar（常驻宿主，默认单行，紧凑高度）
│   ├── 返回工具历史 | 工程名（省略）| 运行状态/版本
│   └── 全局启停停靠槽 | 工程 ▾ | 设备 | 日志 | 设置
├── contextBanner（需要时显示）
│   ├── 故障/操作错误：摘要、详情/复制
│   ├── 查看已有运行：观察目标 | 控制目标 | 返回运行页面
│   └── 正在初始化/加载/重载/更新/停止等现有操作的反馈
└── contentStack（QStackedWidget）
    ├── businessHost（RuntimePages 或 workflowTabs）
    ├── startupPage（起始/Runtime 设置）
    └── logView（独立端本机 JSONL）
```

- `工程 ▾` 收纳选择工程、重新加载、更新工程、运行目标、查看已有运行；以原 `chooseProject`、`reloadProject`、`choosePackage`、`chooseTargets`、`viewRun` 等 handler 及原权限/可用条件为准。可使用 `QAction` 替代原按钮，`_controls()` 同步动作可用态，不额外保留隐藏可触发按钮来迎合测试。
- `设备`、`日志`、`设置` 日常入口不受 `diagnosticsInitiallyExpanded` 影响。`self.pages.diagnostics` 只保留结果服务连接、执行/资源状态、画面适配、结果身份等技术内容。
- 继续复用 `_showTool()`、`backFromTool()`、`toolHistory`、`_switchContent()` 及已有焦点恢复逻辑。设置与日志相互切换后正确返回，不把普通工具返回误接成 `returnToCurrentRun()`（后者退出观察运行）。仅切工具页不重建 `RuntimePages` / `DisplaySession` / `DisplayHub` / `workflowPages`，不加载工程、不触发启停、不改变 Job 或观察目标；正常工程切换与明确查看已有运行仍沿用其原生命周期。
- 顶栏允许正常 DPI 下单行；工程名、版本或状态次要文字优先省略（完整 Tooltip），其次将次要菜单/按钮收为紧凑图标或溢出菜单，带文字 Tooltip 与可访问名称。空间仍不足可用紧凑第二行；不强迫固定 40px 高度，不把停止或返回入口藏入溢出菜单，不靠强制放大最小窗口解决。工具页内容可独立滚动。

### 1.2 全局启停与流程级启停

保留现有全局 `startButton`/`stopButton` 实例及信号绑定。原方案“整个窗口只有一对按钮”修订为“只有一对**全局**按钮”。`workflowControls[key]` 中的独立流程级按钮、流程状态和目标映射必须保留。当前全局多流程停止原本表示「全部停止」，单流程存在多个活动 Job 时由 `_stopChoice(workflowId)` 明确选目标；不得改为停止当前显示页、观察 Job 或某个默认流程。

**运行控制仅保留原有实现（本次用户确认的精简项）。** `ProductionRuntime` 已经维护工作流会话与活动根 Job；单工作流连续运行通常复用同一 Job，多流程或远程启动也可产生多个活动 Job。一个合法停止目标沿用原快捷路径，无需弹窗或额外确认；多个候选沿用原 `QInputDialog.getItem` 及 `_stopChoice()`，全局批量停止沿用 `stopAll()`，已有运行查看沿用 `viewRun()`，取消和观察/控制分离不变。`_stopTarget()` 已校验完整 Job ID 的工程、工作流和根运行归属，无需重复实现。仅为顶栏入口迁移做必要连接及现有回归；不新增运行选择窗口、任务管理中心、停止状态机或并发限制。

| 场景 | 全局按钮停靠与显示 | 原动作语义 |
| --- | --- | --- |
| 单流程、有页面的业务态 | `pages.hostControls`；遵守 `shell.showControls`，使用原 `startControl` / `stopControl` 外观 | 原全局启停 |
| 多流程业务态 | 顶栏；保留原 `PageShell` 可见性规则及 `workflowControls`，保持每流程原外观 | 全局启动已选流程、全部停止；每流程按钮只作用于本流程 |
| 无页面基础反馈态 | 顶栏，保留原业务态显示规则 | 原全局启停 |
| 工具页（日志/设置） | 顶栏标准外观；有活动/待释放运行或原状态允许停止时保留停止入口，即使 `showControls=False`；开始不突破原可见与启用规则 | 原全局操作；多流程文案清楚显示“全部停止” |
| 无工程、初始化失败 | 顶栏保留选择工程、设置、日志和正常关闭/重试入口；启停不呈现为可执行任务 | 原初始化/加载逻辑 |

具体步骤：

1. 以 `_syncHostControlDock()`（或局部等价方法）根据当前视图及页面模式决定父布局。仅在停靠位置改变时移除/加入原按钮，避免每次状态轮询重复 reparent；在正常页面销毁之前将宿主按钮迁回安全的宿主布局，防止随页面销毁。`_attachHostTools()` / `_removePages()` 与多流程页面创建路径一起核查，不把 `technicalHost` 再整体插入业务页。
2. `_controls()` 仍是可用性计算的唯一来源。协调停靠、可见性、外观与 `_controls()`，使轮询不会重新按业务页 `showControls` 隐藏工具页的停止，也不会重新给顶栏套业务按钮 QSS。可见与可点击是两回事：保留现有 `not busy and not closing and controller is not None`、`canLoad`、活动 Job/资源状态等启用条件。不能仅由 `FAULT` 或显示文字推定有可停止任务。
3. 工具页使用既有 `configureHostButton` 的标准外观/等价默认路径，明确重置从自定义样式遗留的图标、QSS、固定尺寸、最小/最大尺寸、文字等；归位时从当前 shell 重新应用完整作者配置。不得改写工程文件或简化作者自定义外观。多流程业务态顶栏若采用紧凑默认样式仅影响宿主按钮，流程级按钮保留作者配置；保留按钮当前目标 Tooltip。
4. 在原有允许操作的状态下，工具页停止能直接走 `stopDetection` / 原全部停止路径；已有单在途命令忙碌、关闭或停止等待中，停止可见但按原规则禁用并给出原因/状态。不绕过 `launch`、`_deferCommand`、重复提交限制或新开停止线程，不自动补发未知结果。若正常忙碌流程有实际无法恢复的阻塞，专项记录并按最小根因修复，不设计第二套调度器。
5. `showControls=False` 时返回业务页恢复原隐藏契约，作者自定义页面中的动作控件不受影响。工具页可见停止是宿主运维出口，而非修改项目配置。这里是软件任务停止，不是硬件紧急停机。

### 1.3 状态反馈

复用 `self.state`、`self.error`、`self.message`、`viewRunInfo`、`viewNotice`、`targetSummary`、当前操作/`busy` 等实际来源，不新建状态账本。状态胶囊区分未加载/就绪/检测中/停止中/故障等当前已知状态；未知值如实显示。正常无事项时横幅隐藏；有操作失败、故障、观察模式或等待时呈现相应摘要。故障/等待与观察提示同时存在时不能把观察/控制对象说明覆盖掉，可在一个横幅内分行简洁呈现。忙碌反馈为不确定进度和具体阶段文案，不能伪造进度百分比；完成/失败后停止动画并保留有价值的结果提示。原初始化失败与重试、加载失败后选工程/开日志/关闭路径继续可用。

## 2. Phase 1：最小浅色主题与 DPI

- 优先复用已存在的 `emo_master/ui/` 及共用 UI 辅助。必要时抽取 Designer 中确需复用的字体回退、浅色色板、`fitWindowToScreen`、纯 Python 离线 Lucide SVG 数据与图标函数到中立模块，例如 `ui/theme.py`、`ui/icons.py`、`ui/_lucide_data.py`；保留原许可头。Designer 原导入路径如确需变更，仅薄转发，不搬迁专属 QSS、资源、对话框过滤器或重写整套主题。
- 浅色色板建议沿用平台已有值（背景 `#f5f6f8`、白色 surface、文字 `#20242b`、次文字 `#626b78`、边框 `#d8dce3`、强调 `#2563eb`）。中文字体按可用字体回退，如 Microsoft YaHei UI / Microsoft YaHei / Noto Sans CJK SC / Source Han Sans SC 等，最后使用系统可用字体；无联网字体及图标下载。
- 只在独立入口创建新的 `QApplication` 前启用 `Qt.AA_EnableHighDpiScaling`、`Qt.AA_UseHighDpiPixmaps`；适用独立进程的 Fusion/浅色基础外观。已有应用实例不重新创建、不在后期强设必须预先设置的属性。QSS 限定宿主与工具对象作用域，避免全局 `QWidget`、`QPushButton` 规则覆盖 `RuntimePages`、`NativeAppearance` 以及业务组件；若基础 palette/font/style 影响业务页，也应核查并最小调整。不可为了主题引入 `emo_master.apps.designer.*`，独立进程无 Designer 导入测试继续通过。
- 100%/150% Windows 原生 Qt 验收：默认窗口、最大化与常见窄窗口；长工程名、自定义启停、工具页导航、可用状态、日志列、设备弹窗和设置长路径。允许合理省略及滚动，不允许关键操作不可达、文字大面积截断。只对本次改动的实际回归做必要断言，不按每种颜色/像素值编写测试。

## 3. Phase 2：独立 Runtime 本机 JSONL 日志

涉及 `log_reader.py`、`log_view.py`。不改变 LogFeed 单在途读取、可见时约 1000 ms 刷新、2 MiB / 2000 条初读、10,000 条视图上限、半行缓冲、文件/字节偏移身份、writer 轮转、保留和脱敏。无日志展示或隐藏时不启动新读取；起始页与日志页共用 feed。此处不是 Designer 的任务/事件日志工作台，不新增远程历史查询服务。

### 3.1 筛选与导航语义

- 工具区：文件、级别、模块、关键字（建议 150 ms 防抖）、恢复全部、刷新、导出与取消；窄窗口可自然分行。模块下拉从已加载 `feed.records` 提取并保留所选值（即使轮转/刷新后暂时无该模块，不擅自清除筛选）。关键字大小写不敏感，匹配脱敏记录的 message/code/eventType/source/component/jobId 等已知字段；优先复用纯函数与现有 `deviceRecordMatches`、级别规范化（WARNING/WARN）。组合条件取交集。不得读取原始凭据或隐式扫描全部历史文件。
- `deviceId` 筛选显示设备名（有真实映射时）与完整 ID Tooltip；无映射直接显示真实 ID。从 `DeviceManagementDialog.openRuntimeLogs()` 下钻时可将当时已知真实名称作为可选参数传入 `setDevice(deviceId, deviceName="")`，避免 `RuntimeLogView` 反向依赖设备客户端或跨工程猜名。点击关闭只清除设备条件。设备弹窗下钻应先设置目标设备并进入日志页，保留其他已生效条件；若无匹配明确提示并可恢复全部。设备筛选 ID 取真实发布设备 ID。
- `恢复全部` 与起始页 `showLogs(resetFilter=True)` 清除级别、模块、关键字、设备四个筛选条件；不擅自更换用户选定的文件或跟随轮转模式。此决策沿用当前 `resetFilter()` 只重置级别/设备的边界，扩展到新条件。业务页/设置页常规进入日志（`resetFilter=False`）保留文件、筛选、选中记录与阅读位置。明确的文件切换沿用原 feed 行为，不虚构跨文件的同一行。
- 空态区分：暂无记录、加载/读取失败、当前已加载窗口无匹配记录。文案提示“搜索当前已加载记录（最多 10,000 条），不是全盘历史搜索”。保留日志来源、活动 writer 与手动固定文件区分、启动诊断文本选区/光标与滚动。不得把无匹配写成日志不存在。

### 3.2 本地时间和脱敏复制

`formatLocalTimestamp` 为展示辅助，优先合法 `timestampMs`，否则解析实际 ISO 时间戳（含 UTC/offset）；按本机时区显示 `YYYY-MM-DD HH:mm:ss.SSS`。无明确时区的值按项目现有契约解释；契约不明或无法解析则保留脱敏原文/未知，不猜 UTC、不产生假时间。Tooltip 展示原始时间及本地时间对照；详情、原始 JSON 和导出仍使用原始脱敏数据，不回写文件。级别列语义色区分 ERROR/WARN/INFO/DEBUG，文字仍可辨识，不仅靠颜色。复制详情/原始 JSON 只复制选中的**已脱敏**记录，无选中时不可用，复用现有 `safeRecord`/`recordDetails` 路径，绝不从原始文件重新读取未脱敏 JSON。复制不触发执行、重试或写日志。

### 3.3 表格模型与阅读锚点

- `QTableView + LogTableModel(QAbstractTableModel)` 持有筛选后的 `LogRecord`，按需返回 6 列、Tooltip、角色颜色；避免每次更新分配最多 60,000 个 `QTableWidgetItem`。可复用 `feed.generation` 和记录身份判断无变化；尾部纯追加用 `beginInsertRows/endInsertRows`。头部淘汰且能可靠计算连续删除/追加时使用成对 remove/insert；否则允许一次 `beginResetModel/endResetModel`，不设计复杂 diff 引擎。筛选变化/文件轮转按正确模型事件更新，不能将 reset 当作任意刷新默认实现。
- 更新前后用原文件及字节偏移身份恢复选中项、详情、首可见记录及其像素偏移（ScrollPerItem/ScrollPerPixel），并保留水平滚动位置。跟随最新时滚动到底部；关闭跟随时不因新记录跳到尾部。身份被淘汰或筛选移除时按原锚点规则选择仍存在的邻近位置并清理失效选中/详情；不把旧详情错配给新的行。无变化不重置详情选区。保留启动诊断选区的既有行为。
- 轻量性能验收仅针对本次卡顿：相同机器/环境、同一批模拟 10,000 条/持续追加与淘汰记录，记录改造前后实际刷新耗时或定向 UI 交互观察。验收以无整表 item 重建、正常筛选/滚动/复制及不丢阅读锚点为准，不设置无依据的绝对毫秒指标，不引入专项性能平台。

### 3.4 三种导出范围（明确新增组合筛选口径）

当前源码已有 `exportCurrentAction` / `exportFileAction` / `exportFilesAction`；完整文件范围走 `LogFeed.exportFiles` 后台线程，当前显示走现有快照导出路径。本轮不强制把所有导出改为后台，也不新增导出 API。

| 范围 | 数据来源 | 生效筛选 |
| --- | --- | --- |
| 当前显示记录 | 发起时 `_shownRecords` 的脱敏快照（含已加载窗口上限） | 当前四条件交集 |
| 选定文件完整范围 | 发起时选定历史或活动文件及其 `endOffset` | 同一份四条件，扫描该文件已捕获范围，不受 10,000 条 UI 上限限制 |
| 本机全部文件范围 | 发起时已有本机日志文件集合及各自 `endOffset` | 同一份四条件，扫描选定范围，不受 UI 上限限制 |

- 保留现有级别、设备在完整文件导出中的语义；本次将新增模块/关键字也用于三范围，并在菜单/导出说明写明“按当前筛选”。这是本修订版明确的设计选择，避免显示与导出筛选不一致；要导出不限条件记录先恢复全部。
- 使用同一脱敏匹配函数及不可变的发起时筛选参数、文件范围/记录快照；弹出保存对话框前冻结，导出期间继续筛选/轮转不得改变本次范围。现有 `snapshotLogFiles()` 接受 `LogFileRange` 并保留捕获偏移，直接复用。关键字防抖期间应在发起导出时提交当前输入或确保导出说明/视图使用同一生效条件，不能出现显示与导出不一致。
- `JSONL/JSON/TXT` 保留原时间、身份与已脱敏记录；现有 filters 元数据可追加 component/keyword，但不得持久化未经脱敏的敏感输入。完整文件导出仍异步、可取消、单在途、保持目标文件覆盖确认与原子替换规则；当前显示范围保持既有实现，若实际测得阻塞再局部优化，不预先新建线程框架。错误/取消不显示成功，不替换旧目标文件。保留现有更早日志查看、打开实际日志目录行为。

## 4. Phase 2：共享设备管理弹窗

涉及 `src/emo_master/ui/device_management.py`。按本机 `78a54727` 源码核查，当前生产代码中仅 `src/emo_master/apps/operator_runtime/main.py` 实例化该弹窗（Designer 另有独立设备工作区），测试中除 `tests/ui/operator_view/test_basic_runtime_feedback.py` 外，`tests/runtime/test_tcp_device_integration.py`（第 618 行）也直接访问了 `view.selector`。实施时须保留 `openRuntimeLogs()` 对非 Operator 宿主的回退提示，并核对最新目标分支是否存在新增调用方。不增加 RPC、状态轮询线程、设备注册或额外连接探测。沿用 `DeviceManagementClient`、单工作线程、现有轮询频率（当前状态约 2 秒）、`projectId()`/`projectLoadGeneration` 与真实 ID、已发布设备数据、现有 `managementAction`、设备控制串行访问、TCP 通道表与诊断。

### 4.1 左列表 + 右详情

- `QSplitter(Qt.Horizontal)`，左侧紧凑列表（建议约 260–300 逻辑像素，允许缩放），展示全量已发布设备的真实名称/类型/短 ID/状态文字和色标；名称缺失仍显示真实 ID，Tooltip 完整 ID。概览按同一互斥分类计数，不用绿/红二分硬套全部类型。右侧摘要（名称、类型、完整 ID、连接/运行状态、最近错误仅在真实字段存在时显示），支持复制完整 ID；原始状态 JSON 默认折叠，展开仍为只读诊断信息。
- 右侧保留全部现有类型声明动作：connect/disconnect/maintenance/resume/recover、业务 `managementAction`、`availableManagementActions`；显示条件取声明能力，启用取 `availableControls`/现有 pending 判断。TCP Server 继续使用“启动服务/停止服务”与 `TcpChannelStatusTable`；保留 `DeviceDiagnosticsView`。不得通过展示层推测可执行动作、给未连接设备自动连接或改变工程配置。
- 按完整设备 ID 保持选择/滚动/原始 JSON 展开状态；正常刷新仅更新数据。项目作用域变化、设备移除或未选中时清除不再有效的详情和动作目标，不把之前设备的错误或日志显示给新设备；继续原有 stale scope 处理，暂未读取/读取失败用未知或明确失败提示，而非假装健康。日志下钻沿用 Operator 宿主 `showLogs`/`setDevice`；非 Operator 宿主保留其实际已有入口及不支持时的提示，不假设其具有 `logView`。
- `selectedDeviceId()` / 原 `selector.currentData()` 调用者应统一迁移到真实选择 accessor（含 `test_basic_runtime_feedback.py` 与 `test_tcp_device_integration.py`），不创建两份列表或第二套选择状态。

### 4.2 设备状态口径

从现有 client 返回的状态契约、设备类型声明与实际 driverState 中选取确切字段。实施者先查明各类型状态来源再实现小型展示映射；不要新增后台健康探测。以下是**展示分类原则，不是假设已有字段名**：

| 展示类别 | 依据与示例 |
| --- | --- |
| 正常（已连接/服务运行中） | 真实连接或服务监听/运行状态，按设备类型呈现 |
| 未连接/未初始化/已停止 | 真实非运行状态；正常空闲或手动停止不等于故障 |
| 维护中 | 已有维护状态明确成立 |
| 异常 | 现有故障/明确失败状态或有依据的当前错误；历史错误不单独判定当前故障 |
| 未知 | 无有效状态、字段不足或尚未成功读取；读取失败单独提示 |

TCP Server 已正常监听、0 客户端连接仍可为正常服务；TCP 通道连接情况单独显示。相机、PLC 的状态按各自契约。颜色仅辅助，保留文字。概览计数合计等于设备总数；没有依据就显示未知，不虚构连接或健康。状态摘要不暴露新的凭据字段；原 JSON 保留既有受控展示范围，不能绕过现有脱敏或将原始状态加入日志、证据或复制功能。

## 5. Phase 3：起始/设置页与窗口偏好

### 5.1 `StartupPage` 与 `ListenerSettingsPanel`

- 起始页主操作为打开工程、最近成功工程；保留最近日志摘要、“查看全部日志”、初始化失败/重试、真实数据目录和必要状态。已加载后的设置页突出目录与远程接入设置；不因顶栏重复删掉具有 `resetFilter=True` 语义的查看全部日志入口。标题/次要说明分层，路径可省略+完整 Tooltip/复制真实路径，较窄窗口允许自然换行。
- 远程接入与已有 StorageSettings 对齐：本次实际/目标监听（区分初始化失败）、已保存下次设置、表单草稿三者独立。监听开关、host、port、每任务展示订阅数变动后即时比较 `self.saved`，提示“未保存；本次监听和任务不变”；改回保存值清除 dirty。刷新 `setCurrent` 不覆盖草稿。保存复用 `ListenerSettings` 校验、原 QSettings 保存/失败恢复，成功更新 `self.saved` 并标明下次启动生效；失败保留草稿与错误，不能误标已保存。
- 仅在已有启动参数来源能证实时明确显示 CLI 覆盖字段；如果当前代码只有有效值而无完整来源信息，沿用现有解析结果，必要时在原入口传递少量实际 CLI 覆盖信息。不得将“本次有效值与刚保存值不同”直接等同于 CLI 覆盖；可能只是下次生效。无证据时使用“本次仍按启动时生效值；启动参数优先于保存设置”。保存不停止任务、不重建监听、不改防火墙、不修改环境变量、目录或凭据。

### 5.2 最近工程与窗口记忆

- 原 `QSettings("EmoMaster", "Runtime")` 中新增 `recentProjects`（建议 JSON 路径列表，上限 5，去重按最近成功加载时间倒序）。沿用原成功加载路径与 `projectPath` 更新时机；仅成功提交的工程加载/切换记录，不记录选择取消或失败；必要时从旧 `projectPath` 初始化列表，不改变启动时显式工程与原在线恢复优先关系，不批量扫描文件。记录路径用现有规范化/本机路径方式，保留实际路径。最近条目仅在原 `canLoad` 等条件允许时走正常 `loadProject` 入口；缺失或无效按既有失败反馈，不在用户显式选择历史工程失败后偷偷恢复其他工程或启动旧任务。坏偏好值简单回退，不做迁移框架。
- 正常关闭被接受时保存 `window/geometry` 与 `window/maximized`（或等价既有偏好机制），不要在关闭被拒绝/取消时误记为成功退出。恢复正常窗口几何，再依据当前屏幕可用区域收敛，再恢复最大化；默认 `1060×800`，屏幕较小则合理收敛。使用隔离 QSettings 测试；不修改 Designer 的独立窗口偏好，不为罕见多屏排列建立持续监控。

## 6. 验证与交付边界

- 以 `tasks.md` 及两份 delta spec 的可观察行为为完成标准。优先复用 `tests/ui/operator_view/` 现有夹具：`test_production_window.py`（包括 `testSourceEntryImportsNoDesignerAndCheckNeverExecutes` 与无效记忆工程）、`test_run_view_selection.py`、`test_runtime_log_view.py`、`test_basic_runtime_feedback.py`、`tests/runtime/test_tcp_device_integration.py`，以及实际相关设置、设备及 Designer 主题测试。调整旧 `QPushButton`/`QTableWidget`/下拉框定位方式可以，但不得弱化核心启停、对象身份、数据、阅读锚点、取消、脱敏、控制 ID 和错误出口断言。
- 阶段完成执行对应定向测试，最终对实际提交执行受影响源码测试及 ruff、仓库现行正式 Source CI（Windows/Linux，沿用届时有效入口，不额外迁移 CI、不手工触发全部 12 个 Worker）。若 CI 迁移中，仅使用已批准路径，记录不可用/阻塞，未验证不写通过。无必要不重复全仓；Web 无改动则不额外扩大 Web 专项。
- 原生 Windows Qt 100%/150% 正常交互走查，使用临时工程、模拟设备；记录实际 DPI、分辨率、HEAD、工作区、操作步骤与截图。100%/150% 真实显示缩放如无法获得，标明所用探针/模拟环境，不把截图或 offscreen 测试冒充真人实机验收。记录日志 10,000 条对照的实际测量/观察。独立源码、原生 Qt、真实硬件、冻结 EXE、发布分层记录。
- 完成当前必要验收即收尾；非阻断风险只记录一次，不反复扩大范围。未经用户明确授权不合并、不推送、不发布、不操作现场设备。若获授权推送，按 `AGENTS.md` 在对应提交或 PR 附实际角色（Codex / ChatGPT Web / dot 等）评论，说明改动、验证、未验证项。所有任务复选框在有证据后才勾选。
