# 算子运行信息完整化与日志可读性增强

- Change ID：enhance-operator-result-semantics-v2-5
- 计划版本：V2.5；2026-11-25 独立复审补充
- 代码基线：ed98a0212820efd0e6436029b69e82dc6e313fc7（已合入 PR19、PR25）
- 复审实现头：3d6448d3da18790c5f97bfdecb5fc845ebd67a1c。
- 计划、P0 与本轮独立复审：ChatGPT Web；既有实现与开发验证：Codex（6.2 Sol）。

## Why

原 P0 基线中的 emo_master 已具备节点生命周期事件、输入输出摘要、metrics、diagnostics、算子注册元数据、原始 JSON 和单算子调试结果，但运行详情仍大量暴露内部字段名。工程师难以理解参数用途、实际生效值、输入输出含义及业务结果。

以下是原 P0 基线的问题统计，不能作为当前仍缺少说明的数量：82 个内置算子的原有实现差异明显。固定基线共有 194 个输入端口、235 个输出端口、366 个顶层配置参数；其中 301 个顶层参数没有 description（不含嵌套参数，不代表其余说明已全部准确）。部分结果已存在但缺少解释，部分参数还会在算子内部被覆盖或归一化，部分算子无需专用 metrics。逐项证据见 reference/operator-field-audit.json。

用户明确不需要历史项目迁移。本计划只面向当前正式工程格式、当前 Runtime、当前 Designer 和当前内置算子，不设计旧工程迁移、旧日志迁移或旧版本兼容机制。

## 本次复审补充

主要实现与既有验证已经交付。本次只关闭三个已复现缺口：R1 调试详情读取实际 sourceIdentity；R2 调试 outputs 外发安全副本处理已知认证 URL，并保留内部 output_ref 原值；R3 共享树消费 paramSchema 中已有的 additionalProperties 对象说明。

三项各补一个共享机制回归，更新原任务状态、交付记录与 PR 正文，随后由 ChatGPT Web 或人工完成 P3.5。没有新增产品能力、布局改版、历史兼容或全量测试矩阵。问题证据见 [独立复审](reference/pr20-review-20261125.md)。

## What Changes

- 对全部 82 个内置算子逐字段审查输入端口、输出端口、配置参数、实际运行参数、metrics、diagnostics 和错误结果。
- 判断每个算子是否缺少有实际业务价值的运行信息；需要时补充真实输出，不需要时记录理由。
- 在现有 PluginManifest 旁挂 resultSchema，通过 OperatorInfo 新增结果语义 JSON 字段传递到 RuntimeClient；端口连接合同保持原样。
- 区分调用参数、内部归一化/输入覆盖后的实际值和托管设备参数；按需采集受控摘要，来源无法区分时不反推。
- 正式 Job 记录准确的 operatorId 和 operatorVersion，使运行详情匹配实际执行的算子定义。
- 改造 Designer 右侧日志详情，优先展示运行结论、输入、参数、输出、指标和诊断，同时保留原始 JSON。
- 补齐单算子调试记录的可信 operatorId，并在 Worker 资源准备后取得必要安全摘要；复用同一字段解释组件。
- 同步完成事件的 JSONL 投影与展示记录脱敏，确保新身份和摘要在历史查询、原始 JSON、复制与导出中保留。
- 全部 82 个算子完成说明覆盖和审查结论；不要求全部增加 metrics 或主动日志。

## Capabilities

### New Capabilities

- operator-result-semantics：算子输入、输出、参数及运行数据的语义说明、字段完整性审查和元数据传递。
- operator-readable-run-details：正式 Job 与单算子调试中的统一可读运行详情。

### Modified Capabilities

- runtime-log-workbench：增加算子身份、版本、实际生效参数与语义化详情要求；保留现有日志查询、分页、导出和原始数据能力。

## Impact

PluginManifest、Manifest validator、参数 schema 的实际生成源、Runtime 调用边界、WorkflowRunner、JSONL 事件投影、OperatorInfo/proto、Designer RuntimeClient/OperatorDefinition、LogRecordDetail、单算子调试和 82 个内置算子的说明。精确位置及实际缺口见 reference/runtime-field-audit.md；不改端口连接类型合同。

不重构算子注册体系，不建立第二套日志存储，不重写执行协议，不改 Web 日志页面，不改变设备管理和工作流调度职责。

## 实施原则

1. 复用本轮全部 82 算子的 P0 审查和已确定结构；后续基线有增量时只复核受影响内容，不重新委托全量审查。
2. 参数说明优先复用现有 paramSchema，不重复维护同一份配置说明。
3. 只补充源码能可靠提供且对工程师有价值的数据；不制造推测值。
4. 同一数据可在概览合并展示，但原始来源与原始值必须保留。
5. 不把节点完成等同业务合格，不把通信回执等同设备动作完成。
6. 仅以当前正式工程格式和当前消费者为目标，不增加历史迁移或旧版兼容任务。

## 依赖与阶段

PR19、PR25 已合并，旧并行文件所有权和等待合并条款退出本计划。P0、主体实现和既有验证继续保留；R1–R3 及本次增量收尾以 tasks.md 为准，P3.5 仍未完成。本补充包只更新文档与证据，不把计划更新记成修复完成。修复交付继续使用现有 PR20，不自动合并或归档。
