# Runtime 运行日志工作台设计 · V2.3 算子覆盖补齐

## 1. 基线与现状

本设计以 `9d59fd16a1d0d3ec0c60725c839ff38183d6916b` 为固定核查基线；2026-11-21 再次查询仓库 HEAD，仍为该提交。实施前仍需核对实际 HEAD 和工作区差异。本包是待实施计划，当前没有产品实现或 Qt 验收结果。

`reference/prototype.html` 保留此前已确认的页面方向。V2.3 将易读详情补齐到框架执行记录和单算子调试，主动日志是附加信息；模拟数据、模拟连接不代表正式 UI。本轮工作是补齐计划、原型与交付包，没有实施产品源码。正式实现沿用 Python 3.10、PySide2 / Qt Widgets；保持已确认布局与操作关系，不引入 WebView，不按 HTML DOM 机械拆类。

已完成的日志整合基线见 `docs/plans/2026-11-03-logging-consolidation-implementation-plan.md`（v3.5）及 `docs/testing/2026-11-03-logging-consolidation/README.md`。本 change 不重做其 EventStore、SQLite、JSONL、`QueryRuntimeLogs`、`RuntimeGlobalLogs`、`DesignerLogWriter`、算子日志或设备故障汇总。

当前关键能力：

- `ListRuns` / `GetRunState`：读取当前 Runtime 进程所持有的 Job 运行目录。
- `JobRepository.get()` 可从 SQLite `jobs` 表回读单条 Job；但 `JobRepository.all()` 只返回当前进程内 `_jobs`，不能作为跨 Runtime 重启的完整历史列表。
- `jobs` 表已保存 Job 状态、工程、工作流、受理时间、开始时间、结束时间、耗时、错误码和错误消息。
- `QueryRuntimeLogs` 已支持全局 eventId 固定上界分页，并可按工程、Job、设备、模块、等级、工作流、节点和时间范围筛选。
- `RuntimeEvent` 已保存 `workflowRunId`、`parentWorkflowRunId`、`nodeRunId`、`iterationPath` 和 `timestampMs`。
- `StructuredLogEntry.fromRuntimeEvent()` 已读取 `workflowRunId` 与 `iterationPath`，但当前没有保留 `parentWorkflowRunId`。
- `LoopRunner` 已发布 `loop.iteration.started` / `loop.iteration.completed`；`loop.started` 已携带循环 `mode`，迭代事件 payload 当前包含 0-based `iteration`。不再另造重复的 `loopType` 字段。
- While 每轮先发 iteration started，再检查条件；条件为 false 时仍发 iteration completed，但循环体没有执行。UI 必须区分“条件检查”和“循环体实际执行轮次”。
- `DeviceDiagnostics` 已提供按 Runtime / 工程加载代次 / deviceId 隔离的有界近期缓存；`GetDeviceDiagnostics` 已存在。
- `executeDevice` 的执行身份放在设备操作 `source` 中；设备 Manager 产生的原生事件仍将该信息放在嵌套 source。`DeviceDiagnostics._event()` 当前主要从顶层字段读取身份，需集中归一化嵌套 source。
- `captureDevice` 当前 source 字段不完整；本 change 同时对齐采图和普通设备操作的上下文传递。
- `DeviceDiagnosticsView` 已支持通道筛选、HEX / UTF-8、详情和导出已返回缓存。
- 当前日志详情已使用 `LogJsonTree`，具有字段 / 值 / 类型、对象与数组惰性展开、长文本和复制能力；现状不是只有一块原始 JSON 文本。本版新增默认的算子易读层，保留该树作为原始记录检查入口。
- 正式 Job 的 `WorkflowRunner` 为已调度节点统一产生 started / completed / failed / skipped 和可用 `ioSummary`；`OperatorExecutor` 为进入 executeNode 的注册算子注入 logger，内置与外部注册算子共用主线。主动业务日志仍取决于算子是否调用 logger，不是每次执行自动拥有 payload.data。
- 基线 82 个内置 manifest 入口的静态核查发现 4 个入口类有主动 logger 调用，78 个没有直接调用；部分已调用入口的日志仅位于兼容执行支路。此计数不是正式运行的日志覆盖率，78 个仍可拥有框架事件、指标和诊断；不据此给所有算子批量加重复日志。完整清单与方法见本包覆盖审查材料。
- 单算子调试已有 `ReadOperatorDebugEvents` 和 `GetOperatorDebugExecution` 两类数据；`OperatorDebugWindow` 的普通日志当前主要使用 `str(event)`。调试 worker 直接调用 `OperatorExecutor`，不经过 `WorkflowRunner`，没有自动 node.completed / ioSummary 的保证；本版复用已有会话读取与结果，不将调试伪装成生产 Job。

## 2. 总体导航与归属

### D1. 只保留两个一级入口

1. **Runtime 运行日志**
   - 运行任务
   - 单次任务详情
   - Runtime 系统事件（只显示没有 Job 归属的 Runtime 事件）
2. **Designer 本机日志**
   - 当前会话
   - 本机历史日志文件
   - 流程设计、页面设计、工程编辑、Runtime 客户端连接与查询错误

“运行任务”是 Runtime 日志的任务视图，不是与 Runtime 日志并列的另一套日志来源。先按 `originApp` / 已确认的采集来源区分 Runtime 和 Designer；仅在 Runtime 来源内，再按真实 Job 归属分任务事件与系统事件。Designer 客户端错误即使携带 jobId / workflowId / nodeId，仍只属于 Designer 本机日志。旧记录缺少 originApp 时沿用已有可信来源，不凭 Job ID 猜来源。

