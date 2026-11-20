# 页面控件高级外观

## Purpose

让工程师在现有 Designer 和 Web 页面编辑流程中，为多数可见控件配置常用高级外观，并使用各端的原生 QSS 或 CSS 继续定制；配置能够预览、保存、发布并在正常数据更新后的运行页面中持续生效，同时保持已有布局、数据与宿主动作语义。

## ADDED Requirements

### Requirement: Advanced appearance covers all visible component types

系统 SHALL 为 image、text、number、indicator、runtime_status、table、navigation_button、container、page_title、page_navigation、clock、sqlite_table、sqlite_value 十三种可见组件以及页头开始／停止按钮提供适用的高级外观和本端原生样式入口。spacer 继续作为布局对象。

#### Scenario: Complete coverage is required for delivery

- **WHEN** 评审本 change 的完成情况
- **THEN** 十三类组件和两个宿主按钮分别具备 Qt／Web 的实际生效样例
- **AND** 不能将按钮、状态行、数值卡三类样例完成作为整个 change 完成

#### Scenario: Only applicable options are shown

- **WHEN** 用户选择某种组件或 spacer
- **THEN** 可见组件仅显示其适用字段与真实部位选择器
- **AND** spacer 不显示原生样式代码入口

### Requirement: Common advanced fields preserve existing meanings

系统 SHALL 在现有外观定义上提供自定义颜色、边框细节、适用图标排列和按钮状态外观，保留原语义色及已有配置的含义。未配置的新增字段 SHALL 使用当前默认外观或该对象基础配置。

#### Scenario: Configure a custom border and color

- **WHEN** 用户为适用部位设置有效自定义颜色、边框宽度和线型
- **THEN** Qt 与 Web 显示相同含义的配置效果
- **AND** 原数据绑定和控件位置保持正确

#### Scenario: Omitted state fields inherit the base appearance

- **WHEN** 用户只配置按钮悬停背景而未配置该状态的其他字段
- **THEN** 其他外观字段沿用该按钮基础配置

#### Scenario: Combine new icon layout with existing button fields

- **WHEN** 用户通过原外观入口设置右侧图标、12 px 尺寸和 6 px 间距，并同时使用已有文字、字号、圆角和内距字段
- **THEN** 两端真实预览和对应运行页呈现该组合，文字完整且图标排列正确
- **AND** 本端代码未显式覆盖的基础字段继续生效

### Requirement: Native style languages are stored independently

系统 SHALL 分别保存 QSS 与 CSS 的启用状态和代码。编辑一端或修改通用表单 SHALL 保留另一端内容；当前端仅应用自己支持的语言，不自动转换代码。

#### Scenario: Edit QSS without losing CSS

- **WHEN** 配置同时带有 QSS 和 CSS，用户在 Designer 修改 QSS 后保存
- **THEN** 新 QSS 与启用状态被保存
- **AND** 原 CSS 内容和启用状态保持不变

#### Scenario: Edit CSS without losing QSS

- **WHEN** 用户在 Web 修改 CSS、保存草稿并发布
- **THEN** 发布数据保留新 CSS 与原 QSS
- **AND** Web 仅应用 CSS

### Requirement: Native rules are restricted to their owning object

系统 SHALL 将受支持规则的直接匹配限定到当前组件自身部位或选定宿主按钮，避免直接命中其他组件和编辑器。嵌套子组件 SHALL 保留自身基础字体／颜色等公开属性；复制后重新绑定范围。容器正常布局影响不属于外溢，不承诺任意原生规则的全属性沙箱。

#### Scenario: Container styles do not leak into child components

- **WHEN** 用户修改容器自身边框和文字色／字体，内部包含一个没有自定义代码的文字组件
- **THEN** 该容器外观更新
- **AND** 内部子组件仍按自身基础配置显示，相邻同类型组件的配置不被覆盖

#### Scenario: Copied components keep local styling

- **WHEN** 用户复制一个带原生代码的组件并修改副本样式
- **THEN** 副本保留可用的代码且只影响副本
- **AND** 原组件外观保持原配置

### Requirement: Selector hints correspond to real rendered parts

系统 SHALL 根据当前组件、模式和端提供可实际命中的部位选择器与示例。不存在的部位 SHALL 不出现在可用列表中；自绘 item 或单元格不得被描述为不存在的 QWidget 子控件。

