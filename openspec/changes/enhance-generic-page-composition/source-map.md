# 源码落点与事实依据

**V1.2：用户已明确没有现成工程，本轮以当前正式格式与一套实现路径为目标，不承担旧工程、旧页面包或旧SDK兼容。**

## 审查范围与证据等级

- 固定审查提交：[`31afa5ecab8b7ddb53f0d6bdedc620f0a243e340`](https://github.com/jsdfhasuh/emo_master/tree/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340)。
- 审查目录：`/workspace/scratch/e23d9d983bbc/emo_master_original`；审查期间工作区干净。
- 本文件以**静态源码审查**为主；V1.1 另对基线 readPage 做了一次临时小库的直接函数验证，复现正常新增导致的 OFFSET 重复与空筛选列错误，详见 review-report.md / review-evidence.json。没有执行本计划的新实现或界面测试。现有测试文件是复用入口，不代表本轮已通过。
- 执行者应先记录实际 HEAD 和工作区状态，对照后续提交复用已完成能力；不为匹配本计划而回退用户代码。
- 本变更是通用页面能力增强，示例可以使用任意视觉业务，不新增产品类别、PLC 地址或脚垫专属控件。
- 方案落实仍沿用 Python 3.10 / PySide2 / Qt Widgets 和现有 Web 技术栈。

## 1. 仓库规则和 OpenSpec 组织

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [AGENTS.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/AGENTS.md) | 正常路径、局部修改、正式工程格式、实际验证范围和推送角色评论规则。实施时遵循用户最新指令；获授权推送需附 ChatGPT Web 评论。 |
| [openspec/config.yaml](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/openspec/config.yaml) | 当前为 spec-driven，无额外 artifact rules。 |
| [openspec/changes/add-independent-project-authoring/proposal.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/openspec/changes/add-independent-project-authoring/proposal.md) | 现有 proposal 使用 Why / What Changes / Capabilities / Impact。 |
| [openspec/changes/add-independent-project-authoring/design.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/openspec/changes/add-independent-project-authoring/design.md) | 现有 design 使用 Context / Goals–Non-Goals / Decisions，说明范围与数据职责。 |
| [openspec/changes/add-independent-project-authoring/tasks.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/openspec/changes/add-independent-project-authoring/tasks.md) | 用唯一实施任务清单保留已做/未做状态，修订文档不是实施完成。 |
| [openspec/changes/add-independent-project-authoring/specs/operator-capability-discovery/spec.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/openspec/changes/add-independent-project-authoring/specs/operator-capability-discovery/spec.md) | Requirement/Scenario、SHALL、WHEN/THEN 的现行格式。 |

基线未发现 `openspec/specs/` 主规范，本 change 的能力增量用 `ADDED Requirements`；不凭业务代码已存在就虚构 `MODIFIED Requirements` 的旧主条目。后续若已有正式归档，先核对真实规范落点。

## 2. 唯一模型、严格枚举与版本配对

本计划只以 **Project 2.10 ↔ Presentation 1.3** 作为正式创建、编辑、运行和交付目标。Designer、Runtime、Web 与当前SDK一起更新；不新增旧格式读取、自动迁移、按属性启用才升级或版本撤销机制。

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_panel_protocol/models.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_panel_protocol/models.py) | Model.extra=forbid、strict=True；VisualProps、IndicatorStyle、Action、ComponentType、Component.checkShape、Presentation.checkStructure 都有限定。当前样式/动作/状态属性须进入同一正式1.3模型。基线中targets、Runtime数据位置及canvasTop被版本条件限制，需改成当前格式可用的单一路径，而不是新增兼容分支。 |
| [src/emo_master/core/presentation/models.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/presentation/models.py) | 重导出共享页面模型，不能在应用内另建第二份页面 DTO。 |
| [src/emo_master/core/project/models.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/models.py) | 基线ProjectDocument.validateVersion严格约束2.9↔1.2；改为当前正式2.10↔1.3，统一创建和序列化。变量、资源、生产和设备能力在当前格式中完整保留，不增加旧配对兼容要求。 |
| [src/emo_master/apps/designer/state/workflow_store.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/state/workflow_store.py) | reset()通过Presentation默认创建；toPayload()/loadPayload()现有版本分支需统一到2.10/1.3正式路径，保留当前工程变量等内容。无需保留“打开旧工程不升级”的分支。 |
| [src/emo_master/apps/designer/state/project_edit_session.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/state/project_edit_session.py) | editPresentation()继续承担普通属性编辑与undo/redo。基线needsLatest分支会写2.9/1.2，需消除其对当前1.3的干扰；不新增格式升级/降级及版本撤销事务。 |
| [src/emo_master/core/project/migration.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/migration.py) | 基线已有历史迁移入口。本轮不新增迁移规则或转换器；当前正式load/configure路径若调用此文件，只处理使2.10/1.3正常贯通的必要分支，不改写无关历史档案。 |
| [src/emo_master/apps/designer/state/project_store.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/state/project_store.py) | 正式load/save入口继续使用，不以手写JSON绕过校验。 |
| [docs/project-json-spec.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/docs/project-json-spec.md) | 同步当前2.10/1.3字段、正式生成/保存方式和数据职责；删除本变更中的旧工程迁移承诺，同时更新AGENTS中的当前格式说明。 |
| [scripts/independent_web_editor_demo.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/scripts/independent_web_editor_demo.py) | create_demo_project使用正式新建，但断言版本为2.9/1.2；随新建默认同步为2.10/1.3。现有Web/Runtime软件验收及交付脚本消费此生成器，不能只改模型而遗留旧断言。 |
| [scripts/create_designer_space_acceptance.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/scripts/create_designer_space_acceptance.py) | createProject同样断言正式新建为2.9/1.2；调整为新的默认组合，保留原空间布局场景。这是当前生成器适配；受影响fixture可改为当前格式，保留它实际验证的空间布局行为。 |

