# Runtime 总体源码与证据地图（R3）

**固定基线：** `48b30191884ad5ef5d96e8d26591104d16d54a58`。

**证据层次：** 下表定位固定提交的代码和既有用例。R2 可行性审查还执行了启动许可的内存反例、双 Job 返回页面的 Qt 离屏软件验证，条件见[修订记录](review-notes.md)。R3 的产品验收仍待执行。

## R3 五项修改的入口

所有行号均对应上述固定提交。

| 行为 | 当前实现与实施位置 | 现有验证如何复用 |
| --- | --- | --- |
| 手动和自动启动许可 | `apps/operator_runtime/controller.py:388–408` 产生工作流状态，`apps/operator_runtime/main.py:786,986` 消费它。Runtime 的 `apps/runtime/jobs/read_model.py:66–81` 已提供准入结果与原因。这里的 `apps/` 均相对于 `src/emo_master/`。 | 验收 A 覆盖另一工作流占满与释放名额。验收 B 复用 `tests/ui/operator_view/test_production_window.py::testWindowUpdatePreservesOutputsAndUsesNewAutoStartPolicy` 核对包更新后新增的 Job 数。 |
| 跨 Job 返回原生产页 | `src/emo_master/apps/operator_runtime/main.py:552–585,603–638` 保存与恢复页面。`src/emo_master/ui/presentation/renderer.py:813–837` 的状态包含页面 ID。单工作流的两个视图当前共用保存位置。 | `tests/ui/operator_view/test_run_view_selection.py` 有同工作流双 Job 夹具。给 A、B 选不同页后，核对返回页及实际结果的 Job 身份。 |
| 系统事件查询入口 | `src/emo_master/apps/designer/ui/log_dialog.py:1507–1526,1593–1597,1618–1619` 分别创建系统事件视图、注入客户端、打开时查询。 | 验收 F 从 Operator 打开该入口，以一条无 Job 的事件和一条属于 A 的事件核对来源。 |
| 停止动作归属 | `src/emo_master/apps/designer/ui/runtime_job_logs.py:207,667–671,778–788` 创建、显示并执行 Designer 目录的停止动作。Operator 日志宿主只接观察能力。 | `tests/ui/operator_view/test_run_view_selection.py::testViewingConcurrentRemoteRunKeepsExplicitStopSelection` 已断言保持查看 B 时从生产控件停止 A 后，B 仍为 RUNNING。 |
| 两种 Job 导出 | `src/emo_master/apps/designer/ui/runtime_job_logs.py:228,1182–1184,1249–1263` 包含 Job 导出入口与视图筛选。`src/emo_master/apps/designer/ui/log_dialog.py:912–919` 的普通表筛选导出使用另一份缓存。 | `tests/designer/test_operational_log_integration.py::testNativeWorkbenchHistoryAndWholeJobExportAfterRestart` 有整 Job 导出基础。按验收 F 检查两个输出文件的事件 ID，覆盖同级别的其他 Job 记录和本 Job 尚未载入的记录。 |

## 1. Operator、宿主与外观

以下路径均相对仓库根目录。

| 路径 | 职责 / 本期关注 |
| --- | --- |
| `src/emo_master/apps/operator_runtime/main.py` | OperatorWindow、QStackedWidget、工具导航、工程/启停/查看/设备入口、technicalHost 挂载 |
| `src/emo_master/apps/operator_runtime/controller.py` | ProductionRuntime、Runtime/Presentation owner、工程加载、Job 和关闭 |
| `src/emo_master/apps/operator_runtime/startup_page.py` | 起始页、目录与监听设置、920px 布局切换 |
| `src/emo_master/apps/operator_runtime/settings.py` | 本机目录与监听配置优先级 |
| `src/emo_master/apps/operator_runtime/log_view.py` | LogFeed、RuntimeLogView、本机 JSONL 读取和 UI |
| `src/emo_master/apps/operator_runtime/log_reader.py` | 文件枚举、JSONL 解析和文本读取 |
| `src/emo_master/apps/operator_runtime/validation.py` | 独立 Operator/source-entry 验证入口 |
| `src/emo_master/ui/presentation/appearance.py` | 现有主题到 Qt 外观辅助 |
| `src/emo_master/ui/presentation/native_style.py` | 局部 QSS 作用域 |
| `src/emo_master/ui/presentation/shell.py` | 页头和宿主控件外观 |
| `src/emo_master/ui/device_management.py` | 已发布设备状态/动作窗口 |
| `src/emo_master/ui/communication_controller.py`、`src/emo_master/ui/communication_panel.py` | 共享通信状态/诊断；Operator 样式优先限定在自身对话框 |

