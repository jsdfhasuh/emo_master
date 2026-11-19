# 页面组合与外观

## Purpose

让现有通用控件组成生产监控与历史回看页面，重点落实紧凑尺寸、单层卡片、清楚的文字层级和真实可编辑的布局。同一配置在 Designer、Qt 运行端和 Web 中表达相同结构，继续使用已有响应式布局。

## ADDED Requirements

### Requirement: Compose editable pages from existing component types

系统 SHALL 使用现有 container、text、number、indicator、runtime_status、image、page_navigation、navigation_button 和 sqlite_table 组合页面。交付工程中的区域 SHALL 是可分别修改、绑定、复制、编组和解组的普通组件，不增加业务专用 ComponentType。本轮 SHALL 以一个可编辑双页工程展示这些能力，不以新增 palette 组件树预设入口或第二个示例工程为前提。

#### Scenario: Adapt an existing result card

- **WHEN** 用户在交付工程中把一个数值字段换为测量值，修改单位和已有状态映射
- **THEN** 原卡片结构仍可使用，修改通过普通组件属性和绑定完成，无须新增控件类型或修改平台代码

### Requirement: Component surfaces use current defaults

系统 SHALL 通过可选 surface 表达无装饰布局组和卡片，并支持背景、边框、圆角与四侧内边距。无装饰叶子 SHALL 不被额外包装成可见卡片；外层卡片的 padding 为零时，其图像内容 SHALL 能使用完整内部宽度，标题与页脚可由普通子容器分别设置内边距。未配置 surface 时，container SHALL 使用本版 card 默认，普通叶子组件使用 none；布局分组明确设置 none。不保留 cardStyle 或按工程版本分流的表面渲染。

#### Scenario: Compose one image card

- **WHEN** 外层 container 设置卡片和零内边距，标题行与页脚各自设置内边距，中间 image 设置无装饰表面
- **THEN** 只显示外层卡片边界，图像使用中段可用宽度，标题和页脚保持各自留白，不出现重复边框或双重 padding

### Requirement: Compact content measurement uses configured geometry

系统 SHALL 使用一份有限浅色语义 token，并复用已有 responsive 尺寸、权重、对齐和换行规则。Qt 的 sizeHint、minimumSize、heightForWidth、实际布局及 Designer 候选拖放测量 SHALL 使用有效字体、真实内容和配置内边距，不叠加与配置无关的固定尺寸。页面默认使用浅色主题、contentMaxWidth=0、pagePadding=24；显式配置覆盖当前默认。

text 的动态内容变化 SHALL 使实际文本控件及其父布局重新测量：`width=content` 使用新内容的自然宽度，fill 或多行内容按当前可用宽度重算高度。更新 SHALL 保留配置的对齐，不继续使用初始占位符的尺寸，也不因替换纯文本而把横排主值恢复为左对齐。实现复用 Qt 的几何失效通知和既有布局，不增加第二套测量或页面重建机制。

#### Scenario: Measure a padded page

- **WHEN** 页面或卡片使用显式内边距与当前字体
- **THEN** Qt 的本机布局和拖放预览采用相同可用空间

#### Scenario: Remeasure updated text without losing alignment

- **WHEN** 同一 text 从中性占位更新为完整时间或多行日志，随后切换到较短记录，其中横排字段配置为主值末端对齐
- **THEN** 内容宽度或换行高度随实际文本更新，完整时间和日志可读，较短内容收回多余高度，末端对齐保持；不需要重新打开页面或手动调整窗口触发正确测量

### Requirement: Use configured content and control sizes

交付工程中的无装饰文字、图例、计数和紧凑字段组合 SHALL 明确设置适用的 `responsive.minHeight=0`、`minWidth=0`、`preferredHeight=0`；按钮、状态行和主列再按用途设置已有最小尺寸。系统 SHALL 尊重明确配置的尺寸，不让统一 minHeight=64、minWidth=160 或布局固定加成把所有字段撑成大卡片。当前组件工厂、属性面板和渲染器 SHALL 使用一致的用途默认，不为旧工程另留尺寸分支。