### 当前格式与单一默认路径

所有本轮页面直接使用当前appearance/shell和组件默认：容器默认card、叶子默认none，布局组可明确none；文字继承当前主题，图像默认aspect。cardStyle/fontSize等已有字段若与新属性重叠，可在同一正式模型和编辑器中直接统一，不维护“旧字段优先/新字段覆盖”的回退链。

正式序列化只需保证当前工程编辑、保存重读与发布自洽，继承/显式配置意图可恢复；不要求为了旧.emoweb摘要而隐藏新增默认值。当前新包的完整性校验仍要成立。迁移旧项目与旧schema共存不在范围内，也不需要扫描全仓删除与本轮无关的历史代码。

### 2.10 必须继承的既有能力门槛

以下都是基线中会影响新工程实际功能的版本判断。统一使2.10能够使用已有能力，不为旧配对新增分支，不改变业务算法或另造版本框架。

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_master/core/workflow/dispatch_contracts.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/workflow/dispatch_contracts.py) | 2.10 继续支持既有 Dispatch 合同。 |
| [src/emo_master/core/workflow/validation.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/workflow/validation.py) | 2.10 继续支持已有工作流接口及循环周期边界。 |
| [src/emo_master/core/project/package_builder.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/package_builder.py) | 正式项目/页面包保留资源与页面，不走旧格式分支。 |
| [src/emo_master/core/project/snapshots.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/snapshots.py) | 新工程继续采集已声明的设备与配置身份。 |
| [src/emo_master/core/project/test_delivery.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/test_delivery.py) | 复用已有受管设备项目交付路径。 |
| [src/emo_master/core/project/runtime_package.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/runtime_package.py) | 项目包导出、导入及 requiredDeviceTypes 检查接受 2.10。 |
| [src/emo_master/apps/designer/page_designer/delivery.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/delivery.py) | 2.10 仍通过正式 Runtime 项目包导出。 |
| [src/emo_master/apps/designer/controllers/project_controller.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/controllers/project_controller.py) | 正式打开、编辑会话、保存换路径时沿用受管项目规则。 |
| [src/emo_master/apps/designer/ui/main_window.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/ui/main_window.py) | 设备配置与操作入口使用当前2.10，不增加旧工程迁移入口。 |
| [src/emo_master/apps/designer/ui/project_configuration.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/ui/project_configuration.py) | 不要把 2.10 判作旧项目，保留现有配置写入路径。 |
| [src/emo_master/apps/runtime/presentation/normal_capture.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/presentation/normal_capture.py) | 普通采集保留新工程的全局变量和设备定义。 |
| [src/emo_master/apps/runtime/project_config/imports.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/project_config/imports.py) | 正式配置导入识别 2.10。 |
| [src/emo_master/apps/runtime/project_config/rpc.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/project_config/rpc.py) | 既有导入/配置 RPC 对新格式放行；不新增 RPC。 |
| [src/emo_master/apps/runtime/communication/rpc.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/communication/rpc.py) | 新工程继续使用受管通信分支，不恢复旧顶层 communication。 |
| [src/emo_master/apps/runtime/operator_debug/contracts.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/operator_debug/contracts.py) | 调试输入的受支持工程版本包含 2.10。 |
| [src/emo_master/apps/runtime/grpc_server/service.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/grpc_server/service.py) | 加载、配置、设备/通信/调试分支认定 2.10 继承原有受管能力。 |

