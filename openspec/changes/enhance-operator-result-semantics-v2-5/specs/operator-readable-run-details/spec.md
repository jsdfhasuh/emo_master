# 算子可读运行详情

## Purpose

让工程师通过 Designer 右侧详情理解算子运行情况，同时保留原始数据和开发排障能力。正式 Job 与单算子调试复用相同展示组件。

## ADDED Requirements

### Requirement: Present run details in business-readable sections

节点详情 SHALL 按实际存在的数据组织运行概览、输入数据、本次生效参数、输出结果、运行指标、诊断信息和技术详情。概览优先展示真实状态、节点耗时、算子内部耗时及有业务意义的核心结果。无数据区域可以隐藏，不得填充推测内容。

#### Scenario: Read a YOLO detection result

- **WHEN** 工程师打开 YOLO 检测完成记录
- **THEN** 可以直接理解检测数量、可用类别/置信度摘要、模型和推理后端信息及耗时；没有完整历史集合时不得伪造目标明细

### Requirement: Distinguish node duration from operator duration

系统 SHALL 分别展示框架测得的节点总耗时和算子自身返回的内部耗时。两者统计边界不同，不得相互覆盖，也不得将差值解释为未经测量的阶段耗时。

#### Scenario: Compare duration values

- **WHEN** 节点记录同时具有节点耗时和 latencyMs
- **THEN** 界面分别标记节点总耗时和算子内部耗时，并保留来源与单位

### Requirement: Explain inputs and outputs using port semantics

输入和输出 SHALL 使用当前算子版本对应的端口中文名称及用途。图像、集合、几何对象、数值、回执使用适合类型的摘要。系统不得把日志中不存在的完整图像、集合或历史 outputs 当作可恢复内容。

#### Scenario: View a summarized collection

- **WHEN** 正式 Job 事件只保存集合摘要
- **THEN** UI 展示真实数量和摘要，并说明完整内容不可从当前记录取得

### Requirement: Display effective parameters with their sources

详情 SHALL 展示本次实际传入算子的参数值及可确认来源。配置默认值、节点保存值和最终生效值不一致时，应突出实际生效值。敏感参数只显示已隐藏状态。

#### Scenario: Review a device-inherited parameter

- **WHEN** 参数来自设备继承配置
- **THEN** 详情显示实际值（仅在允许持久化且非敏感时）及设备来源

### Requirement: Group metrics by meaning

metrics SHALL 按字段语义分为执行耗时、业务统计、计算结果或实际处理参数等类别，不得将所有 metrics 一律称为性能指标。

#### Scenario: Read a threshold result

- **WHEN** Threshold 返回实际阈值、foregroundPixels 和 foregroundRatio
- **THEN** 用户能分别理解实际阈值、前景像素数量和占比

### Requirement: Preserve original values and source fields

可读展示 SHALL 不改变原始值。0、false、null、空字符串、空数组、空对象和字段缺失必须可区分。系统 SHALL 保留原始 JSON、原始字段名、类型、复制值、复制记录及对象/数组展开能力。

#### Scenario: Inspect duplicated fields

- **WHEN** 同一统计值同时出现在 outputs 和 metrics
- **THEN** 概览可突出一次，技术详情仍能查看两个原始来源

### Requirement: Reuse readable details for single-operator debugging

单算子调试 SHALL 复用正式 Job 的字段语义与详情组件，但使用调试会话真实返回的身份、outputs、outputAssets、metrics、diagnostics 和 elapsedMs。调试数据不得伪装成正式 Job 事件。

#### Scenario: Inspect a debug execution

- **WHEN** 工程师查看单算子调试执行
- **THEN** 系统用相同中文说明展示真实调试结果，并明确标记调试来源
