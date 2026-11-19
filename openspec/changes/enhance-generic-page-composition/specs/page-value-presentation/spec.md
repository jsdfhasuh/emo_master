# 页面值表达与有限动作

## Purpose

在保持工作流直接绑定和原始类型的基础上提供清楚的状态、数值和日志显示，并复用明确目标的只读页面动作。显示格式不重新判断业务，不改变生产执行。

## ADDED Requirements

### Requirement: Direct typed output bindings

系统 SHALL 继续通过现有 node_output、workflow_output 等 DataSource 与 output catalog 绑定生产值，不要求新增输出别名层、公开变量层或工作流字符串转换节点。

indicatorStates SHALL 接受规范 JSON 的整数、布尔和字符串键，按原始类型精确匹配。映射只影响文字、语义前景、浅背景和标记，不改写值。既有规范键校验继续适用，不使用模糊字符串转换或表达式代替。

Designer 与 Web 正式编辑器 SHALL 都能从属性入口新建、编辑和保存整数、布尔及字符串映射。Web 的类型选项、行默认值、解析、校验和保存 SHALL 使用同一组支持类型；不得只在模型或加载已有工程时支持 integer，而要求用户手写 JSON 才能创作整数映射。

#### Scenario: Display exact typed values directly

- **WHEN** 用户在 Designer 或 Web 正式属性面板直接绑定已有输出，并为整数 1、布尔 true 和字符串 "1" 分别新建映射，再保存重开并在 Qt/Web 显示
- **THEN** 各输入只匹配自己的类型和值，三种类型都能继续通过原属性入口编辑，整数映射无须手写 JSON 或增加工作流转换节点

### Requirement: State representations share a neutral empty display

系统 SHALL 支持 text、badge 和 row 表现，当前默认 text；row 可以在一行内呈现名称、标记与状态词。多个 indicator 可以组合成紧凑的判定组，不要求每行再加卡片。

在 Qt 与 Web 中，`indicatorView=row` 的已映射 `background=soft` SHALL 作用于整条判定行，覆盖名称、标记、状态词及该行配置的内边距；不能只给右侧状态词加一个浅色小块。badge 的状态背景 SHALL 只形成状态值的紧凑徽标；text SHALL 只采用文本前景语义，不由状态映射生成浅底容器。组件显式 surface 继续按页面组合规则应用，不为三种表现增加专用控件或新的背景字段。

NULL、数据不可用和未映射值 SHALL 在内部保持各自含义，但显示配置统一复用既有 `emptyText` 及中性色，不增加三套回退文案字段。更新到这些状态时 SHALL 清除上一条映射产生的彩色背景，不遗留成功或失败色；组件明确配置的中性表面仍可保留。它们不能被转换成业务 NG、成功、0 或 false；原有诊断信息继续保留在既有诊断路径。

#### Scenario: Show mapped states and missing data

- **WHEN** 一个判定组的 row、badge 和 text 按配置显示同类已映射状态，随后某个字段为空、不可用或没有对应映射
- **THEN** Qt 与 Web 的 row 整行、badge 状态值和 text 文本分别采用约定表现；其余项清除旧状态色并显示中性 emptyText，内部仍保留实际原因，不能伪装成失败或成功判定

### Requirement: Readable plain text and source timestamps

系统 SHALL 让 text 以纯文本显示普通字段和日志，保留来源中的换行，并按可用宽度自动换行。历史文本沿用读取器已有 4096 字符截断及省略提示，不为普通 text 新建长度策略；不为普通日志新增 HTML、脚本或文件读取服务。

日期和时间 SHALL 优先直接显示历史来源声明的日期/时间列，或 SQL VIEW 提供的已格式化列。首版不增加任意日期格式 DSL 或时区转换引擎，不根据数字外观猜测时间语义。

#### Scenario: Read multiline details and a declared timestamp

- **WHEN** 历史记录提供有换行的日志及已声明的日期/时间文本列
- **THEN** 时间按来源文本完整显示，日志保持换行并自动折行，动态更新按 page-composition 的规则重算尺寸并保留对齐，已有读取截断标记得到保留，文本不被解释为富文本或脚本

### Requirement: Existing result review actions stay read only

系统 SHALL 保留 navigate、freeze 和 resume_live 的既有语义。freeze 与 displayed_result 导航使用当前已显示结果，resume_live 恢复最新显示；这些动作不启动、停止或重跑工作流，宿主运行状态仍实时更新。同次结果归属沿用 operational-page-shell 的要求。

#### Scenario: Freeze and open result details

- **WHEN** 用户冻结当前结果并通过 displayed_result 打开详情，然后恢复实时
- **THEN** 冻结与详情仍关联原结果身份，恢复后回到现有最新显示机制，整个过程不改变生产执行

### Requirement: History actions have an explicit local target

系统 SHALL 在既有 `navigation_button.actions.clicked` 中增加 history_previous、history_next 和 history_first，以 componentId 指向同页 sqlite_table。动作只改变该来源的查询或选中记录，联动关联图片和字段；目标引用沿用正式校验，不能猜测为页面上的另一个表格。

这些历史动作 SHALL 不接受结果上下文、脚本、任意 API 或设备写入内容，不使用历史记录改变生产控制目标。开始/停止继续归属原宿主入口。新增映射和动作 SHALL 能在正式编辑器中配置、保存并发布，不只保存成运行端忽略的 JSON。

#### Scenario: Browse history from a separate image area

- **WHEN** 用户在属性面板将图像下方按钮配置为 history_next 并指向同页历史列表，保存后在运行页点击
- **THEN** 按该来源的查询顺序选择下一条并更新其图片与字段，不靠按钮位置猜目标，也不发出采图、PLC、启动或停止命令
