# runtime-log-workbench Specification

## Purpose
在 Designer 中以运行任务为入口查看 Runtime 的真实执行过程，按工作流调用、循环迭代和节点执行组织事件，并能从设备事件下钻到现有设备诊断；Designer 本机日志保持独立来源。节点框架执行记录与附加主动日志都提供通用易读详情，现有单算子调试窗口复用展示能力但保留独立会话 / 结果来源。交互以随包原型为参考，正式实现使用现有 PySide2 / Qt Widgets；原型的模拟数据和连接状态不构成实现或验收证据。

## Requirements

### Requirement: Runtime and Designer logs are separate top-level sources

系统 SHALL 只提供 Runtime 运行日志与 Designer 本机日志两个一级来源，先依据 `originApp` 或可信生产者来源分类，再判断 Runtime 记录的 Job 归属。运行任务 SHALL 是 Runtime 来源内部的主要视图。

#### Scenario: Open the log window

- **WHEN** 用户打开日志窗口
- **THEN** 用户可以选择 Runtime 运行日志或 Designer 本机日志
- **AND** Runtime 运行任务和 Runtime 系统事件属于同一来源
- **AND** Designer 本机日志使用 Designer 自身记录

#### Scenario: Inspect a Designer event carrying a Job ID

- **WHEN** Designer 本机事件携带 Job、工作流或节点标识
- **THEN** 该事件仍属于 Designer 本机日志
- **AND** 不仅因携带 Job ID 就将其归为 Runtime 任务事件

#### Scenario: Runtime system events exclude Job-owned events

- **WHEN** 用户查看 Runtime 系统事件
- **THEN** 只显示没有真实 Job 归属的 Runtime 事件
- **AND** `__runtime__` 分区中 payload 明确携带真实 Job ID 的事件仍归属对应 Job
- **AND** 客户端过滤后的空可见页仍按服务端游标与 `hasMore` 处理，不被误判为历史结束

### Requirement: Run task home prioritizes active and recently ended Jobs

系统 SHALL 保留“运行中”和“最近已结束”两个首页分组，以任务自身 status 判断分组，而不以 active 单独判定工作流仍在执行。当前实时 RunState SHALL 优先于同一身份的历史摘要；常规 recent 页与当前仍 active 的终态任务 SHALL 合并去重后展示在“最近已结束”。任务操作 SHALL 遵循现有 allowed_actions 和身份校验。

#### Scenario: View active and recently ended Jobs

- **WHEN** 用户打开 Runtime 运行任务首页
- **THEN** 已受理、启动中、运行中、停止中等非终态任务显示在上组，终态任务显示在下组并按可用结束时间倒序排列
- **AND** 当前返回任务的实际状态保留，不将“已完成”改写为运行中或结束处理中
- **AND** 同一 Job 在主目录只有一个任务条目，同一 Job 内循环轮次和连续根调用不生成首页任务
- **AND** 当前 Runtime、工程和 Job 的身份用于合并，当前 RunState 覆盖历史摘要

#### Scenario: Keep a terminal but Runtime-owned task visible

- **WHEN** 某任务已完成、失败或已停止，但当前 ListRuns 仍返回它且 active 为 true
- **THEN** 该任务出现在“最近已结束”，即使它不在常规最近历史页内
- **AND** 它不受常规 recent 页额度裁剪，也不在“运行中”重复显示
- **AND** 使用当前成功 ListRuns 快照与 recent 合并，不新增永久保留任务缓存
- **AND** 后续它不再 active 后按普通 recent 或更早历史规则处理

#### Scenario: Keep the result duration and allowed action distinct

- **WHEN** 用户查看或选择终态 active 任务
- **THEN** 本任务自身耗时按既有终态时间规则固定，不随关联任务活动增长
- **AND** 若 Runtime 返回 stop 权限，保留该任务已有合法停止操作与执行前身份检查
- **AND** 不因任务被移动到“最近已结束”就清除权限，也不因副提示存在自行创造停止权限

#### Scenario: Browse history after Runtime restart

- **WHEN** 用户查看更早历史
- **THEN** 系统继续从现有 SQLite jobs 表读取终态摘要，保留工程、状态、结束时间条件及稳定分页
- **AND** 不新建任务表，不将纯历史摘要当作当前 Runtime 所拥有的可停止任务
- **AND** 历史事件保留原生产实例标识，查询使用当前目标实例校验

