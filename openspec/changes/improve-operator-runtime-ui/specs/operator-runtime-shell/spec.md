# operator-runtime-shell Specification

## Purpose

规范独立操作员端 `OperatorWindow` 的常驻宿主外壳、全局与流程级控制、运维导航、状态反馈、最小共享外观与 DPI、起始/设置页和本机偏好。与现有 `PageShell` 契约协同生效；不修改 Runtime 协议、工程格式、显式启停或资源释放。

## ADDED Requirements

### Requirement: Persistent operational shell is independent of business pages and technical diagnostics

系统 SHALL 在 `contentStack` 上方提供常驻顶栏与按需上下文横幅，独立呈现日常运维入口和真实运行状态；工具页切换 SHALL 不重建业务页面或改变既有运行会话。

#### Scenario: Navigate tools while a paged project is running

- **WHEN** 单流程有页面工程正在运行，操作员进入日志或设置并按工具历史返回
- **THEN** 顶栏保留工程名、当前真实状态、停止入口及返回入口
- **AND** Job、RuntimePages、DisplaySession、DisplayHub 的对象身份及当前业务分页、冻结/历史回看状态保持不变，不触发新加载或启停
- **AND** 普通工具返回不等同于退出“查看已有运行”，已有焦点恢复与工具历史继续有效

#### Scenario: Reach operational tools with diagnostics collapsed

- **WHEN** 工程诊断区处于折叠状态（含 `diagnosticsInitiallyExpanded=false`）
- **THEN** 可从顶栏访问选择工程、重新加载、更新工程、运行目标、查看已有运行、设备、日志与设置，动作仍受原可用条件约束
- **AND** 技术诊断区只呈现实际连接、任务执行/资源、画面适配及结果身份等技术信息，初始展开规则不变

#### Scenario: Preserve startup and load-failure recovery

- **WHEN** Runtime 尚未就绪、初始化失败或记忆工程无效
- **THEN** 可访问原有设置、日志、关闭及适用的重试/选择工程出口
- **AND** 不把失败伪装成已运行，不使开始按钮成为可执行任务入口

### Requirement: Global control docking preserves existing workflow actions, appearance and enablement

系统 SHALL 保留同一对全局启停按钮实例及其原有执行链路，并保留所有既有流程级控制；工具页的停止可见性 SHALL 与业务页控制隐藏规则解耦，但不绕过原可用性约束。本变更只保留现有运行控制及选择能力，不新增或替换运行选择窗口。

#### Scenario: Dock global buttons between business and tools

- **WHEN** 单流程有页面工程从业务页进入日志或设置，再返回业务页
- **THEN** 原全局按钮迁至宿主顶栏，工具页采用宿主标准文字、图标、尺寸和样式
- **AND** 返回业务页后恢复原 `PageShell` 作者配置和 `showControls` 规则
- **AND** 定时状态刷新不使停靠位置、可见性或样式错误回退，不产生第二条命令链路

#### Scenario: Keep software Stop reachable even when business controls are hidden

- **WHEN** `presentation.shell.showControls=false` 且存在活动运行或原状态可停止，操作员进入工具页
- **THEN** 顶栏停止入口仍可见，业务页控制配置不被修改
- **AND** 在原状态允许停止时可经既有 handler 停止；busy、closing、停止等待等原禁用条件仍有效并呈现原因/状态
- **AND** 返回业务页恢复原隐藏规则；不因此暴露原本隐藏的开始动作，不将该操作描述为硬件急停

#### Scenario: Preserve global and workflow-specific control semantics

- **WHEN** 同时存在多个工作流，或一个工作流存在多个活动 Job
- **THEN** 全局启动仍作用于既有已选流程，全局“全部停止”仍作用于原全局范围，流程级按钮仍只操作其对应流程
- **AND** 单活动 Job 仍按原路径直接停止，不增加选择或确认步骤；多个候选沿用现有选择弹窗和完整 Job ID/归属校验，不以当前显示页或观察目标替代控制目标；取消仍不发命令
- **AND** 切换工具页及正常页面销毁重建不丢失全局按钮，也不删除 `workflowControls` 或创建重复信号绑定

#### Scenario: Keep essential controls within the visible area

