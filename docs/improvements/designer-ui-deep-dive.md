# Designer UI 深入改动指南与下一步优化建议

> 这份文档服务于未来工程师，目标是回答两个问题：
>
> 1. 如果我要改 Designer 的 UI，应该改哪些文件、哪些函数、这些函数分别做什么？
> 2. 当前 Designer UI 还有哪些地方值得继续优化，下一步应该往什么方向推进？

这不是一份用户说明，而是一份**面向开发的函数级速查表 + 改进分析**。

---

## 1. 先建立一个 Designer UI 心智模型

如果你要改 Designer UI，先记住下面这个关系：

```text
main_window.py
  -> 负责组装界面、调度 controller/presenter、连接 Designer 状态与 Runtime 结果

controllers/project_controller.py
  -> 负责项目生命周期（打开/保存/最近项目/启动入口）

controllers/runtime_controller.py
  -> 负责运行主线（开始/停止运行/事件消费/运行态回写）

controllers/layout_controller.py
  -> 负责 splitter、响应式布局、侧栏折叠、菜单字号等布局状态

presenters/node_details_presenter.py
  -> 负责右侧“当前节点”详情的摘要与结构化模型生成

project_entry_dialog.py
  -> 负责启动首页 / 最近项目 / 新建空白 / 打开项目

designer_graphics_view.py
  -> 负责画布视图交互（缩放、空白拖移）

flow_scene.py
  -> 负责画布图元（节点、端口、边、运行态视觉）

param_form.py / node_param_dialog.py
  -> 负责参数编辑

app.qss
  -> 负责样式收口
```

一个很实用的经验法则：

```text
改 Designer UI = 先看 main_window.py 找入口
               -> 看 controller / presenter 谁负责该主线
               -> 再看具体组件文件
               -> 最后看 app.qss 收样式
```

---

## 2. 文件级职责与函数级入口

下面按未来最常改的 UI 区域来拆。

---

## 3. 启动首页 / 最近项目 / 新建空白

核心文件：

- `src/emo_master/apps/designer/ui/project_entry_dialog.py`
- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/styles/app.qss`

### 3.1 `ProjectEntryDialog` 里最关键的函数

#### `__init__(...)`
作用：
- 构建启动首页布局
- 创建 Hero 区、主操作区、最近项目区
- 绑定按钮与最近项目列表交互

什么时候改它：
- 你要改首页整体布局
- 你要增加新的首页分区
- 你要改首页主操作按钮结构

#### `setRecentProjects(projects)`
作用：
- 把最近项目数据渲染到首页列表
- 负责最近项目空态显示/隐藏

什么时候改它：
- 你要改最近项目显示内容
- 你要把最近项目从两行文本改成更丰富的信息块

#### `execSelection()`
作用：
- 启动首页阻塞等待用户选择
- 返回 `(action, path)` 给主窗口

什么时候改它：
- 一般不常改
- 只有当返回协议本身要扩展时才改

#### `_onOpenProject()`
作用：
- 打开原生文件选择器
- 返回 `project.json` 路径

什么时候改它：
- 想切换文件选择策略
- 想增加多种项目来源

#### `_onNewBlank()`
作用：
- 返回 `new_blank` 选择

什么时候改它：
- 新建空白逻辑变化时

#### `_onRecentProjectActivated(item)`
作用：
- 双击最近项目后直接返回该项目路径

什么时候改它：
- 想把单击打开、双击打开、回车打开等交互变掉时

#### `_onRemoveRecentProject()`
作用：
- 记录当前要从历史里移除的项目路径

#### `_onClearRecentProjects()`
作用：
- 标记“清空最近项目历史”

#### `getLayoutMode()` / `getRecentCardMode()` / `getActionSectionMode()` / `getVisualSectionNames()`
作用：
- 测试与文档约定接口
- 用来表达当前首页 UI 结构是否按预期工作

什么时候改它：
- 首页结构发生明确演进时

### 3.2 `MainWindow` 里和首页入口强相关的函数

#### `showStartupProjectEntry()`
作用：
- 主窗口启动时调起首页
- 把最近项目传给首页
- 读取首页返回的 action/path
- 根据 action 执行：打开项目 / 新建空白 / 退出

什么时候改它：
- 你要改启动入口整体流程
- 你要改首页与主窗口之间的数据协议

#### `getRecentProjects()`
作用：
- 从 `QSettings` 读取最近项目
- 过滤无效数据
- 返回结构化项目条目

#### `recordRecentProject(path)`
作用：
- 成功打开项目后写入最近项目历史
- 去重并保持最近使用在前

#### `removeRecentProject(path)` / `clearRecentProjects()`
作用：
- 删除一条或清空最近项目历史

什么时候改它们：
- 想增强最近项目行为（固定、分组、搜索、标星）时

### 3.3 这一块还能怎么优化

推荐方向：
- 最近项目搜索过滤
- 最近项目卡片真正组件化（现在仍然偏 QListWidget 文本条目）
- 无效项目显示灰态，而不是直接过滤
- “继续上次项目”快速入口

---

## 4. 主窗口整体布局 / 菜单栏 / 工具栏 / 三栏比例

核心文件：

- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/styles/app.qss`

