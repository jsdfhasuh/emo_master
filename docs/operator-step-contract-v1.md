# 单算子调试契约 v1

日期：2026-10-09；2026-10-10 增补 A3-A4 实现边界。类型化资产、隔离状态和 Designer 调试入口已接入源码；A5 正在全量回归与审查。软件包和实机未验收。

## 1 当前基线

- 工作区 HEAD：`61a42bf3ab181c3b2339f0425edfb40a60d0ec7b`，包含既有未提交改动。
- 当前入口路径 `C:/Users/jsdfhasuh/my_scripts/emo_master` 解析到 `D:/jsdfhasuh/documents/my_project/emo_master`，是本次脚本记录的物理根目录，不是另一份已同步仓库。
- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，3.10.21。
- 已检查依赖：PySide2 5.15.2.1、pytest 9.1.1、grpcio 1.78.0、pydantic 2.13.5、numpy 1.26.4。
- 60 个内置 manifest 的入口、端口、版本、声明的生命周期方法及源文件哈希记录于 `docs/testing/operator-step-2026-10-09/baseline/operators.json`。静态继承列表不等于运行时实例检查，未在审查阶段打开设备或实例化设备算子。
- 基线和修改后验证覆盖 `tests/runtime`、`tests/core`、`tests/plugins`、`tests/sqlite_writer`；完整 Designer 回归单列。脚本记录测试期间源码是否变更，变更非空的结果不能称为固定源码快照验收。

## 2 能力分类

下表是完整准入实施清单，不是“全部已支持单步”的发布声明。A3 开放 47 个经来源/版本/入口/端口/参数契约验证的内置算子。以下 13 个仍为 UNSUPPORTED：coordinate_calculator、coordinate_reader、huaray_camera、image_batch_loader、image_loader、image_saver、slmp_read、slmp_write、result_writer、sqlite_writer、tcp.client、tcp.receive_once、yolo。这里使用表内 ID 的尾部名称缩写；能力查询提供准确 ID 与原因。状态类 counter / variable_read / variable_write 使用独立调试存储；未开放设备、模型、文件根或外部写入适配器。

分类：`PURE` 为无外部写入的计算；`RESOURCE` 依赖本地文件、模型或实例状态；`STATE` 必须注入隔离变量；`DEVICE` 有真实设备占用；`WRITE` 有文件、数据库或网络写入。计算类也要经过类型、容量和取消验证才能启用。