### Requirement: Job event windows combine recent history and live updates

系统 SHALL 通过现有 Runtime 日志查询来源合并最近历史与后续事件，并支持固定上界下向更早分页；不得为该窗口增加第二条重复追加的事件订阅。

#### Scenario: Open a long-running Job

- **WHEN** 用户打开一个长时间运行的 Job
- **THEN** 系统先加载该 Job 最近一页未经本地等级筛选裁剪的原始事件，取得固定历史上界
- **AND** 若该页含循环记录，沿固定上界向前读取，直到覆盖最近 50 个已完成外层轮次的起始、到达可查询历史起点或达到事件窗口上限。中间的子流程事件、未完成轮次和 While 退出检查一并保留，后两者及嵌套轮次不占 50 轮配额
- **AND** 执行结构默认展开到外层循环的轮次列表。同一循环的最新轮次排在最上方，包括尚未结束的一轮。展开某轮后，节点、子流程和事件按实际执行顺序展示
- **AND** 同级的多个外层循环及其调用分组按各组最近轮次的记录排序，向下翻页时可以继续阅读更早运行的循环
- **AND** 补齐上界之后的增量，与现有全局读取来源返回的事件合并
- **AND** 在当前查询存储与目标上下文内按全局 eventId 去重，不以每 Job 的 sequence 代替全局游标

#### Scenario: Load earlier events

- **WHEN** 用户在轮次列表向下滚到底部，或在按时间正序展示的视图向上滚到顶部
- **THEN** 查询沿用固定历史上界和服务端返回的向前分页游标
- **AND** 向前加载时沿用相同的 50 轮规则。若第 50 轮的起始落在原始页中间，下次从该起始事件的 ID 向前读取本页余下记录
- **AND** 每次到达边界的滚动操作加载一批，刷新、切换视图及定位事件不触发连续翻页。读取期间的重复操作不重复请求
- **AND** 游标与是否还有下一页依据原始服务端结果推进，不依据本地筛选后可见的事件计算
- **AND** 页合并遵循有界窗口规则，保留当前选择与阅读锚点，不静默替换为另一页

### Requirement: Event windows stay bounded while preserving the reading position

系统 SHALL 将 Job 详情原始事件窗口限制在 10000 条以内，并使树与详情缓存随窗口范围受控；展开某个分支不得永久锁住该分支的全部事件。

#### Scenario: Read history while a Job continues

- **WHEN** 用户离开最新位置阅读历史，且 Runtime 继续产生事件
- **THEN** 当前历史窗口停止接入新事件载荷，仅更新已观察到的新事件数量或新事件提示
- **AND** 新事件不强制改变滚动位置、选中事件或筛选
- **AND** 用户返回最新位置后，以新的最近页及增量补齐恢复实时查看

#### Scenario: Reach the event window limit

- **WHEN** 用户主动加载更早页导致窗口接近 10000 条上限
- **THEN** 系统可以从窗口另一端移出非当前范围的事件，并明确告知窗口范围变化
- **AND** 当前选择与阅读锚点保持可用
- **AND** 无法同时保留锚点并满足上限时停止继续扩窗，提示用户切换窗口，不无限缓存或静默移走当前阅读位置

### Requirement: Execution tree uses real invocation identities

系统 SHALL 按 `workflowRunId`、`parentWorkflowRunId`、`nodeRunId` 和 `iterationPath` 建立执行树；名称和时间邻近关系不得替代真实执行身份。

#### Scenario: View normal and child workflows

- **WHEN** Job 执行普通工作流、子工作流或并行分支
- **THEN** 节点事件按真实调用身份归并，同名节点不会跨调用合并
- **AND** 子调用按真实父调用关系显示，并行调用并列展示，排列不暗示串行因果
- **AND** 当前范围缺少关联证据时显示未完整关联状态，不猜测父子关系

#### Scenario: View repeated root workflow calls

- **WHEN** 一个 Job 连续多次调用根工作流
- **THEN** 每个不同的根 `workflowRunId` 独立显示
- **AND** 不使用 `cycleId` 或日志条数代替调用次数
- **AND** 只有部分历史已加载时，调用数量明确限定为已加载范围

