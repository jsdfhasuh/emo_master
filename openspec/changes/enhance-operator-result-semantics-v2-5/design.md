# 设计

## 1. 目标与边界

将算子运行信息变成可理解、可追溯、可统一展示的数据，而不是机械翻译 JSON 字段。本次只针对当前正式工程格式和当前 Runtime / Designer，不设计历史工程迁移、旧 Manifest 兼容、旧 SDK 双版本或历史日志回填。

## 2. 当前实现基础

审查基线：b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd

- PluginManifest 已有 operatorId、displayName、version、inputPorts、outputPorts、paramSchema、summary。
- 端口描述符当前校验 type、required、nullable、schemaVersion。
- Runtime OperatorInfo 已传递端口 specs、paramSchema、summary、版本等元数据。
- 正式 Job 完成事件已有 metrics、diagnostics、ioSummary；日志投影不保留完整 outputs。
- 单算子调试已有 sourceIdentity、operatorVersion、outputs、metrics、diagnostics、elapsedMs。
- Designer 已有 LogRecordDetail、LogJsonTree 和通用值展示能力。

优先复用这些能力，不另建注册中心、日志数据库、结果查询服务或逐算子 UI。

## 3. P0：先审查，再确定结构

对 82 个 operatorId 逐一核对当前 Manifest、实际参数 schema、端口定义和执行实现。共享 helper、继承类和设备托管路径需要沿各 operatorId 的真实分支检查，不能只按 Python 文件合并审查。

每个算子需要形成以下结论：

- 输入和输出端口：真实类型、用途、数据结构、单位/坐标含义（适用时）、当前日志可见性。
- 配置参数：名称、用途、默认值、约束、枚举、条件参数及敏感性。
- 实际生效参数：节点配置、默认值、输入/绑定覆盖、设备继承等来源；只对源码能确认的部分下结论。
- metrics / diagnostics：实际赋值来源、单位、统计口径、重复数据及业务意义。
- 缺失信息：是否有业务价值、是否能从真实执行路径可靠取得、应补充还是复用已有结果。

Runtime 的失败、跳过、取消等通用生命周期行为统一审查一次；只有算子存在专属处理或特殊返回结构时，才在该算子的审查记录中补充。

P0 完成后才确定 resultSchema、端口说明和必要的生效参数记录结构。不能预设一套字段强迫所有算子迁就。

## 4. 语义元数据与参数

复用现有 PluginManifest 和 paramSchema。元数据说明覆盖输入、输出、metrics、diagnostics；适用时提供中文名称、用途、单位、格式和展示重要程度。端口的 type、required、nullable、schemaVersion 仍由现有端口契约负责，说明字段不得改变端口连接兼容性。

配置参数说明直接使用 paramSchema，不另建重复参数定义。配置值与本次实际生效值必须区分；只有沿真实执行调用链确认的值才能标记为生效值，无法确认来源时如实标记。

生效参数只记录对排障有价值且确实需要持久化的字段，不复制整份工程或设备配置。若需要持久化敏感参数，必须在进入日志前脱敏；不能只在界面隐藏明文。

新增 metrics / diagnostics 必须来自真实执行路径，并且不能重复已有 outputs、ioSummary 或框架生命周期信息。纯数值算子可以没有专用 metrics；不能为统一外观制造重复计数、估算耗时或推断业务状态。

## 5. Runtime 身份与传递

正式 Job 的算子身份从实际注册描述符取得，使用 operatorId 和 operatorVersion 关联节点生命周期记录。不能从显示名称或当前工程配置猜版本。框架节点和无实际注册身份的记录不伪造字段。

优先复用现有事件 payload / OperatorInfo 通道；只有现有通道确实无法表达时才修改 proto，并运行正式生成脚本。单算子调试沿用已有 sourceIdentity 和 operatorVersion。

## 6. 日志详情展示

复用 LogRecordDetail 和通用值组件。左侧执行树、查询、分页、导出和设备诊断不在本 Change 重做。

右侧详情按实际存在的数据组织：

1. 运行概览：算子名称、状态、节点总耗时、算子内部耗时、核心业务结果。
2. 输入数据：中文端口名称、用途和真实摘要。
3. 本次生效参数：实际值、可确认来源和说明；敏感值隐藏。
4. 输出结果：中文端口名称、用途、真实标量/集合摘要及现有可用结果资产。
5. 运行指标：按耗时、业务统计、计算结果等真实语义分组。
6. 诊断信息：处理摘要、模型/设备/通信信息、警告和回执。
7. 技术详情：原始字段、类型、原始 JSON 和记录身份。

无数据区域可以隐藏，不填充推测内容。概览可以合并重复值，但原始来源必须保留。0、false、null、空字符串、空数组、空对象和字段缺失继续区分。

正式 Job 目前只保留有限 ioSummary，不保留完整 outputs。语义元数据只能解释已有摘要，不能恢复未保存的图像、集合或完整检测框。调试结果只有真实 outputAssets 存在时才预览。

## 7. 验证

全部 82 个算子做静态字段和元数据覆盖检查；真实执行验证选取少量代表算子，覆盖不同执行类别。Runtime 参数来源与脱敏、元数据传递、Designer 详情和单算子调试按共用链路验证，不为每个字段或算子重复测试。

使用现有模拟设备、隔离服务和测试夹具，不连接生产相机或 PLC，不要求全部算子加载真实模型。

## 8. 非目标

历史项目/日志迁移、旧版本兼容层、日志回填、第二套日志存储、全量 outputs 持久化、逐算子自定义 UI、所有算子强制增加 metrics 或主动 logger、Web 日志 UI、工作流调度和设备管理重构，以及没有实际场景支持的防御性框架和重复测试。