## 3. 布局、组件样式和 Qt 渲染

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_master/core/presentation/responsive.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/presentation/responsive.py) | 已有solveResponsive、横/纵/换行、比例、对齐、最小/期望尺寸；直接复用，不重建布局引擎。 |
| [src/emo_master/ui/presentation/renderer.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/renderer.py) | RuntimePages._children当前把所有非spacer组件包装为带边框卡片；appearanceStyle同时作用于标题/内容。增加可选表面和角色样式；editorHost的顶部28px与bindingHint不能再挤占新业务布局。ImageView.setImage目前只update，比例变化需更新几何。syncNavigation已有显式页内导航隐藏宿主的机制，直接复用。维持数据订阅/结果提交职责。 |
| [src/emo_master/ui/presentation/responsive_layout.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/responsive_layout.py) | ResponsiveWidgetLayout 既有9px边距、minimumSize基础高度与sizeHint固定加成；新显示需按实际padding/字体/content测量，和minHeight64/minWidth160默认值一起检查，不能只去边框。 |
| [src/emo_master/ui/presentation/container_title.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/container_title.py) | 容器标题与正文间距、独立字样式落点。 |
| [src/emo_master/ui/presentation/page_navigation.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/page_navigation.py) | PageNavigation目前始终QComboBox，未落实已声明tabs/buttons；落实导航模式与当前页状态。 |
| [src/emo_master/ui/presentation/images.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/images.py) | 现有图像所有权/转换辅助继续复用，不引入第二条图像服务。 |
| [src/emo_master/ui/presentation/path_image.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/path_image.py) | 历史图像复用既有读取与显示路径，样式改动不改变资源位置规则。 |
| [src/emo_master/ui/presentation/sqlite_widgets.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/ui/presentation/sqlite_widgets.py) | 既有筛选/分页/选中行事件，列表变体和逐条选择在原查询链上实现。 |
| [src/emo_master/core/presentation/sqlite_reader.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/presentation/sqlite_reader.py) | 现有LIMIT/OFFSET和SHA rowId不足以在持续新增时定位邻条；在原只读路径增加按真实键同条/邻条的可选分支，保留既有查询预算。readValue已有单列过滤；非空关键词配空filterColumn会报错。 |
| [src/emo_master/core/presentation/validation.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/presentation/validation.py) | 绑定类型、动作引用、页面目标校验；整数状态等扩展需同步这里，不能只让控件看起来可选。 |
| [src/emo_master/core/presentation/values.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/presentation/values.py) | 已有值读取语义继续保留；显示映射不得改写业务原值。 |

