# 历史记录回看

## Purpose

增强既有 SQLite 历史组件，使生产监控与历史回看使用同一次实际归档的图片和记录。历史页提供紧凑列表、所选记录详情、逐条浏览、筛选和切页恢复；持续生产新增记录时仍能正确查看相邻记录。实现复用既有查询、图片引用和页面会话，不新增 RPC、游标服务、全局业务状态或任意文件读取能力。

## ADDED Requirements

### Requirement: Shared Table and List Views

历史组件 SHALL 保留 `sqlite_table` 类型，通过可选 `props.historyView={mode,titleField,subtitleField,trailingField}` 选择 `table` 或 `list` 表现。列表角色 SHALL 引用来源声明的标量列，并复用统一状态映射显示末端结果。表格与列表 SHALL 共用来源、筛选、排序、查询和选择，不建立两条数据链。

列表可见字段 SHALL 与查询所需的详情字段分开；角度、耗时、日志不必全部显示在列表中。时间与日期 SHALL 使用已声明字段或既有 VIEW 提供的显示列，大图旁的时间 SHALL 来自所选记录；不增加日期格式 DSL，不用当前时钟代替检测时间。

table 和 list 在没有已保存会话状态的首次非空读取中 SHALL 统一选中首条；空集合保持无选择。未配置 historyView 时使用 table 表现。列表 SHALL 提供紧凑的筛选、刷新与排序入口，不把来源诊断展开为页面主体。

#### Scenario: Open a Compact Record Review Page

- **WHEN** 用户首次进入配置了时间、日期和结果字段的非空历史列表
- **THEN** 列表选中查询首条，显示紧凑的记录行和清楚的选中状态
- **AND** 大图标题旁显示该记录的完整时间，其他已声明详情字段可用于列表外的控件

### Requirement: Typed Record and Image Details

`text`、`number`、`indicator` SHALL 支持 `props.recordValue={componentId,kind:'field',field}`；目标 SHALL 是同页 `sqlite_table`，字段 SHALL 来自该来源的声明列。它 SHALL 与同控件的生产 `bindings.value` 互斥，不加入 Runtime 结果采集或 `resultScope`。

所选记录 SHALL 保留 `HistoryRow.values` 的实际标量类型。数字组件直接格式化数值，指示组件使用统一状态映射，空值使用配置的中性 emptyText（可为“未提供”）；不把 SQLite 数字 0/1 隐式转为布尔，也不用最新生产值补齐旧记录缺失字段。

图片 SHALL 继续使用既有 `props.imagePath(kind='selected_row')` 与 `imageRefs`。同一选择 SHALL 驱动结果图、原图或局部图及所有标量详情，图片引用与标量值分开保存；`recordValue` 不承担文件读取。

#### Scenario: Select One Record for All Details

- **WHEN** 用户点击另一条历史记录，生产任务同时继续产生新结果
- **THEN** 大图、局部图、时间、结果、角度、耗时和回执显示所选记录的数据
- **AND** 缺失回执使用配置的中性 emptyText，生产推送不会改变这条历史选择

### Requirement: Anchored Queries for Stable Record Review

`props.sqlite.recordKey` SHALL 允许声明一个真实、唯一、非空的标量键字段，供表或 VIEW 使用。未声明时，物理表 SHALL 沿用原主键或 rowid；VIEW 使用逐条回看、记录联动或切页恢复时 SHALL 声明稳定键；未声明稳定键的 VIEW 可用于普通只读表格。服务器 SHALL 依据来源声明确定键列，并保留键值类型；不使用行号或显示时间猜测身份，也不为每次查询做整表唯一性扫描。

具有稳定键的来源 SHALL 在 `HistoryRow` 返回 `keyValues`，其内容为服务器按上述键顺序读取的实际标量数组。没有可用稳定键时该字段为空，不表示具备按键回看能力。单字段键为单元素数组，原有复合主键按原主键顺序返回。`rowId` SHALL 继续作为 UI 身份，不能反解其 SHA 来定位记录。Runtime、Qt 与 Web SHALL 使用同一当前响应结构，不按旧客户端或工程版本切换字段。

#### Scenario: Display stable keys for a view
- **WHEN** 已声明唯一记录键的视图返回一页历史
- **THEN** 每行提供真实键数组及稳定 UI 标识，保留键值类型

### Requirement: Anchor request preserves the declared query

