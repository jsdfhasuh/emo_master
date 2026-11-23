# 源码定位与需求归属

## 核对基线

本计划基于仓库 `jsdfhasuh/emo_master` 的固定提交 [`6cbff0dcbea671663cf9ab773c6ce5c81690da35`](https://github.com/jsdfhasuh/emo_master/commit/6cbff0dcbea671663cf9ab773c6ce5c81690da35)。该提交为 2026-11-20 本轮核对时的 `main`，提交说明为 `docs(openspec): align V1.4 closeout with final CI evidence (#14)`。后续执行应记录实际工作提交与本基线的差异，不能把本文件解释为对未来 `main` 的验证。

以下路径对应基线中的既有文件；“计划改进位置”是待实现设计。源码读取只能证明入口和处理链路存在，不能证明高级功能已实现、实际页面已运行或效果已验收。实现范围与行为以本 change 的 `specs/page-advanced-appearance/spec.md`、`design.md`、`tasks.md` 为准。

## 1. 共用模型、保存与发布

| 已有文件 | 当前职责与计划改进位置 |
| --- | --- |
| [src/emo_panel_protocol/models.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_panel_protocol/models.py) | 定义组件类型及 `VisualProps`、`ControlAppearance`、`PageShell`。在共用模型中增加结构化高级外观和分别保存的 Qt QSS／Web CSS 字段；模型采用严格字段约束，不能只在某个编辑器临时附加属性。 |
| [src/emo_panel_protocol/published.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_panel_protocol/published.py) | 发布模型中的 `PublishedProps` 继承 `VisualProps`，发布页面沿用 `PageShell`。同步核对发布模型是否保留高级外观和两端自定义样式。 |
| [src/emo_master/core/project/web_panel.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/core/project/web_panel.py) | `publishedSnapshot()` 将工程投影为发布快照；`buildWebPanel()`、`readWebPanel()` 处理发布包。验证新增字段在投影、导出、重新读取中不丢失，尤其是未在当前端执行的另一端样式。 |
| [scripts/build_panel_contract.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/scripts/build_panel_contract.py) | 现有协议生成入口。修改源模型后按仓库现有流程生成 Schema、TypeScript 类型及相关合约产物；不能仅手改生成文件制造表面兼容。 |
| [src/emo_master/apps/designer/page_designer/delivery.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/delivery.py) | Designer 的测试包及 Web 面板导出入口。沿既有导出路径验证新增配置进入真实交付物。 |

结构化字段承担跨端可理解的共同含义；QSS 与 CSS 各自执行和保存，不承诺任意语法自动互转。是否启用某端样式与该端代码应成对保留。新字段的最终名称、默认值与版本策略由本 change 的设计和实现任务统一定义。

## 2. Designer：在现有交互中扩展

| 已有文件 | 当前职责与计划改进位置 |
| --- | --- |
| [src/emo_master/apps/designer/page_designer/property_panel.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/property_panel.py) | 现有属性分组包括内容、位置与大小、外观、数据来源等。普通控件的高级入口放在既有“外观”路径内；保留 `spacer` 仅布局、隐藏外观组的规则。 |
| [src/emo_master/apps/designer/page_designer/tools.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/tools.py) | `editAppearance()` 在打开外观编辑前提交已有待提交属性，再取得快照并调用外观编辑；接受后通过 `session.editPresentation()` 写回。新增交互须接入同一编辑路径。取消高级对话框仅丢弃该对话框草稿，不撤销打开前已经提交的修改。 |
| [src/emo_master/apps/designer/page_designer/appearance_properties.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/appearance_properties.py) | 既有 `SettingsDialog`、组件／全局外观编辑与宿主按钮字段。这里扩展结构化高级选项、QSS 编辑、状态预览和按组恢复；保持选中组件与全局／宿主对象的作用域明确。 |
| [src/emo_master/apps/designer/page_designer/workspace.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/workspace.py) | 现有设计工作区、画布和浮动面板组织。保留当前布局，只增加入口和编辑内容，不另造一套 Designer 外壳。 |
| [src/emo_master/apps/designer/page_designer/preview.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/preview.py)、[src/emo_master/apps/designer/page_designer/preview_window.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/preview_window.py) | Designer 预览入口和窗口。使预览消费当前外观草稿，并区分外观状态预览与真实运行条件。 |
| [src/emo_master/apps/designer/page_designer/page_canvas.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/designer/page_designer/page_canvas.py) | 当前设计画布。检查选择、刷新和编辑状态是否仍正确；高级外观不改变控件的定位、布局和选择职责。 |

普通控件路径为“选中控件 → 属性／外观 → 高级外观”。页头开始、停止按钮属于页面外壳，沿“页面设置／全局外观 → 页头 → 开始／停止控件”编辑，不伪装成可在画布拖入的普通组件。具体文案与位置应以实际现有界面核对后确定，参考图只说明目标改进点。

## 3. Qt 渲染与宿主状态

| 已有文件 | 当前职责与计划改进位置 |
| --- | --- |
| [src/emo_master/ui/presentation/renderer.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/renderer.py) | 组件构建与渲染汇合处。将高级外观应用到各类组件的真实根节点及允许开放的子部件；同步覆盖 Designer 预览和运行页面使用的渲染路径。 |
| [src/emo_master/ui/presentation/appearance.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/appearance.py) | 现有表面、标题、数值、标记等样式，以及值／状态刷新后的样式重建。QSS 扩展须与既有局部样式合成并在刷新时保留；仅给外层挂一份样式不足以覆盖内层重新设置的样式。本轮落实图中已公开的 marker，将当前拼接到文字中的标记接入可独立命中的部件。 |
| [src/emo_master/ui/presentation/shell.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/shell.py) | `applyShellHeader()` 和 `configureHostButton()` 等页面外壳入口。增加页头与宿主按钮外观，不改宿主动作或真实可用性判定。 |
| [src/emo_master/apps/operator_runtime/main.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/apps/operator_runtime/main.py) | 运行端宿主按钮及真实启动／停止条件。检查高级外观与实际启用、禁用和动作绑定共存；Designer 的状态预览不得成为运行控制条件。 |
| [src/emo_master/ui/presentation/table.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/table.py) | `CollectionModel`／`CollectionView` 表格显示。纳入表头、行、单元格、选中态等适用部件，避免只覆盖卡片外壳。 |
| [src/emo_master/ui/presentation/sqlite_widgets.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/sqlite_widgets.py) | `SqliteTableView`、`SqliteValueView` 及记录列表委托等数据库视图。分别核对表格、列表和单值的真实绘制路径，保留查询和选择行为。 |
| [src/emo_master/ui/presentation/page_navigation.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/page_navigation.py) | `PageNavigation` 页面导航。覆盖导航项及当前项外观，同时保留原导航目标和切换语义。 |
| [src/emo_master/ui/presentation/container_title.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/container_title.py)、[src/emo_master/ui/presentation/clock.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/clock.py)、[src/emo_master/ui/presentation/job_status.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/job_status.py) | 容器标题、时钟和运行状态的独立实现路径。分别接入适用的部件与状态外观，避免公共外观函数之外的控件遗漏。 |
| [src/emo_master/ui/presentation/images.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/images.py)、[src/emo_master/ui/presentation/path_image.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/path_image.py)、[src/emo_master/ui/presentation/static_assets.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/static_assets.py) | 图像与资源相关路径。外观仅作用于允许的显示外壳和部件；不重定义图像数据、算法标注或资源解析行为。 |

控件刷新、结果切换、重建、换页后都应按同一外观合成规则处理。QSS 的目标是样式，不开放借助 `qproperty-*` 修改业务属性的通道，也不把有限检查宣称为完整 Qt QSS 语义验证。

## 4. Web 编辑、运行、草稿与发布

| 已有文件 | 当前职责与计划改进位置 |
| --- | --- |
| [web_panel/frontend/src/editor/Editor.tsx](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/editor/Editor.tsx) | Web 编辑器及其画布卡片渲染路径。增加高级外观入口和即时预览；编辑器有自己的呈现路径，不能只改最终运行渲染器。 |
| [web_panel/frontend/src/editor/PresentationFields.tsx](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/editor/PresentationFields.tsx) | 既有组件、页面、宿主按钮外观字段。扩展结构化字段及 Web CSS 编辑；保存时保留 Qt QSS 原文和启用标志。 |
| [web_panel/frontend/src/editor/model.ts](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/editor/model.ts) | 编辑模型与 `renderModel` 预览转换。核对 props、shell 与新增字段的投影、默认处理和往返保存。 |
| [web_panel/frontend/src/editor/drafts.ts](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/editor/drafts.ts) | 本地草稿标识、保存与恢复。将新配置纳入原草稿内容，验证重新载入不丢失双端样式。 |
| [web_panel/frontend/src/editor/editor.css](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/editor/editor.css) | Web 编辑器现有界面样式。只对新增编辑区做必要适配，保持当前工具箱、画布与属性面板结构。 |
| [web_panel/frontend/src/renderer/PanelRenderer.tsx](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/renderer/PanelRenderer.tsx) | 最终组件渲染及既有 `data-component-id` 标识。按当前控件建立 CSS 作用域，并隔离嵌套控件，防止容器样式泄漏到子组件或编辑器外壳。 |
| [web_panel/frontend/src/renderer/presentation.tsx](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/renderer/presentation.tsx) | 共享表面、角色、指示器与图像等呈现样式。现有内联样式须与高级配置有明确优先级；不能靠普遍追加 `!important` 代替层叠设计。 |
| [web_panel/frontend/src/renderer/shell.tsx](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/renderer/shell.tsx) | 页头和宿主按钮；已有部分宿主样式变量，字体、圆角、内边距等仍需逐项核对。接入同一结构化外观和按宿主按钮隔离的 CSS。 |
| [web_panel/frontend/src/renderer/HistoryView.tsx](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/renderer/HistoryView.tsx) | 历史／数据库记录视图。纳入表格与列表的实际呈现路径，不仅修改通用容器。 |
| [web_panel/backend/emo_web_panel/editor.py](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/backend/emo_web_panel/editor.py) | `validate_source`、`DraftStore.save`、`build_package` 等校验、草稿与打包处理。新配置须通过真实校验、SQLite 草稿保存和发布包生成；不改变 Web 草稿独立保存、不会自动写回本地 `.emoproj` 的现有交付关系。 |

Web CSS 只控制当前控件／当前宿主按钮的允许部件；不在第一轮提供整页原始 CSS 级联入口。CSS 编辑、基础检查、预览、禁用及恢复需要独立、可解释的结果，不能把浏览器忽略无效声明当作“验证成功”。

## 5. 完整覆盖边界

基线 `ComponentType` 有 14 类，`spacer` 仅参与布局。此次完整计划覆盖其余 **13 类可见组件**：

`image`、`text`、`number`、`indicator`、`runtime_status`、`table`、`navigation_button`、`container`、`page_title`、`page_navigation`、`clock`、`sqlite_table`、`sqlite_value`。

另覆盖页面外壳中的 **`startControl` 与 `stopControl` 两个宿主控件**。各类型只开放实际适用的状态和部件；“覆盖”不表示把按钮悬停、图像标注、表格单元格等无差别配置到所有类型。首批 3 类仅用于贯通模型、编辑、渲染、保存及发布，不能据此关闭完整任务。

## 6. 既有 OpenSpec 能力与新需求归属

| 基线中的既有规范 | 应复用的约束／避免重开的问题 |
| --- | --- |
| [openspec/changes/enhance-generic-page-composition/specs/page-composition/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/enhance-generic-page-composition/specs/page-composition/spec.md) | 已有组件组合、表面、卡片默认、间距、圆角、文字角色、响应布局、图像显示以及导航按钮基础外观。新 change 只增加更细部件、状态和自定义样式能力，不把既有基础外观重复声明为新功能。 |
| [openspec/changes/enhance-generic-page-composition/specs/page-value-presentation/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/enhance-generic-page-composition/specs/page-value-presentation/spec.md) | 已有类型化绑定、指示器映射和有限动作语义。高级样式不新增任意表达式、结果计算或动作脚本。 |
| [openspec/changes/enhance-generic-page-composition/specs/operational-page-shell/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/enhance-generic-page-composition/specs/operational-page-shell/spec.md) | 已有页面外壳、宿主命令、目标、权限和可用性边界。外观与状态预览不能改变这些运行职责。 |
| [openspec/changes/enhance-generic-page-composition/specs/page-authoring-delivery/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/enhance-generic-page-composition/specs/page-authoring-delivery/spec.md) | 关联现有页面创作与交付能力。实施前读取该能力完整规范，按当前工程／发布流程接入，不建立第二套交付通道。 |
| [openspec/changes/enhance-generic-page-composition/specs/history-record-review/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/enhance-generic-page-composition/specs/history-record-review/spec.md)、[openspec/changes/enhance-generic-page-composition/specs/vision-annotation-presentation/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/enhance-generic-page-composition/specs/vision-annotation-presentation/spec.md) | 关联历史回看与视觉标注。表格／图像高级外观仍受其原有语义约束，不能以外观实现替代查询、选择或标注规则。 |
| [openspec/changes/complete-page-configuration/specs/page-configuration/spec.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/changes/complete-page-configuration/specs/page-configuration/spec.md) | 已有 logo、页头、开始／停止按钮基础外观、标题与标记、历史控件和运行选择等能力；本次不重做其已完成验收。 |
| [openspec/config.yaml](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/openspec/config.yaml)、[AGENTS.md](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/AGENTS.md) | 仓库采用 `spec-driven`；遵守仓库已有工作区、范围和验证规则。交付包不复制或覆盖这两个文件。 |

在此固定基线中，`openspec/specs/` 和 `openspec/changes/archive/` 只有占位文件，相关旧规范仍位于上述活动 change 目录；不存在 `openspec/AGENTS.md`。因此本包以独立能力 `page-advanced-appearance` 承载真正新增的 `ADDED Requirements`，引用旧能力作为前置约束，不对尚不存在的主规范编造 `MODIFIED Requirements`。

若实际实施基线已经归档这些旧 change，应先按当前目录重新核对规范归属；只有确实改变已有 requirement 的行为时，才把该部分写为对应能力的完整 `MODIFIED Requirement`，其余新增要求仍归本能力。
