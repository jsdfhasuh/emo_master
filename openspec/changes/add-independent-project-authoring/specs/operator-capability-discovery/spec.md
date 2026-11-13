# 算子与编辑能力发现

## Purpose

让 AI 在编排前了解指定目标实际列出的算子及其参数、端口、模式、设备要求和已知限制，同时了解当前编辑服务真正实现的操作；明确区分声明信息与工程预检结果，避免把目录存在误当成已具备执行条件。

## ADDED Requirements

### Requirement: Targeted catalog discovery

系统 SHALL 提供按明确 targetId 查询的算子摘要列表、按 ID 读取详情及设备类型声明。摘要含身份、版本、名称、分类和用途；列表支持关键词筛选，不静默截断。目标不可达是查询失败，不得伪装成空目录或“不支持”。

#### Scenario: Find an operator by purpose

- **WHEN** 客户端指定目标并按“阈值”或该算子 ID 查询
- **THEN** 在目标真实目录存在该算子时返回匹配摘要，并允许进一步按 ID 获取详情

#### Scenario: Distinguish absence from a query failure

- **WHEN** 完整目录查询成功但某算子未列出，或目标连接失败
- **THEN** 前者只说明目标目录未列出该算子，后者明确返回查询失败；不得为二者编造相同的安装或平台原因

### Requirement: Complete operator details

系统 SHALL 返回真实完整参数和端口声明、设备需求、模式及既有适用性元数据。默认值、枚举、描述、required、nullable、版本和 x 扩展不得在映射中丢失；properties/items 的嵌套对象、数组及其 xVisibleWhen/xValidateWhen 等条件须递归保留；来源具备的参数变更生效范围及已知限制也必须可查询。

#### Scenario: Read Threshold configuration constraints

- **WHEN** 客户端读取已经接通目录元数据映射的目标 Threshold 详情
- **THEN** 返回真实模式、blockSize 的奇数及模式适用声明、threshold 输出可空性、nextCycleParameterPaths 及源具备的 Core 版本范围；目录身份来自本次真实 Runtime 回复，值不由 MCP 硬编码。仅有 protobuf 字段定义不满足此场景

#### Scenario: Read nested ROI region declarations

- **WHEN** 指定目标的真实 ROI 声明支持组合区域，客户端查询其详情
- **THEN** 返回 `roiType=regions`、区域对象数组、keep/exclude、enabled、几何字段与嵌套点集及原有条件，不展平或转为不透明字符串；桌面 customUi 只作为元数据，不要求启动其窗口

### Requirement: Explicit metadata provenance and unknowns

系统 SHALL 标明声明对应目标、实际注册版本、观察信息及实时或缓存来源，复用目录回复的真实实例身份。已声明字段尚未填充也属于未提供，不能因生成代码有字段就认为已知。next_cycle_parameter_paths_json 的空字符串与 JSON `[]` 必须区分：前者标入 unavailableFields，后者输出明确空列表。缺少 Core 版本或实例身份时保持未知，不填默认版本、不拿 targetId 代替实例 ID。缓存不借用其他目标的数据；资产等版本化请求使用实际目录/注册版本，开发机新 manifest 不能替代目标声明。

#### Scenario: Distinguish missing metadata from an empty declaration

- **WHEN** 目录返回参数 schema，而 next_cycle_parameter_paths_json 分别为未提供/空字符串、JSON `[]` 或有效非空路径列表
- **THEN** 未提供时省略或返回 null 并标记未知；`[]` 作为明确空列表返回；非空列表保留真实值。空字符串既可能来自旧目标也可能来自新协议尚未填值，不能据此断言原因；其他已知字段和目标实际版本继续保留

### Requirement: Discoverable implemented editing operations

系统 SHALL 让客户端查询实际已实现的编辑命令、输入结构、对象类型、交付形式及已接通的文件传输约定，并对尚不支持的操作给出明确说明。此信息随实际实现更新，不能依据未来计划、单纯工具名或算子存在就宣称配置操作可用。

#### Scenario: Runtime support exceeds editing support

- **WHEN** Runtime 列出某算子，但当前服务尚未实现它所需的特殊配置操作
- **THEN** 客户端仍可读取算子声明，同时得知缺少的编辑操作；服务不返回笼统的“完全支持”

### Requirement: Separate declaration editing and readiness

系统 SHALL 分开表达算子功能声明、编辑服务能力及指定工程版本的预检结果；不得用一个 supported 布尔值或百分比替代。目录列出、编辑成功和预检通过均不得被描述为实际执行成功。

#### Scenario: Configurable operator with a missing model

- **WHEN** 目标有推理算子，编辑服务支持其节点配置，但草稿缺模型文件
- **THEN** 声明和编辑支持正常返回；工程预检独立报告缺项，不将算子改标为“不支持”，也不宣称工程已可运行

### Requirement: Observation-only discovery

系统 SHALL 以只读方式提供能力信息；查询目录和详情不得执行算子、启动任务、连接现场设备或加载模型试跑来猜测支持度。没有可靠来源的环境依赖或限制必须保持未知。

#### Scenario: Inspect a device-dependent operator

- **WHEN** 客户端读取相机或 PLC 相关算子的声明
- **THEN** 返回已有设备要求，不建立设备连接，不改变在线工程，也不将查询成功当成硬件可用验证

### Requirement: Declaration adoption for existing drafts

系统 SHALL 为已保存、正常重启或导入的现有草稿提供可发现的声明恢复/重新采用途径，依据明确目标、工程算子依赖版本和设备类型/驱动版本。采用真实声明不得隐式升级工程依赖或改变节点参数、绑定；同一业务路径可由 HTTP/MCP 使用。已知的目标来源和版本必须保留，不把其他草稿或最近查询目标的声明自动借来。

#### Scenario: Continue authoring after restart or import

- **WHEN** 客户端重新读取已保存/导入的工程，按正常声明采用入口选择匹配目标及版本
- **THEN** 声明重新关联到该草稿，本地校验、草稿输出和声明相关编辑恢复其可执行范围，项目内容保持；不需要先新增再删除临时同类节点

#### Scenario: Keep unavailable declarations truthful

- **WHEN** 目标离线或其真实声明不能满足工程已保存的依赖版本
- **THEN** 返回明确缺项/不匹配，原草稿仍可读取与保存，适用本地检查可以 INCOMPLETE；不更改工程版本、伪造 PASSED 或借用其他目标声明