不要新增“指标卡”“设备卡”“脚垫结果面板”等专用类型。页头、状态条、双列大卡、紧凑状态行由增强后的普通组件组合；状态判定仍来自工作流，页面只负责映射和显示。

## 4. Designer 编辑、预览与撤销

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_master/apps/designer/page_designer/tools.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/tools.py) | EditingTools的字段创建、加载和applyFields继续作为当前属性编辑入口；样式/动作在这里可配置，可选组支持继承当前主题和恢复默认。编辑一个字段不改变其他组的显式/继承选择；页级appearance/shell进入已有页面设置。 |
| [src/emo_master/apps/designer/page_designer/property_panel.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/property_panel.py) | 按控件组织内容、位置、外观、数据来源；加入角色样式、有限状态映射等已有分组。 |
| [src/emo_master/apps/designer/page_designer/property_adapters.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/property_adapters.py) | 现有bool/string映射严格解析；扩展整数并保留类型区别；动作字段继续转正式Action。 |
| [src/emo_master/apps/designer/page_designer/responsive_properties.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/responsive_properties.py) | 复用现有布局编辑，增加真实缺少的间距/表面属性；不重写布局面板。 |
| [src/emo_master/apps/designer/page_designer/responsive_preview.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/responsive_preview.py) | candidateGeometry复制了硬编码卡片构造，必须与正式渲染共用表面/间距解析，避免拖放候选位置与落下位置不同。 |
| [src/emo_master/apps/designer/page_designer/page_canvas.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/page_canvas.py) | 已有原生控件画布、拖放、终端尺寸预览；使用新属性时维持测量一致。 |
| [src/emo_master/apps/designer/page_designer/workspace.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/workspace.py) | 已有可调逻辑视口及360×640/768×1024/1280×720/1920×1080预设；复用，避免重复建设。 |
| [src/emo_master/apps/designer/page_designer/preview_window.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/preview_window.py) | 已有示例/运行/历史来源，运行信息可收起。正式预览应与生产外壳区分，不能宣称此前完全没有诊断折叠能力。 |
| [src/emo_master/apps/designer/page_designer/palette.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/palette.py) | 基线palette以单组件类型为入口，尚非组件树预设系统。本轮只做必要说明/变体，不新增预设拖入协议；样例复用现有复制、编组。 |
| [src/emo_master/apps/designer/state/presentation_store.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/state/presentation_store.py) | 沿用原页面会话、复制、绑定和撤销关系，不能另建页面草稿Store。 |
| [src/emo_master/apps/operator_runtime/main.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/operator_runtime/main.py) | 正式运行外壳与已有开始/停止入口；诊断可收起，业务页面不承载任意执行脚本。 |
| [src/emo_master/apps/operator_runtime/controller.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/operator_runtime/controller.py) | 沿用真实运行控制与状态，页面按钮不另造任务控制服务。 |

## 5. 既有独立 authoring 通达

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_master/authoring/commands.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/authoring/commands.py) | 已有page.configure、component.put、component.bind、presentation.configure，正式模型JSON Schema自动装入命令定义。新字段通过这些命令可发现、可配置。 |
| [src/emo_master/authoring/drafts.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/authoring/drafts.py) | _page_command复用现有PresentationStore和当前正式模型；同步新字段，不新建业务命令或旧格式转换层。 |
| [src/emo_master/authoring/service.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/authoring/service.py) | 已有能力发现和get_project_presentation；返回实际新模型能力，不手工维护第二份属性目录。 |
| [src/emo_master/authoring/validation.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/authoring/validation.py) | 复用共享绑定/工程检查。读取、保存、导出不能丢新字段。 |