## 2. Runtime 核心：保留并按实际影响选择回归

| 路径 | 已有职责 |
| --- | --- |
| `src/emo_master/apps/runtime/grpc_server/service.py`、`src/emo_master/apps/runtime/grpc_server/aio_entry.py` | Runtime RPC 组合与传输 |
| `src/emo_master/apps/runtime/main.py` | 独立 RuntimeServer 启动 |
| `src/emo_master/apps/runtime/jobs/manager.py`、`src/emo_master/apps/runtime/jobs/supervisor.py` | Job、Worker 与资源生命周期 |
| `src/emo_master/apps/runtime/jobs/read_model.py` | RuntimeInfo、ListRuns、ListJobHistory、RunState 与轻量 startAvailability |
| `src/emo_master/apps/runtime/workflow/runner.py`、`src/emo_master/apps/runtime/workflow/loop_runner.py`、`src/emo_master/apps/runtime/workflow/subflow_runner.py` | 工作流、循环与子流程 |
| `src/emo_master/apps/runtime/execution/operator_executor.py`、`src/emo_master/apps/runtime/execution/background_tasks.py` | 算子执行与 Job 后台任务 |
| `src/emo_master/apps/runtime/frame_dispatch/` | Dispatch/Join/帧调度 |
| `src/emo_master/apps/runtime/project_config/rpc.py` | 正式配置校验、提交、资源转移 |
| `src/emo_master/core/project/configuration.py`、`src/emo_master/core/project/runtime_package.py` | 正式配置与 Runtime 工程包 |
| `src/emo_master/apps/runtime/devices/manager.py`、`src/emo_master/apps/runtime/devices/diagnostics.py` | owner、设备代次、串行控制、诊断缓存 |
| `src/emo_master/apps/runtime/communication/manager.py` | 工程通信服务 |
| `src/emo_master/clients/runtime/devices.py` | 设备调用客户端 |
| `src/emo_master/apps/runtime/presentation/service.py`、`src/emo_master/apps/runtime/presentation/history.py`、`src/emo_master/apps/runtime/presentation/output_requests.py` | 结果、历史/图像和页面输出 |
| `src/emo_master/ui/presentation/hub.py`、`src/emo_master/ui/presentation/renderer.py` | DisplayHub 与 RuntimePages |
| `src/emo_master/clients/runtime/display_session.py`、`src/emo_master/clients/runtime/history.py` | 观察会话与历史客户端 |

`read_model.py::startAvailability` 的现有说明明确完整预检只在 StartJob。GetRuntimeInfo 不是新的多组件健康检查服务。UI 映射现有状态，不增加启动门槛。

## 3. 日志事实来源

| 路径 | 职责 |
| --- | --- |
| `src/emo_master/apps/runtime/events/event_store.py` | Job 事件存储、顺序与 sink 发布 |
| `src/emo_master/apps/runtime/events/jsonl_writer.py` | 已有事件 JSONL 投影 |
| `src/emo_master/apps/runtime/events/app_log.py` | Runtime 应用日志 |
| `src/emo_master/core/log_export.py` | 现有导出与范围/取消能力 |
| `proto/runtime.proto` | 现有 Job、日志、设备、算子声明与结果 RPC 合同 |

本机 LogFeed 优先 `runtime.operationalLogWriter`，不可用时沿用 application writer fallback。listLogFiles 同时枚举 `runtime-*.jsonl` 与 `runtime_app-*.jsonl`。接入 Job 工作台不能移除两类文件原有能力。

既有 EventStore→JSONL 投影保留。每宿主复用一个实时读取驱动，不新增客户端落盘；两个客户端可以各自观察同一 Runtime。

## 4. Designer 工作台与本次最小抽取

