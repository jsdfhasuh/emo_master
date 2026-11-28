# runtime-log-workbench Delta Specification

## ADDED Requirements

### Requirement: Job-level events and partial execution windows have accurate labels

共享任务页 SHALL 按事件的实际归属显示任务生命周期，并在节点、工作流和循环的时间说明中标明当前可见证据。

#### Scenario: Inspect a Job while live or paused

- **WHEN** 用户在实时或暂停阅读的任务中查看事件树
- **THEN** 无工作流调用身份的合法任务级事件（如 `job.started`、`job.failed`）显示在“任务生命周期”，本应有关联但缺少证据的事件仍显示在“未完整关联事件”
- **AND** 普通节点、内部工作流和循环轮次只有开始记录时，实时连续跟随且能确认分支活跃才显示“进行中”和墙钟估算耗时。暂停阅读后，分支显示“结束状态待刷新”，估算值停止增长。Job 自身按实际状态计时。运行中或停止中且有有效开始时间、尚无结束时间时沿用墙钟估算。终态耗时固定
- **AND** While 收到迭代结束记录后按实际 `bodyExecuted` 区分是否进入循环体，旧结束记录缺少该字段时显示“执行情况未记录”
- **AND** 终态 Job 的分支在已加载窗口只有开始记录时显示“当前范围未见结束记录”，循环头在窗口之外时说明范围。只展示可确认的起止时间和耗时

### Requirement: A selected execution or loaded failure opens its actual event

共享任务页 SHALL 支持从节点执行或已加载的失败记录直接打开对应原始事件的共享详情。

#### Scenario: Inspect a node or locate a failure

- **WHEN** 用户点选节点执行分支，或从当前 Job 的失败提示选择一条失败记录
- **THEN** 节点分支优先打开同一次执行的结束记录，其次是开始记录或确属该执行的附带记录。抽屉标题标明节点名称（缺失时显示 ID）及当前记录类型。切换同次主动日志或设备事件后，标题、详情和原始 JSON 均指向所选记录。纯结构占位显示“当前范围未加载记录”
- **AND** 点击“查看失败”前，提示已显示当前窗口的实际失败记录数，并列出代表性失败已有的轮次、工作流、节点、日志代码和简短原因，优先选可定位的节点失败。点击后显示并选中真实记录，可继续查看其他失败。没有已加载失败时隐藏提示

### Requirement: Compact execution rows retain diagnostic evidence

共享任务页 SHALL 在树行和时间顺序中提供简短上下文，便于用户从大量普通事件中看到异常。

#### Scenario: Read a collapsed branch and search its records

- **WHEN** 用户折叠工作流、循环或轮次，切换时间顺序，或搜索已加载事件
- **THEN** 折叠分支显示已加载 WARN/ERROR 的实际条数，每条记录在该分支内只计一次。状态和等级保留文字并使用语义色。节点主行优先显示已有失败原因或一两项结果摘要
- **AND** 默认折叠已有结构行能表达的普通起止事件。异常、跳过、主动日志和有独立诊断意义的回执、设备、产物记录仍可见。关闭折叠可查看所有符合筛选的原始行。按事件 ID 定向定位时，被折叠的目标原行可见、选中并打开该条详情
- **AND** 时间顺序行展示可确认的工作流、节点、从 1 开始的轮次及日志代码。当前窗口的关键词搜索支持日志代码、工作流名、节点名、显示文案、原始消息和当前权限可见的已加载 payload

### Requirement: History and device views expose relevant time and context

Designer 历史列表与设备诊断 SHALL 提升已有记录的可读性，保留现有查询及下钻范围。

#### Scenario: Review history or drill into a device event

- **WHEN** 用户查询历史、阅读桌面框架消息、在共享 Job 抽屉选择 `device.*` 事件，或从任务页进入设备诊断
- **THEN** Designer 历史列表默认收起重复的本地筛选，已应用的条件仍可见、可展开和修改。列表显示日期与毫秒、日志代码、中文模块名及从 1 开始的轮次，翻页和范围导出沿用生效条件
- **AND** 即使近期设备缓存不可用，Job 抽屉仍直接用所选事件显示毫秒时间，前置已有设备、动作、通道、槽位、累计次数、回执及 HEX/UTF-8 预览。累计次数按已知范围解释，可跨 Job 或轮次的计数注明这一点，截断提示保留。下钻到身份匹配的近期缓存时，设备诊断页也显示毫秒时间、带文字与等级样式的记录和可读详情，可查看原始报文并返回原 Job 上下文
- **AND** 易读详情把调用、派发和修订等技术上下文（如 `cycleId`、`dispatchId`、`targetSlotId`）以及重复设备包络归入折叠的“记录身份”，空技术字段不占用业务详情。业务值 `0`、`false` 保留其原义，原始查看和复制保持收到的记录
- **AND** 任务详情、历史列表、系统事件和单算子调试详情复用共享中文文案，按事件类型翻译子工作流、派发、停止/中止/失败（含重启）、进程终止、SQLite 起止、产物、日志丢弃等已知默认文案，显示已知状态、模块及跳过原因。已有预启动容量或资源失败摘要、SQLite/TCP 回执、模型或 IO 摘要按记录展示。原始字段、自定义业务文本和未知值保持原义