既有 `QueryHistoryPage` SHALL 仅增加可选 `anchor_key_json` 与 `anchor_step`：前者是 `keyValues` 数组的 JSON，后者仅允许 `-1`、`0`、`1`，默认 `0`，分别表示当前排序的前一条、同条、后一条。没有锚点时 SHALL 保留现有分页行为；有锚点时 SHALL 按键定位目标，保留当前筛选、排序及 pageSize，原 page 参数不决定目标位置。客户端 SHALL 不传键列名、SQL 或图片路径。

#### Scenario: Restore the same record
- **WHEN** 当前筛选下提交所选记录的键和 step=0
- **THEN** 定位同一记录，保留当前排序、筛选和 pageSize

### Requirement: Return the actual page and selection in one read transaction

锚点查询 SHALL 在原只读路径中按服务器声明的键列参数化查询，确定同条或相邻条，并返回包含目标的实际页、有效 `imageRefs` 和 `selectedRowId`；普通分页响应不必指定 selectedRowId，这是请求语义区别，不是版本兼容分支。实现 SHALL 用现有排序及稳定键确定顺序，通过目标前置行数计算所在页；定位、计算页位置和读取该页 SHALL 在一次短只读事务内完成。不跨请求维持事务，不新增排名服务，不把全部记录物化到 Python 或浏览器。

锚点记录在当前筛选下确实不可读取时 SHALL 明确显示不可用并清空选择，不静默选择同位置的其他记录；正常新增造成的页码变化 SHALL 不被当成记录不可用。

#### Scenario: Browse Adjacent Records While Production Continues

- **WHEN** 时间倒序列表当前页显示记录 5、4、3，用户选中 3，生产随后归档了记录 6，用户触发下一条
- **THEN** 查询按记录 3 的真实键找到记录 2，返回其当前所在页，并通过 `selectedRowId` 选中 2
- **AND** 随后触发上一条返回 3；前一步的下一条操作不因旧 OFFSET 漂移重复选中 3 或跳到更晚记录

### Requirement: Local Navigation and Declared Filtering

`history_previous`、`history_next`、`history_first` SHALL 通过动作的 `componentId` 指向同页 `sqlite_table`。上一条和下一条 SHALL 统一携带当前 `keyValues` 发起 `anchor_step=-1/1` 的既有查询，不用旧页偏移猜测相邻记录。first SHALL 保持筛选与排序，读取首页并选首条；时间倒序模板可将其按钮命名为“回到最新记录”。

前后按钮 SHALL 在已知对应边界或无选择时不可用，加载时避免重复触发；first 在已选首条或上次结果为空时 SHALL 仍可刷新。边界查询未取得相邻记录时 SHALL 保持原记录并更新可用性。这些动作 SHALL 不启动检测、不读写设备或工作流全局变量。

#### Scenario: Browse using the current selection
- **WHEN** 历史查询已有有效的选中记录
- **THEN** 上一条和下一条使用该记录键，按钮反映查询边界

### Requirement: Filter by one declared column

筛选 SHALL 使用现有单个声明列加包含关键词的语义，Qt 与 Web 保持一致。界面 SHALL 提供有效默认列、应用和清除入口，去掉尚未实现的“所有声明列”选项。应用或清除筛选 SHALL 从新查询首页开始；table/list 均选首条，空结果保持无选择。列表排序变化使用同样的重置规则，不增加搜索引擎或组合条件语言。

#### Scenario: Refresh the Latest Record from a Boundary

- **WHEN** 用户已在查询首条或上次结果为空，随后点击“回到最新记录”
- **THEN** 页面重新读取当前筛选的首页，非空时选中首条，空时明确显示无记录
- **AND** 前后按钮符合返回结果的边界，生产运行状态保持不变

#### Scenario: Filter and Clear Records

- **WHEN** 用户选择一个声明列并应用关键词，再清除该筛选
- **THEN** Qt 与 Web 分别显示对应筛选及清除后的首页，选择和详情跟随新结果
- **AND** 请求使用明确的筛选列，未找到记录时不保留先前记录的图片或详情

### Requirement: Session Review with Fresh Image References

当前查看会话 SHALL 保存筛选、排序、页码、所选 `rowId/keyValues`、已读记录和配置使用的数量信息，并归属工程、历史声明及页面组件。生产推送 SHALL 不刷新或抢走历史选择；切页往返保留查看意图，更换工程或不相容来源时释放原状态。

从生产页返回已有选择的历史页，或显式刷新当前选择时，页面 SHALL 使用保存的 `keyValues` 和 `anchor_step=0` 重取同条记录及新的 `imageRefs`，恢复其当前所在页。它 SHALL 不仅重放旧图片引用，也不因新增记录而改选首条。没有已保存选择时，按首次列表行为读取。