| 路径 | 当前真实职责 |
| --- | --- |
| `src/emo_master/apps/designer/ui/runtime_job_logs.py` | Job 目录、执行树/时间视图、实时和历史窗口 |
| `src/emo_master/apps/designer/ui/log_record_detail.py` | 易读详情、参数/结果语义和 JSON 树 |
| `src/emo_master/apps/designer/ui/log_dialog.py` | 日志条目、窗口与来源接线 |
| `src/emo_master/apps/designer/state/job_log.py` | JobEventWindow、执行树、轮次和耗时模型 |
| `src/emo_master/apps/designer/ui/log_filters.py` | 筛选与身份补全 |
| `src/emo_master/apps/designer/services/global_logs.py` | RuntimeGlobalLogs 实时查询驱动及游标/重置/重连 |
| **`src/emo_master/apps/designer/services/runtime_client.py`** | **完整 RuntimeClient 的实际位置**；listOperators、listRuns/listJobHistory、getRunState、queryRuntimeLogs 和设备诊断 |
| **`src/emo_master/apps/designer/page_designer/runtime_logs.py`** | **eventPayload 的实际来源**，被查询与实时驱动使用；只抽所需转换，不迁移无关预览逻辑 |
| `src/emo_master/apps/designer/ui/main_window.py` | makeEntry/operatorLookup/client 注入及事件读取接线 |
| `src/emo_master/clients/runtime/calls.py` | DisplayCallContext/DisplayCallError：只读调用取消/期限基础，**不是完整 RuntimeClient** |

共享读取优先限定为 ListRuns、ListJobHistory、QueryRuntimeLogs、ListOperators、GetDeviceDiagnostics。Runtime 身份和 GetRunState 优先沿用 Operator 现有宿主。该清单表示已有能力，不是新增 RPC 或整包迁移要求。

Operator 与共享模块不得依赖 apps.designer；Designer 保留完整客户端，通过现有回调或薄适配使用共享部分。工作台接通且既有行为保留后停止扩大抽取。operatorLookup 来自 Runtime 注册的真实 ID/版本，不能用编辑器正在编辑的 catalog 猜测。

## 5. 既有测试入口：按影响选用

下列为已有定位线索，不是本期全量运行名单。实施前核对测试位置与当前断言，复用与本次调用关系相符的用例；不存在本轮执行证据的项目不得记 PASS。

- Runtime 相关：`test_runtime_job_lifecycle_integration.py`、`test_parallel_workflows.py`、`test_frame_dispatch.py`、`test_run_catalog.py`、`test_project_configuration.py`、`test_remote_project_import.py`、`test_runtime_persistence_contract.py`、`test_device_manager.py`、`test_device_lifecycle.py`、`test_operator_runtime_logs.py`、`test_operator_runtime_settings.py`。
- Operator UI：`tests/ui/operator_view/test_basic_runtime_feedback.py`、`test_operator_page_publication.py`、`test_parallel_workflow_window.py`、`test_production_configuration_window.py`、`test_runtime_log_view.py`、`test_startup_origin.py`、`test_startup_settings.py`、`test_production_window.py`。
- Designer 日志：`test_runtime_job_workbench.py`、`test_log_json_tree.py`、`test_log_export.py`、`test_log_history_instance.py`、`test_operational_log_integration.py`。

P9 汇总已有有效结果；不因同一行为同时出现在任务、规格和验收中而重复执行。保留现有必需 CI，本地只选择受影响验证；协议检查按实际接口影响触发。

## 6. OpenSpec 与相邻计划

- `openspec/config.yaml`：spec-driven。
- `openspec/specs/runtime-log-workbench/spec.md`：正式日志工作台合同。第 360–368 行规定完整导出覆盖上界内仍可查的 Job 记录。本包沿用首条来源 Requirement 的 MODIFIED 及原三个 Scenario，通过 ADDED 描述 Operator 接入。
- `openspec/changes/archive/2026-11-22-redesign-runtime-log-workbench/`：已归档日志实现与证据，不重做 V2.4。
- `openspec/specs/runtime-background-task-service/spec.md`：已有后台任务语义，未受影响不追加专项重验。
- `openspec/specs/page-advanced-appearance/spec.md`：现有页面外观合同。
- `docs/plans/2026-10-30-runtime-web-integration-plan.md`、`docs/plans/2026-10-30-web-panel-dot-development-plan.md`：既有远程/Web 边界，按影响使用。
- `authoring/`：独立工程草稿编排和 MCP，不能当作 Runtime 生产控制 MCP。

## 7. 证据边界

本次核对的是固定源码及计划文本，不等于产品功能已运行。设备缓存不是完整 Job 历史；open_api 草稿不等于已交付生产 API；LAN gRPC 的公网扩展不属于本期。

本轮没有修改、提交或推送产品源码，没有运行 pytest、产品 CI、原生 Qt、冻结包或真实设备。计划包实际检查单列在 validation.md。原型模拟局限见 reference/prototype-notes.md。