任务关联的 Runtime 算子、设备、工作流、循环和页面运行事件在对应 Job 中查看；查看者所在机器不能改变生产者归属。

### D2. Runtime 系统事件不得重复展示 Job 事件

- Job 事件通过任务详情查询。保留记录原始 jobId；在同一个归属适配点计算有效 Job：Runtime 的普通 jobId 直接使用，`__runtime__` 才使用 payload 中明确的真实 Job ID。树分组用有效归属，原始详情/导出不重写来源字段，不再次持久化一份副本。
- `jobId='__runtime__'` 且 payload 没有真实 `jobId` 的记录，才进入 Runtime 系统事件视图。
- `__runtime__` 分区中 payload 明确携带真实 Job ID 的设备故障摘要或恢复摘要，仍归入对应 Job 的事件上下文；现有 `queryRuntimeLogs(jobId=...)` 已将这类摘要纳入 Job 查询。
- 系统事件页复用 `QueryRuntimeLogs` 查询 `jobId='__runtime__'`，再剔除 payload 中明确关联真实 Job 的记录；过滤后当前页为空但服务端 `hasMore=true` 时，继续读取后续页，不能把“本页无可见记录”当作“没有系统事件”。
- 系统事件筛选和分页使用固定 `upperEventId`；游标按服务端返回的 `nextEventId` 前进，不能按过滤后的可见记录数量推进。
- Designer 连接失败、日志查询失败属于 Designer 本机日志，不伪装成 Runtime 故障。
- Runtime 应用启动 JSONL 属于 Runtime 主机本机文件；本 change 不通过远程文件系统把它混入 Designer 本机日志。

## 2.1 页面结构示意

以下是交互层级，不要求按图中的名称机械拆成大量 Qt 类。

```mermaid
flowchart TD
    A[Designer 日志窗口] --> B{日志来源}
    B --> C[Runtime 运行日志]
    B --> D[Designer 本机日志]
    C --> E[运行任务首页]
    E --> F[正在运行]
    E --> G[最近已结束]
    E --> H[更早历史]
    F --> I[Job 详情]
    G --> I
    H --> I
    I --> J[运行概览]
    I --> K[执行结构]
    I --> L[事件时间顺序]
    K --> M[工作流调用]
    M --> N[While / ForEach / Repeat]
    N --> O[迭代轮次]
    O --> P[节点与事件]
    P --> Q[右侧详情抽屉]
    P --> R[设备诊断下钻]
    C --> S[未关联 Job 的 Runtime 系统事件]
    D --> T[当前会话]
    D --> U[本机历史文件]
```

```mermaid
flowchart TD
    A[Job] --> B[根 WorkflowRun 1]
    A --> C[根 WorkflowRun 2 ...]
    B --> D[节点 / Loop 节点]
    D --> E[IterationPath 第 1 轮]
    D --> F[IterationPath 第 2 轮]
    E --> G[节点事件]
    E --> H[子 WorkflowRun]
    H --> I[子节点事件]
    F --> J[节点事件]
    C --> K[后续工作流调用]
```

### 页面布局规则

| 页面 | 主体 | 次要信息 |
|---|---|---|
| Runtime 运行任务首页 | 正在运行 + 最近已结束任务 | 工程、状态、开始时间、已运行时长、最近活动 |
| Job 详情 | 运行概览 / 执行结构 / 事件日志 | 高级筛选默认收起；右侧详情按需打开 |
| Runtime 系统事件 | 未关联 Job 的 Runtime 事件列表 | 等级、模块、设备、时间筛选 |
| Designer 本机日志 | 当前会话或本机文件日志列表 | 流程设计、页面设计、连接诊断筛选 |
| 设备诊断 | 当前设备近期诊断缓存 | 通道、HEX / UTF-8、回执、缓存范围说明 |

详情抽屉不固定占用底部高度。关闭后主列表恢复宽度和滚动位置；窗口变窄时允许抽屉覆盖主内容，但不得丢失当前选择。

## 3. Runtime 运行任务首页

### D3. 首页分区

默认首页固定为：

- **正在运行**：从 `ListRuns` 获取活动 Job，包含运行中、启动中、停止中及现有运行组所有权语义判定为仍活动的任务。
- **最近已结束**：按结束时间倒序显示最近完成、失败、已停止任务。
- **更早历史**：进入持久化历史查询，按工程、状态和时间范围检索。

正在运行区域始终在上方，不因新日志到达而改变任务卡片位置。任务内部事件更新只刷新“最近活动”摘要和状态。

### D4. 历史任务目录

新增只读 `ListJobHistory`，直接读取现有 SQLite `jobs` 表；不新增表、第二份任务账本或事件数聚合。

| 合同 | 明确定义 |
|---|---|
| 请求 | 当前查询目标 `runtime_instance_id`、可选 project_id、终态 statuses、可选结束时间区间、limit、cursor |
| 状态 | 仅 `COMPLETED / FAILED / ABORTED`；未指定 statuses 时查全部终态；ABORTED 显示“已停止” |
| 返回 | ok/code/message、查询目标 runtime_instance_id、Job 摘要列表、has_more、next_cursor |
| 摘要 | jobId、projectId、projectRevision、workflowId、status、acceptedAtMs、startedAtMs、endedAtMs、errorCode、message、stopMode；不存在的时间为未记录 |
| 排序 | `COALESCE(endAt, startAt) DESC, jobId DESC`；cursor 包含最后实际返回行的这两个原始排序值，由服务端编码/解析，客户端只透传 |
| 翻页 | 下一页严格小于上一页的排序键；默认 limit=50，上限 200；用一条 look-ahead 判断 has_more；空末页 has_more=false、next_cursor 为空 |
| 范围 | 开始时间包含、结束时间不包含；筛选的是实际 endAt。缺 endAt 的旧终态行仅在未限定结束时间时参与排序，并标“结束时间未记录” |

