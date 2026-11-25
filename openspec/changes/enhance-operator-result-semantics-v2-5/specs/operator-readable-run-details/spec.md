# 算子可读运行详情

## Purpose

让工程师通过 Designer 右侧详情理解算子运行情况，同时保留原始数据和开发排障能力。正式 Job 与单算子调试复用相同展示组件。

## ADDED Requirements

### Requirement: Present run details in business-readable sections

节点详情 SHALL 按实际存在的数据组织运行概览、输入数据、本次生效参数、输出结果、运行指标、诊断信息和技术详情。概览优先展示真实状态、节点耗时、算子内部耗时及有业务意义的核心结果。无数据区域可以隐藏，不得填充推测内容。

#### Scenario: Read a YOLO detection result

- **WHEN** 工程师打开 YOLO 检测完成记录
- **THEN** 可以直接理解检测数量、可用类别/置信度摘要、模型和推理后端信息及耗时；没有完整历史集合时不得伪造目标明细
- **AND** 新执行检出目标时，历史完成记录保留最终集合首个最高分目标的类别摘要和实际置信度。空集合仅报告数量零。

#### Scenario: Read a business decision from the overview

- **WHEN** 数值比较、PatchCore 或 SQLite 的完成记录包含已声明的业务结论
- **THEN** 概览优先显示实际比较布尔值、异常判定或事务回执状态，节点状态和内部耗时各自保留
- **AND** 无法匹配算子版本时，模型路径及附加来源字段按收到的原始结构展示

#### Scenario: Distinguish SQLite submission from final database outcome

- **WHEN** 正式 Job 使用后台写入，节点返回排队回执，后续收到后台终态事件
- **THEN** 节点记录保留当时的 QUEUED，后台事件显示本次 writeId、实际事务状态和影响行数
- **AND** 单算子调试在同一 writeId 出现排队输出与终态诊断时，两者都可辨认并分别核对原值

### Requirement: Distinguish node duration from operator duration

系统 SHALL 分别展示框架测得的节点总耗时和算子真实测得的内部耗时。两者统计边界不同，不得相互覆盖，也不得将差值解释为未经测量的阶段耗时。后台提交耗时 SHALL 明示提交边界，固定占位常量不得作为真实计时。

#### Scenario: Compare duration values

- **WHEN** 节点记录同时具有节点耗时和 latencyMs
- **THEN** 界面分别标记节点总耗时和算子内部耗时，并保留来源与单位

### Requirement: Explain inputs and outputs using port semantics

输入和输出 SHALL 使用当前算子版本对应的端口中文名称及用途。图像、集合、几何对象、数值、回执使用适合类型的摘要。系统不得把日志中不存在的完整图像、集合或历史 outputs 当作可恢复内容。

#### Scenario: View a summarized collection

- **WHEN** 正式 Job 事件只保存集合摘要
- **THEN** UI 展示真实数量和摘要，并说明完整内容不可从当前记录取得

#### Scenario: Inspect inputs of a previous debug execution

- **WHEN** 用户回看一次已经准备好资源并进入调用阶段的单算子调试
- **THEN** 输入摘要属于所选执行，保留当时的安全标量、图像形状或集合数量，并与输入来源一同展示
- **AND** 准备失败且尚未取得调用输入时，记录保留此前已经确定的信息

### Requirement: Display effective parameters with their sources

详情 SHALL 展示必要参数条目的 call/effective 阶段及可确认来源。调用参数、保存值和内部归一化后的最终值不一致时，只有可靠最终值可称生效值；无法取得时如实保留调用阶段。敏感参数只显示已隐藏状态。

#### Scenario: Review a device-inherited parameter

- **WHEN** 参数来自设备继承配置
- **THEN** 详情显示实际值（仅在允许持久化且非敏感时）及设备来源

### Requirement: Group metrics by meaning

metrics SHALL 按字段语义分为执行耗时、业务统计、计算结果或实际处理参数等类别，不得将所有 metrics 一律称为性能指标。

#### Scenario: Read a threshold result

- **WHEN** Threshold 返回实际阈值、foregroundPixels 和 foregroundRatio
- **THEN** 用户能分别理解实际阈值、前景像素数量和占比

### Requirement: Preserve original values and source fields

可读展示 SHALL 不改变供查看的安全记录中的非敏感原值。0、false、null、空字符串、空数组、空对象和字段缺失必须可区分。系统 SHALL 保留原始 JSON、原始字段名、类型、复制值、复制记录及对象/数组展开能力。已知认证信息遵循现有安全副本要求；此要求不授权将执行内部原值改成脱敏占位值。

#### Scenario: Inspect duplicated fields

- **WHEN** 同一统计值同时出现在 outputs 和 metrics
- **THEN** 概览可突出一次，技术详情仍能查看两个原始来源

#### Scenario: Explain a dynamic object member

- **WHEN** 对象字段以 additionalProperties 对象定义动态成员值，例如 Annotate 的 classStyles 以检测类别原始 label 为 key
- **THEN** 系统 SHALL 保留动态 key，并使用该定义及其 properties/items 解释 displayLabel、color 和数组元素等已知子字段
- **AND** 具名字段说明优先；布尔形式的 additionalProperties 不作为说明，没有适用说明的字段继续显示原始名称、值与类型
- **AND** 展开和复制 SHALL 保留供查看记录的实际值及原始结构

### Requirement: Reuse readable details for single-operator debugging

单算子调试 SHALL 复用正式 Job 的字段语义与详情组件，但使用调试会话真实返回的身份、outputs、outputAssets、metrics、diagnostics 和 elapsedMs。调试数据不得伪装成正式 Job 事件。

#### Scenario: Inspect a debug execution

- **WHEN** 工程师查看单算子调试执行
- **THEN** 系统用相同中文说明展示真实调试结果，并明确标记调试来源

#### Scenario: Explain an actual debug execution record

- **WHEN** Designer 接收真实调试执行记录，可信身份位于 sourceIdentity.operatorId/operatorVersion
- **THEN** 系统 SHALL 使用该身份匹配同版本定义，显示已有结果的中文说明、适用单位、指标分类及可确认的调用/生效信息
- **AND** 系统 SHALL 展示本次真实算子身份，保留原始 sourceIdentity、session/generation/executionId 归属、原始 JSON 和复制内容
- **AND** 正式 Job 继续使用事件 payload 身份；调试不依赖人为增加的顶层身份，也不从当前编辑器状态猜测身份

#### Scenario: Explain a resource prepared by the debug worker

- **WHEN** Worker 将文件路径解析为实际 Runtime 路径，或将 SQLite 目标替换为隔离调试库
- **THEN** 详情依据资源准备后的安全摘要或已有资源诊断解释实际使用值，不以提交前的 effectiveParams 覆盖它
- **AND** operatorId/operatorVersion 来自可信调试会话，并继续匹配 generation/executionId
