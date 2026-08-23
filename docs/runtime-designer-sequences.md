# Designer / Runtime 调用时序与未来演进建议

> 这份文档是 `docs/engineer-guide.md` 的补充材料，聚焦“按时间发生顺序”的调用链解释。适合需要理解跨端协作、事件流、状态回写、项目保存与执行闭环的新工程师。

## 1. 这份文档看什么

如果 `engineer-guide.md` 更像“地图”，那这份文档更像“导航录像回放”。

它重点回答：

- Designer 启动时先做了什么，后做了什么
- 一个项目从打开到显示到执行，经过了哪些层
- Runtime 的事件为什么能回写到 Designer
- 当前执行器为什么只支持 `If` 而不支持 `While`
- 未来应该先往哪些方向演进

---

## 2. 时序一：启动 Designer

### 简化时序

```text
User
  -> runDesigner()
  -> QApplication
  -> applyDesignerStyle(app)
  -> RuntimeClient(RuntimeService or gRPC Stub)
  -> MainWindow(..., showStartupEntry=True)
  -> showStartupProjectEntry()
  -> [用户选择成功]
  -> MainWindow.show()
```

### 关键代码

- `src/emo_master/apps/designer/main.py`
- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/project_entry_dialog.py`

### 解释

Designer 启动流程是一个“先决定入口、后显示主窗口”的过程：

1. `runDesigner()` 创建 `QApplication`
2. 加载 `app.qss`
3. 根据 `EMO_RUNTIME_TARGET` 决定使用：
   - 本地 `RuntimeService()`
   - 或远程 gRPC stub
4. 构造 `RuntimeClient`
5. 构造 `MainWindow`
6. 调 `showStartupProjectEntry()`
7. 如果用户取消，则直接退出；否则显示主窗口

### 设计意义

这样设计让启动入口成为真正的项目入口，而不是主窗口上方又套一层弹窗。

---

## 3. 时序二：打开项目 / 新建空白

### 简化时序

```text
ProjectEntryDialog / Menu / Toolbar
  -> MainWindow.loadProjectSelection(...)
  -> MainWindow.loadProjectDirectory(...)
  -> project_store.loadProject(...)
  -> FlowGraphModel.loadProjectGraph(...)
  -> FlowScene.clearGraph()
  -> FlowScene.addFlowNode(...)
  -> FlowScene.renderEdge(...)
  -> recordRecentProject(...)
  -> RuntimeClient.loadProject(...)
```

### 关键代码

- `src/emo_master/apps/designer/ui/project_entry_dialog.py`
- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/state/project_store.py`
- `src/emo_master/apps/designer/state/flow_graph_model.py`

### 解释

项目加载不是“把 JSON 显示出来”，而是完整的三层同步：

1. 从 `project.json` 读磁盘数据
2. 恢复 `FlowGraphModel` 内存状态
3. 用 `FlowScene` 重建画布图元

新建空白项目也是同一条主线，只是前面多了一步：

- `createProjectSkeleton()` 先创建项目目录结构

### 设计意义

这保证了：

- 磁盘格式与 Designer 内存模型可以解耦
- 画布层不承担持久化职责

---

## 4. 时序三：在画布里编辑流程

### 简化时序

```text
左侧分类 / 气泡面板
  -> addNodeFromOperatorPayload(...)
  -> FlowGraphModel.addNode(...)
  -> FlowScene.addFlowNode(...)
  -> 用户双击节点
  -> NodeParamDialog / ParamForm
  -> FlowGraphModel.setNodeParams(...)
  -> 右侧“当前节点”刷新
  -> 用户拖线
  -> FlowGraphModel.connectNodes(...)
  -> FlowScene.renderEdge(...)
```

### 关键代码

- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/flow_scene.py`
- `src/emo_master/apps/designer/state/flow_graph_model.py`
- `src/emo_master/apps/designer/ui/param_form.py`

### 解释

画布编辑遵循一个稳定原则：

**先改模型，再改场景显示。**

例如新增节点时：

- `FlowGraphModel.addNode(...)` 先生成节点数据
- `FlowScene.addFlowNode(...)` 再创建图元

这也是为什么 `FlowScene` 不是系统真相，它只是 UI 可视化层。

---

## 5. 时序四：点击开始运行

这是整个系统最关键的一条链路。

### 简化时序

```text
User click Start
  -> MainWindow.startJob()
  -> RuntimeController.startJob()
  -> _syncRuntimeProjectBeforeRun()
    -> saveProjectToDirectory(...)
    -> RuntimeClient.loadProject(...)
  -> RuntimeClient.startJob(...)
  -> RuntimeService.StartJob(...)
  -> _runLoadedGraph(...)
  -> executeGraph(...)
  -> RuntimeEventBus.publish(...)
  -> RuntimeClient.streamJobEvents(...)
  -> MainWindow.applyRuntimeEventToNode(...)
  -> 右侧详情刷新 / 节点颜色刷新 / 日志刷新
```

### 关键代码

- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/services/runtime_client.py`
- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/apps/runtime/execution/dag_executor.py`
- `src/emo_master/apps/runtime/events/event_bus.py`

### 解释

运行不是直接拿当前画布去执行，而是：

1. Designer 先把当前图同步到磁盘项目
2. Runtime 再重新 `LoadProject`
3. Runtime 按磁盘项目图执行

这个设计确保了 Runtime 的输入始终是稳定、可复现的 `project.json`，而不是 Designer 内存对象。

### 设计意义

这条链路是整个项目最重要的“跨端边界”之一：

- Designer 负责保存与发起
- Runtime 负责读取与执行
- 事件总线负责回传运行结果

---

## 6. 时序五：Runtime 事件如何实时回写 UI

### 简化时序

```text
executeGraph()
  -> 生成 nodeStatus / branchHits
  -> RuntimeService._appendJobEvent(..., node_id, payload_json)
  -> RuntimeEventBus.publish(...)
  -> RuntimeClient.streamJobEvents(...)
  -> 解析 payload_json 为 payload
  -> MainWindow.startJob() 逐条消费事件
  -> applyRuntimeEventToNode(...)
  -> setCurrentNodeRuntimeState(...)
  -> 当前节点详情刷新
  -> FlowScene.setNodeRuntimeState(...)
  -> 画布节点颜色刷新
```

### 关键代码

- `src/emo_master/apps/runtime/execution/dag_executor.py`
- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/apps/runtime/events/event_bus.py`
- `src/emo_master/apps/designer/services/runtime_client.py`
- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/flow_scene.py`

### 解释

当前系统已经不只是“作业完成后再统一更新”，而是支持运行期间的实时反馈。

关键点有三个：

1. Runtime 执行器返回结构化结果
   - `nodeStatus`
   - `branchHits`

2. Service 把节点级信息打包进事件流
   - `node_id`
   - `payload_json`

3. Designer 逐条消费事件并立即更新 UI
   - 右侧当前节点详情
   - 画布节点运行态颜色

### 当前限制

目前是**节点级实时**，但还没有做到：

- 连线级分支高亮
- 更细粒度的 node.started / progress
- 独立调试时间线视图

---

## 7. 时序六：插件发现与执行

### 简化时序

```text
Runtime startup
  -> PluginRegistry.scan(pluginRoot)
  -> load manifest.json
  -> validate manifest fields
  -> load operator class
  -> validate manifest/operator consistency
  -> activeOperators / rejectedOperators
  -> Designer ListOperators
  -> User adds operator to graph
  -> Runtime executeNode(...)