### Requirement: Loop iterations are independently expandable

系统 SHALL 将 While、ForEach、Repeat 的迭代显示为独立可展开条目，支持嵌套 `iterationPath`，并以第 1 轮、第 2 轮展示 Runtime 的 0-based 索引。

#### Scenario: View While body execution evidence

- **WHEN** 任一种现有 While 执行路径产生迭代完成事件
- **THEN** `bodyExecuted=true` 表示该轮实际执行循环体并正常完成，`false` 表示条件检查后没有进入循环体
- **AND** false 条件检查独立可见，但不计入已执行循环体轮次
- **AND** 旧事件未提供该字段时保留“执行情况未记录”语义，不将缺省强制转换为 false 或 true
- **AND** 失败或取消导致缺少迭代完成事件时，不据此推定循环体没有执行

#### Scenario: View ForEach and Repeat progress

- **WHEN** ForEach / Repeat 的总量可以准确确认
- **THEN** 系统复用现有循环 `mode` 并显示准确的总项数与进度
- **AND** 未知总量或无限 While 不显示虚构的总数或百分比
- **AND** 旧记录缺少 mode / totalIterations 时按未记录显示，不为补元数据额外查询工程定义
- **AND** 普通非循环调用的空 iterationPath 视为合法

### Requirement: Execution time reflects actual lifecycle evidence

系统 SHALL 为 Job、工作流调用、循环轮次和节点显示可确认的开始时间、结束时间及墙钟耗时；受理时间、收到消息时间和 UI 时间回退值不得冒充实际执行开始时间。

#### Scenario: View a running or stopping Job

- **WHEN** Job 有有效 startedAt、尚无实际结束时间，且自身状态为 RUNNING 或 STOPPING
- **THEN** 系统继续显示从实际开始时间计算的墙钟估算耗时
- **AND** 不把调试暂停、等待或停止收尾期间的墙钟时间称为纯计算耗时

#### Scenario: View a terminal Job with incomplete child events

- **WHEN** Job 已终态但内部工作流或节点缺少结束事件
- **THEN** Job 不继续动态计时，内部对象显示结束事件缺失或时间不完整
- **AND** 不使用 Job 结束时间伪造每个内部对象的结束时间
- **AND** 下游资源仍活跃不会延长已结束 Job 的自身耗时，连续调用之间的间隔不计入单次工作流调用耗时

#### Scenario: Inspect a Job marked failed after Runtime restart

- **WHEN** Job 因 `E_RUNTIME_RESTARTED` 被确认失败，记录的结束时间为重启后的确认时间
- **THEN** 界面标明该时间是中断确认时间
- **AND** 不把开始到确认时间的差值称为精确实际执行耗时

#### Scenario: View incomplete timestamps

- **WHEN** 原始时间缺失、配对事件未加载或无法确认缺失原因
- **THEN** 系统显示有证据支持的不完整状态；没有明确清理证据时不宣称“历史记录已清理”
- **AND** 不使用 UI 回退时间、相邻日志间隔或缺少 started 事件的 skipped 节点伪造耗时

### Requirement: Filters preserve execution context and existing history search

系统 SHALL 默认收起高级筛选，保留条件摘要、搜索与等级快捷筛选，并明确区分已加载事件过滤与现有服务端历史检索。

#### Scenario: Filter warnings in an execution tree

- **WHEN** 用户对 Job 执行结构应用 WARN / ERROR 或其他本地筛选
- **THEN** 执行树先由未裁剪的已加载原始事件建立，再显示命中事件及其祖先路径
- **AND** 界面标注筛选范围为当前已加载事件，加载更早页后该范围相应更新

#### Scenario: Switch tree and chronological views

- **WHEN** 用户切换同一 Job 的执行结构和时间顺序
- **THEN** 使用同一原始事件窗口，保持筛选、游标、选中事件和各视图阅读位置

#### Scenario: Query events outside the loaded window

- **WHEN** 用户执行现有服务端历史检索
- **THEN** 已有工程、Job、设备、模块、等级、工作流、节点和时间条件继续作用于指定的可查询历史范围
- **AND** 结果显示为搜索命中列表，不将稀疏命中列表冒充完整执行树
- **AND** 不因任务详情改为本地筛选而删除既有服务端检索能力，也不宣称尚未实现的全文搜索能力

