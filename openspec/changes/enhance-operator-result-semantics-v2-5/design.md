# 设计

## 1. 基线、阶段和边界

代码基线：`ed98a0212820efd0e6436029b69e82dc6e313fc7`，已含 PR19 后台任务服务和 PR25 收尾修复。PR20 工作分支同步此 main；不再按 PR19 未合并或双方并行的旧条件安排实施。

本轮由 **ChatGPT Web** 更新计划并进行 P0 源码审查。逐算子证据见 `reference/operator-field-audit.json`，共用链路见 `reference/runtime-field-audit.md`。P0 结论供后续实现直接使用；P1/P2/P3 尚未完成，不以文档审查冒充产品验收。

本 Change 面向当前 Runtime、Designer 和内置算子。保留已确认的日志工作台及单算子调试入口；不做历史迁移、日志回填、旧 SDK 双版本、第二套日志存储、全 outputs 持久化、设备管理或工作流调度重构。

## 2. 已核实的实现基础

| 链路 | 当前事实 | 本 Change 的最小改动 |
|---|---|---|
| 注册 | PluginManifest + PluginDescriptor 已含真实身份、版本、端口、paramSchema、runtimeCapabilities | 可选结果说明沿用实际注册对象 |
| 参数定义 | Manifest.paramSchema 与实际 class.meta.paramSchema 全量相等校验 | 沿各算子的 schemaSource 同步补说明 |
| 端口合同 | 描述符只允许 type/required/nullable/schemaVersion | 说明按端口 key 旁挂，不改连接合同 |
| 元数据传输 | ListOperators / OperatorInfo 缺少独立结果语义载荷 | 在现有消息增加一个 JSON 字段，不增加 RPC |
| 正式运行 | 节点已有 metrics/diagnostics/ioSummary；完成日志有投影白名单 | 补可信身份、受控参数摘要并同步投影 |
| 调试 | 已有结果和 operatorVersion，但记录缺 operatorId；资源准备还会改写参数 | 从可信会话补身份，采集资源准备后的摘要 |

精确代码位置集中在 Runtime 共用链路审查。实施前若有后续变更，只更新受影响条目，不重做一次全量发现性审查。

## 3. 统一结果说明结构

采用 Manifest 可选 `resultSchema`，缺省为空。内置算子的完整覆盖由静态检查保证；第三方没有说明仍可正常注册和执行。

```text
resultSchema
  inputs:        {真实输入端口 key: FieldDescription}
  outputs:       {真实输出端口 key: FieldDescription}
  metrics:       {实际返回字段 key: FieldDescription}
  diagnostics:   {实际返回字段 key: FieldDescription}
  captureParams: [[参数路径片段, ...], ...]

FieldDescription
  title: 中文名称
  description: 真实用途、条件和统计口径
  unit: 适用时的单位
  group: duration/business/result/parameter（指标有明确分类时）
  fields: 已知对象子字段说明（可选）
  items: 已知数组元素说明（可选）
```

- inputs/outputs 只引用真实端口，不重复 type、required、nullable、schemaVersion。
- metrics/diagnostics 解释已有值，不改变业务返回。动态键和未声明字段继续原始展示，不增加表达式语言或逐算子组件。
- fields/items 只解释审查确认的结构。强类型集合复用既有合同及通用组件，不逐算子再定义一份数据模型。
- captureParams 仅选择算子 params 命名空间，按 paramSchema 解释，不复制参数名称、默认值或说明。默认不采集，按审查结论选择必要字段；不得混入 deviceParameters/connection 的同名路径。
- 校验说明结构及端口/参数引用；字段缺失和缺少说明分开处理。不把内置说明完整性变成所有第三方插件的执行门槛。
- 不新增必填 OperatorMeta 字段。补参数 title/description 时同步其内嵌、共享或继承的 schema 来源。

在现有 OperatorInfo 中新增 `result_schema_json`，序号选择目标 proto 的未占用编号（本基线字段到 20）。Runtime ListOperators、RuntimeClient、OperatorDefinition 同步处理，并运行 `scripts/gen_proto.py`。结果说明不塞进 paramSchema、summary 或 editor_spec_json。

## 4. 身份和受控参数摘要

### 4.1 身份

正式 Job 从执行子进程实际扫描得到的 PluginDescriptor 取 operatorId/operatorVersion，关联注册算子的节点生命周期记录。不能从显示名、共享父类版本或 Designer 当前工程反推。框架节点没有注册身份时保持缺失，skipped 不补本次执行参数或输出。

调试从已准入 spec 补 operatorId，沿用 version 和 session/generation/executionId。记录缺少匹配身份或当前目录版本不同，使用通用原始值；不建立旧版本注册表或历史回填。

### 4.2 参数取值阶段

新增 `parameterSummary` 为小型条目列表，每项包含参数 `path`、真实 `value`、`stage`（call/effective）及能够确认的 `source`。来源只表述现有路径能证明的事实：准备后的节点参数、变量覆盖、输入覆盖、设备解析或算子内部归一化。已经默认化而无法区分原始填写来源时，不反推用户输入。

