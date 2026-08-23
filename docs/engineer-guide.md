# 工程师导读：EmoMaster 架构、调用流程与开发落点

> 面向未来工程师的总览文档。目标不是替代源码，而是帮助你在第一次接触项目时快速建立系统心智，知道主线调用从哪里开始、数据在哪一层是真相、改功能应该优先看哪些文件。

## 1. 项目概览

EmoMaster 目前是一个桌面式视觉流程设计与执行系统，核心由两侧组成：

- `Designer`：负责项目入口、最近项目、画布编辑、节点参数编辑、运行触发、运行结果可视化。
- `Runtime`：负责插件扫描、项目图执行、事件流输出、作业状态管理。

整体运行模式是：

1. Designer 打开或新建项目；
2. 用户在画布中编辑流程图；
3. Designer 将当前图保存为 `project.json`；
4. Runtime 读取项目图并执行；
5. 事件流与结果回到 Designer，更新日志、节点详情、节点颜色等 UI。

如果只记住一句话：

**Designer 是编辑与显示层，Runtime 是执行层，`project.json` 是两者共享的磁盘契约。**

---

## 2. 模块地图

### 2.1 `src/emo_master/apps/designer`

这是桌面端设计器。

- `main.py`
  - Designer 进程启动入口。
  - 负责创建 `QApplication`、加载 QSS、连接 Runtime、本地或远端 stub 切换、创建 `MainWindow`。
- `ui/main_window.py`
  - 当前项目最核心的 UI 装配与桥接器。
  - 经过重构后，主要负责装配界面、连接 controller/presenter，并保留少量桥接逻辑。
- `controllers/project_controller.py`
  - 项目生命周期控制器。
  - 负责最近项目、启动入口结果处理、项目保存/加载、新建空白等主线。
- `controllers/runtime_controller.py`
  - 运行控制器。
  - 负责开始运行、停止运行、Runtime 事件消费、节点运行态回写。
- `controllers/layout_controller.py`
  - 布局控制器。
  - 负责 splitter、响应式布局、侧栏折叠、菜单栏字号自适应等布局状态。
- `presenters/node_details_presenter.py`
  - 当前节点详情 presenter。
  - 负责右侧“当前节点”摘要模型与文本生成。
- `ui/project_entry_dialog.py`
  - 启动首页入口。
  - 负责“打开项目 / 新建空白 / 最近项目 / 清空历史”等交互。
- `ui/flow_scene.py`
  - 画布场景层。
  - 负责节点、端口、边、选中态、If 节点特殊样式、节点运行态颜色等图元逻辑。
- `ui/designer_graphics_view.py`
  - 画布视图层。
  - 负责滚轮缩放、空白拖移、隐藏滚动条等视图交互。
- `ui/runtime_panel.py`
  - Runtime 事件到 UI 状态的轻量映射。
- `services/runtime_client.py`
  - Designer 访问 Runtime 的统一封装层。
  - 负责调用 gRPC / 本地 service，并把事件 `payload_json` 解析成结构化 `payload`。
- `state/flow_graph_model.py`
  - Designer 侧流程图内存真相。
  - 节点、边、参数、选中节点都以它为准。
- `state/project_store.py`
  - 负责 `project.json` 与项目目录骨架的创建、保存、读取。

### 2.2 `src/emo_master/apps/runtime`

这是执行端。

- `main.py`
  - Runtime gRPC 服务入口。
- `grpc_server/service.py`
  - Runtime 的主服务实现。
  - 提供 `LoadProject / StartJob / StopJob / StreamJobEvents / ListOperators` 等能力。
- `execution/dag_executor.py`
  - 执行器核心。
  - 负责 DAG 拓扑排序、节点执行、输出路由、`SKIPPED` 语义和 `If` 分支命中记录。
- `events/event_bus.py`
  - 运行事件总线。
  - 当前用于收集作业执行事件并供 `StreamJobEvents` 输出。
- `scheduler/*`
  - 作业状态管理和状态机。

### 2.3 `src/emo_master/core/plugin`

这是插件系统核心。

- `registry.py`
  - 扫描 `manifest.json`
  - 校验版本和字段
  - 动态导入 operator class
- `validator.py`
  - manifest 结构校验、类一致性校验
- `models.py`
  - 插件注册相关数据结构

### 2.4 `src/emo_master/plugins/builtins`

这是内置算子集合。

当前关键内置算子有：

- `image_loader`
  - 从磁盘读取图片
- `image_saver`
  - 将图片写回磁盘
- `flow_if`
  - 条件分支控制节点
- `canny_edge`
  - 边缘检测示例

### 2.5 `proto/runtime.proto`

Designer 与 Runtime 的协议定义。现在主要包含：