#### Scenario: Carry effective log levels from a Job into history search

- **WHEN** 用户启用告警／错误快捷条件后从 Job 进入服务端高级历史查询
- **THEN** 有效等级为 `WARN`、`ERROR` 与所选等级的交集，等级不限时查询这两个等级，页面显示实际提交的范围
- **AND** 没有共同等级时显示空结果，不用不限等级查询或导出扩大范围
- **AND** 历史翻页和选定范围导出沿用已提交的有效等级，完整 Job 导出仍使用该任务自己的可查询范围

### Requirement: Context and current-position navigation use available evidence

系统 SHALL 允许查看本轮已加载上下文、继续加载更早记录并返回原筛选；定位当前执行位置必须具备对应 Job 的可信、连续的生命周期证据。

#### Scenario: Inspect an iteration context

- **WHEN** 用户从筛选结果查看本轮上下文
- **THEN** 临时显示该轮当前已加载的上下文，并明确是否仍有更早记录可加载
- **AND** 未加载完整时不称为“完整上下文”
- **AND** 返回时恢复原筛选、选中事件和阅读位置；容量边界仍遵循事件窗口规则

#### Scenario: Locate the current execution

- **WHEN** 用户选择定位当前
- **THEN** 仅在同 Job 连续观察到的生命周期能确认活动位置时（既有状态摘要必须身份匹配且没有接续缺口），定位该位置并保留必要祖先
- **AND** 当前筛选隐藏目标时可临时显示定位上下文并允许返回原筛选
- **AND** 证据不足时返回最新活动区并提示“当前执行位置未确认”，不把最后一条日志自动视为活动节点
- **AND** 不为该交互增加新的当前节点 RPC

### Requirement: Selected events open a right-side detail drawer

系统 SHALL 在选中事件时通过右侧详情抽屉显示业务信息和原始数据。

#### Scenario: Inspect and close an event drawer

- **WHEN** 用户选择一条日志
- **THEN** 右侧显示发生时间、等级、消息、日志代码、Job、工作流调用、循环路径、节点及可确认的设备和耗时信息
- **AND** 当前收到的 JSON / payload 及技术标识可以折叠展开，不被描述为脱敏或截断之前的全部原文
- **AND** 关闭抽屉后主区域恢复空间，当前选择、筛选、展开状态和阅读位置保持

### Requirement: Operator logs provide readable data without losing raw inspection

系统 SHALL 默认以易读详情展示当前选中节点生命周期记录的实际 `ioSummary / metrics / diagnostics` 和原因字段，不以是否存在主动日志为前提；`node.log` 的消息和 `payload.data` SHALL 作为附加日志数据展示。系统 SHALL 保留既有 `LogJsonTree` 原始记录检查能力，复用现有摘要类型，不为本功能重新定义 logger 协议、批量给算子增加重复日志、保存全部算子输入输出或建立历史图片库。

#### Scenario: Read framework execution records without active logging

- **WHEN** 用户选择一个没有主动 node.log 的算子所产生的 node.started / completed / failed / skipped 记录
- **THEN** 详情直接显示当前记录中实际提供的输入输出摘要、指标、诊断或失败 / 跳过原因，不显示为仅因没有主动日志而空白的详情
- **AND** 开始记录不冒充节点当前仍运行，失败 / 跳过不补成功输出或虚构耗时；字段缺失、空字段和明确不可用保持不同含义
- **AND** 图像 / 数组摘要显示已知 shape 与 dtype，集合 / 对象摘要显示数量，omitted / truncated 说明保留，不由摘要造出完整输出或图片
- **AND** 展示当前记录不依赖逐次跨事件查询或全历史扫描；可选的已加载关联记录单独标明真实执行身份和来源，不改写当前事件

#### Scenario: Identify the model used by a production node

- **WHEN** 用户查看含有模型加载路径的节点完成记录
- **THEN** 易读详情优先展示本次使用的模型，实际加载路径可复制
- **AND** YOLO 检测和分类的生产记录显示节点选定的模型文件名、来源路径和实际加载路径。Runtime 使用已校验的工程副本时同时显示 SHA-256，工程师可以据此核对同名模型的内容
- **AND** 对于已经记录了资源绑定来源和被覆盖填写值的历史事件，易读详情保留两者
- **AND** 旧记录缺少来源时，显示已记录的实际加载路径；对于按内容指纹命名的 Runtime 模型副本，同时展示该指纹。历史详情仅使用所选记录中的信息