## 6. Web 渲染与独立页面编辑

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [web_panel/frontend/src/renderer/PanelRenderer.tsx](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/frontend/src/renderer/PanelRenderer.tsx) | 同一发布配置的表面、字样式、状态、导航和图像渲染；源预览版本列表接纳1.3；修复筛选默认空列与后端不符的问题，text保留纯文本换行；按键恢复选择与图像。 |
| [web_panel/frontend/src/style.css](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/frontend/src/style.css) | 共用默认样式及组件变体，不把业务名称或屏幕绝对坐标写死。 |
| [web_panel/frontend/src/contracts/types.ts](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/frontend/src/contracts/types.ts) | 浏览器视图模型接收新属性，不能代替正式生成Schema/TS。 |
| [web_panel/frontend/src/editor/model.ts](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/frontend/src/editor/model.ts) | 独立页面创建、编辑、保存使用当前1.3正式模型，不新增旧1.2页面读取或升级路径。 |
| [web_panel/frontend/src/editor/Editor.tsx](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/frontend/src/editor/Editor.tsx) | 必要字段可编辑、保存、撤销；数据绑定和映射与正式模型相符。 |
| [web_panel/backend/emo_web_panel/editor.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/backend/emo_web_panel/editor.py) | blank_presentation/validate_source/build_package有1.2常量；默认/允许版本/整数状态同步，发布sourcePresentationVersion取真实模型版本。 |
| [web_panel/backend/emo_web_panel/packages.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/backend/emo_web_panel/packages.py) | OFFICIAL_REQUIREMENTS、组件语义校验、源版本与_normalize_official属性透传；不忽略未知字段、不泄露本机资源路径。 |
| [web_panel/frontend/src/data/contract.ts](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/frontend/src/data/contract.ts) | 当前生成契约TypeScript的消费者，需指向新SDK对应目录。 |

## 7. 当前发布、生成物与SDK

本计划保持外层 `Panel Contract 1.0`、`published-1`、`emo-web-panel-1`；新源版本为1.3。按实际启用的能力生成：

- `presentation.composable-v1`
- `history.record-selection-v1`
- `shell.operational-v1`

当前发布按实际所用能力声明requirements，完整性及未知能力校验继续使用现有发布链路；不承担旧发布包读取或摘要兼容。

当前SDK目标为 **1.6.0**，交付目录为 `web_panel/contracts/panel-contract-v1/`。若当前仓库已正式采用后续版本，统一跟随实际版本；Designer、Runtime、Web和生成物同时更新，无需双版本消费或旧客户端适配。

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_panel_protocol/published.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_panel_protocol/published.py) | PublishedProps继承VisualProps；sourcePresentationVersion枚举纳入1.3；发布DTO保持路径剥离后的显示配置。 |
| [src/emo_master/core/project/web_panel.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/project/web_panel.py) | publishedSnapshot/buildWebPanel/readWebPanel；按实际特性增加requirements，正式导出并验证包摘要。 |
| [scripts/build_panel_contract.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/scripts/build_panel_contract.py) | 权威生成入口，输出JSON Schema、TypeScript、示例工程/页面包、manifest和可选SDK wheel；沿用当前panel-contract-v1生成目录，更新当前输出，不手改生成文件。 |
| [src/emo_panel_protocol/__init__.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_panel_protocol/__init__.py) | SDK版本来源之一，随新wheel一致更新。 |
| [packages/emo_panel_protocol/pyproject.toml](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/packages/emo_panel_protocol/pyproject.toml) | 当前SDK wheel版本与依赖元数据同步，实际消费的是本次生成的同一正式交付。 |
| [web_panel/pyproject.toml](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/pyproject.toml) | 当前依赖精确pin为SDK1.5.0，必须改为新交付版本。 |
| [web_panel/backend/emo_web_panel/app.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/backend/emo_web_panel/app.py) | health中的sdkVersion硬编码应跟随实际交付，不虚报版本。 |
| [.github/workflows/web-panel-ci.yml](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/.github/workflows/web-panel-ci.yml) | 既有job的wheel安装和版本断言统一到当前panel-contract-v1生成目录及SDK1.6.0；直接验证当前交付，不保留仅锁定旧快照的门槛，不另建CI产品线。 |
| [scripts/package_independent_web_editor_delivery.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/scripts/package_independent_web_editor_delivery.py) | 实际交付脚本读取SDK和contract-manifest的目录要同步。 |
| [docs/development/panel-contract-v1.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/docs/development/panel-contract-v1.md) | 当前协议生成/消费说明；保留wire contract与SDK版本的区别。 |
| [web_panel/README.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/README.md) | 实际安装、构建及交付命令改用新wheel。 |
| [web_panel/tests/test_contract_manifest.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/tests/test_contract_manifest.py) | 基线测试锁定历史panel-contract-v1/sdk-1.5.0的摘要和文件数。本轮删除仅为旧交付兼容设置的验收义务；相关活动测试改为验证当前生成内容/摘要与消费者一致，不以旧字节作为CI门槛。 |
| [tests/core/presentation/test_panel_contract.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/tests/core/presentation/test_panel_contract.py) | 从当前源码生成契约的测试入口，核对当前属性、默认值和正式新包完整性；受影响fixture按当前模型同步。 |

