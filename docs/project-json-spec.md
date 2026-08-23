# project.json 规范说明与未来演进建议

> 这份文档解释 `project.json` 当前在 EmoMaster 中扮演的角色、字段结构、谁来写它、谁来读它、哪些字段是当前有效契约，以及未来可能怎样扩展。它是 `Designer` 和 `Runtime` 之间最重要的共享磁盘契约之一。

## 1. 这份文档解决什么问题

如果你是未来工程师，这份文档主要帮你回答：

- `project.json` 到底是谁生成的？
- Designer 保存项目时写了哪些字段？
- Runtime 加载项目时真正用了哪些字段？
- 哪些字段已经稳定，哪些字段只是预留？
- 如果以后要扩展项目格式，优先往哪里加？

一句话概括：

**`project.json` 是 Designer 的持久化输出，也是 Runtime 的执行输入。**

---

## 2. 文件定位与项目目录结构

当前项目目录结构由 `project_store.py` 维护，最小结构如下：

```text
<projectDir>/
  ├── project.json
  ├── assets/
  └── outputs/
```

关键代码：

- `src/emo_master/apps/designer/state/project_store.py`

其中：

- `project.json`
  - 项目本体描述
- `assets/`
  - 输入资源（图片等）
- `outputs/`
  - 输出产物

---

## 3. 顶层字段结构

当前 `project.json` 顶层字段固定为：

```json
{
  "version": "1.0",
  "meta": { ... },
  "runtime": { ... },
  "designer": { ... }
}
```

### 3.1 `version`

作用：
- 标识项目文件格式版本

当前值：
- 固定写为 `"1.0"`

谁写：
- `project_store.createProjectSkeleton()`
- `MainWindow._buildProjectPayload()`

谁读：
- 目前主要用于人类理解和未来扩展
- Runtime 现在没有根据版本分支处理逻辑

### 3.2 `meta`

作用：
- 存放项目元信息

当前字段：
- `name`
- `createdAt`
- `updatedAt`

谁写：
- `project_store.createProjectSkeleton()` 会初始化三者
- `project_store.saveProject()` 会补 `createdAt` 并刷新 `updatedAt`
- `MainWindow._buildProjectPayload()` 当前只主动设置 `name`

谁读：
- Designer 当前主要用 `name` 作为项目显示名称来源之一
- Runtime 目前基本不消费 `meta`

### 3.3 `runtime`

作用：
- 为 Runtime 留出的项目级执行上下文字段

当前字段：
- `sourceImagePath`

谁写：
- `project_store.createProjectSkeleton()` 默认写空字符串
- `MainWindow._buildProjectPayload()` 会把 `loadedProjectPath` 写进去

谁读：
- 当前 Runtime 实际上**已经不依赖它作为执行输入主来源**
- 当前推荐输入路径来源是节点级 `Image Loader` 参数

结论：
- `runtime.sourceImagePath` 目前更接近“遗留兼容字段 / 预留字段”，不是当前推荐主路径

### 3.4 `designer`

作用：
- 存放流程图编辑态的核心结构

当前子字段：
- `nodes`
- `edges`

谁写：
- `MainWindow._buildProjectPayload()`

谁读：
- `MainWindow._restoreProjectPayload()`
- `RuntimeService.LoadProject()`

这是当前 `project.json` 中最重要的部分。

---

## 4. `designer.nodes` 的结构

每个节点当前最少可能包含：

```json
{
  "nodeId": "node-1234abcd",
  "operatorId": "vision.io.image_loader",
  "displayName": "Image Loader",
  "x": 20.0,
  "y": 20.0,
  "inputPorts": {},
  "outputPorts": {
    "image": "image"
  },
  "paramSchema": { ... },
  "params": { ... }
}
```

### 字段说明

#### `nodeId`
- Designer 内唯一节点 ID
- 边连接、右侧详情、运行态更新都依赖它

#### `operatorId`
- 节点引用的算子标识
- Runtime 执行时通过它找到 operator class