#### Scenario: Read scalar, empty and missing values

- **WHEN** 用户选择包含 `0`、`false`、`null`、空字符串、空数组或空对象的算子日志
- **THEN** 易读详情区分这些值和未提供字段，不以真假值判断将有效数据替换为缺失符号
- **AND** 日志代码不被一律称为错误码，记录字段不被称为完整输入或运行参数
- **AND** 未知 key 保留原名，不推测单位或含义；lifecycle 仅表示生命周期阶段，不推测初始化或销毁

#### Scenario: Explore nested and collection data

- **WHEN** 用户展开当前记录中的对象、数组或长文本
- **THEN** 对象和混合 / 嵌套数组可按层级展开，只有结构一致且值为简单类型的对象数组显示紧凑表格
- **AND** 分段浏览和展开仅展示当前返回的数据，不为此发起全量查询
- **AND** 复制动作明确作用于选中值、当前返回数据或当前记录，不被称为完整 Job 导出

#### Scenario: Switch between readable details and raw record

- **WHEN** 用户切换同一事件的易读详情与原始 JSON，或查看已标记截断的记录
- **THEN** 两视图对应相同选中事件和相同收到的数据，保留筛选、执行位置与选择
- **AND** 原始视图保留既有 JSON 树的层级、类型、惰性展开、长文本及复制能力，文本查看不能替代树能力
- **AND** 截断 / 脱敏标志按原意显示，展开和复制不承诺恢复已裁剪或未记录的数据；没有顶层截断标志也不宣称内容全量

#### Scenario: Inspect a log from an older execution

- **WHEN** 用户查看旧循环或旧调用中的算子日志
- **THEN** 算子主动记录字段与节点输入输出摘要、指标和图片结果保持不同含义
- **AND** 仅可展示真实身份匹配且可用的执行摘要或结果；未加载或不可用时如实说明
- **AND** 不拿该节点最新一次结果或图片顶替所选旧执行，也不承诺所有轮次均保留完整结果图

#### Scenario: Read existing preview failure information

- **WHEN** 已有纯预览失败返回文本摘要，或实时预览失败事件携带 payload.logs
- **THEN** 系统展示实际失败文本或附带的 level / message / code / payload，不将 BufferedOperatorLogger 的 payload 误当 Runtime node.log 的 data 包裹
- **AND** 预览保留已有来源与范围，不伪造生产 Job，不将文本拆成未实际返回的事件
- **AND** 此能力不增加成功预览持久日志流、存储或 RPC

### Requirement: Operator debug reuses readable details with its real session and result sources

系统 SHALL 在既有单算子调试窗口中实际复用日志工作台的 Qt 详情组件或格式化适配点，消费既有 `ReadOperatorDebugEvents` 和 `GetOperatorDebugExecution`；不得以新增调试日志存储 / RPC、生产 Job 或通用渲染注册框架实现复用。调试日志与执行结果 SHALL 保持各自真实来源。

#### Scenario: Read a wrapped debug log

- **WHEN** 现有调试轮询返回包含 sequence / type / generation / executionId / event 的日志 wrapper
- **THEN** 详情使用内层 event 的真实 eventType / message / level / code / payload，外层 wrapper 用于会话归属与原始查看
- **AND** sessionId 取现有连接上下文，generation / executionId 按实际记录匹配；会话通知和没有执行身份的记录不补成某次生产 Job 事件
- **AND** execution.started / finished、logs.dropped 和本地文本反馈按其原有类型显示；nextSequence / gap 沿用已有含义，不转成 Runtime eventId
- **AND** 易读 / 原始查看和复制不改写 wrapper，也不以外层 type=node.log 推断内层必为业务日志

#### Scenario: Read an operator debug result without a completed event