### 4.1 关键函数

#### `__init__(...)`
作用：
- 这是当前 Designer UI 的总装配函数
- 负责创建：
  - 菜单栏
  - 工具栏
  - 左栏
  - 中间画布
  - 右栏
  - splitter
  - 各类对话框/服务入口

什么时候改它：
- 改整体布局结构时几乎必改

#### `_buildMainMenuBar()`
作用：
- 组装菜单栏

#### `_addToolbarGroup(...)`
作用：
- 组装工具栏分组标签 + 按钮

#### `applyResponsiveLayout()`
作用：
- 根据窗口宽度切换普通/大窗口布局参数
- 调整左右栏宽度、菜单字体、卡片高度等

什么时候改它：
- 大屏/小屏响应式表现不对时

#### `resizeEvent(...)`
作用：
- 窗口尺寸变化时触发响应式布局重算

#### `getMainSplitterSizes()` / `saveMainSplitterSizes(...)` / `restoreMainSplitterSizes()` / `onMainSplitterMoved(...)`
作用：
- 主界面左右面板拖拽宽度持久化

#### `applySidebarState()`
作用：
- 控制左栏折叠/展开视觉状态

什么时候改它：
- 左栏宽度、折叠逻辑、splitter 交互不对时

### 4.2 这一块还能怎么优化

推荐方向：
- 拆分 `main_window.py`：现在职责过多
- 把菜单栏/工具栏/右栏/项目控制拆成独立协调器
- `applyResponsiveLayout()` 进一步规则化，减少硬编码尺寸

---

## 5. 左侧栏：分类、当前节点、导航

核心文件：

- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/styles/app.qss`

### 5.1 关键函数

#### `setActiveCategory(categoryName)`
作用：
- 维护当前分类选中态

#### `toggleCategoryDrawer(categoryName)`
作用：
- 点分类按钮时切换气泡算子面板

#### `_refreshBubbleOperators()`
作用：
- 让 bubble 面板按当前分类刷新算子列表

#### `refreshSidebarNodeList()`
作用：
- 刷新左栏“当前节点”列表

#### `selectNodeFromSidebar(item)`
作用：
- 单击左栏节点项时，选中画布节点

#### `navigateToSidebarItem(item)` / `navigateToNodeFromSidebar(nodeId)`
作用：
- 双击左栏节点项时，让画布居中到目标节点

### 5.2 这一块还能怎么优化

推荐方向：
- 当前节点列表支持搜索
- 节点项显示运行态小角标
- 节点项按类型/状态分组

---

## 6. 中间画布：视图交互 vs 场景图元

这个部分最容易混：

- `designer_graphics_view.py` 负责“怎么看画布”
- `flow_scene.py` 负责“画布里有什么图元”

### 6.1 `designer_graphics_view.py`

#### `zoomByDelta(delta)`
作用：
- 滚轮缩放核心逻辑

#### `beginPanAt(x, y)`
作用：
- 左键按空白开始拖移画布

#### `updatePanTo(x, y)` / `endPan()`
作用：
- 更新/结束拖移状态

#### `getScrollBarVisibility()`
作用：
- 当前用于测试，确认画布滚动条已隐藏

什么时候改这里：
- 你要改缩放、拖移、惯性、滚轮行为、拖手模式时

### 6.2 `flow_scene.py`

#### `addFlowNode(...)`
作用：
- 把节点模型变成画布图元

#### `renderEdge(...)`
作用：
- 把边渲染到画布

#### `startConnectionDrag(...)`
作用：
- 从输出端口开始拖线

#### `_updateDragTargetState(...)`
作用：
- 处理拖线过程中的吸附、合法性判断、提示文案

#### `getNodeVisualStyle(nodeId)` / `setNodeRuntimeState(nodeId, state)`
作用：
- 查询/设置节点当前视觉状态
- 已用于 `If` 特殊样式和运行态变色

#### `getContentBounds()`
作用：
- 用于主窗口自动聚焦内容

什么时候改这里：
- 改节点样式、边样式、端口样式、连线交互、运行态着色时

### 6.3 这一块还能怎么优化

推荐方向：
- 连线级分支命中高亮
- 节点内部状态角标
- 更细的画布性能优化（节点多时）

---

## 7. 右侧面板：运行摘要 / 当前节点 / 结果预览

核心文件：

- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/ui/runtime_panel.py`

### 7.1 关键函数

#### `getCurrentNodeSummary()`
作用：
- 生成右侧“当前节点”详情文本

#### `setCurrentNodeRuntimeState(nodeId, status, branch)`
作用：
- 更新当前节点运行结果缓存
- 同时驱动画布节点运行态变色

#### `applyRuntimeEventToNode(event)`
作用：
- 把 Runtime 返回的结构化事件应用到 Designer 当前节点状态