#### Scenario: Text and table targets use their actual implementations

- **WHEN** 用户为文本或表格请求插入选择器
- **THEN** 提示匹配该控件实际使用的文本对象、表头或原生 item 部位
- **AND** 不统一生成可能无法匹配的 QLabel 或虚构单元格选择器

#### Scenario: Page navigation hints match the active mode

- **WHEN** 用户编辑页签或按钮模式的页面导航
- **THEN** Qt 分别提供实际 tab 选中态或可选中按钮状态的示例
- **AND** Web 提供对应真实选中属性的示例

#### Scenario: The advertised indicator marker is independently styleable

- **WHEN** 用户使用判定控件已公开的 marker 选择器
- **THEN** 规则可独立命中实际圆点，不把主值文字一同作为圆点处理
- **AND** 原圆点位置、显隐和业务状态联动保持正确

### Requirement: Effective appearance combines common fields and native rules

系统 SHALL 协调默认外观、通用配置、适用状态及本端原生规则的覆盖关系，让公开支持的用户规则实际生效。系统 SHALL 处理现有 Qt 叶子控件局部样式及 Web 行内外观，不能要求用户为所有声明追加强制覆盖标记。

#### Scenario: A native border rule overrides the common border detail

- **WHEN** 基础配置设置统一边框，用户启用当前对象的左边框加粗规则
- **THEN** 编辑预览和对应运行页显示加粗的左边框
- **AND** 其他基础外观按其适用配置保留

### Requirement: Designer edits use an isolated preview draft

系统 SHALL 在现有 Designer 外观设置流程中提供高级入口、QSS 编辑、启用开关、选择器提示和真实 Qt 预览。确定 SHALL 一次提交本次草稿并支持原撤销；取消、关闭、Esc SHALL 放弃本次弹窗草稿。

#### Scenario: Confirm a QSS draft

- **WHEN** 用户调整 QSS、查看局部预览并点击确定
- **THEN** 原画布中的同一组件显示提交后的外观
- **AND** 用户可通过现有撤销操作恢复本次提交前的外观

#### Scenario: Cancel a QSS draft

- **WHEN** 用户修改弹窗草稿后取消、关闭或按 Esc
- **THEN** 此次弹窗外观草稿不进入工程
- **AND** 打开弹窗前已经提交的其他属性不被错误撤销

### Requirement: Web edits use the existing authoring workflow

系统 SHALL 从现有 Web 属性区提供高级字段、CSS 编辑和真实浏览器局部预览，确认后进入现有撤销与草稿流程。局部预览、原编辑画布、已有预览入口和发布运行页 SHALL 使用一致的样式处理，不要求新增其他预览模式。

#### Scenario: Confirm and publish a CSS change

- **WHEN** 用户确认 CSS 修改、保存草稿并发布页面
- **THEN** 编辑画布、预览与发布页均应用当前 CSS
- **AND** 不产生向 Designer 工程自动回写的操作

#### Scenario: Cancel a CSS change

- **WHEN** 用户取消本次高级外观弹窗
- **THEN** 本次 CSS 草稿不进入已确认的 Web 页面模型

### Requirement: Host controls retain their business ownership

系统 SHALL 在现有页头配置中为开始／停止按钮分别提供高级字段、QSS／CSS 与状态预览。预览 SHALL 不执行任务或导航；实际运行中的命令目标与按钮可用性 SHALL 继续由原宿主逻辑决定。

#### Scenario: Preview disabled and pressed styles

- **WHEN** 用户在作者端查看按钮禁用或按下样式
- **THEN** 只更新预览样本的外观
- **AND** 不启动或停止真实任务

#### Scenario: Apply host button styles in Runtime

- **WHEN** Runtime 加载配置并改变连接或任务状态
- **THEN** 对应按钮显示已配置的适用外观
- **AND** 原开始／停止行为与禁用条件仍正常

### Requirement: Normal data refresh preserves advanced appearance

系统 SHALL 在结果更新、时钟刷新、图片换帧、表格刷新、页签切换及已有选中态变化后保留适用外观，不改变原业务值、映射和动作可用条件。结构化状态外观按当前状态选择，原生规则按声明的优先级应用；不增加配色合规判定，交付示例应能区分不同结果。