- **WHEN** 单算子调试已经取得某次 GetOperatorDebugExecution 结果，但没有 WorkflowRunner 的 node.completed 或 ioSummary
- **THEN** 系统以独立的调试执行结果展示实际返回的状态、outputs / outputAssets、metrics、diagnostics、elapsedMs 和已保存参数 / 来源说明，缺失项不补造
- **AND** 调试结果与选中的日志行分开，并匹配现有 session / generation / executionId；不可用时不拿最新结果替代
- **AND** 不把 inputSources 称为完整输入、不把 finishedAtMs 单调时钟值当成日历时间，也不制造 node.started / completed 或 ioSummary
- **AND** 保留现有结果、参数、PLC 专门文案与入口；不把调试结果追加进生产日志，不改变调试准入范围或资产读取 / 执行控制机制

### Requirement: Device diagnostics are a drill-down

系统 SHALL 复用现有设备诊断缓存作为 Job 设备事件的下钻，不复制成另一套日志来源，也不将缓存记录再次追加到 Job 执行树。

#### Scenario: Open diagnostics from a Job device event

- **WHEN** 用户从有明确设备身份的 Job 事件打开设备诊断
- **THEN** 使用匹配的 Runtime、工程加载代次和设备身份查询现有缓存
- **AND** 沿用通道、HEX / UTF-8、原始回执和已返回缓存导出能力
- **AND** 返回时恢复原 Job、事件、筛选和阅读位置，不以设备缓存 cursor 冒充 Runtime eventId

#### Scenario: Device cache does not match the historical scope

- **WHEN** 历史事件对应的 Runtime 或工程加载代次与当前设备缓存不匹配
- **THEN** 界面说明近期缓存不可用于该历史范围
- **AND** 已持久化 Job 事件仍可查看，不把当前缓存冒充历史完整诊断

#### Scenario: Device event lacks execution attribution

- **WHEN** 设备事件有关联 Job 但缺少足以确定执行调用的身份
- **THEN** 界面如实显示执行关联未记录，不按时间猜测循环归属
- **AND** 普通非循环事件的空 iterationPath 不被视为缺失轮次

### Requirement: Device context normalization preserves trusted identity and summary meaning

系统 SHALL 在原生设备诊断 append 形成记录前集中归一化已存在的 source 执行字段，并保留原始 source；可信顶层范围、Job 和设备身份不得被 source 覆盖。

#### Scenario: Normalize execution and capture diagnostics

- **WHEN** executeDevice 或 captureDevice 提供有效的执行上下文
- **THEN** 两条路径传递相同的可用执行字段，Diagnostics 只补充缺失的 source 执行身份到统一记录
- **AND** 有效顶层字段与合法空 iterationPath 保持原意，不在多层分别添加冲突兜底
- **AND** 本次修订不会声称已修复没有相应信息的旧历史事件

#### Scenario: Inspect aggregated device failure and recovery summaries

- **WHEN** 设备故障或恢复摘要包含聚合次数
- **THEN** 界面明确该次数可能跨 Job 或轮次累计，不将其当作当前轮失败次数
- **AND** 后续恢复事件不会将已经发生的节点失败改为成功

### Requirement: Designer local logs remain independent

系统 SHALL 保留 Designer 当前会话和本机历史文件日志及已有筛选能力，各来源保留各自的范围和阅读状态。

#### Scenario: Inspect a Runtime connection failure

- **WHEN** Designer 无法连接 Runtime 或查询 Runtime 日志失败
- **THEN** 该记录显示在 Designer 本机日志
- **AND** 不将其误判为 Runtime Job 失败或重新写入 Runtime

### Requirement: Export and retention boundaries are explicit

系统 SHALL 保留固定上界与完整分页导出，明确区别整个 Job、当前已加载结果和设备近期缓存的范围。

#### Scenario: Export a whole Job

- **WHEN** 用户选择导出整个 Job
- **THEN** 系统以该 Job 为范围，固定本次导出上界，复用现有完整分页读取与导出能力
- **AND** 导出包括上界内仍可查询但未加载到界面的事件，不受详情当前页面、本地等级筛选或 10000 条窗口限制
- **AND** 导出期间新增事件不改变本次上界，取消或失败不被报告为成功
- **AND** 导出范围描述为当前仍可查询的记录，不宣称包含已清理或从未记录的历史

#### Scenario: Export device diagnostics

- **WHEN** 用户导出设备诊断
- **THEN** 导出范围标注为已返回的近期缓存
- **AND** 沿用可获得的截断信息，被截断缓存不被描述为完整设备历史

