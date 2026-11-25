# 算子运行结果语义说明

## Purpose

让 Runtime 和 Designer 使用算子实际注册定义解释输入、输出、参数和运行数据。字段说明必须来自对应算子及版本的源码审查，不允许仅按英文 key 猜测含义。

## ADDED Requirements

### Requirement: Every built-in operator has a field-level audit

系统 SHALL 对本 Change 基线中的全部 82 个内置 operatorId 逐项审查输入端口、输出端口、配置参数、实际生效参数、metrics 和 diagnostics。Runtime 通用失败、跳过和取消生命周期由框架统一审查；只有算子存在专属处理时才纳入该算子的字段审查。

审查 SHALL 以实际 Manifest、OperatorMeta、执行实现、共享 helper、继承类和托管设备执行路径为依据。共享模块中的不同 operatorId SHALL 分别记录结论。已知字段和嵌套结构 SHALL 有真实语义、类型、单位（适用时）、数据来源和展示建议；动态键说明形成规则，不要求穷举所有运行时名称。无法可靠取得的数据 SHALL 明确记录该结论，不得推测。

#### Scenario: Review shared implementation operators

- **WHEN** 多个算子共享同一 Python 模块
- **THEN** 审查记录仍分别覆盖每个 operatorId 的版本、参数、端口和执行分支

### Requirement: Operator metadata describes input and output ports

每个内置算子的输入和输出端口 SHALL 能提供中文名称及用途说明。说明 SHALL 与真实端口类型和执行行为一致。端口语义元数据 SHALL 不改变 type、required、nullable、schemaVersion、连接兼容性和执行协议。

端口说明 SHALL 通过 resultSchema.inputs/outputs 引用真实端口 key；不为文案改写端口连接合同或将调试动态端口伪造为 Manifest 静态端口。

#### Scenario: Explain an image output

- **WHEN** Designer 展示一个输出端口
- **THEN** 用户能看到中文名称、当前算子中的用途和真实数据摘要，而不只是英文 key 和类型

### Requirement: Configuration parameters have complete descriptions

全部内置算子的 paramSchema 属性（含嵌套和条件参数）SHALL 有明确中文名称和用途说明；适用时补充单位、默认值、枚举和约束。系统 SHALL 复用 paramSchema，不维护重复参数说明副本。修改 SHALL 同步实际 class.meta 的 schema 生成源，保持现有注册一致性。配置定义 SHALL 与本次实际生效参数区分。

#### Scenario: Understand a threshold parameter

- **WHEN** 用户查看阈值算子参数
- **THEN** 用户能理解模式、固定阈值生效条件、数值范围和输出影响

### Requirement: Runtime result metadata is bound to operator identity and version

运行结果说明 SHALL 归属于准确 operatorId 和 operatorVersion。正式 Job 的实际执行节点 SHALL 从注册算子描述符获取身份，并与生命周期信息关联。框架节点或无法确认身份的记录不伪造身份；缺少身份的记录使用通用展示。

#### Scenario: Explain a completed node

- **WHEN** Designer 打开具有 operatorId 和 operatorVersion 的正式节点记录
- **THEN** 系统使用匹配的算子定义解释记录，不根据显示名称或当前工程同名节点猜测版本

### Requirement: Effective parameters represent values passed to execution

系统 SHALL 区分准备后的节点配置、调用参数、输入/绑定覆盖、设备解析和算子内部归一化值。parameterSummary SHALL 标明 call/effective 阶段及可确认来源；已经默认化且无法区分原始填写来源时不得反推。只有实际执行路径能确认的最终值才可作为生效参数展示。参数摘要 SHALL 在进入事件或调试展示记录前脱敏。密码、Token、API Key 和设备认证信息不得出现在日志 payload、原始 JSON、查询、复制或导出中。

#### Scenario: Display a bound parameter

- **WHEN** 算子参数被输入端口或绑定值覆盖
- **THEN** 运行详情显示实际传入值及来源，而不是未生效的节点默认配置

#### Scenario: Explain normalization inside an operator

- **WHEN** 集合筛选按启用条件归一化参数，或输入端口覆盖保存参数
- **THEN** 详情使用真实处理后的结果或明确标为调用阶段，不把调用前快照冒充最终生效值
- **AND** 已有输出或诊断能表达该值时优先复用，不为统一展示复制整份配置

#### Scenario: Hide a credential

- **WHEN** 设备或算子配置包含认证信息
- **THEN** 持久化日志和原始记录均不包含明文凭据

#### Scenario: Hide authenticated output URLs while preserving output references

- **WHEN** 单算子调试 outputs 包含已知认证 URL，且该执行输出可通过 output_ref 复用
- **THEN** 系统 SHALL 在对外查询及查看、复制、导出共用的记录副本中隐藏 URL 认证信息，保留非认证内容
- **AND** 可信内部输出及实际 output_ref 解析得到的执行值 SHALL 保持原值，查询与序列化不得原地污染内部记录
- **AND** 系统 SHALL 复用已有认证字段和 URL 规则，不增加任意字符串秘密识别、全工程扫描或新的存储/RPC

#### Scenario: Preview and share an attached JSON debug result

- **WHEN** 单算子输出的 JSON 通过附件传递，用户预览该端口或导出脱敏结果包
- **THEN** 预览及包内 JSON 使用与内联输出相同的认证 URL 处理，包内长度和摘要对应实际导出的字节
- **AND** 保存原始附件与后续 `output_ref` 取值仍使用原始数据，界面标明原始附件的导出边界

### Requirement: Metrics and diagnostics are semantically described

metrics 和 diagnostics 的固定字段、已知嵌套结构及动态键形成规则 SHALL 有明确用途、单位、统计口径和来源说明（适用时）。新增字段 SHALL 依据源码审查决定：有价值且可可靠取得的数据应补齐；已有权威输出、重复信息、无法可靠测量或没有业务价值的字段不要求新增，并记录理由。占位常量不得表述为测量结果。

#### Scenario: Avoid duplicating an output metric

- **WHEN** 同一数量同时存在于 outputs 和 metrics
- **THEN** 概览可以合并展示，但原始来源保持不变

#### Scenario: Operator has no dedicated metrics

- **WHEN** 纯数值算子没有专用 metrics，但 outputs 已包含结果
- **THEN** 系统展示真实 outputs，不强制生成重复 metrics 或主动日志

#### Scenario: Explain an asynchronous image save

- **WHEN** ImageSaver 后台保存返回 accepted=true、saved=false、state=SUBMITTED
- **THEN** 说明是任务已受理，其 latencyMs 为复制/提交阶段耗时，不表述为文件已保存或完整落盘耗时

### Requirement: Unknown fields remain inspectable

系统 SHALL 保留未知字段原始 key、原始值和真实类型。缺少说明不得阻断正常注册和执行，不得按通用字段名猜测业务含义。

#### Scenario: Display an unknown third-party field

- **WHEN** 第三方算子返回没有语义元数据的字段
- **THEN** 系统按通用字段展示原始名称和值

### Requirement: Same metadata is available to local and remote Designer

Runtime SHALL 通过现有 OperatorInfo 的 result_schema_json 传递语义元数据，RuntimeClient SHALL 解析为统一 OperatorDefinition；不增加独立元数据 RPC。单算子调试 SHALL 从可信会话取得 operatorId/operatorVersion，并复用同一解释定义。

#### Scenario: Connect to local and remote Runtime

- **WHEN** 用户分别连接本地和远程 Runtime 查看同一版本算子
- **THEN** 两种连接使用相同字段语义说明