#### `displayName`
- UI 展示名称

#### `x`, `y`
- 节点在画布中的坐标
- 由 `MainWindow._buildProjectPayload()` 基于 `FlowScene.getNodePositions()` 写入

#### `inputPorts`, `outputPorts`
- 端口结构描述
- Designer 用于画布显示
- Runtime 用于执行图解析

#### `paramSchema`
- 参数 schema
- Designer 用它生成参数表单

#### `params`
- 节点当前参数值
- Runtime 执行时把它传给 `executeNode(...)`

### 字段来源

主要来自：

- `FlowGraphModel.toProjectGraph()`
- `MainWindow._buildProjectPayload()`（补 `x/y`）

---

## 5. `designer.edges` 的结构

当前每条边结构如下：

```json
{
  "fromNode": "node-a",
  "fromPort": "image",
  "toNode": "node-b",
  "toPort": "value"
}
```

### 字段含义

- `fromNode`
- `fromPort`
- `toNode`
- `toPort`

这四个字段共同描述一条从输出端口到输入端口的连线。

### 谁写
- `FlowGraphModel.toProjectGraph()`

### 谁读
- `FlowGraphModel.loadProjectGraph()`
- `RuntimeService.LoadProject()` -> `parseRuntimeGraph(...)`

---

## 6. 谁写 `project.json`

### 6.1 初次创建项目骨架

文件：`src/emo_master/apps/designer/state/project_store.py`

函数：
- `createProjectSkeleton(projectDir, projectName)`

负责：
- 创建目录
- 创建空项目结构
- 写入最小可用 `project.json`

### 6.2 正常保存项目

文件：`src/emo_master/apps/designer/ui/main_window.py`

函数链：

```text
saveProjectAction()
  -> saveProjectToDirectory(...)
    -> _buildProjectPayload(...)
    -> project_store.saveProject(...)
```

这里真正决定 `project.json` 内容的是：

- `FlowGraphModel.toProjectGraph()`
- `MainWindow._buildProjectPayload()`

---

## 7. 谁读 `project.json`

### 7.1 Designer 读项目

文件：`src/emo_master/apps/designer/ui/main_window.py`

函数链：

```text
loadProjectDirectory(...)
  -> project_store.loadProject(...)
  -> _restoreProjectPayload(...)
    -> FlowGraphModel.loadProjectGraph(...)
    -> FlowScene.clearGraph()
    -> FlowScene.addFlowNode(...)
    -> FlowScene.renderEdge(...)
```

Designer 关注的是：
- 节点是否能恢复
- 坐标是否能恢复
- 参数是否能恢复

### 7.2 Runtime 读项目

文件：`src/emo_master/apps/runtime/grpc_server/service.py`

函数：
- `LoadProject(...)`

Runtime 读的重点是：
- `designer.nodes`
- `designer.edges`

然后交给：
- `parseRuntimeGraph(...)`

最终进入执行器：
- `executeGraph(...)`

Runtime 当前对 `meta` 和大部分 UI 字段不敏感，只关注执行所需字段。

---

## 8. 当前 `project.json` 的真实作用边界

这部分非常重要，因为未来工程师很容易误解它的职责。

### 8.1 它是共享契约，但不是所有字段都会被两边同等使用

- Designer 更关心：
  - `displayName`
  - `x/y`
  - `paramSchema`
  - `params`
- Runtime 更关心：
  - `operatorId`
  - `params`
  - `edges`
  - 端口结构

### 8.2 它不是纯“运行时配置文件”

因为它还存了大量编辑态信息（比如坐标、显示名）。

### 8.3 它也不是纯“UI 保存文件”

因为 Runtime 是直接拿它执行的。

结论：

**`project.json` 是一个 Designer / Runtime 混合契约。**

这也是未来演进时最需要谨慎处理的点。

---

## 9. 一个最小示例

下面是一个极简项目文件（省略部分 schema 细节）：

