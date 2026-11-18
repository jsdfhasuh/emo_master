# 通用生产页面外壳

## Purpose

用同一份页面配置组织桌面和 Web 的简洁页头、导航、运行状态与诊断入口，复用现有宿主控制和结果显示。生产监控与历史回看是独立页面；页面调整不改变 Runtime 执行、设备访问或权限。

## ADDED Requirements

### Requirement: One current operational header

系统 SHALL 使用统一的 `presentation.shell` 显示配置，包含 `title`、`subtitle`、`logoResourceId`、`showConnection`、`showJobState`、`showControls` 和布尔字段 `diagnosticsInitiallyExpanded`。未配置 shell 时也 SHALL 使用本版紧凑默认，诊断初始展开值为 false；标题为空时使用工程显示名，Logo 沿用受管页面图片资源及现有保存、校验和发布路径。

shell 的自定义字段 SHALL 只覆盖当前默认；不按工程版本维护另一套页头。Action、绑定、resultScope 和宿主控制继续复用原机制。

#### Scenario: Configure and reopen a concise header

- **WHEN** 用户配置标题、Logo、状态可见性与诊断初始折叠，保存并重新打开或发布同一工程
- **THEN** Qt 与 Web 按相同配置显示页头和受管 Logo，配置不启动工作流；未声明 shell 的页面也使用同一紧凑默认

### Requirement: Collapsible diagnostics retain actionable summaries

系统 SHALL 用 `diagnosticsInitiallyExpanded` 决定诊断区域初始展开或折叠，并提供随时展开查看的入口。Job 标识、版本、结果身份和资源明细可以收起；影响当前操作的连接或命令错误 SHALL 仍以简短摘要显示。

诊断展开状态 SHALL 只影响显示，不是角色、权限或运行模式，不改变当前任务、控制目标或日志记录行为。

#### Scenario: Inspect a problem from a compact page

- **WHEN** 诊断区域处于折叠状态，当前操作出现错误，用户再展开诊断
- **THEN** 折叠时已有可理解的错误摘要，展开后可查看既有详情；展开动作不改变执行或权限

### Requirement: Host controls reuse existing commands and targets

系统 SHALL 将开始和停止保留在宿主页头的内建操作区，由 `showControls` 控制可见性，复用原宿主命令、实际目标及既有可用性和权限判断。页面 SHALL 不新增启动或停止 Action。

原宿主需要显式选择控制目标时，紧凑页头 SHALL 保留正常选择入口和目标标识。观察对象与控制对象不同时仍应能够区分；切页、冻结和历史选择不隐式修改控制目标。显示操作区不授予原本没有的控制权限。

#### Scenario: Operate the existing selected target

- **WHEN** 操作者通过紧凑页头对原宿主已确定且允许操作的目标执行开始或停止
- **THEN** 使用原有命令与目标处理，每次操作不产生第二条控制链路；随后查看历史不会改变该目标

### Requirement: Navigation follows the existing page definition

系统 SHALL 复用 `presentation.pages`、`pageOrder`、`defaultPageId` 和 `navigation.mode`，使 Qt 与 Web 正确呈现已有的 tabs/buttons、当前页及页面顺序。导航表现使用同一当前实现，不依赖 shell 或 appearance 是否存在显式覆盖值。

当前页面存在显式 `page_navigation` 时，宿主 SHALL 沿用既有规则自动隐藏重复导航；没有显式组件时由宿主提供导航。用户可以有意在长页面上下放置多个显式导航，它们 SHALL 同步同一当前页，不因布局重复增加保存、发布或运行门槛。详情页可以沿用普通导航按钮返回。

交付样例的生产监控与历史回看 SHALL 使用两个独立 Page，切页只切换显示，不重启检测或写入 PLC。

#### Scenario: Use page navigation at two positions

- **WHEN** 当前页上下各放置一个显式 page_navigation，用户通过其中一个切换生产监控与历史回看
- **THEN** 两个组件使用同一页面列表和激活状态，宿主不再重复展示导航，工程可以正常使用

### Requirement: Host status remains live during result review

系统 SHALL 允许无 bindings 的现有 `runtime_status` 通过可选 `props.statusField=connection|job_state` 直接读取宿主连接或当前观察任务的真实状态。该字段不新增 DataSource、不进入工作流采集，也不需要全局变量转发；使用普通 bindings 的组件继续从该绑定显示数据，不按工程版本区分。

宿主状态 SHALL 在冻结结果和历史回看期间保持实时。没有可用任务或连接状态时应明确显示无任务或不可用，不根据产品判定和历史记录推导运行状态。

#### Scenario: Freeze a result while the host changes state

- **WHEN** 用户冻结已显示结果，宿主状态随后变化
- **THEN** 被冻结的业务内容保持不变，connection/job_state 仍反映当前宿主状态

### Requirement: Completed results keep their invocation identity

系统 SHALL 保留现有输出绑定、resultScope 成组提交与图像就绪机制，使检测图、方向、该次请求和设备回执对应同一次结果。另一区域显示当前轮询请求时 SHALL 按其实际来源标明，不把新请求拼入旧结果卡。

冻结和 displayed_result 详情导航 SHALL 使用用户实际看到的已提交结果；恢复实时只恢复显示。历史选择属于当前查看会话，不替换生产结果或发送生产命令。缺失回执 SHALL 使用明确中性显示，不继承上一件成功回执，也不由任务完成推导已确认。

#### Scenario: Update and review one complete result

- **WHEN** 新结果的图片尚未就绪，用户查看或冻结当前已显示结果，然后恢复实时
- **THEN** 图像与方向、对应请求、回执始终成组；恢复后显示最新已完成的一组，宿主继续运行，历史浏览不改写该组数据

#### Scenario: A result has no acknowledgement

- **WHEN** 本次图像和方向已经形成，但没有可用设备回执
- **THEN** 本次可用内容仍可显示，回执以该控件的中性 emptyText 显示，不能冒充业务成功或沿用上一次确认