| 算子 ID | 类别 | 准入与特殊检查 |
| --- | --- | --- |
| vision.image.absdiff | PURE | 双图形状和 dtype 匹配 |
| vision.image.add_weighted | PURE | 双图输入，不重采样隐式对齐 |
| vision.preprocess.affine | PURE | 保留输出 frame 的坐标变换 |
| vision.render.annotate | PURE | 图像与几何/检测结果来源一致 |
| vision.mask.apply | PURE | 图像、mask 及 frame 配对 |
| vision.analysis.blob | PURE | 列表与 mask 输出分别保留 |
| vision.preprocess.blur | PURE | 固定图像输入 |
| vision.edge.canny | PURE | 图像参数校验 |
| vision.preprocess.clahe | PURE | 颜色/通道限制沿用算子 |
| vision.collection.count | PURE | 原始集合，不使用 I/O 摘要 |
| vision.collection.filter | PURE | 保留语义集合类型 |
| vision.collection.select | PURE | 越界和多类型返回可见 |
| vision.collection.sort | PURE | 输入副本，不污染前次输入 |
| vision.preprocess.color_convert | PURE | 不隐式接受未支持图像 dtype |
| vision.analysis.contour | PURE | 完整轮廓受资产预算限制 |
| vision.geometry.coordinate_calculator | PURE | 保留坐标空间与单位 |
| vision.io.coordinate_reader | RESOURCE | 显式文件根；session 模式需目标节点专用的准入快照 |
| vision.preprocess.crop | PURE | croppedImage/croppedFrame 成组保留 |
| vision.geometry.detection_bbox | PURE | 明确所选检测项 |
| vision.demo.empty | RESOURCE | init/dispose 生命周期夹具，无设备动作 |
| vision.preprocess.equalize | PURE | 颜色/通道限制沿用算子 |
| vision.preprocess.flip | PURE | frame 与图像同时变化 |
| vision.flow.error | PURE | 故意抛出业务错误，不能伪装为成功 |
| vision.flow.if | PURE | 只返回选中分支数据，不运行分支下游 |
| vision.flow.switch | PURE | 未选中端口缺失不等于 null |
| communication.gateway.ack_validate | PURE | 只核对传入 request/response，不发送报文 |
| communication.gateway.coordinate_format | PURE | 只构造 payload，不连接网关 |
| vision.geometry.extract_points | PURE | 保留 point2d schemaVersion |
| vision.state.counter | STATE | 隔离计数存储；绝不使用生产 accessor |
| vision.analysis.histogram | PURE | histogram 数据完整，图像仅供显示 |
| vision.analysis.hough_circle | PURE | 几何集合及 frame 配对 |
| vision.analysis.hough_line | PURE | 几何集合及 frame 配对 |
| vision.io.huaray_camera | DEVICE | 会话独占、取消、曝光等参数变更；实机未验收禁用通用入口 |
| vision.io.image_batch_loader | RESOURCE | dispose-only 生命周期；hasNext=false 的最后一张仍是有效输出 |
| vision.io.image_loader | RESOURCE | Runtime 侧文件路径和图像解码预算 |
| vision.io.image_saver | WRITE | 明确目标和覆盖策略，默认不写正式文件 |
| vision.segment.in_range | PURE | 图像及 mask 输出 |
| vision.mask.logic | PURE | 多 mask/frame 不能跨帧混用 |
| vision.analysis.minimum_enclosing_circle | PURE | 完整点集及对应坐标空间 |
| vision.preprocess.morphology | PURE | 固定输入、结构元参数 |
| vision.compare.number | PURE | 必需 left、可选 right 与参数回退 |
| vision.value.number | PURE | 无输入算子 |
| vision.preprocess.perspective | PURE | 坐标变换保持 |
| communication.plc.slmp_read | DEVICE | 读取也会连接设备；复用现有专用调试入口 |
| communication.plc.slmp_write | WRITE | 真实写入确认、回执、结果不确定，不自动重发 |
| vision.geometry.reframe_points | PURE | 坐标系映射校验 |
| vision.preprocess.resize | PURE | 输出图像和 frame 一致 |
| vision.io.result_writer | WRITE | 目标文件、覆盖和多类型输入约束 |
| vision.color.rgb_statistics | PURE | 图像、ROI 及统计 payload |
| vision.preprocess.roi | PURE | 保留已有 pure 预览，不因此自动跳过通用入口校验 |
| vision.preprocess.rotate | PURE | 输出尺寸及 frame 一致 |
| vision.analysis.shape_measurement | PURE | contours/blobs 互斥输入 |
| vision.io.sqlite_writer | WRITE | mappedOutputs、事务回执与目标库保护；默认阻止 |
| communication.tcp.client | WRITE | 一次发送可能部分完成，不因超时自动重试 |
| communication.tcp.receive_once | WRITE | 可发送 ackText；即使空 ACK 仍有监听端口占用 |
| vision.analysis.template_match | PURE | image/template 双输入、ROI 和 frame |
| vision.preprocess.threshold | PURE | 固定原图便于参数比较 |
| vision.state.variable_read | STATE | 从会话初始值/显式导入快照读取 |
| vision.state.variable_write | STATE | set/increment/reset 只影响调试存储 |
| vision.inference.yolo | RESOURCE | ONNX 文件、进程级模型缓存；关闭实例不等于回收模型 |

除以上明确审查的 ID/版本/入口外默认 UNSUPPORTED，包括未知第三方实现。相同 ID 由其他来源覆盖也不能继承内置准入。A2 使用 Runtime 已注册描述符及受信任来源核对，不凭客户端提供的“安全”标记授权。设备、网络和写入类没有通过专项验收前继续使用现有入口或显示限制，不通过隐藏确认框开放。

## 3 公共调用边界

A1 的公共节点执行器接收一个已解析的 CompiledNode、实际端口值、RunContext、CancellationToken，以及显式注入的变量、计数器、坐标快照和事件发布器。它不需要 CompiledProject，不编译图、不访问上下游、不打开 RuntimeService。