```json
{
  "version": "1.0",
  "meta": {
    "name": "demo",
    "createdAt": "2026-03-20T00:00:00Z",
    "updatedAt": "2026-03-20T00:00:00Z"
  },
  "runtime": {
    "sourceImagePath": ""
  },
  "designer": {
    "nodes": [
      {
        "nodeId": "loader",
        "operatorId": "vision.io.image_loader",
        "displayName": "Image Loader",
        "x": 20.0,
        "y": 20.0,
        "inputPorts": {},
        "outputPorts": {"image": "image"},
        "paramSchema": {},
        "params": {"imagePath": "C:/demo/input.png"}
      },
      {
        "nodeId": "if1",
        "operatorId": "vision.flow.if",
        "displayName": "If",
        "x": 280.0,
        "y": 20.0,
        "inputPorts": {"value": "object"},
        "outputPorts": {"true": "object", "false": "object"},
        "paramSchema": {},
        "params": {"mode": "bool", "compareValue": ""}
      }
    ],
    "edges": [
      {
        "fromNode": "loader",
        "fromPort": "image",
        "toNode": "if1",
        "toPort": "value"
      }
    ]
  }
}
```

---

## 10. 常见问题与排查方向

### 问题 1：保存后重新打开，节点位置不对

先查：
- `MainWindow._buildProjectPayload()` 是否写了 `x/y`
- `MainWindow._restoreProjectPayload()` 是否正确恢复了 `x/y`

### 问题 2：Designer 能打开项目，但 Runtime 执行失败

先查：
- `operatorId` 是否有效
- `params` 是否满足插件要求
- `edges` 是否构成合法执行图

### 问题 3：参数面板显示正常，但运行结果不对

先查：
- `paramSchema` 是否只是 UI 正确
- `params` 是否确实写进了 `project.json`

### 问题 4：控制流节点（如 If）行为不对

先查：
- `designer.nodes[*].params`
- `dag_executor.py` 中对 `branchHits` / `SKIPPED` 的处理

---

## 11. 当前格式的局限性

### 11.1 Designer 和 Runtime 耦合在同一个 JSON 模型里

这意味着：
- UI 字段和执行字段混在一起
- 演进时很容易两边互相影响

### 11.2 `runtime.sourceImagePath` 角色已经弱化

当前主推荐输入来源是：
- `Image Loader` 节点自身的 `imagePath`

所以这个字段未来要么继续保留做兼容，要么逐步弱化。

### 11.3 缺少显式 schema version migration 机制

虽然有 `version: "1.0"`，但当前没有 migration 层。

---

## 12. 下一步该怎么改（顺路分析）

### P1：把 `project.json` 分成更清晰的“编辑态”和“执行态”

未来可以考虑：

- `designer`
- `runtime`

更彻底分层，甚至由 Runtime 只消费一个更干净的执行子结构。

### P2：为 `project.json` 建立显式 migration 机制

如果后面持续加字段、加控制流、加更多项目级元数据，这一步会变得非常重要。

### P3：把 `runtime.sourceImagePath` 明确降级为兼容字段或删除

否则未来工程师会误以为它仍然是主输入来源。

### P4：补项目文件 JSON schema 或文档化验证器

现在主要靠 Python 代码隐式定义格式，未来最好明确化。

### P5：为复杂控制流单独设计执行态字段

如果未来做 `While`、子流程、调试快照等，当前格式很快会变得吃力。

---

## 13. 给未来工程师的建议

如果你要改 `project.json`：

1. 先查它是 Designer 字段、Runtime 字段，还是两者共享字段
2. 同时检查：
   - `project_store.py`
   - `main_window.py`
   - `flow_graph_model.py`
   - `service.py`
3. 不要只改一边

最重要的一点：

**`project.json` 是跨边界契约，不是单边配置文件。**

改它时一定要同时想清楚：
- Designer 会不会坏
- Runtime 会不会坏
- 旧项目还能不能打开
