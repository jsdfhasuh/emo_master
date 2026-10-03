# Designer / Runtime v0.5 时序

## 打开和保存项目

```text
ProjectEntryDialog
  -> ProjectController
  -> project_store.loadProject
  -> WorkflowController.loadPayload
  -> WorkflowStore (all workflows)
  -> active workflow -> FlowGraphModel -> FlowScene
```

保存时先由 `WorkflowController.captureActiveWorkflow` 把画布写回当前工作流，
再由 `WorkflowStore.toPayload` 生成 v2；`project_store.saveProject` 负责严格
校验、备份和原子替换。

## 点击开始运行

```text
MainWindow.startJob
  -> RuntimeController
  -> save current project / LoadProject
  -> RuntimeWorker(QThread)
  -> RuntimeClient.StartJob
  -> RuntimeService creates JobRecord
  -> JobSupervisor.spawn(worker_main.runJobProcess)
  -> EventBridge -> EventStore/SQLite
  -> RuntimeWorker.eventReceived
  -> RuntimePanelState + FlowScene
```

StartJob 不等待 DAG 完成。RuntimeWorker 只在后台消费 `follow=true`，因此长作业
不会阻塞 Designer 主线程。

## 打开算子独立编辑页面

```text
FlowScene node double-click
  -> MainWindow.openNodeParamDialog
  -> OperatorEditorManager[(projectId, workflowId, nodeId)]
  -> RuntimeClient.GetOperatorEditorAsset(operatorId, version)
  -> SHA-256 cache / Controller trust
  -> QUiLoader(bytes) + Controller.bind(EditorContext)
  -> OperatorWorkspaceWindow (non-modal)
```

没有 `editor` 声明、旧 Runtime 不支持资源 RPC、UI/Controller 失败时，窗口使用
`SchemaParamForm`。节点删除、项目切换和应用退出都通过 Manager 强制释放 Controller、
后台线程和预览会话。

pure preview 由页面先选择本地临时图片、当前节点最近结果或直接上游快照，再调用
`RunOperatorPreview`；Runtime 复用正式算子和端口校验，副作用算子不能进入此路径。
每次纯预览携带唯一 `requestId`；参数再次变化或窗口释放时，Designer 调用
`CancelOperatorPreview`，Runtime 将取消信号传入正式算子的取消检查，并清理迟到的临时输出。
live preview 使用独立 session，只保存最新 JPEG 帧。正式 Job 启动与打开 live session
由同一互斥区协调：Job 最多等待三秒释放项目预览，失败则拒绝启动，避免相机被并发占用。

项目加载、Job 启动及预览打开由项目状态锁串行化，不能混用不同项目的 document、revision
或预览缓存键。上传图片、纯预览输出和作业快照都绑定规范化项目键；下载时再次校验当前项目，
已知其他项目的 assetId 也不能读取其内容。

成功 Job 的可预览输出先写 `preview_staging`，只有整个 Job COMPLETED 才原子提升到
Runtime 全局缓存；FAILED/ABORTED 不覆盖旧结果。图片以全分辨率 PNG 保存，项目与全局
缓存分别受 512 MiB / 2 GiB LRU 限制。

## Subflow 和 Loop

WorkflowController 切换 tab 时先保存当前图，再装载目标 workflow 的图和布局。
Subflow 节点从目标接口同步动态端口；删除工作流前会检查所有
`targetWorkflowId`、`bodyWorkflowId` 和 `conditionWorkflowId` 引用。Loop runner
按确定顺序发布 iteration 事件，嵌套调用通过 `iterationPath` 和
`parentWorkflowRunId` 关联。

`Repeat(0)` 是确定性的 no-op：不调用 body workflow，也不发布 iteration
事件；Loop 输出仅透传同名输入。若输出端口无法由输入透传，编译器以
`E_LOOP_ZERO_OUTPUT_UNSATISFIABLE` 拒绝项目，避免运行末尾才出现缺失输出。

Loop v2 端口统一由核心契约解析器派生：Repeat 原样继承 body；ForEach 将 body
每个输出分别聚合为有序列表；While 将 body 的同名输入/输出作为类型化状态。
修改被引用工作流接口后，Designer 会同步所有 Subflow/Loop，并清理端口缺失或
类型不兼容的连线；Compiler 会再次派生并拒绝陈旧端口快照。

## 事件回写

RuntimeClient 把 protobuf `payload_json` 转为普通 DTO。Designer 以
`workflowRunId:nodeId` 更新节点状态，并从 `artifact.created` 的结构化
ArtifactRef 读取预览路径；旧的完成消息文本解析只作为 fallback。