- 正式 WorkflowRunner 与未来调试 Worker 共用参数绑定解析、算子获取、执行、错误归一化、输出校验、日志和生命周期逻辑。
- 正式运行的构造/初始化、输入缺失规则、错误码、SQL 回执顺序和日志身份保持不变；不借提取逻辑顺带改变历史契约。
- 调试入口负责原始请求、端口白名单、数据传输、必需端口、参数 schema、准入及隔离；这些不能由客户端自行保证。
- 普通类算子沿用每次构造；实现 init/dispose 的类按 workflowId/nodeId 保留；现有注册实例语义保留。改变配置的调试请求先按策略重置再执行，不能修改正式 Runner 的缓存规则。
- 清理失败保留原始错误和 resourceCleanup；取消后的清理失败仍需可见。executor 的 dispose 完成不代表外部进程/硬件已退休，A2 负责最终监管确认。
- 公共调用层不复制所有输入；A3 的冻结输入与调用副本是调试会话职责，避免给正常生产流程无条件增加图像拷贝。

## 4 冻结的 RPC 职责

v1 不改变旧 RunOperatorPreview 的消息含义。以下 16 个新增 RPC 已定义于 `runtime.proto` 并生成服务端/客户端代码，使用独立的 OperatorDebugRequest/OperatorDebugReply，不复用 JobStatus。DebugValue 通过 OperatorDebugValue 的 oneof 表达。

| RPC | 请求 | 回应与幂等 |
| --- | --- | --- |
| GetOperatorDebugCapabilities | 当前 runtimeInstanceId 或初次空值 | protocolVersion=1、limits、受信任算子能力及禁用原因 |
| OpenOperatorDebugSession | openRequestId、草稿 JSON、目标三元组、operatorId、resourceRoot 引用 | sessionId、generation、内容摘要、有效期；同请求同内容返回原会话 |
| GetOperatorDebugSession | runtimeInstanceId，sessionId 或 openRequestId | 状态、资源占用、在途 executionId、最后事件序号；不隐式续租 |
| PrepareOperatorDebugInputs | session 身份、requestId、按端口的 DebugValue 映射及来源 | inputSetId 和摘要；绑定前校验，资产引用计入预算 |
| ExecuteOperatorDebugNode | session 身份、requestId、inputSetId、原始参数和绑定、timeoutMs | 立即返回 executionId/接收状态；不等待节点完成；一次只接受一个 |
| GetOperatorDebugExecution | session 身份，executionId 或执行 requestId | 已接收/执行中/终态，结果、诊断、参数与来源；过期明确返回 |
| ReadOperatorDebugEvents | session 身份、afterSequence、limit | 有界日志/状态事件及下一序号；丢失区间返回明确 gap，终态仍可查询 |
| CancelOperatorDebugExecution | session 身份、executionId、requestId | 请求取消的接收状态，不伪报资源释放 |
| ResetOperatorDebugSession | session 身份、requestId、expectedGeneration | 空闲时释放实例、重置隔离变量，成功后 generation 加一 |
| RenewOperatorDebugSession | session 身份 | 更新同一会话租约，不推进算子 |
| CloseOperatorDebugSession | session 身份、requestId | 幂等关闭；未退休时返回 closing/held |
| WriteOperatorDebugAsset | session 身份、requestId、assetId、offset、totalBytes、content、mimeType、sha256、provenance | 256 KiB 分片；首片分配 ID；完整校验后可用于输入 |
| ReadOperatorDebugAsset | session 身份、assetId、offset | 完整资产元数据和最多 256 KiB 原始字节；无本地路径接口 |
| ListOperatorDebugSources | session 身份、offset、limit | 当前已加载同项目的完整历史资产及调用来源，不返回摘要伪造值 |
| ImportOperatorDebugSource | session 身份、requestId、assetId | 显式复制到调试存储；历史淘汰不影响已导入数据 |
| CopyOperatorDebugVariables | session 身份、requestId | 显式读取并复制生产持久变量；不读取其他 Job 的临时值，不写回生产 |

session 身份包含 runtimeInstanceId、sessionId、generation。只有重置成功才增加 generation，迟到请求必须拒绝。重复重置请求在检查旧 generation 前先命中原请求去重，不连续重置两次。

openRequestId 的去重与查询由 Runtime 实例级有界登记管理：达到登记预算时拒绝新会话，或在明确发布的查询保留期后以过期标记拒绝旧请求，不自动重建旧 ID。客户端换 Runtime 后不能自动重发旧开会话/执行命令。

图片/大数据上传和下载复用资产传输组件，增加会话所有权校验；不能把任意服务器本地路径当作资产引用。未保存项目不能被旧上传接口“必须已 LoadProject”的前置条件阻断，新调试资产入口按已校验的草稿会话授权。