- 项目加载
- 作业开始/停止/状态
- 事件流
- 算子列表

---

## 3. 五条最重要的调用主线

下面这五条主线，是未来工程师阅读项目时最应该先掌握的部分。

### 3.1 主线 A：启动 Designer

入口文件：`src/emo_master/apps/designer/main.py`

调用顺序：

1. 创建 `QApplication`
2. 读取并应用 `ui/styles/app.qss`
3. 根据环境变量 `EMO_RUNTIME_TARGET` 决定：
   - 使用本地 `RuntimeService()`
   - 或创建 gRPC stub
4. 创建 `RuntimeClient`
5. 构造 `MainWindow(runtimeClient, showStartupEntry=True)`
6. 先显示启动入口 `showStartupProjectEntry()`
7. 若用户取消，则直接退出；否则显示主窗口

开发提示：
- 改 Designer 启动行为，先看 `main.py`
- 改启动首页入口，继续看 `project_entry_dialog.py` 和 `main_window.py`

### 3.2 主线 B：打开/新建项目

主要入口：

- 启动首页 `ProjectEntryDialog`
- 主窗口 `loadProject()` / `saveProjectAction()`

打开项目调用链：

1. 用户选中 `project.json`
2. `MainWindow.loadProjectSelection(...)`
3. `MainWindow.loadProjectDirectory(...)`
4. `project_store.loadProject(...)` 读取磁盘文件
5. `FlowGraphModel.loadProjectGraph(...)` 恢复内存模型
6. `FlowScene.clearGraph()` 后重建节点与边
7. 记录最近项目
8. 同步 Runtime 的 `LoadProject`

新建空白调用链：

1. 用户在启动首页选择“新建空白”
2. 选择目标目录
3. `createProjectSkeleton(...)` 创建：
   - `project.json`
   - `assets/`
   - `outputs/`
4. 再走一次项目加载链路，把空项目加载到 Designer 和 Runtime

开发提示：
- 改项目目录结构，先看 `project_store.py`
- 改加载后如何恢复画布，先看 `flow_graph_model.py` 和 `main_window.py`

### 3.3 主线 C：画布编辑

核心文件：

- `ui/main_window.py`
- `ui/flow_scene.py`
- `state/flow_graph_model.py`

调用链：

1. 左侧分类按钮 -> 气泡面板 `OperatorBubble`
2. 用户拖算子到画布
3. `MainWindow.addNodeFromOperatorPayload(...)`
4. `FlowGraphModel.addNode(...)` 更新内存图
5. `FlowScene.addFlowNode(...)` 创建画布图元
6. 用户拖线时，由 `FlowScene` 处理端口命中与预览
7. 成功连线后，`FlowGraphModel.connectNodes(...)`
8. 双击节点打开 `NodeParamDialog`
9. 参数提交后写回 `FlowGraphModel.setNodeParams(...)`
10. 左侧“当前节点”和右侧“当前节点详情”同步刷新

开发提示：
- 改节点数据结构 -> `flow_graph_model.py`
- 改节点视觉/边视觉/端口交互 -> `flow_scene.py`
- 改工具栏/侧栏/右栏联动 -> `main_window.py`

### 3.4 主线 D：点击开始运行

核心入口：`MainWindow.startJob()`，实际主逻辑已由 `RuntimeController.startJob()` 接管

调用链：

1. `MainWindow.startJob()` 转调 `RuntimeController.startJob()`
2. 若当前项目目录存在，先 `_syncRuntimeProjectBeforeRun()`
   - 保存 Designer 当前图到 `project.json`
   - 再调用 Runtime `LoadProject`
3. 调用 `RuntimeClient.startJob(...)`
4. Runtime 进入 `RuntimeService.StartJob(...)`
5. Runtime 执行 `_runLoadedGraph(...)`
6. `executeGraph(...)` 对项目图做拓扑排序并逐节点执行
7. Runtime 把作业级和节点级事件写入 `RuntimeEventBus`
8. Designer 用 `streamJobEvents(...)` 拉回事件流
9. Designer 逐条消费事件，更新：
   - 日志
   - `RuntimePanelState`
   - 当前节点详情
   - 画布节点运行态颜色

开发提示：
- 改作业启动/同步逻辑 -> `controllers/runtime_controller.py`
- 改 Runtime 服务行为 -> `grpc_server/service.py`
- 改节点执行语义 -> `dag_executor.py`

### 3.5 主线 E：插件发现与执行

核心入口：`PluginRegistry.scan(...)`

调用链：

