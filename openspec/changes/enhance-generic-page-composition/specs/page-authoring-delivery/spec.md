# 页面创作、当前格式与正式交付

## Purpose

V1.3收尾继续使用当前正式格式；已交付能力复用，只补已确认的创作和发布断点。

以当前正式格式实现并交付页面增强。用户没有需要迁移的既有工程，本轮不承担旧工程、旧页面包或旧 SDK 兼容；Designer、Runtime、Web 和生成物同步采用同一当前契约。交付一个可继续编辑的双页工程，实际展示模拟生产结果写图、写库后在历史页查看同一条记录。

## ADDED Requirements

### Requirement: One current project and presentation format

系统 SHALL 以 Project `2.10` 与 Presentation `1.3` 作为当前创建、读取、编辑、保存和发布格式。正式入口和生成器 SHALL 统一生成该组合；资源、生产设置、设备、变量、Dispatch、配置导入及输出采集能力继续在该格式中可用。

系统 SHALL 不为本轮增加旧工程迁移、按属性启用触发版本升级、版本撤销或新旧格式双轨。当前模型的默认值与序列化规则 SHALL 一致服务当前工程和页面包，不以维持旧包摘要为目标。

#### Scenario: Create and reopen a current project

- **WHEN** 用户通过正式新建入口建立工程，配置工作流和页面，保存后重新打开
- **THEN** 工程使用 2.10/1.3，资源、设备及工作流配置完整，页面与工作流可继续编辑和使用

### Requirement: Page and component settings use normal edit transactions

Designer 和 Web 编辑器 SHALL 在已有页面设置中提供 appearance 与 shell 入口；组件属性面板 SHALL 提供表面、文字角色、状态、图像及历史交互配置。修改 SHALL 使用现有工程模型、编辑事务和 undo/redo，不要求手工编辑 JSON 或新建草稿系统。

属性组 SHALL 支持“使用当前默认”和“自定义”，并允许恢复当前默认。未指定样式 SHALL 使用本版主题与组件默认值，不经 cardStyle、旧字体属性或旧外观分支回退。属性修改、撤销、恢复默认及保存 SHALL 保留用户配置意图，不改写无关绑定。编辑画布和预览 SHALL 遵循 page-composition 的业务内容测量规则。

#### Scenario: Configure undo and reopen page settings

- **WHEN** 用户在页面设置修改标题、Logo、内容宽度，在组件面板设置卡片和主值样式，执行撤销与重做后保存重开
- **THEN** 编辑操作正常撤销和恢复，保存结果保留最后确认的配置与绑定，实际预览显示相应结构

#### Scenario: Restore a style group to current defaults

- **WHEN** 用户把自定义 typography 或 surface 属性组恢复为使用当前默认
- **THEN** 该组使用本版主题和组件默认值，其余属性与绑定保持，保存重开后仍表达该选择

### Requirement: Existing authoring operations expose the current schema

已有 `presentation.configure`、`page.configure`、`component.put`、`component.bind` 等创作操作 SHALL 使用同一当前正式模型与 Schema，使字段可发现、配置、校验和保存。系统 SHALL 复用原编辑操作，不为具体业务页面新增专用命令，也不在 HTTP/MCP 端复制另一份属性规则。

#### Scenario: Configure a component through an existing operation

- **WHEN** 客户端查询创作能力并通过 component.put 设置带当前样式的普通组件
- **THEN** 返回的 Schema 包含实际支持字段，修改经过同一正式模型，重新读取及正式导出保留该配置

### Requirement: Current panels use one generated contract

当前页面包 SHALL 使用 Panel Contract `1.0`、`published-1` 和 `emo-web-panel-1` 的正式封装，明确 `sourcePresentationVersion=1.3`，并按实际使用声明 `presentation.composable-v1`、`history.record-selection-v1`、`shell.operational-v1`。正式投影 SHALL 保留表现和本地引用，继续剥离 Runtime 私有资源路径，当前生成与读取的完整性校验一致。

Designer、Runtime、Web 的 SDK、生成 Schema、TypeScript 和打包消费者 SHALL 同步使用同一当前交付，使配置实际可加载和渲染。系统 SHALL 不为旧 SDK、旧响应结构或旧页面包维护并行消费路径。

#### Scenario: Publish and open the two current pages

- **WHEN** 用户从正式双页工程生成 .emoweb，并用本次交付的 Web 打开
- **THEN** 样式、页头、历史记录引用和动作实际生效，当前包的读取与完整性校验通过
- **AND** 浏览器配置不包含 Runtime 本机数据库或图片路径

### Requirement: One editable example connects production to stored history

本变更 SHALL 交付一个正式可编辑的 2.10/1.3 `.emoproj`、必要资源和由它发布的 `.emoweb`。工程 SHALL 包含生产监控和历史回看两个 Page，并以三个明确标记的模拟业务状态展示通用表达能力。

样例 SHALL 使用既有 ImageSaver（`vision.io.image_saver`）保存同次主图和局部图，再由既有 SQLiteWriter（`vision.io.sqlite_writer`）将实际图片引用和同次结果字段写入声明的历史表。历史页 SHALL 通过现有历史查询及图像引用路径查看这些真实新增记录；初始数据库可以准备表结构和视图，不能用与生产过程断开的预置记录代替该链路。图片和标量 SHALL 能通过同次流程身份及稳定记录键关联。

本轮 SHALL 使用这一个双页工程完成编辑与复用展示，不要求新增 palette 预设引擎或另一测量工程。通用性 SHALL 通过在同一工程中更换一次字段、单位和状态映射证明。

#### Scenario: Find a completed simulated result in history

- **WHEN** 用户运行一次明确标记的模拟生产，主图和局部图已保存且记录写入完成，随后进入历史页刷新并选中该条记录
- **THEN** 历史主图、局部图和结果字段对应刚完成的同次生产数据；查看记录不重新运行检测或触发设备命令

#### Scenario: Edit and reuse the delivered pages

- **WHEN** 用户在交付工程中更换一个测量字段、单位及状态映射，保存重开并查看 Qt 和 Web 页面
- **THEN** 修改保留，普通组件和布局继续可用，不依赖特定工位控件或仅用于效果图的 HTML


### Requirement: Web copies retain declared Runtime history sources

系统 SHALL 在当前创作模型提供 props.historySource：componentId引用已有Runtime来源，选中行图片的sourceComponentId引用同页表格实例。引用与实际来源声明、生产绑定、静态图片互斥；原生Designer保持拥有来源声明的语义。

Web SHALL 为显示实例生成UI ID；复制保留来源ID，重映射复制集合内的页内关联。发布 SHALL 复用既有Published history，不新增目录或RPC。

网关 SHALL 先解析获准UI实例，再按Runtime来源查询；图片按Runtime来源查找imageRefs，选择状态按UI实例隔离。纯模型校验形状与页内关系，现有发布上下文校验目录成员、列和图片父表。再次编辑 SHALL 从active包恢复原来源目录。

#### Scenario: Copy a history page and publish it again

- **WHEN** 用户从当前Runtime已声明来源添加历史表及主图/局部图，复制包含记录字段与历史按钮的页面，保存重开并正式发布，随后再次打开编辑器发布
- **THEN** 两页各自选择记录，主图、局部图、字段及翻条按钮都跟随本页表格，RPC仍访问原声明来源，重新编辑和发布可完成
- **AND** 复制没有新增数据库来源、RPC或生产采集，也没有使原页面选择被复制页抢走