### Requirement: Related Jobs are explained using actual run identities

系统 SHALL 从已有运行快照的 Runtime、工程、运行组和来源任务关系确认关联 Job，展示实际未终态成员的数量、名称、状态及查看入口；不得仅凭 active、相同名称、时间邻近或循环路径猜测关联。原因尚未明确时 SHALL 使用次要说明“结束处理未完成”，保留原执行结果。

#### Scenario: Explain a completed producer with running consumers

- **WHEN** 已完成主任务在当前快照中有可确认的未终态下游 Job
- **THEN** 显示“还有 N 个关联任务未结束”，N 来自实际匹配去重后的成员
- **AND** 展开后可看各任务状态和独立日志，关联入口仅为快捷引用，不增加主目录任务计数
- **AND** While 轮次、同一 Job 的子工作流调用和连续根调用不计为关联 Job
- **AND** 同名但身份不匹配的其他任务不进入列表

#### Scenario: Keep ordinary subworkflow calls inside their Job

- **WHEN** 一个普通子工作流、循环体或连续根调用在同一 Job 内执行
- **THEN** 使用实际 workflowRunId、parentWorkflowRunId、nodeRunId 和 iterationPath 在任务内展示，不生成额外首页 Job 或关联任务数量
- **AND** 普通子工作流沿用现有同步等待语义，正常情况下子调用未返回时不将整个 Job 误报为已完成
- **AND** 只有实际创建独立 Job、且具备来源与运行组关系的下游才进入关联任务列表
- **AND** Dispatch 与 Join 的既有提交 / 等待语义保持，不要求源任务一定先于下游结束

#### Scenario: Explain an owned terminal task without a specific reason

- **WHEN** 终态任务仍 active，但当前数据不能确认具体未结束的关联任务
- **THEN** 显示“结束处理未完成”，详情说明 Runtime 尚保留该次任务的处理状态、具体未完成项暂未提供
- **AND** 不声称相机未释放、正在保存结果或清理一定正在推进
- **AND** 普通已完成且没有后续活动的任务不附加该说明

#### Scenario: Open a related Job and return

- **WHEN** 用户从任务查看某个真实关联 Job 后返回来源任务
- **THEN** 以真实目标身份打开其既有日志页
- **AND** 使用轻量来源书签恢复原筛选、视图和可查询的选择/阅读锚点
- **AND** 继续使用单个有界事件窗口与现有查询，不建立每任务长期读取器或多个持久事件缓存
- **AND** 若原锚点已经不可查询，明确提示并显示可用范围，不伪造原位置已恢复

#### Scenario: Round-trip from a temporary iteration context

- **WHEN** 用户在普通告警／错误筛选下查看本轮上下文、选中 INFO 事件并打开详情，再访问关联 Job 后返回
- **THEN** 先恢复可查询的临时范围，再恢复原事件、详情抽屉和阅读位置，即使普通告警筛选会隐藏该事件
- **AND** 用户再返回原筛选时，恢复进入临时范围前的选中项、展开和阅读位置
- **AND** 临时范围或位置无法恢复时保留可确认范围并提示，选择和范围均使用原有执行身份

### Requirement: Optional date-time filters preserve the existing range contract

系统 SHALL 在任务已加载事件筛选、服务端事件历史查询和历史任务目录的现有时间条件上提供可空日期时间输入，显示本机时区，保留毫秒精度，并转换为原有整数毫秒参数。系统 SHALL 沿用开始含、结束不含的范围语义，不新增时区 RPC 或改变导出范围。

#### Scenario: Query using a local date-time range

- **WHEN** 用户在任一已有时间筛选入口设置有效日期时间范围
- **THEN** 系统按本机时区转换为同一个时间点的整数毫秒，保留毫秒值
- **AND** 起点相等的事件包含，终点相等的事件排除
- **AND** 任务事件使用事件发生时间，历史任务目录使用任务结束时间，标签明确区分
- **AND** 控件回填、条件摘要、历史下一页和返回沿用同一已生效范围，不在翻页时悄悄重算

#### Scenario: Leave either bound unset or use an empty interval

- **WHEN** 用户清空任一时间边界，或者输入相等的开始与结束时间
- **THEN** 清空边界代表不限制该侧，默认不自动附加当天或 1970 年边界
- **AND** 相等边界表示合法空区间，不扩大成全天或报错
- **AND** 清空后已有范围参数被实际移除，而不只清除显示文本