1. Runtime 启动时 `_scanBuiltins()`
2. `PluginRegistry.scan(pluginRoot)` 扫描 `**/manifest.json`
3. 校验 manifest 字段
4. 校验核心版本兼容
5. 通过 entry 动态导入 operator class
6. 校验 manifest 与 operator meta 一致性
7. 成功的进入 `activeOperators`
8. 失败的进入 `rejectedOperators`
9. Designer 通过 `ListOperators` 获取可用算子列表
10. Runtime 执行时根据 `operatorId` 找到对应 operator 并调用 `executeNode(...)`

开发提示：
- 新增算子时，最重要的是 manifest 与 operator 两边保持一致

---

## 4. 三份关键数据真相

未来工程师最容易混淆的，就是“到底哪一份数据才是真相”。当前项目里至少有三份。

### 4.1 磁盘真相：`project.json`

文件位置：项目目录下的 `project.json`

职责：
- 项目保存/加载的磁盘契约
- Designer 与 Runtime 共享的持久化格式

它包含：
- `meta`
- `runtime`
- `designer.nodes`
- `designer.edges`

### 4.2 Designer 真相：`FlowGraphModel`

文件：`src/emo_master/apps/designer/state/flow_graph_model.py`

职责：
- 当前画布编辑态的内存真相
- 节点、边、参数、选中节点都以它为准

典型规则：
- 新增节点先改 `FlowGraphModel`
- 画布只是把 `FlowGraphModel` 可视化出来

### 4.3 Runtime 真相：`RuntimeGraph` / `executeGraph` 输入

文件：
- `src/emo_master/apps/runtime/execution/models.py`
- `src/emo_master/apps/runtime/execution/dag_executor.py`

职责：
- 运行时执行语义的真相
- 只关心拓扑顺序、输入输出路由、节点状态和产物

### 4.4 三者关系

关系总结：

- 编辑时：`FlowGraphModel` 是真相
- 保存时：`FlowGraphModel -> project.json`
- 执行时：`project.json -> RuntimeGraph -> executeGraph`

不要直接把 `FlowScene` 当真相。它只是显示层。

---

## 5. 当前关键能力

截至当前版本，系统已具备的核心能力包括：

- 项目目录结构（`project.json / assets / outputs`）
- 启动首页入口
- 最近项目历史
- 主窗口菜单栏 + 工具栏并存
- 画布滚轮缩放 / 空白拖移 / 隐藏滚动条
- 左右面板可拖宽度并持久化
- 左栏当前节点导航
- 右栏当前节点详情
- `image_loader / image_saver` 内置算子
- `flow_if` 条件分支节点
- 运行过程中节点详情实时更新
- 运行过程中画布节点实时变色

---

## 6. 当前限制与边界

这些限制最好在开发前就知道：

- 仅支持 `If`，还不支持 `While`
- `If` 的实现方式是“输出分流 + 下游未命中节点 `SKIPPED`”，而不是真正的执行图裁剪
- 当前 `If` 只支持三种模式：
  - `bool`
  - `equals`
  - `not_equals`
- 当前 Designer 主逻辑仍然比较集中在 `main_window.py`
- 启动首页已经可用，但仍然是 Dialog 形态，不是独立 launcher 进程

---

## 7. 常见改动入口索引

如果你要改某类功能，优先看这里：

- **改启动流程 / 启动入口**
  - `src/emo_master/apps/designer/main.py`
  - `src/emo_master/apps/designer/ui/project_entry_dialog.py`

### 7.1 如果我要改 Designer 的 UI，先看哪里

这是未来工程师最常问的问题。下面按 UI 类型给速查表：

- **启动首页 / 最近项目 / 入口按钮**
  - `src/emo_master/apps/designer/ui/project_entry_dialog.py`
  - `src/emo_master/apps/designer/ui/styles/app.qss`

- **主窗口整体布局（三栏比例、splitter、菜单栏、工具栏）**
  - `src/emo_master/apps/designer/ui/main_window.py`
  - `src/emo_master/apps/designer/ui/styles/app.qss`

- **左侧栏（分类、当前节点列表、折叠逻辑）**
  - `src/emo_master/apps/designer/ui/main_window.py`
  - `src/emo_master/apps/designer/ui/styles/app.qss`

- **中间画布交互（缩放、拖移、滚动条、视图行为）**
  - `src/emo_master/apps/designer/ui/designer_graphics_view.py`

- **中间画布节点与连线视觉（节点样式、边样式、端口、运行态颜色）**
  - `src/emo_master/apps/designer/ui/flow_scene.py`

- **右侧面板（运行摘要、当前节点详情、结果预览）**
  - `src/emo_master/apps/designer/ui/main_window.py`
  - `src/emo_master/apps/designer/ui/runtime_panel.py`

- **节点参数表单 / 文件浏览按钮 / 参数弹窗**
  - `src/emo_master/apps/designer/ui/param_form.py`
  - `src/emo_master/apps/designer/ui/node_param_dialog.py`

