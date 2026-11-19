# 完善通用页面配置

## Why

V1.3 已交付双页、共享外观和历史联动，但工程仍无法完整表达目标页面中的宿主按钮、页头标志、左侧状态标题/圆点和简洁历史列表。两目标中心之间的虚线也缺少可直接连接已有标注算子的构造步骤。用户要求把这五项补成通用平台能力。

## What Changes

- 扩展现有 `PageShell`，配置 logo 显示范围、页头表面/字体、开始与停止按钮文字和结构化外观。
- 扩展现有 indicator，允许标题随状态变色、圆点放在标题前。
- 扩展现有 `HistoryView`，独立显示或隐藏筛选、排序和分页控制。
- 新增通用 `vision.geometry.line_from_points`，将两个已选点变成既有 `lineCollection`，复用 Annotate 的虚线及端点。
- Qt、Web、两端作者属性入口和预览使用同一套字段；同步当前 SDK、生成物、可编辑示例及发布包。
- 在所涉及的 Web 宿主/预览任务选择入口补充短任务 ID，避免同名同状态运行不可区分。

## Impact

仍使用工程 2.10、页面 1.3、Panel Contract 1.0；共享 Python SDK 发布为 1.8.0。当前配置通过正常保存、发布与重编辑链路传播。不引入新 RPC、页面命令系统、历史服务或旧格式迁移。

影响位置：`emo_panel_protocol`、页面 Designer 属性窗、Qt 页面/宿主、Web 属性/渲染/宿主、算子注册，以及 `examples/generic_page_composition`。现场 PLC、相机和夹持控制不在本次变更中。
