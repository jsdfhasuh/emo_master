# project.json v2.1 规范

`project.json v2.1` 是 Designer 与 Runtime 的唯一项目源。磁盘模型由
`ProjectDocument` 严格校验；Designer 读取 v1/v2.0 时只在内存中迁移，保存 v2.1
前先备份旧文件并通过临时文件原子替换。

## 目录

```text
<projectDir>/
  project.json
  project.json.bak       # 保存已有项目时生成
  assets/
  outputs/
```

## 顶层结构

```json
{
  "schemaVersion": "2.1",
  "project": {
    "projectId": "project-id",
    "name": "demo",
    "revision": 1,
    "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-01-01T00:00:00Z"
  },
  "entryWorkflowId": "main",
  "workflowOrder": ["main", "body"],
  "workflows": { "main": { "name": "Main", "inputs": {}, "outputs": {}, "nodes": [], "edges": [], "layout": { "nodePositions": {} } } },
  "runtime": {
    "maxConcurrentJobs": 2,
    "gracefulStopTimeoutMs": 5000,
    "heartbeatTimeoutMs": 5000,
    "eventRetentionPerJob": 10000
  },
  "dependencies": { "operators": [] },
  "devices": { "bindings": {} }
}
```

`workflowOrder` 是稳定的 Designer tab 顺序；`entryWorkflowId` 是未指定入口
时 Runtime 使用的工作流。每个工作流包含 `inputs`、`outputs`、`nodes`、
`edges` 和仅用于编辑布局的 `layout.nodePositions`。

## 节点

```json
{
  "nodeId": "node-1",
  "kind": "operator",
  "operatorId": "vision.io.image_loader",
  "displayName": "Image Loader",
  "inputPorts": {},
  "outputPorts": { "image": "image" },
  "paramSchema": {},
  "params": {}
}
```

`kind` 可为 `operator`、`workflow_input`、`workflow_output`、`subflow` 或
`loop`。Subflow 节点使用 `targetWorkflowId`；Designer 会从目标工作流接口
动态生成输入和输出端口。Loop 节点使用 `loop` 配置，例如：

```json
{
  "kind": "loop",
  "loop": {
    "contractVersion": 2,
    "mode": "foreach",
    "bodyWorkflowId": "body",
    "itemInputPort": "image",
    "indexInputPort": "index",
    "maxIterations": 100,
    "timeoutMs": 30000
  }
}
```

`mode` 为 `repeat`、`foreach` 或 `while`。所有新 Loop 使用
`contractVersion=2` 并必须有
`maxIterations`；While 另外需要 `conditionWorkflowId`，body 需要
`bodyWorkflowId`。端口由 `deriveLoopContract` 从被引用工作流接口派生，节点中
保存的 `inputPorts/outputPorts` 只是画布快照，编译时不会作为接口真相。

- Repeat 原样继承 body 输入和输出，最终返回最后一次 body 输出。
- ForEach 使用 `items:list<T>` 加 body 的共享输入；`itemInputPort` 指定每项绑定到
  哪个 body 输入，body 每个输出分别聚合为 `list<T>`。
- While 要求 body 输入和输出同名且类型兼容；condition 输入必须来自该状态，且
  必须输出 `continue:boolean`。While 对外直接暴露 body 的类型化状态端口。

Repeat 的 `repeatCount=0` 是定义明确的无操作：它不运行
body，并将收到的输入端口原样透传到输出端口；因此 Repeat 的每个输出必须有
同名输入，且输入类型必须可安全赋给输出类型，否则项目在编译阶段被拒绝。
普通边必须保持 DAG，循环只能通过
结构化 Loop 调用子工作流。

## 旧版本迁移

旧文件的 `version/meta/designer` 字段会迁移为 `schemaVersion/project/workflows`。
旧节点的 `x/y` 会移动到 `layout.nodePositions`；迁移会补充工作流边界节点。
v2.0 项目迁移到 v2.1 时，已有 Loop 会标记为 `contractVersion=1`，继续使用旧的
`results:list` / `state:object` 协议；新建或重新应用配置的 Loop 使用 v2 契约。
迁移函数为 `emo_master.core.project.migration.migrateProjectPayload`。

Runtime、`ProjectRepository` 和 package builder 都只消费 v2 文档。包内
`plugins.lock` 从 `dependencies.operators` 派生，不再维护 YAML 双轨项目源。

跨项目复用单个工作流及其依赖时使用 `.emowf.json` 工作流包，格式和导入规则见
[`workflow-package-spec.md`](workflow-package-spec.md)。