- **WHEN** Windows 100% 或 150% DPI、较窄窗口、长工程名或长自定义按钮文字导致顶栏空间有限
- **THEN** 优先保留停止及返回可达性，其余文字可省略并有完整 Tooltip，次要入口可紧凑化/溢出或合理分行
- **AND** 不依赖固定像素高度或强制超屏窗口，业务页自定义外观仍不被覆盖

### Requirement: Contextual status feedback reflects actual operation and observation state

系统 SHALL 以现有状态为唯一事实来源，在顶栏显示简明真实状态，并按需呈现错误、观察/控制对象区别和忙碌反馈，不增加独立状态账本。

#### Scenario: Display faults, viewing context and in-progress operations

- **WHEN** 正常就绪/运行且无额外上下文，或发生实际错误、查看已有运行、加载/更新/停止等待
- **THEN** 正常态横幅隐藏；特殊态显示真实摘要、适用的错误详情/复制、观察与停止控制目标、返回运行入口或不确定进度
- **AND** 错误或忙碌反馈不会误导观察/控制目标；操作结束后等待提示按真实结果结束，不伪造进度或成功

### Requirement: Scoped shared appearance and high-DPI support retain the standalone boundary

系统 SHALL 采用必要的共享字体、浅色色板、离线图标与屏幕边界辅助实现一致外观，且不得引入 Designer 运行依赖或覆盖工程作者配置。

#### Scenario: Launch a standalone Qt process

- **WHEN** 由独立入口创建新的 QApplication
- **THEN** 创建前启用支持的高 DPI 属性，启动后应用适合宿主的 Fusion/浅色外观、中文字体回退与离线图标
- **AND** RuntimePages 的自定义 PageShell/QSS、组件样式及流程级按钮外观保持原契约
- **AND** 独立入口不导入 `emo_master.apps.designer.*`；现有 `--check` 非执行行为保持不变

#### Scenario: Share only needed UI utilities

- **WHEN** 抽取共同字体、色板、图标或屏幕适配能力
- **THEN** Designer 原有调用、专属样式和资源继续正常工作，不因本变更重写整个 Designer 主题
- **AND** 无新增网络依赖或强制技术栈迁移

### Requirement: Startup and listener settings keep effective values separate from next-start drafts

系统 SHALL 保留本机存储/远程监听设置原有生效时机与正常错误出口，并准确显示当前实际/目标值、已保存值及未保存草稿。

#### Scenario: Edit and save listener settings during a run

- **WHEN** 修改局域网开关、监听 IP、固定端口或展示订阅数
- **THEN** 与已保存值不同的草稿立即标明未保存，改回已保存值清除提示；状态轮询和工具页往返不覆盖草稿
- **AND** 成功保存提示下次启动生效，失败保留草稿和真实错误，本次监听与任务不变
- **AND** 不重建监听、停止任务、自动修改防火墙或迁移目录

#### Scenario: Explain startup overrides without inventing provenance

- **WHEN** 启动参数实际覆盖已保存监听设置，或本次有效值与下次设置不同
- **THEN** 有真实来源证据时标明启动参数优先及其生效字段
- **AND** 不将所有当前/保存值不同都标成 CLI 覆盖，缺乏来源时只说明本次启动有效值与下次生效规则

### Requirement: Recent projects and window state remain local conveniences

系统 SHALL 仅在本机偏好记录最近成功工程和正常关闭窗口状态，不改变显式工程选择、在线恢复和工程格式。

#### Scenario: Open a recent successful project

- **WHEN** 起始/设置页有最近成功工程（最多五个，按成功加载倒序去重）
- **THEN** 显示工程名、路径及完整 Tooltip，可复制真实路径，点击走原正常加载入口并遵守 `canLoad`
- **AND** 仅在加载成功时更新最近列表及原有 `projectPath`；取消、文件缺失或加载失败不加入列表，不自动启动旧任务或静默回退
- **AND** 旧偏好及未传参启动的既有在线恢复规则继续有效

#### Scenario: Restore a window on the available screen

- **WHEN** 调整窗口或最大化后正常关闭，再次启动独立 Runtime
- **THEN** 恢复保存的正常窗口几何及最大化状态，并按当前可用屏幕范围收敛
- **AND** 无可用偏好时使用原默认 1060×800（受可用屏幕边界约束），不修改 Designer 窗口偏好