### DebugValue 与记录

DebugValue 以明确的 oneof 表示 inlineJson、assetRef 或 outputRef(executionId, port)。inlineJson 必须是严格 JSON：拒绝 NaN、Infinity、重复键、超深嵌套和任意 Python 对象。未提供端口由映射缺键表示，显式 null 只能在端口允许时提供。any/object 端口也不能用于绕过传输和体积限制。

上传和导入输入由会话固定持有至 reset/close；未再被输入集或最近结果引用的调试输出可回收。配额不足时拒绝新资产，不隐式更换固定输入。单值编码和图像解码上限各为 64 MiB；同时计入编码和解码数据的会话预算。此预算不是 Python/Qt/模型进程总 RSS 上限。reset 会销毁旧资产、旧输入集和旧执行结果，旧 requestId 仍保留去重身份。

Designer 节点菜单、编辑菜单及既有参数窗口有同一调试入口；F5/F6 不变。文件和历史源仅在显式选择时导入，变量复制需要确认。输入支持未提供/值/null/完整源；复杂值使用结构树，JSON 仅高级入口。编辑参数直接供下一次执行，变量绑定变化需结束并重开会话。关闭/节点删除/工作区变化使原 UI 失效，后台仍使用固定的原 Runtime 连接完成清理。结果最多展示最近两次，完整历史调用身份和有效参数可在详情中检查。

首版图像传输限定 uint8 GRAY(H,W)/BGR(H,W,3)，PNG 无损；文件选择器解码后明确报告不支持的 alpha、uint16 或浮点输入，不静默丢通道/位深。图像资产带 shape/dtype/内容摘要和 provenance；通用 ndarray 不在 v1 范围内。结构化几何/通信 payload 沿用现有 schemaVersion，不另造坐标协议。

执行记录区分 rawParams、effectiveParams、bindingSources、inputSetId、输出资产和 sourceIdentity；字段含义与计划一致。参数和输入的内容摘要基于确定性序列化，不使用工程 revision 代替草稿版本。日志含凭据时脱敏，用户明确选择的输入值与日志不是同一保留策略。

### 限额与错误

| 项目 | v1 默认 |
| --- | --- |
| Runtime 通用调试会话 | 1；正式运行之间的 maxConcurrentJobs 不变 |
| 会话在途执行 | 1 |
| 租约/续租 | 60 秒 / 20 秒，独立于执行查询 |
| 执行期限/协作取消宽限 | 30 秒 / 3 秒；强制退出需真正 join 后才释放 |
| 草稿、参数、内联请求总预算 | 768 KiB，保留 1 MiB RPC 上限 |
| 会话资产预算 | 256 MiB，所有输入、输出、在途数据计入；模型另测 |
| 完整结果数/执行去重数 | 最近 32 次 / 4096 次；去重满后新建会话，不删旧 ID 后重放 |
| 单次事件读取 | 最多 100 条；事件环最多 1000 条，日志正文沿用既有截断与脱敏 |
| 内联结构深度 | 最多 32 层，越界拒绝 |

错误区分：E_DEBUG_UNSUPPORTED、E_DEBUG_CONTEXT_INVALID、E_DEBUG_STALE_SESSION、E_DEBUG_SESSION_EXPIRED、E_DEBUG_REQUEST_CONFLICT、E_DEBUG_RESULT_EXPIRED、E_DEBUG_LIMIT、E_DEBUG_SIDE_EFFECT_DENIED、E_RESOURCE_BUSY、E_RESOURCE_CLEANUP_FAILED，以及既有 E_INPUT_* / E_PARAM_* / E_OUTPUT_* / E_CANCELLED。请求结果无法确认时记录 UNKNOWN，禁止伪报未执行或自动重跑。

暂不新增通用插件可执行代码沙箱，不新增自动网络重试；任何能力开放都必须落实到 Runtime 准入，而非仅禁用 Designer 按钮。

## 5 A0-A1 交付边界

A0 交付本契约、60 算子静态来源清单、固定源码基线及可复跑验证脚本。A1 交付公共节点执行模块、正式 Runner 接入和对照回归。二者都不表示已经有可点击的单步执行按钮。

A2 接续会话与进程监管；A3 补完整输入/结果；A4 接入 Designer；A5 完成全链路验收。设备、模型、UI 原生和打包验证分别保留状态，不把本批无界面的公共调用测试替代后续验收。