- `acceptedAt` 是受理时间；旧行没有 acceptedAt 时才可使用 `startAt` 作为受理时间回退。`startedAt` 才是实际执行开始时间。不得把 `startAt` 标成实际开始。
- 同一翻页链固定筛选；改条件、刷新或目标 Runtime 变化时重开第一页。新结束任务在刷新后出现；不承诺跨状态变更的数据库快照事务。
- 当前活动任务继续由 `ListRuns` / `GetRunState` 提供。实时列表与历史摘要按 Job ID 去重，当前有效 `RunState` 优先；是否可停止只认当前身份及 `allowed_actions`。纯历史行没有停止按钮；不能把仍持有下游资源的当前 RunState 行误当纯历史。
- jobs 行没有原始 Runtime 实例 ID，不虚构它。RPC 返回的 runtime_instance_id 是当前查询目标；每条历史事件的 runtimeInstanceId 是其原始生产实例，两者不同不代表事件无效。
- 工作流显示名只在工程版本对应时使用，否则显示 workflowId。`executionMode='runtime'` 不是连续模式标志；无可确认配置时不推断单次/连续。
- 使用现有实例切换校验；在既有 `RuntimeInfoReply.capabilities` 加 `job_history_query_v1`。旧端未支持时保留当前进程的 ListRuns 目录和原历史日志查询，提示“跨重启任务目录不可用”，不造本地替代账本。
- 只有实际查询计划证明需要时才加索引；保留 ListRuns 原语义，不做首页成功率、全量计数或统计仪表盘。

## 4. 单次 Job 详情

### D5. 三个视图

1. **运行概览**：Job 状态、运行时长、开始时间、结束时间、最近活动、错误摘要、当前活动工作流调用。
2. **执行结构**：按工作流调用和循环层级组织事件，默认进入此视图。
3. **事件日志**：按事件时间排序查看同一 Job 的事件。

执行结构与时间顺序使用同一批已加载原始事件和同一筛选状态。切换视图不重置游标、筛选、选中事件和滚动位置。

### D5.1. QueryRuntimeLogs 的最近页与翻页合同

只扩展已有 `QueryRuntimeLogsRequest`：增加 `latest_first`（默认 false）和可选 `before_event_id`。沿用全局 eventId、固定 upper_event_id、既有 SQL 筛选、回复条数及字节限制；不新建日志查询 RPC。

| 模式 | 请求与范围 | 回复游标 |
|---|---|---|
| 原前向模式 | latest_first=false，不能带 before；`after < eventId <= H`，eventId ASC | 完全保留原语义：还有页时指最后返回行；读完指 H |
| 最近第一页 | latest_first=true，after=0，无 before；未给 H 时取全局 MAX(eventId)，`eventId <= H`，DESC | 非空页 next_event_id=实际返回原始行的最小 eventId，包括末页 |
| 更早页 | latest_first=true，after=0，before>0；沿用 H，`eventId < before AND eventId <= H`，DESC | 同上；before 使用上一页的 next_event_id，而非 UI 可见行 |
| 降序空末页 | 没有匹配原始行 | has_more=false，next_event_id=0；客户端不得再拿 0 发起下一页 |
| 字节预算截断 | 先按既有预算撤回放不下的最后一条 | 返回已装入的原始行；next 指最后成功返回行，has_more=true；未装入行必须留给下一页 |

- 降序首条即超预算：沿用 `EVENT_TOO_LARGE`，不提交游标、不自动重复请求，不跳过该记录；沿用现有错误展示/手动重试。
- `latest_first=true` 与正向 after>0 互斥；before 仅在降序模式有效。沿用非负上界、时间范围、limit=默认200/最大1000 等现有输入校验；不加重复的多层校验框架。
- has_more 来自服务端原始结果的 look-ahead / 字节预算，和客户端筛选后有几行无关。过滤掉一整页仍按服务端 next 继续，直到命中或 has_more=false；查询保持异步可取消，不能在 UI 线程递归拉页。
- 同一历史链固定 H；新事件不会改变“加载更早”的历史边界。降序数据在客户端按 eventId 升序合并；时间视图可按原始 timestampMs、eventId 排序，但不能据显示顺序计算游标。
- 用现有 capabilities 增加 `runtime_logs_recent_v1`，明确代表支持上述两字段及降序回复语义。未宣告能力时保留旧前向历史查询并说明最近页不可用；不让旧服务器忽略新字段后把最早页当最新页，不后台扫完整个 Job 模拟最近页。

### D5.2. 最近快照与唯一实时源的交接

所有实时事件继续由现有 `RuntimeGlobalLogs` 提供。禁止为每个 Job 再开 `StreamJobEvents` 或独立常驻轮询。最近页、更早页和必要补页只是有限 QueryRuntimeLogs 请求。