#### Scenario: Reject a reversed range without changing the displayed result

- **WHEN** 用户输入开始晚于结束的范围
- **THEN** 界面给出简短行内提示，不发出无效查询，也不以倒置条件重绘为“无结果”
- **AND** 已显示结果与其生效条件保持，输入纠正后可以正常应用
- **AND** 无需弹出额外确认框或新增重试机制

#### Scenario: Keep history filtering and full Job export separate

- **WHEN** 用户从已加载筛选进入历史查询或发起完整任务导出
- **THEN** 历史查询只携带原接口支持且范围明确的有效条件，任务身份固定，结果仍为历史命中列表
- **AND** 完整任务导出继续使用原完整 Job 范围及固定上界，不意外继承本地日期/等级/节点筛选
- **AND** 返回任务时恢复原有效筛选和阅读状态

### Requirement: Advanced filters retain capability with compact disclosure

系统 SHALL 默认收起高级筛选，将常用条件与技术条件分层显示，保留全部原有字段、精确 ID 和原始轮次路径能力；任何折叠动作不得清除已应用条件。候选名称与轮次 SHALL 仅来自当前可用且身份匹配的元数据或已加载记录。

#### Scenario: Collapse technical filters while they are active

- **WHEN** 用户设置调用 ID 或原始轮次路径后收起技术条件
- **THEN** 筛选继续生效，条件摘要或计数仍可见，并可清除
- **AND** 重新展开后保留输入值
- **AND** 不将隐藏字段当成无条件，也不删除原服务端历史查询入口

#### Scenario: Choose names and iterations without losing precision

- **WHEN** 用户按工作流、节点或循环轮次筛选
- **THEN** 已知名称绑定真实 ID，同名对象可区分，用户仍可输入精确 ID
- **AND** 第 1 轮、第 2 轮来自真实 0-based 路径的展示转换，嵌套路径保留
- **AND** 候选来自已加载范围时明确标注，不声称列出全部历史轮次
- **AND** 不新增全量目录查询、历史扫描或按可见位置重编号

### Requirement: Execution labels are concise while identity remains exact

系统 SHALL 在执行树主行优先展示可确认名称、循环轮次、状态、时间和消息；完整调用/执行 ID 与时间不完整的详细原因放入详情或提示。系统 SHALL 保留以真实 workflowRunId、parentWorkflowRunId、nodeRunId、iterationPath 构造和选择执行结构的方式。

#### Scenario: Read repeated and nested executions

- **WHEN** 同名工作流或节点在多轮循环、嵌套调用或连续运行中出现
- **THEN** 不同执行保持独立，折叠展开、筛选祖先、选择和时间顺序切换仍指向原对象
- **AND** 主行不默认重复输出完整 UUID，详情可查看和复制完整身份
- **AND** 没有名称时使用真实标识回退，不能凭日志顺序制造全局调用次数

#### Scenario: Read an incomplete execution time

- **WHEN** 起止记录或必要时间信息不足
- **THEN** 时间列采用简短的不完整状态，详情或提示展示实际已知原因
- **AND** 不把缺失字段伪装为已结束、未执行或历史已清理
- **AND** 不为了缩短文字隐藏未知状态或推算不存在的耗时

### Requirement: Readable framework text does not rewrite original events

系统 SHALL 在既有显示适配处为已知框架默认事件和状态提供中文易读表达，继续复用共享详情组件；算子主动消息、用户业务文本、未知值、原始记录与导出内容 SHALL 保持。

#### Scenario: Read a known framework event or result status

- **WHEN** 用户在任务详情或既有算子调试详情中查看可识别的框架事件/状态
- **THEN** 显示易读文案，例如循环开始、该轮开始、已完成
- **AND** 原始 JSON 保留原 eventType、status、message 和完整执行身份
- **AND** 共享映射不扩成逐算子专属渲染器

#### Scenario: Read a user message or an unknown framework value

- **WHEN** 所选日志包含算子自定义消息，或未知事件/状态
- **THEN** 保留原文或原值，不猜业务含义，不按英文字符串全局替换
- **AND** 复制当前记录与完整导出仍对应实际原始记录
