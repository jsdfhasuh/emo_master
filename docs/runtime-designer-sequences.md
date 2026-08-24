# Designer / Runtime v0.2 时序

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

## Subflow 和 Loop

WorkflowController 切换 tab 时先保存当前图，再装载目标 workflow 的图和布局。
Subflow 节点从目标接口同步动态端口；删除工作流前会检查所有
`targetWorkflowId`、`bodyWorkflowId` 和 `conditionWorkflowId` 引用。Loop runner
按确定顺序发布 iteration 事件，嵌套调用通过 `iterationPath` 和
`parentWorkflowRunId` 关联。

## 事件回写

RuntimeClient 把 protobuf `payload_json` 转为普通 DTO。Designer 以
`workflowRunId:nodeId` 更新节点状态，并从 `artifact.created` 的结构化
ArtifactRef 读取预览路径；旧的完成消息文本解析只作为 fallback。