1. 先确认全局 reader 已完成初始定位，再登记 Job 详情消费者，然后发该 Job 的最近原始页请求；固定 project / 有效 Job，不预加等级、节点、设备或事件类型条件。初始化期间暂存对应实时记录，暂存也受 D5.3 上限约束。
2. 最近页得到历史上界 H；合并快照及暂存中 eventId>H 的事件。全局读取器的交付上界 W 必须是**当前目标中整页已处理的全局进度（服务端 next_event_id）**，不能用某个 Job 最后一条可见事件代替。
3. 如需补齐打开/切回期间未保留的区间，固定当时 W，以同一个 QueryRuntimeLogs 前向分页读取 `(H, W]`（只限该 Job），读完就结束补页；之后继续由唯一全局 reader 交付。W<=H 时无需补页，reader 自行继续。若当前消费层没有 W 通知，仅给 reader 增加一个在该页事件入消费队列之后发出的进度通知；消费端顺序处理到该通知时确认 W，不另建进度服务。
4. 所有路径进入同一 Job 事件窗口；在当前查询目标/代次内按全局 eventId 去重，不按 Job-local sequence、消息或 timestamp 去重。旧 Runtime 生成的历史事件仍应保留，不用行内 runtimeInstanceId 过滤当前数据库历史。
5. 沿用现有查询 generation、Runtime target 校验与取消；打开其他 Job、改工程、Runtime 重启后拒收旧查询响应并重建窗口。不增加第二套 session token 或任意版本兼容体系。

事件不足/窗口退窗时如实标范围。“不重不漏”指所请求、仍可查询区间的分页和合并，不承诺保留策略已删除的记录可恢复。

### D5.3. 长时间运行的有界窗口

- 复用现有 `maximumEntries`（默认 10000）限制原始事件；树只由该窗口和必要祖先容器建立。不能靠保存所有展开分支、所有 Job 窗口或无限待追加队列绕开上限。
- 用户在最新位置时跟随新增，超上限从旧端退窗；主动阅读历史、展开旧轮次或选中旧事件时暂停自动跟随，固定当前阅读窗口。新事件仅显示数量/有新事件提示，不强行滚动，不无限保存新载荷。
- “加载更早”合并去重后，允许最新端的非阅读区域退窗；保留当前选中事件和阅读锚点，以及其最小祖先路径。若这次扩窗无法同时保住锚点和容量，保持当前窗口，提示已达窗口上限并提供切换到更早范围/返回最新的主动操作；不要悄悄把刚加载的早页裁掉。
- 用户主动“返回最新/切换范围”才可换阅读窗口。对应边界显示“部分事件已移出当前窗口，可重新加载”；展开状态仅保存当前窗口可用路径，不保证所有曾展开分支永久常驻。
- “有新事件”计数仅表示已观察到的新事件；重新连接期间未完整计数时显示提示，不伪称全量总数。无需为计数扫描数据库。

### D6. 执行树的分组规则

树形结构按真实执行身份构建，不按 workflowId / nodeId 名称猜测：

- Job 是根任务。
- 工作流调用以 `workflowRunId` 为身份。
- 子工作流通过 `parentWorkflowRunId` 挂到父调用；调用节点使用该子调用事件中的 `nodeId` / caller node 关联。
- 循环由 `loop.iteration.started` / `loop.iteration.completed` 识别；以 Job、workflowRunId、循环 nodeId 和 iterationPath 组合定位某一轮。
- 嵌套循环按 `iterationPath` 的前缀层级展示；Runtime 的 0-based 索引在 UI 显示为第 1 轮、第 2 轮。
- 节点事件通过 `nodeRunId` 归并开始与终态，通过 `workflowRunId` 和 `iterationPath` 放回正确的工作流调用与循环轮次。
- 连续运行的根工作流每次调用生成新的 `workflowRunId`；在同一 Job 下独立展示。仅统计当前已加载范围的不同根调用 ID，标“已加载 N 次调用”，不能把这一数量称为整个 Job 累计调用次数；未知历史编号显示调用 ID / 开始时间，不用 cycleId 或日志条数代替。
- `cycleId` 是 Runtime 周期/配置边界标识，不是连续运行次数，也不是循环轮次。
- 已知 parentWorkflowRunId 但父事件未加载时，以该真实 ID 保留轻量父容器并标“父调用详情未加载”；确实没有关联字段时放入“未完整关联事件”，不得按相邻时间猜父子。普通非循环的空 iterationPath 合法；字段缺省与合法空值不要混为一谈。
- 并行分支按各自真实事件和调用身份并列展示；树形排列不代表它们串行执行。

### D7. 循环事件补充信息

- 循环类型复用 `loop.started.payload.mode`，不增加重复 loopType。总量精确已知的 ForEach / Repeat 才提供 `totalIterations`，每个循环调用只记录一次。
- 数量必须来自既有输入校验/执行路径，不为填写元数据复制参数校验。若现有 loop.started 早于数量确认，可局部调整为既有校验之后、首次迭代之前只发出一次，保留失败事件与调度语义；不得提前展开整个生成器、修改循环上限或为日志多执行一次输入。
- While 不显示未知总量或百分比；maxIterations 是限制，不是实际总量。
- 对 `_while` 和 `_whileV2` 两条现有路径，在 `loop.iteration.completed` 可选 payload 中写 `bodyExecuted`：true=循环体已执行并正常完成；false=条件为 false、未进入循环体；缺省=该记录未提供这个信息。不可用 `bool(payload.get(...))` 将旧历史误判为 false。
- 条件/循环体失败或取消，保留现有失败事件和未配对迭代，不伪造 completed，不写 false。false 检查仍独立可展开，显示“退出条件检查 · 未进入循环体”，保留条件工作流事件，不计入已完成循环体次数。
- 旧事件无 bodyExecuted 时仍显示“第 N 次迭代”，详情可注明执行情况未记录；不得仅靠工作流定义推断该次条件实际真假。mode / totalIterations 缺失时按未记录显示，不为补旧日志元数据新增工程定义查询或推断分支。

## 5. 执行时间

### D8. 统一时间口径

耗时是执行经过的墙钟时间，包含等待、调试暂停、框架开销，不声称是纯算子 CPU 时间。事件计时只使用原始 timestampMs；`StructuredLogEntry` 保留 timestamp 原始有效性，显示排序可回退，耗时不得使用接收时间回退值。

