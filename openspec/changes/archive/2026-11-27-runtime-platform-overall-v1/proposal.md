# Change: Runtime 总体能力与 Operator 运行端完善（V1 R3）

## Why

用户需要视觉统一、操作连贯的 Runtime 运行端，并让 Operator 具备 Designer 已有的 Job 排障能力。Runtime 总体职责不变：执行、工程配置、Job、设备、结果和日志都由现有 Runtime 核心管理。

当前 Operator 起始/设置和工具页需要统一外观，Job 工作台尚需接入。基线审查发现，工作流占满并发名额时 Operator 与 Runtime 的启动判断可能不同，返回另一个同工作流 Job 后也可能显示错误的生产页。R3 将两处修正、系统事件入口、停止目标与导出范围写进任务及验收，沿用 R2 按影响选择回归的范围。

## What Changes

- 复用 LIGHT_THEME，完善 Operator 起始/设置、主工具栏、日志和设备窗口外观；保留页面栈、返回/焦点、业务回调与工程自定义 QSS。
- 将 Designer 日志工作台需要的中立模型、Qt 组件和最小读取能力共享给 Operator。Designer 保留完整 RuntimeClient，不迁移无关编辑、发布、预览或调试服务。
- 在现有日志工具提供 Job、Runtime 系统事件和本机文件三个可达入口。Job 工作台支持执行树、循环、筛选、同源详情/JSON 和设备诊断往返。Operator 的日志目录只观察任务，生产页继续执行显式停止。
- 导出所选 Job 时可保存点击当时已加载且符合当前筛选的事件，也可固定上界分页导出仍可查的整个 Job。两种方式复用现有文件写入与取消能力。
- 保留 operational/application 两类本机 JSONL、fallback、轮转、历史固定文件和导出取消。
- 每个宿主复用一个实时日志读取驱动；Designer 与 Operator 可独立观察同一 Runtime，保留既有持久化及 JSONL 投影。
- 将 Runtime 对目标工作流的轻量准入用于 Operator 手动开始与既有 `production.autoStart` 回调，完整预检由 StartJob 执行。包更新沿用更新后的正式配置。切换查看另一个 Job 后，返回时恢复原 Job、原生产页和结果来源。
- 仅在实际变更影响配置传输、执行器、设备通信、历史或 Web 共享契约时追加相应定向回归，复用现有测试与证据。
- 纠正宿主来源范围与原型完成度，删除固定测试配额和无依据的长时/尺寸组合要求。

## Capabilities

### New Capabilities

- `runtime-platform-overall`：记录 Runtime 总体职责与本期操作边界；既有内部约束不自动产生全域重验任务。
- `operator-runtime-visual-shell`：Operator 自有页面外观、导航/回调保留、局部 QSS 与必要原生 Qt 验证。

### Modified Capabilities

- `runtime-log-workbench`：明确原有“两类一级来源”属于 Designer；增加 Operator 宿主接入、每宿主读取与本机 JSONL 保留。原 Designer 行为继续保留。

## Scope and Inheritance

承接 Operator V3 的视觉、返回、QSS 隔离与原生 Qt 要求。此前“不接入 Designer Job 工作台”的范围被本次明确接入需求替代。

任务按“本期必做 / 按实际影响触发 / 保留约束”标记。新增日志能力、视觉改版及日常主链属于本期必做；未触及的后台任务、Dispatch、TCP、资源传输和历史内部实现保留现有合同。触及相关源码或调用关系时选用既有定向测试。

## Impact

- 主要改动：apps/operator_runtime、实际需要共享的日志模型/UI/读取适配、Designer 日志入口内部 import/注入。
- 完整客户端在 apps/designer/services/runtime_client.py；事件转换来自 apps/designer/page_designer/runtime_logs.py。clients/runtime/calls.py 只提供取消/期限基础。
- 不预先修改 Runtime 核心或增加 RPC；可复现缺口或直接依赖证明必要时做局部修复。
- Designer 可见界面和既有行为保留；Operator 与共享模块不导入 apps.designer。
- Web 无实现或共享契约影响时不新增独立手工验收门槛，仓库既有必需 CI 不变。

## Non-goals

- 不重写 RuntimeService、JobSupervisor、WorkflowRunner、DeviceManager、PresentationService。
- 不新建 Job/设备状态账本、健康状态服务、通用 SDK、跨进程订阅中心、第二套日志存储或导出器。
- 不增加完整预检副本、全局锁、额外启动门槛或无限重试；不以禁用正常流程掩盖缺陷。
- 不增加工程/设备定义编辑器、全局一级导航、固定 Dashboard、周期调度器、首次装包向导或第二个自动运行开关。
- 不改变循环、Dispatch/Join、配置发布与正常取消语义。
- 不建设 Runtime MCP、公网服务或 Web 全功能对齐，不迁移 PySide2 / Qt Widgets。
- 不新增全仓测试要求、82 算子逐个真实运行、长时压力专项、分辨率/DPI 全排列；真实相机/PLC、冻结包和现场节拍不作为常规软件验收前置。

## Expected Outcome

Operator 外观统一、运行主链连贯、Job 日志可排障、原有本机日志能力保留，Designer 共享后不退化。本期必做项及实际触发项得到有效验证后完成；未影响的既有约束注明保留与本轮未重跑，不借历史结果宣称本轮 PASS。