Designer 的绑定提示、选中标记和编辑辅助 SHALL 通过现有画布辅助或提示入口呈现，不占用业务内容布局。Qt 与 Web SHALL 使用同一属性语义；正常内容超出窗口时使用既有滚动与换行。

#### Scenario: Edit and place a compact field group

- **WHEN** 用户调整一组无装饰字段的字号和内边距，并在 Designer 拖放该组
- **THEN** 候选位置、实际落点和运行内容尺寸一致；绑定提示或选择状态不会额外增加顶部空间、底部说明行或统一 64 像素行高

#### Scenario: Resize the delivered pages

- **WHEN** 同一双页工程在 1366×768 和 1920×1080 逻辑视口中显示
- **THEN** 主区按已声明的列宽规则分配空间，较窄时正常换行，页签和主要操作可访问；超高内容可滚动，不通过缩放整页或绝对截图坐标维持布局，也不以所有详情必须挤进第一屏作为验收要求

### Requirement: Separate title value and helper typography

系统 SHALL 为标题、主值和辅助文字提供独立字号、字重与颜色，并支持文字和数值的横向或纵向排列。horizontal valueLayout SHALL 将名称和主值放在同一行，主值位于末端，辅助说明按配置在下方显示。单位、精度和原始值语义 SHALL 保持。未配置的文字角色 SHALL 直接使用本版主题默认，不经旧字体字段回退。

#### Scenario: Display a large value beside its label

- **WHEN** number 使用 horizontal valueLayout，主值为较大字号，标题和辅助文字为较小字号
- **THEN** 主值突出但标题不被同时放大，单位和精度保持配置，整行高度由实际内容与内边距决定

### Requirement: Aspect preserving image presentation updates layout

系统 SHALL 为 image 提供 aspect 与 fixed 展示方式，当前默认 aspect，并使用 contain 保持图像完整。未指定 aspectRatio 时按有效图像宽高比显示，尚无图时以 4/3 占位。比例尺寸 SHALL 依据实际可用宽度计算；图像或配置变化影响有效比例时，Qt SHALL 通过 updateGeometry 或等价既有机制重新测量，Designer 和运行视图使用同一规则，Web 的固定 max-height SHALL 不覆盖当前配置。

交付工程的主图和局部图 SHALL 使用明确声明的展示区域比例，使切换历史记录时版面稳定；内容比例不同的部分通过背景留白呈现。尚无图像、加载中或缺图时 SHALL 保留该展示区域的合理尺寸并显示对应占位，不保留上一条图片冒充当前记录。

#### Scenario: Review differently shaped images

- **WHEN** 用户切换历史记录，主图和局部图的原始宽高比不同
- **THEN** 两个组件保持各自声明的展示区域比例，完整显示当前图片，留白使用配置背景；若用户修改展示比例，布局随配置重新测量

### Requirement: Button appearance remains independent of action

系统 SHALL 为既有 navigation_button 提供 primary、secondary、link 外观，当前默认 secondary，并把外观与既有有限动作分开。链接外观 SHALL 仍可聚焦和触发；可用性与禁用状态 SHALL 继续来自相应动作，不通过新增按钮类型表达颜色和形态。

#### Scenario: Use a link in the image footer

- **WHEN** 用户把 image 页脚中的 navigation_button 配为 link，并保留明确的 navigate 目标
- **THEN** 控件以文字链接显示并执行该导航动作，修改外观不改变数据绑定或动作目标

### Requirement: Collapsible containers reclaim content height

系统 SHALL 允许 container 使用可选 collapse 设置初始折叠状态。折叠时 SHALL 保留清楚的标题入口并收回子内容占用的高度；展开状态属于当前查看会话，沿用现有页面状态保存方式，不改变工程 dirty 状态。

#### Scenario: Read and close a historical log

- **WHEN** 用户展开所选历史记录的日志，再收起该容器并切页返回
- **THEN** 日志仍属于当前选中记录，折叠时没有残留的空白正文高度，当前会话保留其展开或收起状态