| 对象 | 开始 | 结束 | 关联键 |
|---|---|---|---|
| Job | startedAt | endedAt，重启确认另见下文 | jobId |
| 工作流调用 | workflow.started | workflow.completed / workflow.failed | jobId + workflowRunId |
| 循环检查/轮次 | loop.iteration.started | loop.iteration.completed | jobId + workflowRunId + loop nodeId + iterationPath |
| 节点 | node.started | node.completed / node.failed | jobId + workflowRunId + nodeRunId |

- Job 的 acceptedAt/startAt 是受理时间；实际执行时长使用有效 startedAt。保留数据库 durationMs 原语义（当前含受理等待），不直接拿它作为执行时长，也不改历史值。
- 有 startedAt、无 endedAt，且 Job 自身状态为 RUNNING 或 STOPPING 时继续显示估算已运行时间；ACCEPTED/STARTING 没有实际开始则显示等待。终态 COMPLETED/FAILED/ABORTED 停止动态计时，即使 RunState.active 因下游收尾为 true。
- `E_RUNTIME_RESTARTED` 的 endAt 是新 Runtime 确认中断的时间，不是旧进程实际结束时间：显示“执行中断；实际结束时间未记录”，可展示“重启确认时间”，不得把两者之差标成精确执行耗时。
- 内部节点/调用只有具备有效开始事件、确认该执行仍未终止的连续生命周期证据，并且 Job 自身仍运行时才动态估时。最近页里缺结束事件不等于仍在运行；分页/掉线有缺口且没有可用证据时显示“状态/时间信息不完整”。当前定位遵循 D9。
- Job 已终态时，未配对内部对象停止动态计时，显示中断或结束事件缺失；不以 Job endedAt 伪造每个内部节点终点。`workflow.started` 后输入校验失败可能确实缺少终态事件，不借 UI 改写生产者生命周期。
- 没有 started 的 skipped 节点不显示虚构 0 ms。结束早于开始或原始时间无效时不计算精确耗时。
- 原因提示必须有证据：已知仍有未加载页可说“尚未加载完整”；已读取记录缺字段可说“该记录未包含此字段”；只有接口/元数据明确证明清理时才说“已清理”。其余说“时间信息不完整，原因未确认”，不为区分原因新增清理账本。
- 默认紧凑显示耗时，详情可切换起止时间；实时数值注明估算，结束配对后用原始时间固定。连续两次根调用间的等待不计入任何单次 workflowRun 时长。
- 设备阶段时长仅展示现有 payload 的明确计时；设备聚合摘要 durationMs 另按 D11 解释，不从两条日志间隔推算驱动耗时。

## 6. 事件筛选与查看

### D9. 筛选范围、上下文与当前定位

高级筛选默认收起，收起后保留条件并显示摘要。概览信息也可收起，保留任务名、状态、运行时间等紧凑摘要。常驻关键字、等级快捷筛选、执行结构/时间顺序、定位当前；不删已有高级字段。

| 查看范围 | 行为与限制 |
|---|---|
| Job 执行结构 + 同范围时间顺序 | 共用未按等级裁剪的已加载原始窗口，再本地筛关键字、等级、模块、工作流、节点、设备、事件类型、调用 ID、循环路径、时间；标“筛选范围：已加载事件” |
| 既有历史查询 | 保留服务端 project/job/device/component/levels/workflow/node/start/end 条件，在固定上界的整个指定范围查询并分页；显示命中列表，不能当成完整执行树 |
| 新调用 ID / 循环路径 / 关键字 | 本期仅本地过滤；原服务端不支持这些字段，不伪称全库全文检索，也不因本期未加索引而删除原有历史查询 |

- Job 详情已有高级区域提供明确的范围选择/历史查询操作，不增加第三个日志来源。切换到历史命中列表时保留 Job 原窗口状态；返回恢复。当前 Job 范围中的 Job 条件固定，跨 Job 查询从原有历史查询入口进入。
- 两个本地视图的筛选、选中事件、游标共用，分别保留滚动锚点；历史查询是另一查询范围，单独保存状态，不能把它的分页游标传入 Job 窗口。
- 先从未裁剪原始事件建立结构，再投影筛选。命中的父调用/轮次/节点作为路径容器保留，无关兄弟可隐藏。WARN/ERROR 的服务端命中列表不能替代含 INFO 生命周期的结构源。
- “查看本轮上下文”临时去掉该轮内的本地条件，按真实调用/iterationPath 限定窗口，标题明确“本轮已加载上下文”。可沿 D5.1 加载更早页，每页重新投影；尚有未知区间就继续标范围，不承诺一键取得整轮。配对开始/结束及中间事件均已加载时才可说“该已知区间完整”；不存在的历史不能由 UI 补造。
- 从历史搜索命中项查看结构时，携带 Job 与事件 ID 回到 Job 窗口；为跳转建立新的查询代次，H 采用该历史搜索已确认上界（或重新取得且 H>=目标 eventId），不能沿用旧 Job 窗口较早的上界；在该固定 H 内使用 before=目标 eventId+1 取包含目标的原始降序页，再按需加载相邻页；无法立即取得祖先时保留真实身份容器和“上下文未加载”，不把稀疏命中当完整调用树。不新建按时间邻近关联算法。
- “定位当前”先核对当前 RunState 的 Job 身份和非终态；使用同 Job / 当前连接代次中连续观察的生命周期。既有 `NodeRunInspection` 只有在身份匹配且连续生命周期证据未中断时辅助定位；其中缓存的 RUNNING 本身不构成当前状态证明。`RunState` 本身没有当前 nodeRunId，最近一条日志也不能证明当前节点。
- 有可靠活动位置时展开并选中它；被 WARN 等筛选隐藏时临时展示目标及祖先，返回恢复原条件。缺少节点证据时回到最新活动区，提示“当前执行节点尚未确认”，不任意选最后日志当活动节点，不为此新增执行状态 RPC 或全量回放任务。
- 用户阅读旧记录时按 D5.3 暂停跟随；回最新是明确动作。上下文/抽屉/设备往返均恢复原筛选、选中、展开和阅读锚点。