图片读取确实报告引用过期时，同一次选择恢复 SHALL 只尝试一次按键重取；新引用仍失败时显示当前错误并允许用户重新查询，不无限重试、不增加后台续期服务。两个关联图片 SHALL 共用这一次记录恢复。

#### Scenario: Return to the Same Record After Image References Expire

- **WHEN** 用户查看旧记录后切到生产页，期间生产继续归档，原图片引用到期，用户再返回历史页
- **THEN** 页面按原记录真实键重查同条，取得新引用并恢复大图、局部图和详情
- **AND** 新增记录只可能改变该记录的查询位置，不改变所选记录身份

### Requirement: Optional Query Position and Count

`text`、`number`、`indicator` SHALL 支持可选 `props.recordValue={componentId,kind:'meta',meta:'position'|'count'}`。position SHALL 根据本次返回的实际 page、pageSize 和选中行索引计算为一基查询序号；没有选择时为空。它不代表记录永久编号，时间倒序查询首条为 `1`。

仅页面配置引用 count 时，系统 SHALL 使用现有 `readValue/QueryHistoryAggregate` 请求总数，来源声明使用 `aggregate='count'`，并传递与列表相同的筛选条件。未引用 count 的页面 SHALL 不为导航额外发起总数请求。未取得总数时显示空值或加载提示，确认空集合时显示 `0`，不以本页行数替代总数。

位置和数量 SHALL 表示本次查询结果，不随生产推送实时更新排名；查询后采用新的页信息和数量。异步回调 SHALL 使用当前查询 generation，不让旧筛选结果覆盖新查询；不承诺列表与独立 count 请求的数据库快照相同。

#### Scenario: Display Optional Metadata from the Current Query

- **WHEN** 页面配置了 position/count，查询返回第二页且每页十条，用户选择该页第一条
- **THEN** position 显示 11，count 显示相同筛选范围本次取得的总数
- **AND** 生产新增不会单独刷新这两项；未配置 count 的页面可使用相同导航而不请求总数

### Requirement: Consistent Record Detail Presentation

选择从 A 切换到 B 时 SHALL 立即清除 A 的图片和未更新详情，显示 B 的字段及图片加载占位。实现 SHALL 复用既有请求 generation 丢弃晚到的 A 回调；不必等待所有图片解码完成才显示 B 的文字。

实际缺图 SHALL 显示该图片不可用，并保留当前记录的其他字段，不回退旧图或推导业务 NG。日志和来源说明 SHALL 读取声明文本字段，保留换行并正常折行，可放入通用可折叠容器；超出现有文本长度限制的内容标为摘要，不自动读取文本所指文件。

#### Scenario: Ignore a Late Image from the Previous Selection

- **WHEN** A 的图片仍在加载，用户选中 B，随后 A 的图片返回
- **THEN** 页面只显示 B 的图片或 B 的加载占位，A 的回调被忽略
- **AND** B 的时间、结果或回执下不会暂时保留 A 的图像

#### Scenario: Review a Record with a Missing Image and Stored Log

- **WHEN** 所选记录的一张图片实际缺失，但记录保存了时间、多行日志和其他检测字段
- **THEN** 缺图位置明确提示，其他字段仍可查看，展开日志后保持原有分行
- **AND** 未保存的 PLC 回执保持未提供，不根据缺图或旧角度值重新判定产品结果

### Requirement: Editable Production to History Workflow

上述配置 SHALL 能在正式设计器中编辑、保存、重开并发布，Qt 与 Web 使用相同来源、字段类型、选择和动作语义。

交付工程 SHALL 通过同一个明确标记的软件模拟生产流程生成结果，使用既有 Image Saver（`vision.io.image_saver`）保存实际结果图及需要的局部图，再由 SQLite Writer（`vision.io.sqlite_writer`）把图片引用和同次记录字段写入历史。样例 SHALL 使用可区分的记录键与图片文件，避免新一次生产覆盖前次历史图片；历史页读取这些实际归档的数据，不用另一套静态列表代替生产归档链路。

#### Scenario: Produce and Review the Same Archived Result

- **WHEN** 用户打开并保存交付工程，运行一次软件模拟生产，再进入历史页并点击回到最新记录
- **THEN** 历史列表出现本次 SQLite 记录，所选大图、局部图、时间、结果和日志与本次实际保存内容一致
- **AND** 同一正式工程发布后，Qt 与 Web 都能继续选择、翻阅和筛选这些记录
