# ROI 专用预览完整输出：只读源码定位

固定版本：abc7ae0541e997be0bbe3a4794ad604c239de79a。2026-10-11 本轮只读源码与已有证据；未修改 source、未运行测试或 GUI、未 push。本文件为诊断建议，不是实施记录。

## 结论与证据边界

【确认】主分类是 A：专用界面未暴露完整查看/保存入口，外加缺少结果身份适配层；不是 ROI 算子不能产出完整图像，也不是 Runtime 没有完整 PNG 下载能力。专用控制器只展示 3 个输出，算法实际返回 4 个图像输出（另有 maskedImage）与 croppedFrame、maskedFrame、roi 结构化输出。因此“三张缩略图”不能当作全部结果。 [plugins/builtins/roi/operator.py:229-239](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/roi/operator.py#L229-L239)

【既有实测】evidence/ui/fresh-preview-final-summary.json 记录本地 ROI 专用原生 GUI 预览成功，源图及 4 个输出缓存 PNG 分别与各自预期结果逐像素一致；保留的输出对应临时 X=32 阶段，不是截图163最初 X=16 阶段；evidence/ui/roi-preview-export-boundary.json 记录界面无完整查看/导出入口。这是缓存资产验证，不是用户导出工作流。evidence/ui/export-pixel-verification.json 与 evidence/ui/clahe-history-verification/verification.json 分别记录通用 threshold/CLAHE 的完整导出核验；evidence/targeted/CODEX_HANDOFF.md 另列通用框架既有测试；没有 ROI 专用窗→通用 debug→ROI 执行→导出的整段 GUI E2E。本轮没有补测。

## 1. 创建、布局替换与 UI 断点

【确认】OperatorWorkspaceWindow 在 controller.bind 之前先执行 prepareBuiltinLayout，因此控制器获得的三个 QLabel 实际已被替换为 PreviewLabel；仅改原 editor.ui 的 QLabel 行为可能被替换步骤覆盖。 [apps/designer/operator_editors/workspace_window.py:93-98](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/workspace_window.py#L93-L98)

【确认】_replaceLabel 创建新 PreviewLabel、保留 objectName，替换布局并 deleteLater 旧控件；prepareBuiltinLayout 对 RoiEditor 的 croppedImageLabel/croppedMaskLabel/fullMaskLabel 执行此替换。 [apps/designer/operator_editors/builtin_layout.py:17-50](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/builtin_layout.py#L17-L50)

【确认】ROI 布局之后按 source/canvas/parameters/outputs 四个 item 解构，再将结果行以 stretch=1 放在画布下；新增独立顶层 item 会破坏该约定，宜在现有 outputs 容器内接入或同步修改重排逻辑。 [apps/designer/operator_editors/builtin_layout.py:82-107](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/builtin_layout.py#L82-L107)

【确认】RoiEditorController.bind 连接参数变更并只登记三个 outputLabels；handlePreviewResult 丢弃 outputs，在每个端口下载 bytes 后调用 setImageLabel，未保留导出 selection、资产集合、原始 PNG 或请求参数记录。 [plugins/builtins/roi/editor.py:223-303](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/roi/editor.py#L223-L303)

【确认】setImageLabel 将 bytes 解码为 QImage/QPixmap；遇到 PreviewLabel 就交给该类保留源 pixmap。 [plugins/builtins/_editor_support.py:25-44](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/_editor_support.py#L25-L44)

【确认】PreviewLabel._sourcePixmap 留存原始 pixmap，_rescale 只按控件大小展示；类中没有 contextMenuEvent、mouseDoubleClickEvent、保存按钮或 1:1 viewer。保存 label.pixmap() 将得到展示缩放图；即使使用 _sourcePixmap 重编码，也不是服务器原始 PNG 字节与身份导出。 [apps/designer/ui/widgets.py:75-107](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/ui/widgets.py#L75-L107)

## 2. 专用 pure-preview 调用链与返回对象

【确认】bindPreviewBase 接来源控件、250ms debounce 和 50ms poll。schedulePreview 标脏、取消旧请求、增加 generation；_startPreview 在 GUI 线程 collectParams、生成 requestId，先 preparePurePreview 再提交线程池。回调只入队 (generation, reply, error)。 [plugins/builtins/_editor_support.py:197-204](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/_editor_support.py#L197-L204)

【确认】具体 dispatch/completed/poll 在此：只采纳当前 generation，检查 reply.ok，再 validatePurePreviewResult，解析 outputs_json；把 reply.assets 降维为 port→asset_id 后调用 handlePreviewResult。mime/width/height 没有继续交给控制器，requestId/params 也不在结果记录中。 [plugins/builtins/_editor_support.py:268-348](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/_editor_support.py#L268-L348)

【确认】EditorContext.preparePurePreview 在 GUI 线程冻结 deepcopy(params) 和草稿 payload，检查输入归属；_runDraftPurePreview 调 pinned client，携带 project/workflow/node/operator/imageAsset/request/draftSession。回包仅在原 session 仍匹配时替换输出资产集合，晚到包不能进入替代 session。 [apps/designer/operator_editors/controller_protocol.py:282-316](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/controller_protocol.py#L282-L316)

【确认】RunOperatorPreview 校验 session、节点/草稿、input projectKey，解析并解析绑定参数；草稿使用 initialOnly=True 的变量快照，不借用正式 Job。随后 previewExecutor.execute，成功时 retain(outputs=True) 淘汰上一批输出。 [apps/runtime/grpc_server/service.py:1040-1105](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/grpc_server/service.py#L1040-L1105)

【确认】PurePreviewExecutor 调 operator.executeNode 后将 image ndarray 交 addTransientImage，JSON-compatible 输出放 structured，返回 PreviewExecutionResult(ok, code, message, outputs, assets)，assets 元素是 PreviewOutput(port, PreviewAsset)。 [apps/runtime/preview/executor.py:261-284](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/preview/executor.py#L261-L284)

【确认】RPC RunOperatorPreviewReply 只有 ok/code/message/outputs_json，以及每个资产的 port/asset_id/mime_type/width/height；没有 operator-debug 的 executionId、rawParams/resolvedParams、完整 provenance/hash。请求 ID 并未原样作为回包身份字段返回。 [apps/runtime/grpc_server/service.py:1106-1121](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/grpc_server/service.py#L1106-L1121)

【确认】addTransientImage 对原 ndarray 直接 cv2.imencode(".png")，未按控件尺寸缩放；生成 transient UUID，记录原 width/height、projectKey、port 和时间。 [apps/runtime/preview/store.py:317-354](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/preview/store.py#L317-L354)

【确认】EditorContext.downloadPreviewAsset 先核对当前草稿与 assets 成员身份，再以 pinned transport+draftSession 下载。Runtime StreamPreviewAsset 以 session.projectKey 读取原文件 bytes，并逐块重新验证 lease。 [apps/designer/operator_editors/controller_protocol.py:189-202](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/controller_protocol.py#L189-L202)

下载服务的具体位置： [apps/runtime/grpc_server/service.py:1007-1037](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/grpc_server/service.py#L1007-L1037)

## 3. 不能破坏的生命周期

【确认】Runtime 连接身份由原 service 对象与拷贝 client 固定；_checkPureRuntime 拒绝换 Runtime，_requireCurrentPureDraft 拒绝项目/节点变更；closePurePreview 清客户端 session/asset 集合，随后向原 transport 关闭，不借新连接释放旧 session。 [apps/designer/operator_editors/controller_protocol.py:246-280](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/controller_protocol.py#L246-L280)

close、结果交付前校验位置： [apps/designer/operator_editors/controller_protocol.py:318-339](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/operator_editors/controller_protocol.py#L318-L339)

【确认】服务端 session key 是 draft-preview-<server sessionId>，即使工程完全同字节也隔离。300 秒 lease，最多 8 session、32 asset、128 MiB；require 校验 project、workflow/node/operator、草稿 fingerprint。retain(outputs=True) 删除旧输出；close 删除当前 session 全部 assets；物理过期回收是后续请求/close 时机会式执行，失效 lease 不可继续使用。 [apps/runtime/preview/pure_draft.py:24-124](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/preview/pure_draft.py#L24-L124)

【确认】参数变化增加 generation；换源先清旧视觉；Run/project invalidation 停定时器、取消 future/RPC、close 会话并清图。参数 debounce 时旧图可暂留，所以新导出动作必须另外标记旧结果不可作为“当前参数”导出，不能仅凭像素还在就启用。 [plugins/builtins/_editor_support.py:106-141](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/_editor_support.py#L106-L141)

换源与重新调度位置： [plugins/builtins/_editor_support.py:233-266](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/_editor_support.py#L233-L266)

【确认】窗口 _dispose→controller.onClose/dispose→disposePreviewBase。disposePreviewBase 先 disposed/generation invalidation，停 timer、取消请求；独立 retirement thread 等所有 producer futures 的 executor.shutdown(wait=True)，然后 closePurePreview，不能只等最新 future，也不能把 release 放到同一个双线程池以免超车旧 producer。 [plugins/builtins/_editor_support.py:350-394](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/plugins/builtins/_editor_support.py#L350-L394)

【确认】Runtime timeout/cancel 会设置 cancellation，future 完成后丢弃结果资产；创建输出中异常也删除已分配资产。 [apps/runtime/preview/executor.py:123-147](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/preview/executor.py#L123-L147)

【确认】removeTransient 先取消索引、加入待清理集合后重试删除；新分配在 pending cleanup 未完成时拒绝继续，不能用“已从列表消失”证明物理文件已删。 [apps/runtime/preview/store.py:470-484](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/preview/store.py#L470-L484)

## 4. 最小增量接入建议（未实施、未验证）

1. 在 ROI 现有 outputs 容器增加每端口“查看原尺寸 / 保存 PNG”或单一端口选择工具条；在 RoiEditorController.bind 绑定，避免直接依赖旧 QLabel，也避免随意增加顶层 item。查看器使用完整 bytes/完整 QImage 和滚动/1:1 比例；明确源画布的缩放不是输出完整查看器。
2. 在 _startPreview 的 UI capture 点冻结不可变 PreviewResultIdentity，并随 generation/requestId 传到 _pollResults；在成功且 current 校验后一次性形成结果集合，再由 ROI handlePreviewResult 保存 selection。至少保留 project/workflow/node/operator/version、固定 Runtime transport/session 身份、草稿 fingerprint、generation/requestId、输入 asset/source、提交时 raw params/variable bindings、port/mime/width/height、资产 ID。不要按点击保存时的 collectParams 标注过去的结果。
3. 用 EditorContext 的受限下载桥接获取同一资产原始 PNG；不要读缓存绝对路径、截屏、保存缩放 pixmap、伪造 debug execution ID 或省略 draftSession/projectKey。可复用通用服务的原子写入/取消模式，但需要 pure-preview 专用 materialize adapter 与明确 resultKind。
4. 最小 PNG 保存可只保证原始字节；如果宣称“带完整调用身份”，需要明确当前 pure RPC 缺 resolvedParams/变量快照等服务端证据：不能拿 raw params 冒充 resolved params。要么包中标注缺失及 raw-only，要么另行设计服务端 metadata 扩展。这比补按钮多一层，不是没有图像底层能力。
5. 点击时冻结 selection；下载前及写入提交前复核 generation/session/runtime/fingerprint/取消状态。换源、参数变更、新请求、会话失效/关闭立即禁用旧导出。若要允许历史导出，应显式设计有界不可变历史 bytes 与身份，而非延长旧服务器资产寿命绕过 lease。
6. 新增异步 download/save 必须加入 retirement/取消跟踪；不可在关闭 QWidget 后回调 Qt，不能让清理早于生产者或保存工作。失败/过期/取消保留已有目标并移除临时文件。
7. 后续测试应覆盖三个可见端口及 maskedImage/结构化输出范围、像素/原字节一致、参数变更中的保存、晚到回包、runtime reconnect、同 projectId 不同 session、租约过期、关闭中导出、磁盘失败与临时文件清理。以上都只是建议，当前无新增 PASS。

## 5. 通用 ROI 替代路线：源码可达与成本

【确认】参数窗口的 bindDebug 对所有 operator 节点接入 _openOperatorDebug，不排除 customUi/ROI；valid() 核对原 editor、节点、项目、workflow 与 workspace，再创建 OperatorDebugWindow。 [apps/designer/ui/main_window.py:4322-4355](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/ui/main_window.py#L4322-L4355)

【确认】OperatorDebugWindow 从 editor.collectParams 取未应用参数，构建新 draft 并 connection.open。DebugConnection.open 先 GetOperatorDebugCapabilities，再 OpenOperatorDebugSession，固定 runtime_instance_id/session_id/generation。 [apps/designer/services/operator_debug.py:46-65](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/services/operator_debug.py#L46-L65)

【确认】roi 在 SUPPORTED_FOLDERS，trustedEntries 还要求原装 class/version/manifest/resourceRoot；因此不是只凭 operator ID 名称放行。 [apps/runtime/operator_debug/contracts.py:104-128](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/operator_debug/contracts.py#L104-L128)

白名单位置： [apps/runtime/operator_debug/contracts.py:19-29](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/operator_debug/contracts.py#L19-L29)

【确认】_open 拒绝 Runtime closing、ledger quota/其他 owner；service resourceBusy 还检查活跃 Job、相机/PLC 及 pure preview 正在执行的 cancellation 表。需等资源释放，不能绕过占用检查。 [apps/runtime/operator_debug/manager.py:318-353](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/operator_debug/manager.py#L318-L353)

busy 检查： [apps/runtime/grpc_server/service.py:1614-1621](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/grpc_server/service.py#L1614-L1621)

【确认】输入行由 capability.inputPorts 新建，没有转接 ROI currentAssetId。selectFile 重新选择 PNG/JSON 并上传；也可选择受支持的历史完整 source/debug output，但不是自动复用专用预览资产。 [apps/designer/ui/operator_debug_window.py:193-277](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/ui/operator_debug_window.py#L193-L277)

【确认】execute 重新 collectParams+inputValues，DebugConnection.execute→PrepareOperatorDebugInputs→ExecuteOperatorDebugNode；产生新的 executionId，并非刚才专用纯预览同一次结果。 [apps/designer/services/operator_debug.py:94-135](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/services/operator_debug.py#L94-L135)

【确认】showResult 从 records 建立端口列表；exportSelection 必须有 executionId/status，冻结 rawParams/resolvedParams/sourceIdentity 和 current/history，给 DebugArtifactTools 处理。 [apps/designer/ui/operator_debug_window.py:498-544](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/ui/operator_debug_window.py#L498-L544)

【确认】DebugArtifactTools.exportSelection/exportRawSelection→exportResult/exportRawResult→materializeReference→connection.download→ReadOperatorDebugAsset。下载校验各块 metadata、总 bytes、SHA256；完整包是单端口 raw 文件+metadata.json，PNG 原始保存不带身份。 [apps/designer/ui/debug_artifact_tools.py:163-189](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/ui/debug_artifact_tools.py#L163-L189)

校验下载： [apps/designer/services/operator_debug.py:137-154](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/services/operator_debug.py#L137-L154)

原子落盘与格式： [apps/designer/services/debug_artifacts.py:167-196](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/services/debug_artifacts.py#L167-L196)

【推断】原装注册与正常 Runtime 条件下，ROI 可沿通用框架完成完整输出导出；此前其他算子与服务测试支持该机制，不能替代 ROI 整段 GUI 实测。

额外操作成本：打开独立调试窗并等 READY → 重新选完整 PNG（如需 frame，则须提供与该图片坐标身份匹配的 frame；本地图片无 frame 可用算子默认行为）→ 重新执行 → 结果页选端口 → 逐端口保存/导出 → 结束会话并等资源释放。还需核对两次输入/未应用参数/变量来源相同，不能声称导出的是先前 pure-preview 的原资产。通用图像控件也按窗口缩放，不构成内置 1:1 viewer；需导出后用图像工具查看原尺寸。 [apps/designer/ui/operator_debug_window.py:565-575](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/designer/ui/operator_debug_window.py#L565-L575)

## 最终分层

- 已确认：UI 入口缺失；完整图像生成/受限下载已有；回调未保留完整可导出身份；producer/session/租约清理已有明确边界；通用 ROI 准入/导出路径静态可达。
- 推断：通过小范围 UI+结果记录/下载适配可补齐原 PNG 保存，通常不需改 ROI 图像算法；完整审计包若要求 resolvedParams 等，需要更多元数据设计。
- 未测：本轮所有建议；ROI 通用替代整段 GUI E2E；新 viewer/export 在竞态/断连/过期下的正确性。
- 未实施：任何 source 修改、运行、UI 操作或 push。