### D10. 右侧详情抽屉

选中事件后在右侧展开详情，默认显示：

- 发生时间、等级、消息、日志代码（code 也可能是 INFO / WARN 业务代码，不一律称错误码）；
- Job、工作流、调用、循环路径、节点、设备；
- 有效开始/结束时间与耗时（可计算时）；
- 设备槽位、通道、请求回执（适用时）。

本版增加“易读详情 / 原始 JSON”切换，默认易读详情。技术标识按需展开；关闭抽屉不丢失事件选择、筛选、树展开状态或阅读位置。以下规则仅改展示，不新增日志生产协议、专属存储、查询 RPC 或每个算子的渲染器注册框架。

#### D10.1. 通用执行记录与附加日志

- **通用执行信息是必需展示能力**，不以算子有没有主动 node.log 为前提。直接从当前选中的 `node.started / node.completed / node.failed / node.skipped` 读取实际 `payload.ioSummary`、`metrics`、`diagnostics` 及原因字段，按以下来源显示；不存在的字段给出简短未提供状态，不补造值。只因没有 node.log 就把整个详情做成空页，不满足本设计。

| 当前选中记录 | 易读内容与语义 |
|---|---|
| node.started | 节点开始记录及已有输入摘要；该条开始记录没有输出，不据此断言节点现在仍在运行 |
| node.completed | 当前记录的输入 / 输出摘要、实际返回的指标和诊断；按摘要类型显示标量、数量、shape / dtype 及缺省说明 |
| node.failed | 失败代码 / 消息、当前记录中的输入摘要、指标与诊断；不从其他轮次填入有效输出 |
| node.skipped | 跳过原因、已有输入摘要及 missingInputs / blockedByBranches 等实际字段；不伪造执行耗时或成功输出 |
| node.log | 消息与算子主动记录的日志字段；作为执行信息的补充，不是其他四类记录可读化的替代 |

- 输入 / 输出使用已有 `run_inspection.normalizeInspection` 和端口摘要语义，指标与诊断使用当前 payload 的真实字段。缺省、空对象和明确不可用不同：分别显示“本记录未提供”“无附加指标 / 诊断”或现有不可用原因。无需为显示缺省状态增加后端字段或追查缺失原因。
- 详情默认只解释选中记录。若提供已加载同一次执行的关联信息，使用实际 Job / workflowRunId / nodeRunId 匹配，并单列“关联记录”及其事件类型 / 标识；不能合成一条不存在的事件、改写其原始 JSON，或要求每次点击都跨事件查询和扫描历史。
- `node.log` 的既有 payload 结构为 `{operatorId, phase, data, deviceBindings?, truncated?}`；消息、level、code 与执行身份在事件层。其“日志数据”来自 `payload.data`，称“算子主动记录的字段”，不得改称完整输入、输出或运行参数。没有主动字段不影响同节点框架记录的通用展示。
- 直接显示当前返回的简单值；明确区分 `0`、`false`、`null`、空字符串、空数组、空对象与未提供字段。没有 data 或没有记录字段时显示相应空状态，不凭 `value || '—'` 抹掉有效值。
- 对象使用可展开字段层级；数组默认分段展示。仅当数组项均是结构一致、字段为简单值的对象时提供紧凑表格，混合项或嵌套结构继续用树。分页 / 展开只作用于当前返回的数据，不触发全量历史查询，不声称获得发布端已裁剪的内容。
- 保留原 key；没有既有明确字段契约时不猜中文含义、单位、阈值或成功失败。`phase=execute` 可显示执行阶段，`lifecycle` 仅显示生命周期阶段，不能猜是初始化还是销毁。未知 phase 保留原值。
- 长文本可展开和复制；支持复制选中值、当前返回的日志数据及当前记录。复制数据与当前记录的范围明确，不把折叠 / 分页展示当作后台取回全量数据，也不把这类复制称为完整 Job 导出。
- 已有 `truncated`、`<redacted>`、`<max-depth>`、`<bytes:N>` 等标志按其原意显示；外层未提供 truncated 不证明全部内容完整。原始 JSON 是“当前收到的记录”，不承诺脱敏前原文、未裁剪参数或未记录数据能恢复。

#### D10.2. 保留原始检查与结果边界

- 原始页复用现有 `LogJsonTree` 的字段 / 值 / 类型、惰性展开、长文本和复制；允许附加 JSON 文本查看，但不能以文本块替代已有树能力。易读页和原始页必须对应同一条选中事件，切换不会改写数据或改变过滤条件。
- `node.log.payload.data` 与生命周期记录的 `metrics / diagnostics / ioSummary` 是不同内容。当前选中的生命周期记录按 D10.1 必须可读化，无需先找到一条 node.log；跨记录关联仅是可选补充。`QueryRuntimeLogs` 对完成事件的投影不保留完整 outputs，不能从日志详情承诺完整端口值。
- `ioSummary` 中 collection 只有数量，image / array 主要是 shape 与 dtype；没有项目数据就不生成列表明细或缩略图。字段摘要有 omitted / truncated 时保留其说明。
- 当前节点结果面板每个节点只保留最近两次执行；图片还依赖有效检查会话、精确执行身份、端口和可用快照。此版不新增全轮次图片历史库，不承诺所有旧循环都能查看结果图，更不能用最新一次图片顶替所选旧轮次。没有已加载结果时说“该次执行结果未加载或不可用”，只有明确证据才能说已清理。