- **顶部气泡算子面板 / 搜索 / 最近使用排序**
  - `src/emo_master/apps/designer/ui/operator_bubble.py`
  - `src/emo_master/apps/designer/ui/icon_map.py`

### 7.2 一个经验法则

如果你不确定 UI 改动该先看哪里，可以用下面这条规则：

- **先看 `main_window.py` 判断入口是谁在调度**
- **再看具体 UI 组件文件（`project_entry_dialog.py` / `flow_scene.py` / `designer_graphics_view.py` 等）**
- **最后看 `app.qss` 收样式**

也就是说，Designer 的 UI 绝大多数改动都是：

```text
main_window.py -> 具体 UI 组件文件 -> app.qss
```

- **改最近项目 / 首页入口体验**
  - `src/emo_master/apps/designer/ui/project_entry_dialog.py`
  - `src/emo_master/apps/designer/ui/main_window.py`

- **改画布缩放 / 拖移 / 视图交互**
  - `src/emo_master/apps/designer/ui/designer_graphics_view.py`

- **改节点视觉 / 边视觉 / 端口交互 / 运行态变色**
  - `src/emo_master/apps/designer/ui/flow_scene.py`

- **改当前节点详情 / 右栏联动 / 菜单栏 / 工具栏**
  - `src/emo_master/apps/designer/ui/main_window.py`

- **改项目保存 / 加载 / 项目目录结构**
  - `src/emo_master/apps/designer/state/project_store.py`
  - `src/emo_master/apps/designer/state/flow_graph_model.py`

- **改 Runtime gRPC 行为**
  - `src/emo_master/apps/runtime/grpc_server/service.py`

- **改 DAG 执行规则 / `SKIPPED` / 分支命中**
  - `src/emo_master/apps/runtime/execution/dag_executor.py`

- **改 Runtime -> Designer 事件流**
  - `src/emo_master/apps/runtime/events/event_bus.py`
  - `src/emo_master/apps/runtime/grpc_server/service.py`
  - `src/emo_master/apps/designer/services/runtime_client.py`

- **新增一个内置算子**
  - `src/emo_master/plugins/builtins/<name>/manifest.json`
  - `src/emo_master/plugins/builtins/<name>/operator.py`
  - `src/emo_master/core/plugin/registry.py`

---

## 8. 推荐阅读顺序

如果你是第一次读代码，建议按这个顺序：

1. `src/emo_master/apps/designer/main.py`
2. `src/emo_master/apps/designer/ui/main_window.py`
3. `src/emo_master/apps/designer/state/flow_graph_model.py`
4. `src/emo_master/apps/designer/ui/flow_scene.py`
5. `src/emo_master/apps/runtime/grpc_server/service.py`
6. `src/emo_master/apps/runtime/execution/dag_executor.py`
7. `src/emo_master/core/plugin/registry.py`
8. `src/emo_master/plugins/builtins/image_loader/operator.py`
9. `src/emo_master/plugins/builtins/flow_if/operator.py`

这个顺序的原则是：
- 先看 UI 总控
- 再看画布模型
- 再看 Runtime 执行主线
- 最后看插件样例

---

## 9. 附录：新增一个算子的最短路径

如果你想新增一个内置算子，最短路径是：

1. 在 `src/emo_master/plugins/builtins/` 下新建目录
2. 写 `manifest.json`
3. 写 `operator.py`
4. 确保 operator 提供与 manifest 一致的 `meta`
5. 启动 Runtime 后插件注册会自动扫描到它
6. Designer 调 `ListOperators` 后会自动出现在左侧分类/气泡面板中

最好的参考文件：
- `src/emo_master/plugins/builtins/image_loader/operator.py`
- `src/emo_master/plugins/builtins/image_saver/operator.py`
- `src/emo_master/plugins/builtins/flow_if/operator.py`

---

## 10. 未来扩展建议

如果你准备继续向前推进，这几个方向最值得优先考虑：

- 把 `If` 扩展为更强条件表达式
- 在画布边上直接做分支命中高亮
- 引入真正的循环控制（`While`），但这会挑战当前 DAG 执行模型
- 把 Runtime 事件进一步结构化，用于更完整的调试面板
- 拆分 `main_window.py`，降低单文件复杂度

---

## 11. 最后的建议

阅读这个项目时，不要从 UI 细节或单个算子开始。最稳的方式是：

1. 先看 Designer 启动和主窗口
2. 再看项目保存/加载主线
3. 再看 Runtime 服务与执行器
4. 最后再看单个插件

只要你先掌握“Designer -> project.json -> Runtime -> event stream -> Designer”的闭环，项目的大部分代码都会自然变得容易理解。
