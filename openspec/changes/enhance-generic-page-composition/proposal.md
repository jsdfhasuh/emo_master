# 通用页面组合与历史回看能力增强

**计划 V1.2 · 2026-11-18（北京时间）。依据无存量工程前提修订。**

参考基线：31afa5ecab8b7ddb53f0d6bdedc620f0a243e340。事实见 [source-map.md](source-map.md)，本次审核见 [review-report.md](review-report.md)。

## Why

用户希望用平台正式可编辑能力，得到目标图的生产监控与历史回看：清楚的页头和页签、主要图像、紧凑结果侧栏、历史记录列表、局部图与默认折叠的原始详情。现有基础已包含响应式布局、输出绑定、resultScope、历史查询及宿主控制；差距主要在控件表达、内容测量与历史选择联动。

V1.0 有几项超出目标的机制，也漏了正常使用链路。仅增加颜色和卡片属性不能消除 64px 等通用最小高度；仅在静态历史库点选不能证明新生产记录可回看；仅保存 rowId 不能解决持续生产下 OFFSET 漂移。V1.1收敛额外配置并补齐上述阻断；用户进一步明确没有现成工程，V1.2因此移除旧格式/旧包/旧SDK兼容任务，使用单一当前实现。

## What Changes

- 增强七组已有控件：container、text/number、indicator、page_navigation、image、navigation_button、sqlite_table；不新增业务专用类型。
- 增加有限表面/文字/状态/图像显示配置，修正紧凑测量及 Designer 编辑辅助对布局的影响；配置入口、撤销、保存与发布一起可用。
- 运行外壳沿用宿主状态和开始/停止；诊断默认收起，显式导航沿用自动隐藏宿主导航规则。
- 历史列表与表格共用查询；选中记录驱动图像和标量，既有 QueryHistoryPage 最小扩展按真实键定位同条/邻条，支持持续新增与切页恢复。
- 扩展现有 Annotate 的类别色、中文标签、置信度和线条表达；结果图仍由原工作流产生。
- 当前正式工程统一 2.10 / 页面 1.3；当前SDK及全部消费者同步更新，不提供旧工程迁移、旧页面包兼容或旧SDK固定字节保证。
- 交付一个正式可编辑双页工程，用既有 ImageSaver 和 SQLiteWriter 跑通模拟生产到历史；不另建组合预设引擎或第二个演示项目。

## Capabilities

### New Capabilities

- page-composition：组合外观、真实紧凑布局、图像比例和折叠。
- page-value-presentation：保持类型的状态映射、结果显示动作及纯文本详情。
- history-record-review：记录列表、图像、标量同条联动和按键逐条浏览。
- operational-page-shell：紧凑运行页头、导航、实时状态和宿主操作。
- vision-annotation-presentation：通用类别/线条标注及可部署中文绘图。
- page-authoring-delivery：正式编辑、格式、Qt/Web 与可运行示例交付。

### Modified Capabilities

本基线尚无对应已归档主规范，因此使用 ADDED Requirements。既有 add-independent-project-authoring 活动 change 进度不重写；实际执行基线有后续规范时先比较再落到正式归属。

## Impact

影响正式页面/工程模型、Designer、Qt presentation、现有 Runtime 历史查询及 protobuf 可选参数、Web 编辑/渲染与协议消费者、Annotate。具体路径见 source-map。

业务判断、PLC 地址/码值、模型训练不在本变更中。现有权限、结果作用域、资源保护和查询预算继续使用。首轮不增加任意脚本/表达式、布局框架、历史服务、长期快照、模板平台或 Qt 技术栈迁移。

实现完成以 [acceptance.md](acceptance.md) 的三条用户旅程为准；条目多、字段可序列化或截图好看均不能单独代表完成。
