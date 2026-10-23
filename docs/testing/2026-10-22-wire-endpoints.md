# Designer 连线端点连续显示修复

## 范围与根因

源码基线：`d39169251c9bedc339c440a29a56df5eafba3be5` 加现有工作区修改。
本轮只修改 `flow_scene.py` 的绘图边界与端点短线，以及对应测试；保留已有端口折叠、
配置对比和其它未提交修改。没有修改 Runtime、工程连接关系、路由算法或自动布局。

1. 同源分支的公共绘制图层 `_WireLayer.shape()` 返回空路径以避免重复命中，但 Qt 默认
   `boundingRect()` 依赖这个 shape，因而得到 `(0, 0, 0, 0)`。画布原点移出视口后，
   公共段被裁掉，剩余分支看起来从端口外悬空开始。路由首尾坐标实际上仍是端口中心。
2. 端口位于节点边框内侧，正常连线绘制在节点下方；节点底色遮住圆点到边框之间的短段。
   即使公共段恢复，这里仍会有小间隙。

修复为公共图层单独提供含笔宽的绘图边界，命中 shape 保持为空；节点填充之后补画已连接
端口的内部短线，使用既有连线的优先级、颜色、粗细和受阻虚线样式。未连接端口不画短线；
删除和清空时同步清除派生样式。端口本身的命中范围、公共段分支菜单与真实边身份保持不变。
另给已有 `_comparisonColor` 字段补 `str | None` 注解，解决该文件静态检查中的类型错误，
不改变其行为。

## 验证

环境：Conda `emo_master` Python 3.10 / PySide2 / Windows 原生 Qt。

- 测试先行得到 7 failed，证明空绘图边界及画布平移后的公共段缺失；只修边界后 7 passed。
- 增加节点内部短线像素检查后为 6 failed、1 passed，证明圆点旁仍被节点底色遮挡；
  补齐短线后通过，再增加样式、删除及清空回归，总计新增 8 项。
- 定点使用真实 `QGraphicsView`，将原点移出可见区域，在画布缩放 0.65 / 1 / 1.5 下检查
  普通和选中连线的输出端内部短线、公共段、输入端内部短线像素，同时检查路由首尾坐标。
- 扩展回归 **114 passed**，8.31 秒，见 [regression.txt](wire-endpoints-2026-10-22/regression.txt)。
  范围为新增测试、unused_port_folding、flow_edge_presentation、flow_edge_readability、
  edge_selection_highlight、flow_routing_channels、flow_layout_routing、flow_layout_qt、
  flow_scene_interactions、coordinate_port_presentation。
- 原生 Windows Qt 100%：**37 passed**，3.90 秒，见 [native-100.txt](wire-endpoints-2026-10-22/native-100.txt)。
- 原生 Windows Qt 150%：**37 passed**，4.31 秒，见 [native-150.txt](wire-endpoints-2026-10-22/native-150.txt)。
  两组是相同测试的重复缩放验证，不叠加为不同用例；覆盖新增端点像素测试、端口折叠、
  连线选择高亮、公共段菜单与交互。
- Ruff 两个 Python 文件通过；mypy `flow_scene.py` 通过；`git diff --check` 通过。

截图均为临时模拟图，不是用户现场工程：

- [修复前，平移后公共段缺失](wire-endpoints-2026-10-22/panned-before.png)
- [修复后，100%](wire-endpoints-2026-10-22/native-100/panned-fanout.png)
- [修复后，端点细节](wire-endpoints-2026-10-22/native-100/port-exit-detail.png)
- [修复后，150%](wire-endpoints-2026-10-22/native-150/panned-fanout.png)

已目视检查修复后的端点细节和 150% 截图。没有执行全仓库测试、独立人工桌面验收、
性能专项、冻结包或真实设备验收；没有打包、发布或重启用户程序。

复跑：设置 `QT_QPA_PLATFORM=windows`、`QT_SCALE_FACTOR=1` 或 `1.5`，执行
`python -m pytest -q tests/designer/test_wire_endpoint_rendering.py`。
设置 `EMO_WIRE_ENDPOINT_SCREEN_DIR` 可保存原生视口及端点截图。
