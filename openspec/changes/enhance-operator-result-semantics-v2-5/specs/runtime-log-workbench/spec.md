# runtime-log-workbench Specification Delta

## ADDED Requirements

### Requirement: Runtime node records identify the actual operator

正式 Job 的节点生命周期记录 SHALL 在实际执行对象为已注册算子时携带由 Runtime 注册描述符取得的 operatorId 和 operatorVersion。框架节点及无法确认注册身份的记录不伪造算子身份。日志详情使用匹配的算子语义说明；无法匹配时使用通用原始字段展示。

#### Scenario: Inspect a registered operator execution

- **WHEN** 用户查看由已注册算子产生的节点运行记录
- **THEN** 记录中的 operatorId 和 operatorVersion 与 Runtime 实际执行的注册定义一致，不根据节点名称或当前工程配置猜测版本

### Requirement: Show the effective parameters used by the operator

节点详情 SHALL 区分节点配置、参数默认值、输入/绑定覆盖和设备继承参数。只有从实际执行路径能够确认的值和来源才标记为本次生效参数。确需持久化的敏感字段必须在写入日志前脱敏，明文凭据不得进入原始记录或导出。

#### Scenario: Inspect an overridden parameter

- **WHEN** 节点参数被本次输入或绑定值覆盖
- **THEN** 运行详情显示实际传入算子的值和可确认来源，而不是未生效的节点默认配置

### Requirement: Explain node data using operator semantics

系统 SHALL 使用匹配 operatorId 和 operatorVersion 的算子定义解释输入、输出、metrics 和 diagnostics，同时保留原始 key、原始值和原始 JSON。未知字段继续按通用原始数据展示。

#### Scenario: View a node with semantic metadata

- **WHEN** 用户查看具有匹配语义定义的算子运行记录
- **THEN** 输入、输出、指标和诊断按中文名称及用途展示，未知字段仍以原始名称和值显示

### Requirement: Preserve the existing bounded execution summary

语义化详情 SHALL 解释现有生命周期事件、ioSummary、metrics 和 diagnostics，不为展示而复制完整 outputs 或新增历史结果存储。当前记录只有摘要时，不得伪造可恢复的完整图像、集合或对象。

#### Scenario: Open a summarized output

- **WHEN** 节点事件只包含图像 shape、dtype 或集合数量摘要
- **THEN** 详情展示真实摘要，不推造完整图像或集合