#### Scenario: Refresh different component families

- **WHEN** 各代表组件发生一次正常数据或状态更新
- **THEN** 公布支持的字段和原生规则仍正确显示
- **AND** 实际数值、格式、单位与数据绑定保持正确

### Requirement: Custom-painted parts visibly consume appearance settings

系统 SHALL 将已公开的图片视口及 SQLite 列表等自绘部位接入实际样式绘制，使公开配置可观察地生效。不能仅保存代码或设置父容器样式，就声称自绘内容已支持。

#### Scenario: Change the image viewport background

- **WHEN** 用户配置图片视口背景并加载或切换图像
- **THEN** 可见背景按配置显示
- **AND** 图像像素、缩放语义及检测标注保持正确

#### Scenario: Style a SQLite record list with real sample data

- **WHEN** 用户在已有 SQLite 列表模式中配置支持的行外观并切换当前记录
- **THEN** 实际记录行按配置绘制并保留原选择行为
- **AND** 不用空占位提示代替记录行验收

### Requirement: Existing modes determine table styling targets

系统 SHALL 为集合表格和数据库表格的实际表头、内容、已有分页／筛选及选中态提供适用样式。外观扩展 SHALL 不新增原先不存在的选行或数据操作行为。

#### Scenario: Style a table without adding selection behavior

- **WHEN** 当前表格模式原本没有用户选行功能
- **THEN** 仍可配置其实际存在的表头、内容或外框
- **AND** 不为完成选中态示例而新增选行功能

### Requirement: Persistence and publication retain supported style data

系统 SHALL 通过正式共享模型、工程保存、草稿校验和发布投影保留两端样式及启用状态。没有自定义样式的页面 SHALL 按原默认行为显示；新样例 SHALL 使用实施目标版本的正式工程创建和保存路径。

#### Scenario: Save reopen and load the corresponding runtime

- **WHEN** 用户保存工程后重开并在 Qt Runtime 加载，或保存 Web 草稿后重开并发布
- **THEN** 已确认的通用字段、代码和开关保持一致
- **AND** 对应端应用其语言，另一端内容没有丢失

### Requirement: Disable reset and diagnostics are usable

系统 SHALL 支持停用并保留代码；清空只影响当前对象当前语言的代码，保留开关、通用配置和另一端内容。编辑器 SHALL 提供有限基础检查和明确不支持项提示。已识别错误且当前分支启用时 SHALL 不允许确认；停用后可保存文本。错误预览展示当前通用外观并标明代码未应用。

#### Scenario: Disable and re-enable custom styling

- **WHEN** 用户停用代码后保存重开，再次启用
- **THEN** 停用期间使用通用外观
- **AND** 重新启用可恢复之前保存的代码效果

#### Scenario: Unsupported syntax is not silently presented as applied

- **WHEN** 用户使用首版明确未支持的原生规则形式
- **THEN** 编辑器保留输入、提示问题，并在启用时阻止确认
- **AND** 预览标明当前代码未应用；用户修正或停用后可确认
- **AND** 不把有限基础检查描述为完整原生语义验证

#### Scenario: Clear only the current language draft

- **WHEN** 用户清空当前对象的 QSS 或 CSS
- **THEN** 仅该草稿的 code 置空，开关、当前通用外观和另一端样式保留
- **AND** 确认后按当前通用配置显示，取消则保留进入弹窗前的代码

### Requirement: Delivery evidence proves the complete scope

系统交付 SHALL 包含十三类组件和两个宿主按钮的覆盖记录、可编辑工程、Web 页面包、可用样式示例及真实证据，并通过代表对象回放新增字段和图示组合效果。一字段一规则是类型抽样，不能替代新增能力验收；图稿或格式通过不能替代实际结果。

#### Scenario: Review the completed implementation

- **WHEN** 执行者报告本 change 完成
- **THEN** 覆盖表每项均能定位本端实际生效证据与所用配置
- **AND** 代表对象在实际作者端由原入口完成编辑／预览／确认，再保存并进入对应运行端，重现目标配置的可见变化
- **AND** 报告实际 HEAD、环境、工作区差异和未验证项
- **AND** 共享流程按代表对象验证，同一次操作和证据可覆盖多个场景，不逐控件重复全流程或新增全排列测试