旧交接档案无需作为本轮交付依赖或验收门槛，也无须另开清档任务。当前生成物、Schema、TypeScript、SDK和CI消费入口统一更新。按键历史浏览给QueryHistoryPage增加两个可选请求字段，使用正式脚本生成；可选性用于“分页/锚点”两种查询语义，不承担旧客户端兼容。

## 8. 历史查询、选中数据与图像引用

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_master/core/presentation/history.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/core/presentation/history.py) | 生成历史声明和 historyDefinitionRevision；新增私有 recordKey 应参与真实来源身份，纯显示样式不应改变查询来源修订。 |
| [src/emo_master/apps/runtime/presentation/history.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/runtime/presentation/history.py) | QueryHistoryPage 已返回 columns、typed values、rowId 和 imageRefs；QueryHistoryAggregate 已支持表格的聚合；ReadHistoryImage 处理受声明约束的 opaque 图片引用。复用三个入口；图片引用TTL为300秒，离页返回应按键同条重查取得新refs。QueryHistoryPage只扩两个anchor可选参数，不新增latest或任意文件RPC。 |
| [src/emo_master/clients/runtime/history.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/clients/runtime/history.py) | Qt 客户端当前分别适配行值和图片引用；贯通标量/keyValues/anchor；readValue适配补传既有filterColumn/filterText，原接口当前未传。标量不通过路径读取。 |
| [src/emo_panel_protocol/messages.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_panel_protocol/messages.py) | HistoryRow.values 的 Scalar 已包含字符串、整数、浮点、布尔和空值；HistoryPage 有分页与稳定身份，HistoryAggregate 可返回总数。当前HistoryRow按实际稳定键返回keyValues，锚点HistoryPage返回selectedRowId；不按旧版本区分响应。没有稳定键的VIEW可作普通只读表格，记录联动前需声明键。原DTO extra=forbid，生产者与消费者一起更新即可，无需同义传输模型。 |
| [web_panel/backend/emo_web_panel/runtime_client.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/web_panel/backend/emo_web_panel/runtime_client.py) | Web 已经解析 HistoryPage/HistoryAggregate；保持已有上下文和数据来源校验。 |
| [src/emo_master/apps/designer/page_designer/sqlite_properties.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/sqlite_properties.py) | 复用表/列/排序/位置属性入口，补 recordKey、list 字段角色与记录值选择。 |
| [src/emo_master/apps/designer/page_designer/image_properties.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/apps/designer/page_designer/image_properties.py) | 固定图/资源图/选中行图来源已存在；图片来源继续使用原配置，只补新的显示尺寸属性。 |

SQLite 读取器目前只把路径列放入 selectionRows，普通列已在 rows 中；VIEW 未显式声明键时没有稳定 rowId。新增 recordValue 是显示会话投影，recordKey/keyValues用于真正定位记录；现有SQLite历史接口扩参数即可，不需要新服务。

## 9. 既有 Annotate 与字体打包

