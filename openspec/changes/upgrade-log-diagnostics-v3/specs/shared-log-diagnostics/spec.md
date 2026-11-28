# shared-log-diagnostics Delta Specification

## ADDED Requirements

### Requirement: Periodic heartbeats update liveness without consuming business history

Runtime SHALL 将周期 `process.heartbeat` 在 Supervisor 更新既有判活状态后分流。正常心跳不占 SQLite 或内存业务窗口，不推进业务事件序号。

#### Scenario: Observe an idle Job and then its next business event

- **WHEN** Job 持续收到多于保留配额的正常心跳，随后产生业务事件或终态
- **THEN** 早先业务记录不会被这些心跳挤出，超时判活仍有效。逐 Job 订阅 `StreamJobEvents` 不再收到新周期心跳，能按原有顺序读到后续业务事件和终态
- **AND** 已有数据库保持可读。全局历史查询继续过滤旧心跳，逐 Job 重放仍按原序号返回实际已存的记录。新业务事件从已确认的游标继续，原有裁剪和终态处理规则保持

### Requirement: SQLite and subflow records have accurate messages and local coverage

Runtime SHALL 为普通 SQLite 写入和子工作流保留能对应实际执行的本机 JSONL 记录，并区分“开始写入”与最终回执。

#### Scenario: Inspect ordinary writes, background receipts and subflow calls

- **WHEN** 任务执行 SQLite 写入或子工作流调用
- **THEN** `sqlite.write.started` 说明已开始写入，回执保留真实初始状态。已有 started 记录只有默认消息为 UNKNOWN 时才在易读视图解释为“开始写入”。真正未知的最终结果保留 UNKNOWN
- **AND** 普通写入开始与结束、既有后台最终回执及 `subflow.*` 进入本机运营 JSONL，每条事件按现有 writer 路径写入一次。子工作流归入 execution，记录沿用现有载荷投影、大小与脱敏边界

### Requirement: Historical Jobs retain the names from their actual run

Runtime SHALL 按实际运行版本记录 Job 工程和工作流名称，以及关键工作流、节点、循环或子调用事件已有的显示名。任务目录、详细记录及本机 JSONL 优先使用这些名称，保留原始执行 ID。

#### Scenario: Read root and dispatched Jobs after the project changes

- **WHEN** 用户查询另一个客户端启动的根 Job 或派发消费者子 Job，包括工程修订已变化或 Runtime 已重启的情况
- **THEN** Job 状态和历史摘要包含各自受理时的工程名和实际目标工作流名。普通 Job 与派发子 Job 在各自创建路径取名，已有 `job.accepted` 事件在名称可用时写入工程名与工作流名。消费者任务有真实槽位时显示该槽位。关键事件和 JSONL completed 投影保留该次执行可取得的名称，子调用显示其真实目标
- **AND** 任务目录优先展示冻结名称，工程名的提示可查看完整工程 ID。目录“最近活动”使用共享框架中文文案，自定义消息保留原文
- **AND** 旧记录缺名称时只使用已有可信上下文中同工程、同修订的名称，否则显示 ID。已冻结名称优先，历史记录无需回填

### Requirement: The whole-Job timeline can be exported as Markdown

Designer 与 Operator 的完整 Job 导出 SHALL 提供 `.md` 格式，两个宿主分别沿用各自现有的固定上界与分页入口，复用共享导出器的单遍写入、取消和原子替换。

#### Scenario: Export the readable timeline for a selected Job

- **WHEN** 用户选择完整 Job 的 Markdown 导出
- **THEN** 从 Designer 或 Operator 的“完整 Job”入口均可选择 Markdown。文件标明所选 Job 及完整可查询范围，按顺序列出固定上界内仍可查询的事件，包括界面尚未加载的记录。每条使用可用的毫秒时间、等级、真实上下文、消息、日志代码和简短摘要，末尾给出条数
- **AND** 摘要只取本次实际返回、按既有边界处理过的展示字段。取消或失败时保留原目标。当前已加载范围导出和现有机器格式仍遵循各自原有契约

### Requirement: Local Operator logs use shared readable text

Operator 本机日志 SHALL 在 R4 的 `QTableView` / `LogTableModel` 上补充可读文案与摘要，沿用实际尾读、筛选、阅读位置和已脱敏的本机记录。

#### Scenario: Search a translated message and copy the selected record

- **WHEN** 本机 JSONL 中含可识别的框架消息或自定义业务文本，用户在本机页搜索并复制记录
- **THEN** 表格与详情显示可识别框架消息的中文、模块、日志代码和已有摘要，用户可用屏幕显示的词或原文命中同一条记录。自定义和未知消息按原文展示
- **AND** 原始查看与复制仍使用已脱敏的本机记录。两个 Runtime 文件家族、四条件交集筛选、快照导出与共享 Job 页沿用各自来源及范围

### Requirement: Web Runtime logs accept local dates and show readable details

Web Panel SHALL 使用可编辑的本地日期时间查历史日志，并为现有事件提供可读详情与原始记录入口。

#### Scenario: Apply a Web time range and inspect an event

- **WHEN** 用户编辑历史时间范围或选择 Web 日志
- **THEN** 起止可空，保留毫秒、开始含结束不含的查询语义。倒置范围给出可纠正的提示。输入转换为既有查询参数
- **AND** 列表与详情均把已知默认框架消息显示为中文，自定义业务文本与未知消息保留原文。列表用文字和样式表达等级，详情展示已有摘要并可切换到实际返回的 JSON。实例校验、字符串游标与既有 JSONL 导出继续沿用
