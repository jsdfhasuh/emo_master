# Runtime 共用链路审查

基线：`ed98a0212820efd0e6436029b69e82dc6e313fc7`（2026-11-25，已含 PR19、PR25）。
执行角色：ChatGPT Web。方式：静态源码审查；不是运行测试或桌面验收。

以下路径均相对仓库根目录，行号对应上述固定提交。算子专属字段见 `operator-field-audit.json`，不在 82 个条目中重复通用生命周期。

## F01：注册身份和元数据的权威来源

- `core/plugin/models.py:18-35` 的 `PluginManifest` 保存 operatorId、version、端口、paramSchema 和 PR19 的 runtimeCapabilities；`PluginDescriptor` 将实际导入类与该 Manifest 绑定。以下省略的 `core/`、`apps/` 路径均位于 `src/emo_master/`。
- `core/plugin/registry.py:232` 形成实际 activeOperators；正式 Job 的 `apps/runtime/jobs/worker_main.py:106-110` 在子进程重新扫描插件，以该进程实际描述符为准，不能用 Designer 当前目录猜版本。
- `core/plugin/validator.py:511-578` 的 `validateConsistency` 同时核对身份、端口和 **完整 paramSchema 相等**。在 Manifest 增加 title/description 也必须更新实际 OperatorMeta 的内嵌 schema 或共同生成来源，否则算子可能注册失败。
- 继承实现要看最终 entry 类：例如 Histogram 子类修改版本，不能从共享父类抄版本。

**实施决定：** 结果说明新增为 Manifest 的可选 `resultSchema`，通过现有注册描述符取得；不增加必填 OperatorMeta 字段，不另建注册表。补配置说明时同时维护实际 schema 来源。

## F02：端口合同不能顺便重写

`core/contracts/port_types.py:45-132` 的端口描述符只接受 type、required、nullable、schemaVersion。normalizePortSpec/连接校验依赖这些字段，简单向所有端口对象塞 title 会被拒绝。字符串端口和描述符端口的 required 默认行为还随输入/输出调用位置不同，不能机械统一。

**实施决定：** `resultSchema.inputs` / `outputs` 按真实端口 key 旁挂中文说明；保留原端口结构，不为文案扩展 PORT_DESCRIPTOR_FIELDS。变量读写的动态端口继续由 `core/project/global_variables.py:variablePorts` 决定；SQLite 调试映射输入继续由 `apps/runtime/operator_debug/contracts.py:debugInputPorts` 生成，不伪造为静态 Manifest 端口。

## F03：OperatorInfo 能复用，但缺少独立结果语义载荷

- `proto/runtime.proto:message OperatorInfo` 已有 input_port_specs_json、output_port_specs_json、param_schema_json、版本和设备声明，字段编号到 20；不存在结果语义字段。
- `apps/runtime/grpc_server/service.py:ListOperators` 从当前描述符序列化这些字段；`apps/designer/services/runtime_client.py:listOperators` 解析为同一个 OperatorDefinition，本地与远程共用该通路。

**实施决定：** 为现有 OperatorInfo 增加一个 `result_schema_json` 字段，同步 Runtime、RuntimeClient、OperatorDefinition，并执行 `scripts/gen_proto.py` 正式生成。结果语义不塞进参数 schema、summary、editor_spec_json，也不增加新 RPC。具体字段编号实施时核对目标 proto 中尚未占用编号。

## F04：实际参数至少有三个阶段

| 阶段 | 当前源码与事实 | 可以如何表述 |
|---|---|---|
| 工程准备 | `core/project/snapshots.py:141-190,242-318` 应用默认值、节点配置、节点输入文件、资源/现场绑定及变量校验 | 准备后的节点参数；若来源区分已丢失，不能反推“用户明确输入”或“默认值” |
| 调用边界 | `apps/runtime/execution/operator_executor.py:127-151` 解析本次设备参数和全局变量；`invoke` 将 params、nodeInput、runtimeContext 传给 executeNode | 本次调用参数与已解析设备参数；不能假设 params 包含设备连接配置 |
| 算子内部 | 输入端口覆盖参数、按模式忽略字段、effectiveFilterParams 置中性值、图像尺寸裁剪、实际模型/文件路径等 | 仅已确认最终使用的值才能称生效值；已有输出/诊断优先作为权威来源 |