```

### 关键代码

- `src/emo_master/core/plugin/registry.py`
- `src/emo_master/core/plugin/validator.py`
- `src/emo_master/plugins/builtins/*/operator.py`

### 解释

新增算子的最短路径是：

1. 写 `manifest.json`
2. 写 `operator.py`
3. 保证 manifest 与 operator meta 一致
4. 让 Runtime 扫描到它
5. Designer 自动从 Runtime 读取算子列表

这意味着 Designer 本身不需要硬编码大部分算子，只需要消费 Runtime 提供的 operator metadata。

---

## 8. 当前架构的优点

这是当前项目最值得保留的部分：

### 8.1 Designer / Runtime 分层清晰
- Designer 负责编辑和展示
- Runtime 负责执行
- `project.json` 作为边界契约

### 8.2 插件化算子模型已经可扩展
- 内置算子和未来扩展算子走同一套机制

### 8.3 UI 主线已经完整
- 启动入口
- 最近项目
- 项目保存加载
- 画布编辑
- 运行触发
- 实时反馈

### 8.4 `If` 已作为控制节点迈出第一步
- 说明系统已经不仅是“纯线性算子链”

---

## 9. 当前架构的主要限制

这一节是给未来改造时最需要警惕的地方。

### 9.1 `main_window.py` 过大
- 它同时承担：
  - 菜单栏
  - 工具栏
  - 项目操作
  - 运行触发
  - 最近项目
  - 右栏刷新
  - 事件回写
- 长期来看应拆分成更小的协调器或 presenter。

### 9.2 执行器仍然是 DAG 思维
- `If` 是通过“分流 + SKIPPED”扩展进去的
- 还不是真正的工作流引擎
- 这意味着：
  - `While` 不应直接照搬当前架构粗暴接入

### 9.3 Runtime 事件仍偏轻量
- 已有节点级结果
- 但还缺：
  - `node.started`
  - 更细粒度 progress
  - 连线级分支高亮事件

### 9.4 启动首页已具备入口价值，但仍是 Dialog
- 这对当前阶段够用
- 但如果以后继续扩成“工作台首页”，可能更适合独立 launcher 页面

---

## 10. 下一步演进建议（按优先级）

这是你特别要求顺路分析的部分。我按价值 / 风险比做了优先级排序。

### P1：继续强化分支可视化与调试能力

建议内容：
- 连线级分支命中高亮
- `If` true/false 路径在画布里直接可见
- 增加最近事件摘要面板
- 增加 `node.started / node.skipped / node.completed / node.failed` 统一时间线视图

为什么优先：
- `If` 已经存在，下一步最自然的是让它更可调试、更可观察。

### P2：拆分 `MainWindow` 责任

建议内容：
- 拆分出：
  - ProjectController
  - RuntimeController
  - StartupEntryController
  - NodeDetailsPresenter

为什么优先：
- 当前功能已足够多，继续往 `main_window.py` 叠会降低可维护性。

### P3：为控制流做真正的架构准备

建议内容：
- 在开始实现 `While` 前，先重新审视执行模型
- 决定是否：
  - 继续沿用 DAG + 扩展语义
  - 或演进为更接近 workflow engine 的执行器

为什么优先：
- `While` 一旦做错，会带来全局复杂度暴涨。

### P4：启动首页继续产品化

建议内容：
- 最近项目搜索
- 最近项目内联操作更细致
- 近期模板项目
- “继续上次项目”

为什么优先：
- 当前首页已可用，但还没有完全产品化。

### P5：更细的项目级工程能力

建议内容：
- 自动保存
- 脏状态提示
- 项目资源面板（assets / outputs）
- 项目名编辑

为什么优先：
- 这会把当前“可用编辑器”推向“更像真正桌面工具”。

---

## 11. 我对未来工程师的建议

如果你准备继续开发这个项目，建议遵循这个顺序：

1. 先读 `docs/engineer-guide.md`
2. 再读这份时序文档
3. 再去看：
   - `main_window.py`
   - `service.py`
   - `dag_executor.py`
4. 再看一个具体插件（如 `image_loader` 或 `flow_if`）

不要一上来就直接改 UI 细节或者随便往执行器里塞功能。这个项目目前最重要的是：

**保持 Designer / Runtime / project.json 三者边界清晰。**

一旦这条边界被破坏，后续任何新能力（尤其控制流）都会迅速变得难以维护。