- call：OperatorExecutor 在全局变量解析后、executeNode 调用前选取必要字段。托管设备参数从 deviceParameters 和真实动作路径取，不能用 params 同名连接字段冒充。
- 设备值优先解释既有 deviceFrame/回执/诊断及 slot。确需补充时，在对应已有 diagnostics 中记录带 slot、设备 schema 来源的少量实际值，不纳入 captureParams 或复制 connection/policies。
- effective：优先复用本次输出/metrics/diagnostics 的真实值。仅有价值且缺失时，经对应算子既有 diagnostics 通路补少量归一化结果；框架不猜内部覆盖。
- CollectionFilter 的条件中和、CollectionSort 的默认排序键、Crop 的 roi 覆盖、模型路径及 SQLite 调试隔离路径，采用处理后的值或明确标为调用参数。
- 模式未启用、字段缺失、显式空字符串保持原意；FlowSwitch 可选 case 不得无条件填成默认空串。
- 调试 Worker 在 `resources.prepare` 后取得摘要。manager 原有 rawParams/effectiveParams 不直接改称最终生效值，resourcePaths/debugDatabasePath 等来源继续复用。

### 4.3 事件与脱敏

摘要在进入事件或供查看的调试记录前筛选和脱敏，执行仍使用可信原值。复用已有敏感 key 处理，补足审查确认的认证字段和含认证信息的 URL。必要的敏感性标记使用参数校验器允许的 x 扩展，不引入未支持的 JSON Schema 关键字。

调试展示/复制/导出的副本同时处理现有 rawParams、effectiveParams、bindingSources 及已知认证路径，不能只保护新 parameterSummary 却原样导出旧字段。现有早期 effectiveParams 明确标为准备/调用阶段；不改执行所需原值，不新建日志库。

JSONL 完成事件白名单必须保留新增身份与 parameterSummary。验证 raw payload、持久化、查询、复制和导出均保留安全结果；沿用既有大小约束，不复制整份工程、设备配置、资源表或大数组。

## 5. 按审查收敛的结果语义

- ImageSaver 后台 accepted/saved/state/taskId 分别解释；提交接受不等于文件完成，latencyMs 只计复制/提交返回。排空、成功产物登记和 SQLite FILE_REFERENCE 等待沿用 PR19。
- TCP 发送到传输层、网关 ACK、PLC 回读和设备播放请求各保留真实含义，不能推成物理动作完成。托管/直接路径的参数来源、attempts 和重试分别说明。
- 数值/几何无专用 metrics 时使用既有输出与框架耗时；百分比未定义保留 null/defined=false，点均值中心、轮廓几何面积和连通域像素面积按各自源码解释。
- 图像/推理解释自适应阈值的 null、模板匹配 quality、PatchCore 相对热图、真实模型来源与输出类型，不统一叫成校准概率。
- EmptyOperator 的 latencyMs=0.0 是占位，P1 去除该伪计时并复用框架耗时；enabled 目前仅校验、无执行开关作用，说明事实，不扩展禁用功能。
- 详细新增/复用/不补决定以逐算子记录为准。已有 outputs、metrics、diagnostics 只说明来源关系，不为齐整再造重复计数或耗时。

## 6. Designer 接入范围

复用 LogRecordDetail、LogJsonTree 和现有调试结果接线，在已确认的详情结构中解释匹配身份的字段。参数说明来自 paramSchema，摘要区分调用/生效阶段。保留原始 key、值、类型、JSON、复制和展开。

0、false、null、空字符串、空集合和缺失继续区分。ioSummary 仅有 shape/dtype/数量时只解释摘要；图像/集合资产必须实际存在。调试结果保持独立，不改设备准入和调试隔离。

本轮计划更新不更换已确认布局。后续若调整具体界面或操作流程，先按用户要求提供可操作原型；本轮源码审查无需重新制作界面原型。

## 7. 验证范围

| 范围 | 必要验证 |
|---|---|
| 全部 82 算子 | 静态核对端口、参数含嵌套/条件项、说明及引用；复用注册一致性检查 |
| 身份与参数 | 代表性继承版本、变量/输入覆盖、内部归一化、资源解析；共用机制不逐算子重复 |
| 日志与脱敏 | 一组完成事件贯通持久化/查询/导出，直接反例确认明文凭据未进入原始记录 |
| 结果语义 | 各执行类别少量代表，已有指标、重复字段和空值含义不失真 |
| Designer/调试 | 共用解释、真实会话身份、资产与安全摘要；一次相关原生 Qt 走查 |
| 收尾 | OpenSpec strict、协议生成一致性、受影响测试及适用 CI，列出未执行/跳过 |

不要求全部算子实际执行、全部模型/生产硬件、逐字段重复测试、线程交错排列、性能专项或冻结包作为开工条件。PR25 取消/故障和 PR19 后台任务测试在受影响时复用，不另建同义测试组。

## 8. 已合并依赖与基线记录

PR19、PR25 已合入，能力通过正常分支同步继承。PR20 相对该 main 的新增差异只属于本 Change；无需临时集成分支或重新分配已结束 PR 的文件所有权。

记录固定源 SHA 和后续增量，不以移动分支名代替证据。合并后源码 CI 曾出现 Designer 进程崩溃，同树合并前 CI 通过，根因未定；涉及该链路实现/验收前针对性核查，不升级成重新验收整个 PR19 或暂停 P0 的条件。