#### D10.3. 单算子调试与既有预览

- 正式 Job 日志详情与 `OperatorDebugWindow` 实际复用一个 Qt 详情组件或格式化适配点，分别输入各自真实数据。单算子调试继续在既有调试窗口展示，不增加日志窗口的第三个一级来源，也不把调试记录重复加入生产 Job / 系统事件。少量按来源区分的适配即可，不建立算子类型注册框架、额外统一日志协议或第二份缓存；不只复制两份相似 UI 就称为复用。保留已有调试结果、参数、PLC 文案与操作入口。
- 沿用 `DebugConnection.snapshot()` 的既有会话轮询。`ReadOperatorDebugEvents` 返回 `{events, nextSequence, gap}`；日志项 wrapper 为 `{sequence, type, generation, executionId?, event?}`。外层 type 为 node.log 时，内层 `event` 才携带 `eventType/message/level/code/payload`；读取内层实际类型，不把外层标签当作其内容一定是业务日志。会话状态、execution.started / finished、logs.dropped 和本地文本反馈按原类型显示。
- 调试日志以连接的 sessionId、generation 与记录的 executionId 归属；没有执行 ID 的会话通知保持会话级。原始查看和复制保留收到的 wrapper，不补造生产 Job、workflowRunId、事件时间或 node.completed。nextSequence / gap 继续沿用现有读取含义，不转成 Runtime eventId，也不重做会话恢复或重试机制。
- 单算子 worker 不经 WorkflowRunner。成功结果来自 `GetOperatorDebugExecution`，可含 status、已返回 outputs / outputAssets、metrics、diagnostics、elapsedMs，以及 manager 保存的 rawParams / effectiveParams / inputSources；失败时只显示实际返回的 code / message / diagnostics 等。将其明确放在“调试执行结果”，与选中的日志行分开，匹配 session / generation / executionId；没有该次结果就显示不可用，不拿最新结果替代。
- 调试的 inputSources 是来源说明，不冒充完整输入；outputAssets 继续沿用已有结果组件按需读取。worker 的 finishedAtMs 是单调时钟值，不作为日历发生时间；有实际 elapsedMs 时显示其执行耗时。结果读取与日志读取是已有两种来源，不为共用界面制造 node.started / completed 或 ioSummary，也不把结果再次追加到生产日志。
- 纯预览与实时预览仅兼容既有失败摘要和附带日志。纯预览失败若只有 message / recent-logs 文本，就显示文本；实时预览的 `node.preview.failed` 如有 `payload.logs`，按现有 BufferedOperatorLogger 记录的 level / message / code / payload 展示为“预览附带日志”，其中 payload 不是 Runtime node.log 的 data 包裹。保留其已知来源，不变成新的 Job，也不新增成功预览持久流、存储或 RPC。

原型中的算子主动日志、无主动日志的框架记录和调试数据均为演示输入；正式产品不增加这些演示切换按钮、不硬编码示例业务字段。只对实际演示和验证的场景作相应声明。

## 7. 设备诊断接入

### D11. 设备诊断的范围与身份

- Job 内的设备故障/恢复摘要、算子设备日志继续由 QueryRuntimeLogs 查看；有明确 deviceId 的事件提供“查看设备诊断”。复用 `GetDeviceDiagnostics`、RuntimeClient 和 `DeviceDiagnosticsView` 的通道、HEX / UTF-8、详情、缓存导出。
- 诊断缓存按 `runtimeInstanceId + projectId + projectLoadGeneration + deviceId` 校验；从事件中明确的绑定 scope/执行上下文取得目标，不拿当前打开工程替历史事件补造代次。范围匹配才查询；不匹配时仍可看持久化 Job/device 日志，不伪造历史设备缓存。
- 标“近期设备诊断缓存”。dropped/truncated 沿用现有返回含义；当前 dropped 是缓存整体累计淘汰信息，不说成所选设备/本 Job 精确丢失条数。设备缓存不重新追加到 Job 树。
- `executeDevice` 和 `captureDevice` 在现有 source 中传相同的可用执行字段（workflowId、nodeId、workflowRunId、parentWorkflowRunId、nodeRunId、iterationPath、slot 等）；jobId 等操作归属仍以可信 operationContext 为准，不新增设备执行协议。
- **归一化落点为 `DeviceDiagnostics.append()` 形成 row 之前**：只补齐允许的执行字段，缓存、故障摘要、持久事件复用这一份结果；保留原 source。不是只修改 `projectEvent()`——后者当前仅投影 node.log/node.failed，无法修复原生 device.action 回执。
- 可信顶层身份优先，source 不可覆盖 Manager 给出的 Job、scope 或设备归属；合法 `iterationPath=[]` 是已提供值，不作为缺失再补。workflow/node/run 字符串缺失时才从 source 的对应有效值补齐，不在 Manager、客户端、UI 各写一套兜底。
- 改动修复后续原生设备事件，旧持久事件不回填。旧事件身份不足时如实显示“已关联 Job，执行轮次未记录”；普通非循环合法空路径不误报。原始 source 仍可在详情查看。
- 设备取自明确 deviceId，或按现有 `diagnostics.deviceSlot + deviceBindings` 规则解析。多绑定且无法唯一确认时展示槽位供选择，不任取第一个设备；device 过滤不强制 component=device，因为算子记录同样可能绑定设备。
- 现有设备摘要聚合 key 不包含 Job/轮次。`occurrences` 展示为“设备同类问题累计次数”，`durationMs` 为聚合经过时间，累计范围可能跨任务/轮次；摘要挂在其携带的执行上下文，但不把计数归为本轮失败次数。恢复表示后续操作成功，不把先前失败节点改绿；本期不改聚合器或另建按 Job 聚合器。
- 诊断 cursor 是设备缓存游标，不是 Runtime eventId；返回原事件靠进入时保存的导航状态。不存在可证明的精确映射时不造“跳回这一条”的关联。
- `getDeviceLogger()` 是平台结构化设备操作日志，不代表采集厂商 SDK / PLC 固件全量日志；本期不新增厂商采集器。