设备路径 `core/plugin/devices.py:validateNodeSources/resolveParameters` 按设备 defaults、节点 overrides、变量/输入 bindings 解析并再次校验。连接与 policies 仍归设备定义；不把 PLC/TCP Manifest 中同名直接连接参数误称托管执行的来源。

**实施决定：** 使用受控参数摘要区分 `call` 和 `effective`，记录可确认的来源。只选择排障所需字段；没有最终值就保留调用阶段说明。算子内部已有真实诊断或输出时引用它，不复制整个 params、整个设备定义或整套资源表。共享框架不推测算子内部覆盖结果。

## F05：正式节点事件缺身份，完成日志还存在白名单

- `apps/runtime/workflow/runner.py:193-305`：node.started 有输入摘要；node.completed 有 outputs、metrics、diagnostics、ioSummary；node.failed 有 code/message 和已有失败指标/诊断；node.skipped 保留缺失输入或未选分支原因。这些节点载荷当前没有 operatorId/operatorVersion 或受控参数摘要。
- `apps/runtime/events/jsonl_writer.py:407-476`：完成事件投影使用字段白名单，保留 metrics/diagnostics/ioSummary，剔除完整 outputs。新增身份/参数只放 Runner 载荷会在持久化后丢失。
- `core/contracts/run_inspection.py:describeValue/summarizePorts`：图像仅 shape/dtype，集合仅数量，普通对象仅字段数；最多 12 个端口和既有字节预算。结果说明不能恢复未保存的对象内容。

**实施决定：** 注册算子生命周期载荷带可信身份；参数摘要在实际值可确认后附加。同步完成事件的 JSONL 投影，并验证查询/复制/导出真实保留且已脱敏。框架节点不补假身份，未执行的 skipped 不补本次参数或输出。保留有界 ioSummary，不新增全 outputs 持久化。

## F06：单算子调试的现有记录不能直接当最终参数

- `apps/runtime/operator_debug/manager.py:318-326` 已存 rawParams、effectiveParams、inputSources、bindingSources；sourceIdentity 含 operatorVersion，但 **没有 operatorId**。
- manager 里的 effectiveParams 取自提交前处理；之后 `apps/runtime/operator_debug/resources.py:63-145` 仍会替换路径、选择工程输入文件、把 SQLite 目标改为隔离库等。
- `apps/runtime/operator_debug/worker.py:130-175` 使用 resources.prepare 返回的参数执行并合并 resourcePaths/debugDatabasePath 等说明，返回 outputs/outputAssets/metrics/diagnostics/elapsedMs；没有回传这一步的完整实际参数摘要。
- `manager.py:execution` 返回保存的记录；`rpc.py:_operatorDebugRpc` 直接 encode payload。不能认为已有 UI 隐藏就等于返回数据经过脱敏。

**实施决定：** 从会话可信 spec 补 operatorId；Worker 在资源准备后取得受控调用参数摘要，已存在的 resourcePaths / debugDatabasePath 优先复用。保留 session/generation/executionId，不制造正式 Job 或 node.completed。展示/导出副本须同时安全化已有 rawParams/effectiveParams/bindingSources；`apps/designer/ui/operator_debug_window.py:826-829` 当前还将 rawParams 放入导出 identity，不能只保护新摘要。`log_record_detail.py:379` 的早期 effectiveParams 标明准备/调用阶段。执行内部原值与安全副本分开，不修改真正传入算子的配置。

## F07：现有脱敏不是所有事件的统一入口

- `apps/runtime/events/operator_logger.py:_safeValue` 对主动日志 payload 的敏感 key 脱敏，并有长度/深度控制。
- `core/contracts/run_inspection.py:_secret` 对端口名检查，但字符串值本身仍按摘要保留。
- `apps/runtime/events/jsonl_writer.py:_jsonValue` 只做 JSON 可表达化，不是凭据过滤器；Runner 的 metrics/diagnostics 不自动经过 OperatorLogger。

