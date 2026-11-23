# 算子运行信息完整化与日志可读性增强

Change ID：enhance-operator-result-semantics-v2-5  
计划版本：V2.5  
审查基线：b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd

## Why

当前 emo_master 已具备节点生命周期事件、输入输出摘要、metrics、diagnostics、算子注册元数据、原始 JSON 和单算子调试结果，但运行详情仍大量暴露内部字段名。工程师难以理解参数用途、实际生效值、输入输出含义及业务结果。

82 个内置算子的现有实现差异明显：部分已有业务结果但缺少说明，部分有价值的运行数据尚未返回，部分算子本身不需要额外指标。不能只做英文翻译，也不能强迫所有算子增加相同 metrics。

用户明确不需要历史项目迁移。本计划只面向当前正式工程格式、当前 Runtime、当前 Designer 和当前内置算子，不设计旧工程迁移、旧日志迁移或旧版本兼容机制。

## What Changes

- 对全部 82 个内置算子逐字段审查输入端口、输出端口、配置参数、实际运行参数、metrics、diagnostics 和错误结果。
- 判断每个算子是否缺少有实际业务价值的运行信息；需要时补充真实输出，不需要时记录理由。
- 在现有 PluginManifest、OperatorInfo、RuntimeClient 链路上扩展统一运行结果说明元数据，具体结构在 P0 审查后确定。
- 区分节点配置、默认值、绑定值、设备继承值与实际传入算子的生效值；按需记录受控参数快照并脱敏。
- 正式 Job 记录准确的 operatorId 和 operatorVersion，使运行详情匹配实际执行的算子定义。
- 改造 Designer 右侧日志详情，优先展示运行结论、输入、参数、输出、指标和诊断，同时保留原始 JSON。
- 单算子调试复用同一套字段解释和展示组件。
- 全部 82 个算子完成说明覆盖和审查结论；不要求全部增加 metrics 或主动日志。

## Capabilities

### New Capabilities
- operator-result-semantics：算子输入、输出、参数及运行数据的语义说明、字段完整性审查和元数据传递。
- operator-readable-run-details：正式 Job 与单算子调试中的统一可读运行详情。

### Modified Capabilities
- runtime-log-workbench：增加算子身份、版本、实际生效参数与语义化详情要求；保留现有日志查询、分页、导出和原始数据能力。

## Impact

PluginManifest、Manifest validator、端口描述符、Runtime 执行上下文、WorkflowRunner、事件投影、OperatorInfo、proto（仅在确有必要时）、Designer RuntimeClient、OperatorDefinition、LogRecordDetail、单算子调试接线、全部 82 个内置算子及相关测试。

不重构算子注册体系，不建立第二套日志存储，不重写执行协议，不改 Web 日志页面，不改变设备管理和工作流调度职责。

## 实施原则

1. P0 必须先完成全部 82 个算子的源码逐字段审查，随后才确定元数据结构。
2. 参数说明优先复用现有 paramSchema，不重复维护同一份配置说明。
3. 只补充源码能可靠提供且对工程师有价值的数据；不制造推测值。
4. 同一数据可在概览合并展示，但原始来源与原始值必须保留。
5. 不把节点完成等同业务合格，不把通信回执等同设备动作完成。
6. 仅以当前正式工程格式和当前消费者为目标，不增加历史迁移或旧版兼容任务。
