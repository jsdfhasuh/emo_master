# operator-runtime-diagnostics Specification

## Purpose

规范独立端本机 JSONL 日志查看器及共享已发布设备管理弹窗。保持底层日志/设备/任务契约，不重建 Designer 的 runtime-log-workbench。

## ADDED Requirements

### Requirement: Loaded local logs support explicit combined filtering and accurate empty states

系统 SHALL 对当前已加载脱敏记录按级别、模块、关键字及设备取交集筛选，明确显示范围及设备条件，不自动扫描全盘历史。

#### Scenario: Filter loaded records and clear individual or all conditions

- **WHEN** 选择级别、模块、输入关键字或携带真实 deviceId 从设备弹窗进入日志
- **THEN** 当前已加载记录（最多 10,000 条）按四条件交集显示，模块刷新保留选定值，关键字按已知脱敏字段匹配
- **AND** 设备筛选以可见标签和完整 ID 呈现，可单独清除而不改变其他条件
- **AND** 任一条件导致无匹配时显示“当前已加载记录无匹配”等准确提示，区分无记录与读取失败，说明不是完整历史搜索

#### Scenario: Preserve normal tool navigation and explicit reset semantics

- **WHEN** 从起始页“查看全部日志”或日志页“恢复全部”进入/操作，或从业务页/设置页普通进入日志
- **THEN** 前两者清除级别、模块、关键字、设备全部条件；普通进入保留现有筛选和阅读状态
- **AND** 重置筛选不隐式更换文件或轮转跟随模式；设备下钻只设置对应设备条件，保持其他条件并可恢复全部

### Requirement: Local log display and copy retain original sanitized evidence

系统 SHALL 以本机时区呈现可解析时间，并在详情、复制原始 JSON 和导出中保留原始脱敏记录，不回写文件。

#### Scenario: Display time and log severity

- **WHEN** 渲染合法毫秒时间戳或含时区的 ISO 时间戳及日志级别
- **THEN** 时间列按本机时区显示毫秒精度，Tooltip 保留原始时间戳；不明确/无法解析的时间如实回退，不猜测
- **AND** ERROR/WARN 等通过语义色和级别文字区分，详情和导出保留原始脱敏时间

#### Scenario: Copy selected log information

- **WHEN** 选择一条记录并点击复制详情/复制原始 JSON
- **THEN** 只复制该条已经脱敏的只读信息，无选择时动作不可执行
- **AND** 不访问未脱敏源文件以补原始 JSON，不触发执行/重试/日志写操作

### Requirement: Model-based log table preserves reading anchors during refresh

系统 SHALL 使用模型/视图方式避免每次变化全量重建 QTableWidgetItem，并完整保留有界读取、选中和滚动语义。

#### Scenario: Append and evict records near the 10,000-record cap

- **WHEN** 日志可见期间持续追加（含超过上限导致头部淘汰）
- **THEN** 表格采用 QAbstractTableModel 按需提供数据，纯追加增量更新；必要重置时恢复真实记录身份
- **AND** 跟随最新时滚动到底部，关闭跟随时保留仍存在的首可见记录及像素/项目锚点（ScrollPerItem、ScrollPerPixel），同时保留水平滚动、有效选中及详情
- **AND** 已淘汰/筛除的记录不被错配至新行；无变化不重置阅读位置和详情；启动诊断的文本选择和滚动行为保持
- **AND** 既有 2 MiB / 2000 条初读、10000 条上限、半行缓冲、单在途读取、可见性及活动/历史文件规则不变

### Requirement: All existing export scopes capture consistent filter and data snapshots

系统 SHALL 保留当前显示记录、选定文件完整范围、本机全部文件范围三种导出方式，统一应用发起时的组合筛选，保持脱敏、文件范围及取消契约。

#### Scenario: Export visible records or complete file ranges with filters

- **WHEN** 发起任一导出并选择目标文件
- **THEN** 在保存对话框前冻结生效筛选与当前显示记录或文件集合及 endOffset；关键字防抖不造成快照与显示不一致
- **AND** 当前显示范围仅导出当时显示记录，完整文件范围在捕获范围内应用同一四条件且不受 UI 的 10,000 条上限约束
- **AND** 后续修改筛选、追加或轮转不改变已发起导出范围，JSONL/JSON/TXT 保留原始脱敏记录和时间

#### Scenario: Preserve cancellation, overwrite and file export behavior

- **WHEN** 完整文件导出执行、取消、失败或需要覆盖目标
- **THEN** 继续使用已有后台单在途路径、取消、覆盖确认及目标原子替换规则，失败/取消不显示成功或替换旧目标
- **AND** 现有当前显示快照导出不强制重构，导出筛选元数据不得泄露未脱敏敏感输入

### Requirement: Shared device management displays truthful fleet status and preserves controls

系统 SHALL 用左列表、右结构化详情和可折叠原始状态呈现全部已发布设备，复用现有设备状态、控制与诊断契约。

#### Scenario: Inspect a fleet with different device states

- **WHEN** 工程中含多个相机、PLC、TCP Server 或其他已声明类型设备
- **THEN** 左列表展示真实名称（缺失时 ID）、类型、状态文字/色标、完整 ID Tooltip，并以同一分类汇总总数与状态数
- **AND** 已连接/服务运行、未连接/未初始化/已停止、维护、异常与未知依据真实状态区分；正常监听但零客户端不被判定为故障，历史错误不单独推出当前故障
- **AND** 未获取有效状态或读取失败时不编造状态；计数与列表一致

#### Scenario: Inspect and operate a selected device

- **WHEN** 选择设备或状态轮询更新
- **THEN** 右侧显示该完整 ID 对应的真实结构化状态、已知最近错误、可展开只读原始 JSON；选中和滚动位置按真实身份保持
- **AND** 控制按钮只显示声明支持的动作，并依现有 availableControls、availableManagementActions、pending 等规则启用；TCP 通道表和设备诊断保留
- **AND** 设备消失/项目改变时不对旧身份执行操作，普通刷新不丢失同一有效设备选择，不新增自动控制

#### Scenario: Navigate from shared device diagnostics

- **WHEN** Operator Runtime 设备管理中点击查看 Runtime 日志，或非 Operator 宿主使用共享弹窗
- **THEN** 独立端跳转对应真实设备的显式日志筛选；非 Operator 宿主保持其现有设备控制、诊断及日志入口/提示契约
- **AND** 不增加设备后端或协议，不扩大原始状态的敏感信息暴露范围
