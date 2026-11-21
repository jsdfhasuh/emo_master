# Runtime 运行日志工作台重构 · V2.3 算子覆盖补齐

## Why

当前 Designer 日志窗口把实时记录、Runtime 历史、本机文件、历史查询条件、实时筛选、分类标签、十列表格和底部 JSON 详情放在同一窗口中。工程师需要先理解日志来源和筛选项，再从平铺事件中还原一次运行；长时间运行、While / ForEach、多层子工作流和设备诊断时尤其不直观。

本 change 将现有日志窗口调整为任务优先的运行日志工作台：先找到一次 Job，再按真实工作流调用、循环迭代和节点执行查看事件；Designer 本机日志保持独立。设备诊断作为运行任务中的进一步排查入口，不再增加一套重复的 Runtime 日志来源。

基线日志详情已有结构化 JSON 树，工程师仍需从技术字段中寻找业务内容。V2.2 的主动日志易读层不能覆盖大部分没有直接 logger 调用的算子。V2.3 将已存在的节点生命周期 ioSummary / metrics / diagnostics 设为必需通用展示，主动日志作为附加；同时把单算子调试已有日志和结果接入同一展示能力。此次只补齐设计、原型和交付包，未实施产品、未进行 Qt / Runtime 验收。

## What Changes

- 一级入口只保留 **Runtime 运行日志** 与 **Designer 本机日志**；运行任务是 Runtime 日志内部的主要视图，不再与 Runtime 日志并列。
- Runtime 首页分为“正在运行”和“最近已结束”，并提供更早历史查询。
- 单个 Job 详情包含运行概览、执行结构、事件日志；默认按执行结构组织，允许切换时间顺序。
- 执行树按 `workflowRunId`、`parentWorkflowRunId`、`nodeRunId`、`iterationPath` 组织工作流调用、子工作流、While / ForEach / Repeat 迭代和节点事件；While 最后一次 false 条件检查不算作循环体执行轮次。
- 运行任务详情先加载最近原始事件页，再补取实时增量，并支持固定上界下向更早分页及有界窗口；保留现有服务端高级历史查询，完整 Job 导出不受本地过滤限制；展示 Job、工作流调用、循环迭代和节点的开始时间、结束时间及执行耗时；时间不完整时明确标记，不推测。
- 高级筛选默认收起，保留等级快捷筛选、搜索、条件摘要、筛选上下文和右侧详情抽屉。
- 算子详情必须直接展示当前选中 started / completed / failed / skipped 的真实输入输出摘要、指标、诊断和原因，不依赖算子主动打日志；node.log.payload.data 是附加记录。共用字段 / 对象 / 数组、长文本和复制能力，保留 LogJsonTree，不猜字段单位、完整输出或历史图片。
- 单算子调试窗口复用同一 Qt 详情组件或格式化适配点；ReadOperatorDebugEvents 的会话 wrapper 与 GetOperatorDebugExecution 的执行结果明确分开。沿用已有轮询、身份与结果组件，不造 node.completed 或生产 Job，不新增调试 RPC / 存储。纯 / 实时预览仅兼容已有失败摘要和 payload.logs，不建立成功预览持久流。
- 设备诊断沿用现有 `GetDeviceDiagnostics` 和设备诊断缓存，从任务中的设备事件继续查看；不把缓存记录再次复制进 Runtime 事件树。
- Designer 当前会话与本机历史文件继续使用现有 `DesignerLogWriter` 和本机文件读取能力。
- 复用现有 `jobEvents` 与 `jobs` 表。为跨 Runtime 重启的已结束任务目录增加只读分页查询，不新建任务表或日志存储。
- 补齐 Designer 对父工作流关联字段的保留；循环类型复用现有 `mode`，只补充准确可知的总量及 While 条件检查是否进入循环体的信息。
- 统一设备 `executeDevice` 与 `captureDevice` 的执行上下文关联，并在DeviceDiagnostics.append 形成记录前集中归一化嵌套 source，使设备诊断能准确回到 Job、工作流调用、节点和循环路径。

## Capabilities

### New Capabilities
- `runtime-log-workbench`：Designer 中 Runtime 任务优先的日志查看、执行树、执行时间、通用执行与主动日志易读详情、单算子调试共享展示、设备诊断联动和 Designer 本机日志入口。

### Modified Capabilities
- 无。本 change 以现有日志整合能力为基础，不重新定义算子日志 API、Runtime 事件持久化规则或设备日志产生机制。

## Impact

- 基线：`jsdfhasuh/emo_master`，本次核查固定提交 `9d59fd16a1d0d3ec0c60725c839ff38183d6916b`。
- Designer：`apps/designer/ui/log_dialog.py`、`ui/log_json_tree.py` 的既有检查能力、`ui/operator_debug_window.py`、`services/operator_debug.py` 既有数据读取接线、`services/global_logs.py`、`services/runtime_client.py`、`services/local_log.py`、节点结果日志入口及相关 UI。V2.3 算子覆盖增量在共享展示与接线，不据此重写 Runtime 日志生产者或调试协议。
- Runtime：`jobs/read_model.py`、`jobs/repository.py`、`context/sqlite_store.py`、`grpc_server/service.py`、`workflow/loop_runner.py`、`execution/operator_executor.py`。
- 设备诊断：沿用 `devices/diagnostics.py`、`devices/manager.py`、`ui/communication_panel.py`、`apps/designer/ui/device_workspace.py`。
- 协议：新增历史任务只读查询所需的 proto / SDK / Web 契约镜像生成物；本 change 不改 Web 日志界面。
- 不新增日志中间件、事件数据库、设备日志数据库、WebView 或独立日志服务。
- 不给所有算子批量添加 node.log，不修改日志限流 / 等级策略，不扩大单算子调试的既有准入范围，也不为界面补造完整输入输出或全轮图片保存能力。
