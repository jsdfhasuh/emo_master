# V2.5 OpenSpec 计划包

## 当前状态

**P0 已完成，P1/P2/P3 待实施。** 本轮由 ChatGPT Web 更新 PR20 计划并完成 82 个算子的静态源码审查；后续可从 P1 开始，不需要再交给其他执行者重做全量审查。

代码基线：`ed98a0212820efd0e6436029b69e82dc6e313fc7`，已含 PR19、PR25。审查源工作区干净；工作分支正常同步此 main。本轮相对该基线只新增或更新本计划包，未实现产品功能。后续源码变化时复核受影响条目即可。

审查覆盖 194 个输入端口、235 个输出端口、366 个顶层参数，逐字段记录还包括嵌套配置、托管设备参数、现有 metrics/diagnostics、复杂输出字段及源码依据。静态审查完成不代表 82 个算子已执行或说明已经实现。

## 阅读入口

- [proposal.md](proposal.md)：范围与影响。
- [design.md](design.md)：根据源码确定的结构和最小实现路径。
- [tasks.md](tasks.md)：唯一实施清单；P0 已勾选，后续任务保留未完成。
- [operator-field-audit.md](reference/operator-field-audit.md)：82 个算子的可读索引与关键结论。
- [operator-field-audit.json](reference/operator-field-audit.json)：完整逐字段事实、当前合同、实际参数来源、处理决定和源码证据。
- [runtime-field-audit.md](reference/runtime-field-audit.md)：注册、参数阶段、事件投影、调试与安全副本的共用链路审查。
- [delivery.md](delivery.md)：本轮基线、修改、验证边界和 CI 状态记录。

## 规格

- [operator-result-semantics](specs/operator-result-semantics/spec.md)：字段语义、可信身份和受控参数摘要。
- [operator-readable-run-details](specs/operator-readable-run-details/spec.md)：正式 Job 与单算子调试的详情解释。
- [runtime-log-workbench](specs/runtime-log-workbench/spec.md)：在现有能力上增加身份、摘要和语义解释的增量要求。

本计划不包含历史工程迁移、历史日志回填或旧版本兼容工作。