## 8. Designer 本机日志

### D12. 独立来源与范围

- 保留 `DesignerLogWriter` 和现有本机目录。
- 支持当前会话和本机历史文件两种范围。
- 保留工程编辑、流程设计、页面设计、Runtime 客户端连接/查询失败等本机记录。
- 不把 Designer 的连接失败写成 Runtime Job 失败。
- Runtime 查询错误仍可作为 Designer 本机事件显示，但不重复写入 Runtime。
- Runtime 运行任务、Runtime 系统事件、Designer 当前会话和 Designer 本机历史文件分别保留各自的筛选、选中项和滚动位置；不共享会导致串扰的 Job / device 条件。

## 9. 导出、保留与性能边界

- 明确提供“导出此 Job 完整记录”：复用原完整历史导出器，使用所选 Job（及已确认 project）的整个可查询范围，导出开始时取得独立固定上界 H_export，从 after=0 以前向模式逐页读到结束。不能带 latest_first/before，不能复用旧 Job 的 `_historyFilters` 或旧查询上界；按原有语义覆盖 `__runtime__` 分区中关联此 Job 的摘要。
- 完整 Job 导出不受屏幕已加载页、WARN 等本地条件、临时上下文模式影响。若保留原“导出当前筛选结果”入口，必须明确范围；不能把缓存导出按钮改名为完整导出。
- 保留现有异步、取消、目标文件保全机制。设备导出仍标“已返回的近期诊断缓存”。不重写导出框架；新增入口需要验证传入的 Job、范围和上界正确。
- 完整指当前仍可查询的数据，不承诺恢复保留策略删除的记录。使用现有 retention 边界；未知缺失原因如实说明，不新增清理审计表。
- 原始事件、树、初始化缓存均有界；不后台全量扫描、不建第二份索引/缓存服务。正常切换、取消、断连恢复与多任务隔离沿用现有实现并验证受影响路径，不扩成极端故障矩阵。

## 10. 非目标

- 不重做 2026-11-03 日志整合已完成的 EventStore、SQLite、JSONL、全局查询、导出和日志生产者。
- 不增加第三个一级“Runtime 日志”入口，不重复显示已归属 Job 的 Runtime 事件。
- 不新建设备日志表或厂商 SDK 原始日志采集。
- 不改变 Job 生命周期、循环调度、设备执行、算子业务语义或停止行为。
- 算子覆盖补齐限定在已有数据的共享展示与调试接线；不批量给 78 个无直接 logger 调用的入口添加重复日志，不改现有限流 / 级别策略，不把单算子调试或成功预览并入生产 Job 事件流。
- 不重做 Web 日志 UI；只同步共享协议生成物。
- 不引入 WebView、前端框架、复杂图表或独立日志分析服务。
- 不把性能专项、所有极端故障排列或全量历史数据恢复作为本 change 的完成前提。


## 11. 实现顺序与完成口径

唯一进度清单是 `tasks.md`。按 `sol-handoff.md` 的数据合同 → 树与时间 → Qt 接线 → 实际验收顺序推进，每阶段确认结果再继续，不把所有功能最后一次拼装验证。已有批准交互保持；原型演示数据不能作为 Runtime 功能证据。

字段、身份、范围以本文件 D1–D12 为准，正式行为要求见 spec，实际验收见 acceptance；遇到目标分支已有不同实现先核对差异，不按固定行号重复修改。编译/协议生成、静态检查、聚焦测试、Qt 交互分别记录，不互相替代。

## 12. 验证范围控制

- 优先复用现有 `test_log_dialog.py`、`test_operational_log_integration.py`、`test_log_history_instance.py`、`test_log_scroll_viewport.py`、`test_device_workspace_usability.py`、`test_device_operational_logs.py`、`test_device_feedback_closure.py`、`test_operator_logging.py`。
- 不为每个控件、每个筛选字段、每种循环嵌套深度分别增加独立测试。
- 不新增全仓压力测试、厂商硬件矩阵、故障注入矩阵、日志中间件测试或重复的导出 / 缓存测试。保留仓库原有 CI 门禁；“不加新矩阵”不等于跳过已有必需检查。
- 新增行为可集中在六类：历史 Job keyset；最近/更早/实时交接与容量；树及筛选投影；While bodyExecuted；Job 状态与时间；原生设备 source 归一化。完整 Job 导出、抽屉、来源与设备往返优先扩展现有集成测试和两条 Qt 走查，不规定每个条目必须另写一个新测试。V2.3 算子补充用少量代表数据覆盖框架记录、主动日志和真实调试 wrapper / 结果接线，并入既有日志 / 调试验证和 G6；不要求逐个算子、每个字段或所有空值排列重新跑一套测试。复用 LogJsonTree 已有展开 / 长文本及调试会话的既有验证，不重测未改动的限流、资产传输或完整调试状态机。
