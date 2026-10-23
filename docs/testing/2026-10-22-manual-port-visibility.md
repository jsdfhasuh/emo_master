# Designer 引脚手动隐藏与恢复

## 范围

源码基线：`d39169251c9bedc339c440a29a56df5eafba3be5` 加已有工作区修改。
本轮按用户确认的右键交互替换自动折叠，不调整 Runtime、工程 schema、算子端口或循环状态协议。
保留此前连线端点、节点结果、配置导航等未提交修改；未修改用户工程、未连接真实设备、未打包发布。

## 行为

- 默认显示全部引脚。右键引脚圆点或名称可选择“隐藏此引脚”，输入和输出分别记录。
- 已连接的引脚不可隐藏，菜单置灰并提示原因；右键输出引脚不再误启动连线拖拽。
- 节点菜单增加“显示引脚”子菜单、“显示全部引脚”、“隐藏未连接引脚”。子菜单按输入、输出区分同名接口。
- 存在隐藏引脚时，标题栏显示加号，提示隐藏数量，点击恢复全部。恢复后不会再自动收起。
- 新连线的端点始终可见；断开连线不触发自动隐藏。隐藏后压紧可见端口行并重新布线。
- 普通算子、子工作流、循环和工作流边界节点共享显示操作。只读图的已有恢复按钮仍可用。
- 本地 QSettings 按 `projectId / workflowId / nodeId` 保存隐藏的方向、名称；工作流切换、画布重建和工程重开后恢复。
  这是本机界面偏好，不写入工程文件、不产生工程撤销记录。保留相同 projectId 的工程副本共享此偏好。
- 任务运行中仍可调整显示，不需要停止任务。没有新增自动接口删除、持久化框架或后台任务。

实现涉及 `flow_scene.py`、`node_context_menu.py`、`main_window.py` 和 `read_only_graph.py`。

## 验证

环境：Conda `emo_master` Python 3.10、PySide2、Windows。

- 第一轮：48 passed、1 failed。失败为旧折叠测试仍要求删除连线后自动隐藏；更新为新手动显示约定，明确断言断线后的可见端口。
- 最终扩展源码回归：**226 passed**，72.82 秒。
- 原生 Windows Qt，100%：**56 passed**，10.65 秒。
- 原生 Windows Qt，150%：**56 passed**，18.61 秒。同组用例重复验证 DPI，不合计为不同用例数。
- Ruff：4 个相关源码文件及 4 个测试文件通过。
- mypy：4 个相关源码文件通过，采用仓库现有配置。
- 定点 `git diff --check` 通过。

扩展回归覆盖 manual_port_visibility、unused_port_folding、node_context_menu、wire_endpoint_rendering、
node_title_editing、control_flow_usability、while_boolean、workflow_navigation、configuration_visual_navigation、
flow_scene_interactions、flow_layout_qt、flow_edge_presentation、flow_edge_readability、edge_selection_highlight、
control_flow_config_safety 和 workflow_interface_dialog。

直接验证右键圆点和文字、同名输入输出独立恢复、已连接引脚、批量操作、运行中显示操作、
画布刷新和工作流切换、实际保存重开入口、工程内容及撤销记录不变、项目和工作流隔离。
连线像素检查同时覆盖引脚完整显示和手动隐藏后位置变化，含普通、选中连线及画布缩放 0.65 / 1 / 1.5。

截图使用临时模拟图，不是用户现场工程；已目视检查 100%、150% 的布局及菜单：

- [隐藏右侧输出，100%](manual-port-visibility-2026-10-22/native-100/loop-hidden-outputs.png)
- [恢复完整引脚，100%](manual-port-visibility-2026-10-22/native-100/loop-all-ports.png)
- [显示操作菜单，100%](manual-port-visibility-2026-10-22/native-100/node-port-menu.png)
- [隐藏右侧输出，150%](manual-port-visibility-2026-10-22/native-150/loop-hidden-outputs.png)

复跑原生测试：设置 `QT_QPA_PLATFORM=windows`、`QT_SCALE_FACTOR=1` 或 `1.5`，执行：

```powershell
python -m pytest -q tests/designer/test_manual_port_visibility.py tests/designer/test_unused_port_folding.py tests/designer/test_node_context_menu.py tests/designer/test_wire_endpoint_rendering.py
```

设置 `EMO_MANUAL_PORT_SCREEN_DIR` 可保存模拟循环图和显示菜单的原生截图。
没有运行全仓库测试、独立人工现场验收、冻结包或真实设备验收。
正在运行的 Designer 不会热加载本次源码；源码启动需重新启动，冻结 EXE 需另行构建。

## 审查问题修正

同日按用户确认修正以下两处，源码基线仍为 `d39169251c9bedc339c440a29a56df5eafba3be5` 加工作区修改。
本轮源码只涉及 `flow_scene.py` 和 `node_context_menu.py`，保留以上初轮验证记录。

1. **恢复引脚覆盖相邻节点。** 隐藏引脚后，流式布局、网格布局及默认新增位置原先都只使用当前可见高度。
   现在保留节点按完整接口测得的不可变 `expandedGeometry`，自动排布和默认新增位置按完整尺寸预留空间；
   当前绘制高度仍可随隐藏变小，布线仍使用当前可见端口坐标。
   单纯隐藏、显示不会移动任何节点或改写工程位置。已有人工紧密摆放的图不会被后台自动重排；
   需要整理时由用户显式执行自动布局。这不是通用碰撞推挤机制。
2. **重复节点菜单累积子菜单对象。** `QMenu.clear()` 只移除子菜单动作，不释放子菜单对象。
   重建前对直属旧子菜单调用 `deleteLater()`，沿用 Qt 事件循环回收，不引入新的菜单管理器。

新增 7 项定点回归，修复前 **7 failed**、修复后 **7 passed**：

- 普通算子和循环节点分别覆盖流式布局、网格布局、默认新增位置；恢复全部引脚后所有节点互不重叠。
- 连续两次隐藏、恢复，节点位置、模型和连接关系不变，路由起终点仍为引脚中心。
- 实际 Designer 菜单在两个节点间连续重建 6 次；处理延迟删除后只保留 1 个当前子菜单，旧菜单对象失效，
  当前恢复动作仍然可用，不修改工程或启动任务。

最终扩展回归：**233 passed**，73.41 秒，范围为上文相同的 16 个测试文件。
原生 Windows Qt 100%：**67 passed**，11.70 秒；150%：**67 passed**，20.41 秒。
原生组包含引脚操作、节点菜单、端点像素和 `test_flow_layout_qt.py` 的布局撤销/重做、保存重开等验证。
补充完整取景断言后，7 项新增测试在两个 DPI 下再次各通过 7 项。
Ruff 两个源码及两个测试文件通过；mypy 两个源码文件通过；定点差异格式检查通过。

以下均为临时模拟图的原生截图，已目视核对节点间距和连线；没有使用现场工程：

- [恢复引脚后不重叠，100%](manual-port-visibility-2026-10-22/review-fixes/native-100/restored-ports-no-overlap.png)
- [恢复引脚后不重叠，150%](manual-port-visibility-2026-10-22/review-fixes/native-150/restored-ports-no-overlap.png)

本轮没有运行全仓库测试、独立人工现场验收或冻结包验收；没有改动 Runtime、工程 schema、用户工程，
也没有重启用户程序、构建发布包或发布。