| 源码落点 | 基线事实与实施职责 |
| --- | --- |
| [src/emo_master/plugins/builtins/annotate/manifest.json](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/plugins/builtins/annotate/manifest.json) | 算子基线1.1.0；已有image/frame、检测/线/圆等输入及overlay/frame输出。新增参数沿用BGR数组，更新正式参数和算子小版本。 |
| [src/emo_master/plugins/builtins/annotate/operator.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/plugins/builtins/annotate/operator.py) | 当前cv2.putText绘制标签，检测标签含置信度，线条使用detectionColor；灰度通道保持。类别色、中文、底色和线条进入一条当前绘图实现。当前参数明确preserve/solid/none、showConfidence=true、labelBackground=false；不保留旧像素路径或版本开关。 |
| [pyproject.toml](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/pyproject.toml) | 已有Pillow依赖；正式包资源声明需要包含实际选定字体及许可证，不新增GUI渲染依赖。 |
| [requirements.txt](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/requirements.txt) | 核对执行环境的既有图像依赖与版本范围，避免只在开发者额外安装环境中可用。 |
| [docs/operator-runtime-build.md](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/docs/operator-runtime-build.md) | 原运行端打包入口及资源规则；当前执行包补字体与许可证。已有外部冻结发行若实际交付再按其入口处理，不将外部Windows EXE发布新增为本轮页面能力门槛。 |
| [tests/plugins/test_annotate_operator.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/tests/plugins/test_annotate_operator.py) | 复用原算子夹具验证当前综合显示、输入/frame不变和执行包字体；删除纯旧默认/像素兼容回归，不增加算法精度或性能专项。 |

## 10. 验证与交付范围

复用已有测试入口，集中验证实际受影响的正常路径与直接反例：

- 当前2.10/1.3工程的正式新建、普通undo/redo、保存重读、运行与发布。
- 当前emoweb的属性/requirements、包摘要与实际SDK消费者贯通；不做旧工程、旧包或旧SDK回归。
- Qt/Designer候选几何与真实布局一致；Web按同一配置表达相同结构/状态/动作。
- 同一个双页示例工程、资源和emoweb，含明确标识的模拟业务状态；用ImageSaver+SQLiteWriter实际产生历史记录，生产与历史按同键核对。编辑/运行/发布归入三条用户旅程，复用证据，不重复分阶段验收。
- 新SDK安装与生成Schema/TS消费验证；既有CI继续使用，不追加硬件、节拍、压力或全版本矩阵。
- 实际截图不能用静态HTML替代已实现控件。未执行的人工验收、打包产物或现场设备检查如实列出。

只有新功能实施后产生的验证证据才能勾选任务；本source-map本身不构成功能通过记录。


## 11. 正常使用链路的补充落点

| 源码落点 | 实际职责 |
| --- | --- |
| [proto/runtime.proto](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/proto/runtime.proto) | QueryHistoryPageRequest 现有标签1–8；实施时核对最新占用后追加可选 anchor_key_json/anchor_step，原RPC及普通分页保持。 |
| [scripts/gen_proto.py](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/scripts/gen_proto.py) | 修改实际协议后用原入口生成，执行其check，不手改生成代码。 |
| [ImageSaver](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/plugins/builtins/image_saver/operator.py) | 现有vision.io.image_saver输出实际result.path；同次主图/局部图使用可区分文件名，避免后续覆盖历史。 |
| [SQLiteWriter](https://github.com/jsdfhasuh/emo_master/blob/31afa5ecab8b7ddb53f0d6bdedc620f0a243e340/src/emo_master/plugins/builtins/sqlite_writer/operator.py) | 既有vision.io.sqlite_writer映射同次字段及FILE_REFERENCE；示例在写图成功后写记录，不新增归档服务。 |

V1.1 的基线直接验证仅调用原 readPage，临时库在调用后删除：pageSize=2时原首页[5,4]，选4后插入6，再读取旧page=1得到[4,3]，旧翻条策略会重复4；真实下一条应为3。相同基线用非空filterText和空filterColumn返回“请选择要筛选的显示字段”。这是两个具体修订依据，不是新实现或完整Runtime/UI测试通过。
