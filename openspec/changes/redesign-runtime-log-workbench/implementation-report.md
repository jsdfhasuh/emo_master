# Runtime 日志工作台实施与验收

## 代码与工作区

- 实施执行者为 Codex。附件是基于 \`9d59fd16a1d0d3ec0c60725c839ff38183d6916b\` 的设计审查和验收计划。远端 \`origin/main\` 在开工时指向同一提交。
- 原工作区 \`main\` 的实际 HEAD 为 \`37120462016e9d64f97e17f38e31db8d954f3642\`，落后远端 4 个提交。原有 4 个独立工程测试记录修改和未跟踪 \`.tmp/\` 已保留。
- 实施在独立工作树 \`D:/emo-runtime-log-workbench\` 的 \`codex/runtime-log-workbench\` 分支进行。产品代码提交为 \`7d79ea91ecc93b963f0c1d22fa376a603822f6b8\`，同一条 Q1 追加 Designer 参数页真实调试入口的验证提交为 \`eb8899cab3a7e31f930bbc6397b593df6702675a\`。提交前运行的回归使用当时已修改的工作树，后续改动涉及的入口另行重验。以下结果不归记为基线通过。
- 使用 Windows、Python 3.10.21、PySide2 5.15.2.1 和 gRPC 1.78.0。Q1、Q2 使用 Windows 平台插件和 QTest 驱动真实 Qt Widgets，Runtime 运行于本机临时目录并使用回环 gRPC。待测工程通过 2.10 工程、1.3 页面模型验证，保存重读后编译。Q1 另外经 Designer 的正常工程打开入口加载。

## 已实现

1. Designer 通过正常日志入口打开工作台。一级来源为 Runtime 和 Designer 本机。Runtime 默认显示任务首页，系统事件排除实际归属 Job 的记录。原有高级历史范围查询仍可用，Designer 当前会话和本机历史各自保留。
2. Runtime 在现有 SQLite 日志和 jobs 表上提供终态目录与倒序窗口能力，保留前向查询及完整导出的游标语义。现有全局读取器在整页交付后报告进度，Job 用固定上界衔接快照和有限补齐。
3. 原生执行树按调用、父调用、轮次、节点执行身份建立。共用窗口支持时间顺序、条件摘要、折叠任务信息、右侧详情、当前定位、轮次上下文以及有界历史阅读。循环旧字段缺失按未记录处理，生命周期时间只使用已记录证据。
4. 节点记录读取本身的输入输出摘要、指标、诊断、原因和主动日志。既有单算子调试窗口复用同一易读详情，按会话、代次和执行 ID 对应实际日志与结果。原始数据继续使用 LogJsonTree。正式窗口去掉原型整条演示导航。
5. 设备诊断复用近期缓存。普通设备操作和采图携带同一套可用执行身份，诊断行集中归一化身份，保留原始 source。下钻按 Runtime、工程加载代次和设备限定范围，返回恢复阅读位置。完整导出使用独立固定上界，从前向游标零开始读取同一 Job 的完整可查询记录。

## Q1 持续任务、诊断和单算子调试

Windows Qt 自动走查通过。Job \`3f6d4e6f-a939-44c1-a00f-091a7f4fe62f\` 使用实际 While、软件相机、整数加法和 SQLite 写入。为快速形成分页，测试向同一 Runtime 存储加入了 207 条明确标记的 DEBUG 夹具。告警由软件相机测试驱动在实际 capture 上下文中触发，携带真实 Job、父调用、节点执行和第 2 轮身份。

- 任务首页只显示一个活动 Job。通过真实开始事件定位到正在执行的采图节点。筛选 WARN 后保留调用和轮次路径，切换时间顺序保留选中事件。加载更早时从固定范围取回较早记录。历史阅读期间显示新增提示，返回最新后恢复跟随。 [告警与筛选](evidence/q1-while-warning.png)
- 整数加法的完成事件 \`235\` 通过易读页显示标量输出摘要 \`5\`。SQLite 主动日志事件 \`242\` 展开实际回执，并复制了与原始页一致的当前记录。关闭详情后保持选择。 [节点执行摘要](evidence/q1-execution-summary.png)
- 告警事件 \`241\` 进入对应当前加载代次的设备诊断缓存，选择软件通道，检查同一段字节的 HEX 与 UTF-8 文本，返回原告警和筛选。Runtime 接受停止并返回 \`STOPPING\`，Qt 检查实际终态的耗时固定，终态定位提示位置未确认。 [设备缓存](evidence/q1-device-cache.png)
- 在同一 Designer、同一软件 Runtime 内，经参数窗口原有调试按钮打开数值算子。完成两次真实执行后，从第一次的结束通知回到该次输出 \`12\`，第二次输出为 \`9\`。原始结果保留真实会话、代次和执行 ID，Runtime 仍只有原 While Job。结束调试后回到原 Job。 [调试易读结果](evidence/q1-operator-debug-result.png) · [同次执行原始记录](evidence/q1-operator-debug-raw.png)

## Q2 Runtime 重启、历史范围查询和完整导出

Windows Qt 自动走查通过。Job \`097ee296-89db-4184-9e95-28c400dddd5d\` 实际执行整数加法后结束。为验证两类分页，在同一存储中补了 50 个历史终态目录夹具及额外事件。随后关闭生产该日志的 Runtime，启动另一个实例并连接原数据库。

- 从历史目录第二页进入只读任务，查询目标实例为 \`q2-after-restart\`，Job 的日志生产实例仍为 \`q2-before-restart\`。界面仅加载最近 200 条。 [重启后的目录](evidence/q2-history.png)
- 用服务端高级查询找到近期窗口外的 WARN。返回 Job 后保持原本地条件和浏览范围。切换 Designer 当前会话和本机文件，带 Job ID 的客户端错误仍在 Designer。系统事件页保留独立 Runtime 事件并排除归属这个 Job 的设备摘要。 [高级历史命中](evidence/q2-range-query.png)
- 通过 Job 的完整记录入口导出 JSON。测试替换文件选择器的路径返回值，保留实际按钮、导出器和 gRPC。固定上界为 \`221\`，两页前向查询导出 \`219\` 条。逐条对照事件 ID、原始 Job 分区、消息和生产者实例，结果包含未加载的记录、INFO 和关联设备摘要。 [完整导出反馈](evidence/q2-export.png)

## 六组行为证据

| 验收组 | 证据 |
| --- | --- |
| G1 来源和目录 | Q2 重启、终态分页和 Designer 来源隔离，Runtime 目录与持久化合同回归 |
| G2 近期窗口与实时 | 正反向分页、固定上界和字节预算回归，覆盖实时交界重叠去重、补齐未完时状态不确认及退窗保护的原生 Qt 接线测试，Q1 历史阅读 |
| G3 执行结构与筛选 | 执行树模型、两条 While 路径、ForEach 和 Repeat 事件测试，Q1 告警祖先与上下文，Q2 跨近期窗口查询 |
| G4 生命周期与运行时间 | 模型覆盖运行、停止中、终态仍占用资源、缺配对、无原始时间和重启确认，Q1 验证运行定位及终态固定时间 |
| G5 设备归属与往返 | 设备日志与归属回归，覆盖可信顶层、嵌套 source、明确空路径及跨任务聚合，Q1 软件相机真实 capture 及诊断往返 |
| G6 通用详情、调试和导出 | 共享值组件覆盖空值、展开、表格与复制，Q1 验证真实通用摘要、主动日志、调试身份及返回，Q2 完整 Job 导出，既有取消目标文件保全回归 |

## 检查命令和结果

以下命令从实施工作树执行。\`python\` 对应 \`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe\`。

| 命令 | 结果 |
| --- | --- |
| \`openspec validate redesign-runtime-log-workbench --strict\` | 通过 |
| \`python scripts/gen_proto.py --check\` | 通过 |
| \`python -m ruff check src tests\` | 通过 |
| \`python -m mypy --config-file mypy.ini src\` | 647 个源文件，无错误 |
| \`git diff --check\` 及 \`git diff --cached --check\` | 通过 |
| Runtime 和 proto 回归组 | 110 通过 |
| Designer 日志、现有导出、调试及设备工作台回归组 | 284 通过。之后调整的任务状态查询和节点结果入口做了定向重验 |
| 节点结果入口、执行树、Panel 合同与同目标实例回归组 | 83 通过 |
| Windows 原生集成组 | 4 通过。随后增强同一 Q1 中的真实参数窗口调试往返，增强版 Q1 独立重跑通过 |

\`\`\`powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q --tb=short tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_run_catalog.py tests/runtime/test_workflow_loop_contracts.py tests/runtime/test_workflow_loop_contracts_v2.py tests/runtime/test_jsonl_logging.py tests/runtime/test_operator_logging.py tests/runtime/test_device_operational_logs.py tests/runtime/test_device_feedback_closure.py tests/proto/test_proto_generation.py
python -m pytest -q --tb=short tests/designer/test_job_log.py tests/designer/test_log_dialog.py tests/designer/test_log_categories.py tests/designer/test_log_json_tree.py tests/designer/test_log_export.py tests/designer/test_log_history_instance.py tests/designer/test_log_scroll_viewport.py tests/designer/test_runtime_log_refresh.py tests/designer/test_runtime_log_names.py tests/designer/test_runtime_log_usability.py tests/designer/test_operational_log_integration.py tests/designer/test_main_window_layout.py tests/designer/test_device_workspace_usability.py tests/designer/test_page_log_action.py tests/designer/test_operator_debug_window.py tests/designer/test_operator_debug_resource_ui.py tests/designer/test_debug_window_recovery.py --maxfail=3
python -m pytest -q --tb=short tests/designer/test_node_result_panel.py tests/designer/test_job_log.py tests/core/presentation/test_panel_contract.py tests/designer/test_log_history_instance.py --maxfail=3

$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONIOENCODING = 'utf-8'
$env:EMO_LOG_WORKBENCH_EVIDENCE_DIR = 'D:/emo-runtime-log-workbench/openspec/changes/redesign-runtime-log-workbench/evidence'
python -m pytest -q -s --tb=short tests/designer/test_runtime_job_workbench.py tests/designer/test_operational_log_integration.py::testNativeWorkbenchHistoryAndWholeJobExportAfterRestart tests/designer/test_operator_debug_window.py::testNativeWindowExecutesUnappliedDraftAndKeepsTwoResults tests/designer/test_operator_debug_resource_ui.py::testSqliteEditorShowsMappedDebugInputsAndNeverWritesProduction
python -m pytest -q -s --tb=short tests/designer/test_runtime_job_workbench.py
\`\`\`

## 验证边界与发布状态

- 实施时发现旧查询夹具未携带新增的读取进度信号、详情归属和历史视图返回有边界问题。对应修复后已重验。Q1 中整数加法用于生产执行摘要，调试选择已有隔离调试能力的数值算子，沿用现有调试准入。
- 原型与图片导航已在浏览器打开查看并交互，产品证据来自真实 Qt 窗口。Q1 和 Q2 是自动走查。真实相机和 PLC、冻结安装包、全部 82 个算子、全仓 pytest 和远端 CI 未执行。
- 当前会话的推送要求以已有明确授权为条件。本轮交付为本地分支与提交。远端无本次推送、合并或评论。