**实施决定：** 复用既有安全值处理中的适用部分，在摘要进入事件或调试展示/导出副本之前执行，并覆盖同一副本中现有参数和绑定字段；按 P0 已识别的密码/认证字段及含认证信息的 RTSP URL 处理。参数说明使用 `xSensitive` 等现有 schema 可接受的 x 扩展，不能使用 `_parameters` 不支持的通用 JSON Schema 关键字来改变校验。验证原始 payload、查询、复制和导出，而不是只测界面遮挡。不增加任意字符串内容识别、全工程扫描或第二套脱敏存储。

## F08：生命周期审一次，保留真实副作用回执

- `OperatorExecutor.invoke/execute/failureDiagnostics` 保留可确认的 TCP、SQLite、归档及设备 outcome；输出校验或之后取消不能抹掉已发生的提交回执。
- `WorkflowRunner` 的 node.completed 表示该节点执行正常结束，不表示产品合格，也不保证后台文件已发布。汇总 metrics.update/diagnostics.update 可能覆盖同名字段；节点解释使用本节点数据，不把工作流聚合当逐节点原始结果。
- PR19 的 ImageSaver 后台分支返回 accepted=true、saved=false、state=SUBMITTED；latencyMs 只计提交返回。后台任务成功后的 Artifact 与 SQLite FILE_REFERENCE 等待沿用 `apps/runtime/workflow/background_outputs.py`，不新建保存状态账本。
- PR25 的排空准入取消通过 `project_config/coordinator.py` 和 `devices/ipc.py` 保留取消结果；`tests/runtime/test_startup_cancellation.py` 已有明确取消与真实配置故障竞争的直接回归。不可把带停止标志的真实故障统一显示为用户取消。

**实施决定：** 原有行为保留；P1 若碰到这些链路，复用对应现有回归。无需给 82 个算子重复生命周期测试，也不把 ACK、发送成功或 PLC 回读称为物理动作完成。

## F09：当前验证边界

本次只完成静态事实核查和计划更新；上述缺失点仍属于 P1/P2 待实施，不代表产品已修复。计划以全部算子静态覆盖、少量代表执行、共享机制直接反例和一次相关原生 Qt 走查验收。

2026-11-25 北京时间 09:39 查询：合并后源码 CI [45520434988](https://github.com/jsdfhasuh/emo_master/actions/runs/45520434988) 已于 09:37 结束，结论 failure。Linux other 在 `test_draft_pure_preview_context.py::testActualLocalImageButtonWithFreshRuntimeAndUnsavedNode[2.1-vision.preprocess.roi]` 出现原生进程 Aborted；Windows other/runtime、Linux runtime 均成功，Ubuntu 汇总因该失败而失败。[Web 检查](https://github.com/jsdfhasuh/emo_master/actions/runs/45520435031) 成功。

同源码树的合并前完整检查 [45382159632/attempts/2](https://github.com/jsdfhasuh/emo_master/actions/runs/45382159632/attempts/2) 通过。该对照不能证明崩溃根因，也不能将合并后 CI 记为全绿。这不是本次文档修改的验证结果，不阻断 P0；涉及该调试链路实现/验收前做针对性定性。本 Change 不自动承担全仓 CI 整治、全硬件或冻结包验收。

## 实施核对

**Codex（6.2 Sol）** 依据上述审查落地通用链路。P0 基线行号继续作为原始证据。

- F01–F03：`core/plugin/result_schema.py` 校验说明与端口、参数的引用。`OperatorInfo` 的字段 21 传输该声明。内嵌 Runtime 和回环 gRPC 经同一个 RuntimeClient 解析。
- F04–F05：`ParameterInspection` 在调用边界截取选定的可显示参数，标明准备、变量或资源解析等来源。算子可通过实际指标、诊断或结构化证据提供生效值。设备证据保留设备槽及实际设备类型和 schema 版本。Runner 从已注册 Manifest 取得身份，JSONL 完成记录投影保留身份与受限摘要。
- F06–F07：调试会话保存可信身份，Worker 等资源准备完成后采集调用值。调试提交、准备参数和绑定值的展示副本使用 schema 标记和已知认证字段脱敏，认证 URL 也处理；业务输出值及输出资产沿用执行结果。
- F08：复测后台受理、产物登记、FILE_REFERENCE、SQLite 提交回执和取消与真实故障等路径。测试通过情况及基线 CI 证据见 [交付记录](../delivery.md)。