## 6 A2 阶段历史记录

以下保留 A2 当时的范围，不代表最终软件能力。A3-A5 已扩展为 47 个审查内置算子、16 个单算子 RPC、完整资产、隔离变量和原生 Designer 界面，并直接验证父进程死亡清理。最终 A 阶段结果见 [A3-A5 验收与审查](testing/operator-step-2026-10-09/A3-A5-review.md)。流程调试另有 13 个 WorkflowDebug RPC，见 [流程控制契约](workflow-debug-contract-v1.md)，不改变单算子入口的执行范围。

- 11 个 RPC 共用有类型的会话/请求身份字段；参数、草稿与结果使用严格有界 JSON。`generation` 同时承担重置请求的 expectedGeneration，重置重复请求先命中去重记录，再判断代次。
- 当前仅支持两个数值算子。准入比较 Runtime 注册描述符的原始 Python 类、入口、版本、端口、schema 与内置资源目录；相同 ID 的替代来源不能获得权限。不接受客户端提供可执行 entry。
- 草稿只解析并验证目标节点和其身份，不编译完整流程，不 LoadProject、不写工程或生产变量库。目标节点的变量/资源绑定和非空资源根返回 E_DEBUG_UNSUPPORTED，等待 A3 适配，不能静默忽略。
- 每会话一个 spawn Worker，使用独立临时工作目录和 JSON 字节管道。复用 CancellationToken、HeartbeatCell 和 OperatorExecutor；没有生产 Job 记录、正式数据库或页面订阅。进程退出、管道线程结束、句柄关闭、临时目录删除全部确认后才归还准入。
- Worker 的独立心跳线程同时检测父 Runtime 是否退出；父进程消失后协作取消，3 秒后仍未停止则退出 Worker。正常结束/重置/强制取消的工作目录由父所有者回收。父 Runtime 强杀路径尚未直接验收，可能遗留临时目录；目前未实现启动时孤儿目录清扫，这也不是第三方插件进程沙箱。
- 打开/执行立即返回接收快照，完成情况必须另行查询。取消、重置、关闭的重复请求返回原接收快照，不表示资源现在已释放；GetOperatorDebugSession 返回当前状态。
- 结果没有得到 Worker 确认便退出时记录 UNKNOWN；超时与取消不伪报“未执行”，也不自动重试。清理失败可以在进程退休后显示 FAULTED 与资源已释放，两者不是同一状态。
- 正式 Job、页运行、已有相机/PLC 会话与新调试保守互斥；不自动停止任何既有所有者。正式 StartJob 在请求去重之后、SQLite 目标冻结和页面采集准备之前拒绝调试占用。普通 Job 之间的并发上限和行为不改变。

### A2 容量细化

原 A0 的 768 KiB 总请求、60 秒租约/20 秒建议续租、30 秒执行上限、3 秒取消宽限保持不变。以下是未开放 A3 资产前的更严格界限：

| 项目 | A2 行为 |
| --- | --- |
| 内联值、参数及 Worker 单帧 | 64 KiB；拒绝重复键、非有限数和超过 32 层嵌套 |
| 输入集/执行结果 | 活动会话各保留最近 32 项；重置清空输入集，旧请求不重放 |
| 请求去重 | 每会话 4096 个普通修改请求，包含准备/执行/重置；另保留 2 个退出控制位置，避免满额后无法取消或关闭 |
| 开会话登记 | 每 Runtime 实例最多 128 个，含已关闭记录；满后拒绝新 ID，不删除旧 ID 后重放 |
| 已关闭会话 | 保留最后一次结果、最后 16 个事件及去重身份；更早结果返回 E_DEBUG_RESULT_EXPIRED |
| 事件 | 活动会话最多 1000 条，每次最多 100 条且事件数组最多 384 KiB；单条合法 Worker 日志可独立分页，游标不因大日志停滞；序号淘汰报告 gap，通信队列丢弃日志报告 logs.dropped |
| Worker 管道 | 父侧发送队列 1 条、接收队列 64 条；JSON 数据不通过网络 pickle 传输 |
| 图像资产、其他算子、绑定变量 | UNSUPPORTED，不把摘要或本地路径转换成输入 |

生命周期算子的参数改变前先清理旧实例；重置通过退休旧进程并启动新进程实现，只有新 Worker 就绪才提升 generation。客户端断连不使已接收命令重发，独立租约管理负责最终回收。