#### `_refreshRuntimePanelView()`
作用：
- 统一刷新右侧摘要/详情/预览区

### 7.2 `runtime_panel.py` 的作用

`RuntimePanelState` 负责把事件流转换成轻量 UI 状态：

- `jobStatus`
- `lastMessage`
- `latestImagePath`
- `nodeStatus`

虽然“当前节点详情”的主体逻辑已经移到 `main_window.py`，但这个状态对象仍然承担了作业级状态汇总角色。

### 7.3 这一块还能怎么优化

推荐方向：
- 右侧增加最近事件时间线
- 当前节点详情改成分区表单，不只是文本块
- 把运行摘要与节点详情拆成更独立的 presenter

---

## 8. 节点参数编辑

核心文件：

- `src/emo_master/apps/designer/ui/param_form.py`
- `src/emo_master/apps/designer/ui/node_param_dialog.py`

### 8.1 关键函数

#### `SchemaParamForm._createControl(...)`
作用：
- 根据 schema 动态创建输入控件
- 现在已经支持文件选择型字段：`xWidget=file`

#### `SchemaParamForm.getValues()`
作用：
- 从表单控件取回参数值

#### `NodeParamDialog`（整体）
作用：
- 承载参数表单
- 对外返回节点参数修改结果

### 8.2 这一块还能怎么优化

推荐方向：
- 针对 `If`、`Image Loader`、`Image Saver` 做更专用的编辑控件
- 对必填项做更强的即时校验与视觉提示

---

## 9. 气泡算子面板

核心文件：

- `src/emo_master/apps/designer/ui/operator_bubble.py`
- `src/emo_master/apps/designer/ui/icon_map.py`

### 9.1 关键函数

#### `setOperators(...)`
作用：
- 设置当前分类下的可见算子

#### `setRecentOperatorIds(...)`
作用：
- 最近使用算子优先显示

#### `setSearchKeyword(...)`
作用：
- 搜索过滤

#### `_filterAndSortOperators()`
作用：
- 决定 bubble 面板最终展示顺序

### 9.2 这一块还能怎么优化

推荐方向：
- If / 控制流节点更专门的分组与图标体系
- 多条件搜索和收藏算子

---

## 10. Designer 与 Runtime 的联动入口

虽然这份文档聚焦 UI，但很多 UI 改动最终都需要跨到 Runtime。

最常见的联动点：

- `src/emo_master/apps/designer/services/runtime_client.py`
  - Designer 对 Runtime 的访问边界
  - 如果 UI 要显示更多运行态、结构化事件，通常先从这里开始加解析

- `src/emo_master/apps/runtime/grpc_server/service.py`
  - Runtime 对外暴露什么信息，由这里决定

- `src/emo_master/apps/runtime/execution/dag_executor.py`
  - 如果 UI 需要更丰富的分支、跳过、状态信息，最终一般要回到这里补执行结果结构

- `src/emo_master/core/plugin/registry.py`
  - 如果 UI 需要更多算子元信息（分组、图标、标签），插件注册层是入口之一

---

## 11. 当前最值得继续优化的地方

这里是我顺路分析出来的、最值得未来工程师优先推进的方向。

### P1：拆分 `main_window.py`

当前它承担了太多职责：

- 启动入口处理
- 最近项目
- 菜单栏/工具栏
- splitter 布局
- 项目保存加载
- 运行触发
- 事件回写
- 当前节点详情刷新

建议拆分为：

- ProjectActionsController
- RuntimeUiController
- LayoutController
- NodeDetailsPresenter

### P2：提升运行调试体验

当前已经有实时节点详情和节点颜色，但还缺：

- 最近事件时间线
- 连线级分支高亮
- `node.started` 等更细粒度事件

这是 `If` 和未来控制流能力最自然的下一步。

### P3：为控制流扩展做架构准备

目前 `If` 通过：

- 分流输出
- 下游无输入 -> `SKIPPED`

来实现。

这对 `If` 足够，但如果未来做 `While`，需要重新审视：

- DAG 执行器是否继续扩展
- 还是演化成更接近 workflow engine 的模型

### P4：启动首页继续产品化

当前首页已经有：

- Hero
- 主操作区
- 最近项目区

下一步可以继续做：

- 最近项目搜索
- 无效项目灰态显示
- 更强的卡片组件化
- “继续上次项目”快捷入口

### P5：参数编辑体验增强

如果要继续面向更复杂的流程控制和视觉项目，参数面板未来要支持：

- 更强的 schema 校验
- 更专用的控制类型
- 条件节点专门编辑器

---

## 12. 一句话建议

如果未来工程师只记住一句话，请记住：

**Designer UI 的入口几乎都从 `main_window.py` 开始，但真正的职责应逐步往具体组件文件和 presenter/controller 拆分。**

所以短期改 UI：先看 `main_window.py`。

长期做对架构：尽量把新逻辑从 `main_window.py` 往外迁。
