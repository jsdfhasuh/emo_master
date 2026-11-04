# S3 与 S5 桌面实现和验证记录

日期：2026-11-03（Asia/Shanghai）。依据外部实施计划 v3.4；遵守仓库 AGENTS.md。

## 源码状态与实现

实施分支 `codex/logging-consolidation-20261103`，固定基线和本轮最终 HEAD 都是 `940994b3420b47ff0add8766ccce5a4f2686a02a`。本次变更留在工作区，没有提交、推送、合并或发布。C: 仓库入口与 D: 实际目录为同一工作区。

S3 已实现：

- `designer/main.py` 在本次启动选项确定后、prepareRuntime 前创建 writer；启动错误先记录，重试先关闭旧 writer，再按新目录创建；成功 writer/目录注入窗口，入口最终关闭。`local_log.py` 继续优先 EMO_DESIGNER_LOG_DIR，并支持本次 options；MainWindow 只关闭自行创建的 writer，closeEvent 与 destroyed 共享所有权判断。注入 writer 初始化失败和后续 failureCallback 接入原窗口错误提示队列，不递归写回。
- 新增薄 `runtime/events/app_log.py`，复用异步 writer，使用 `runtime_app` 独立前缀。OperatorWindow 创建 controller 前即可记录诊断，正常失败在可写日志目录中留下应用日志；每次重试产生新实例 ID，ProductionRuntime 与 RuntimeService 接收同一 ID。独立窗口拥有 writer，生产入口注入时由入口最终关闭当前 writer。失败提示区分提交 writer 与实际文件状态、提供目录。
- `runtime/main.py` 无界面启动同样先建立应用 writer；失败先记录再按现有关闭流程落盘，应用和服务 owner 分开关闭。没有日志回放进 EventStore。

S5 已实现（与 devices-export.md 的纯导出/Runtime 本机部分配合）：

- `RuntimeClient.queryRuntimeLogs()` 保留业务错误码/实例 ID，JobEvent DTO 和 eventPayload 贯通元数据；全局 eventId 使用独立包装，不覆盖 sequence。
- `RuntimeGlobalLogs` 改用唯一 QueryRuntimeLogs 来源。首次无业务筛选 limit=1 仅建立上界起点；后续每轮逐页固定上界，实际消费后才推进；普通中断/重开保留游标/实例，RESET_REQUIRED 重新建起点，UNIMPLEMENTED 按当前连接停止且不循环重试。MainWindow 继续让任务订阅处理状态、控制、节点和结果，但不再向同表重复追加。页面会话自身错误保持，已移除只为转发同表而新建的 PreviewRuntimeLogs 订阅。
- `StructuredLogEntry` 保留 originApp/component/runtimeInstanceId、sequence 和独立 eventId。表中显示应用/模块、详情保留全部关联；来源列宽根据真实可见窗口反馈由 88 调至 160。保留既有 JSON 树与导航。
- 现有日志窗口增加“当前显示记录/实时”“Runtime 历史”“Designer 本机文件”明确范围；历史支持工程/Job/设备/模块/工作流/节点/时间/级别和固定上界分页，不强制当前工程。历史页和本机页拒绝实时追加，主实时缓存继续保留；切目标取消旧请求并拒收旧返回。
- 当前缓存导出保持同步快照；历史与本机完整范围导出使用局部 ThreadPoolExecutor/Event，不占业务 busy。历史复用相同筛选/实例/上界按页迭代；本机使用固定文件集合和截止字节位置。Runtime RPC 请求接入现有 DisplayCallContext 取消，文件/页间检查 Event。取消或失败不替换目标、不报成功；JSONL 没有统计伪记录，JSON/TXT 和完成提示使用实际条数。窗口隐藏/销毁取消在途任务。
- Designer 设备面板打开 project/device 筛选的同一历史查询；缓存导出调用共享局部辅助函数，只导出实际缓存。
- 工作流调试 Worker 保留 node.log 受限 payload，原窗口仅对 node.log 格式化 JSON 详情；控制事件保留原有显示与语义，没有增加调试数据库。

## 执行命令与结果

所有命令在仓库根执行；下文 `python` 为 `D:\Conda\envs\emo_master\python.exe`。测试组有交叠，不能将数量相加当成独立测试总数。

1. 首轮设置 `QT_QPA_PLATFORM=offscreen`，执行：

   `python -m pytest tests/designer/test_designer_startup_settings.py tests/ui/operator_view/test_startup_settings.py tests/ui/operator_view/test_runtime_log_view.py -q --tb=short`

   47 passed / 7 failed，执行输出保存在聊天工具记录，失败事实在此保留。3 个可写目录初始化失败场景和 1 个隐藏窗口场景的旧“未写文件”断言，改成明确验证已提交及实际 runtime_app 文件；另 3 个等待/取服务活动文件的场景，暴露正常无 Job 时服务 writer 无文件，需要 LogFeed 跟随应用 writer，此修复及最终覆盖见 devices-export.md。没有删断言、跳过失败或伪造通过。

2. offscreen 初步桌面/导出/现有窗口回归：

   `python -m pytest tests/designer/test_designer_startup_settings.py tests/ui/operator_view/test_startup_settings.py tests/designer/test_log_export.py tests/designer/test_runtime_log_usability.py tests/designer/test_runtime_log_names.py tests/designer/test_runtime_log_refresh.py -q --tb=short`

   132 passed / 31.39s。后续同类回归见日志 10。

3. [desktop-regression-02.txt](desktop-regression-02.txt)：

   `python -m pytest tests/designer/test_operational_log_integration.py tests/designer/test_startup_progress.py tests/designer/test_startup_runtime_connection.py tests/designer/test_startup_project_entry.py tests/runtime/test_operator_runtime_logs.py tests/ui/operator_view/test_startup_settings.py -q --tb=short`

   74 passed / 2 failed：runDesigner 远端启动现在有 Designer 文件，旧“根目录不存在”断言需要按资源归属核对。

4. [desktop-contract-03.txt](desktop-contract-03.txt) 和 [desktop-contract-04.txt](desktop-contract-04.txt)：

   `python -m pytest tests/designer/test_operational_log_integration.py tests/designer/test_designer_startup_settings.py -q --tb=short`

   初次 26 passed / 2 failed：新实时锚点夹具应等待 cursor 建立后再写事件；启动目录夹具应明确取消环境预填。修正后 28 passed / 5.71s。

5. [desktop-regression-05.txt](desktop-regression-05.txt)：

   `python -m pytest tests/designer/test_startup_runtime_connection.py tests/designer/test_operational_log_integration.py tests/designer/test_designer_startup_settings.py tests/designer/test_startup_progress.py tests/designer/test_startup_project_entry.py tests/runtime/test_operator_runtime_logs.py tests/ui/operator_view/test_startup_settings.py tests/designer/test_runtime_log_refresh.py tests/designer/test_runtime_log_usability.py tests/designer/test_runtime_log_names.py tests/designer/test_log_export.py -q --tb=short`

   193 passed / 3 failed。第一次编辑命中了同文件较早的相同断言，误改 prepareRuntime 直调测试，且未改到 runDesigner 取消测试；已纠正：prepareRuntime 独立调用仍不创建目录，runDesigner 入口只允许 Designer 日志且明确无本机数据库/jobs，原只读 RPC、请求数量、取消与关闭断言保留。最终见 09/11。

6. 设置 `QT_QPA_PLATFORM=windows`，运行真实 Windows Qt 平台自动化。初轮 [desktop-native-06.txt](desktop-native-06.txt) 29 passed / 1 failed、本机“全部文件”夹具同时包含正常客户端诊断；改选原单文件后 [desktop-native-07.txt](desktop-native-07.txt) 49 passed / 1 failed，发现 writer.close 后 activePath 正常清空，测试改用实际文件路径。失败日志均保留。最终命令：

   `python -m pytest tests/designer/test_operational_log_integration.py tests/designer/test_designer_startup_settings.py tests/designer/test_startup_runtime_connection.py tests/runtime/test_runtime_startup_progress.py -q --tb=short`

   [desktop-native-09.txt](desktop-native-09.txt)：57 passed / 23.11s；接入实际 RPC 取消后 [desktop-native-11.txt](desktop-native-11.txt)：57 passed / 22.72s。

   新增原生 Qt 自动化实际覆盖：QTest 点击完整历史导出/取消、固定上界与全部页、全局 ID/Job sequence 分开、历史错误保持游标/记录、本机单文件选择与 JSON 详情、Runtime 切换拒绝旧返回、普通重开游标接续、可写目录启动失败文件、Designer 换目录重试及两 writer 各关闭一次、注入 writer 不由窗口关闭、无界面启动失败日志和同实例/分 owner 关闭。既有设置布局和取消/只读连接测试继续执行。

7. [desktop-debug-08.txt](desktop-debug-08.txt) 首轮 30 passed / 4 failed / 1 skipped：所有控制日志都改成 JSON 影响既有 CONFIRMED 精确显示断言。实施最小修正，仅 node.log 用 JSON 详情，控制日志保持原显示，原正确断言不变。最终设置 offscreen，执行：

   `python -m pytest tests/designer/test_workflow_debug_window.py tests/designer/test_workflow_debug_startup.py tests/runtime/test_workflow_debug_acceptance.py tests/designer/test_runtime_log_refresh.py tests/designer/test_runtime_log_names.py tests/designer/test_runtime_log_usability.py tests/designer/test_log_export.py -q --tb=short`

   [desktop-debug-10.txt](desktop-debug-10.txt)：126 passed / 1 skipped / 85.40s。唯一跳过是 `tests/runtime/test_workflow_debug_acceptance.py::testDesignerConnectionRenewsWhilePausedBeyondLease`，原测试要求 `EMO_DEBUG_STRESS=1` 才执行 65 秒 live lease acceptance，本轮没有开启该既有专项，不将其当通过。

   进一步直接核对新增调试 payload：

   `python -m pytest tests/runtime/test_workflow_debug_acceptance.py -k WorkflowStop -q --tb=short`

   [desktop-debug-payload-12.txt](desktop-debug-payload-12.txt)：2 passed / 3 deselected / 4.06s；实际执行 mock Probe 工作流，断言 node.log payload 的 operatorId/phase 到达原调试链路。

8. 当前连接不支持查询的缓存和注入 writer 初始化错误提示，分别属于计划直接要求和集成发现的正常路径。针对性 Windows Qt 最终重跑：

   `python -m pytest tests/designer/test_operational_log_integration.py -q --tb=short`

   [desktop-query-final-13.txt](desktop-query-final-13.txt)：10 passed / 6.33s；补充注入失败提示测试后 [desktop-query-final-14.txt](desktop-query-final-14.txt)：11 passed / 6.39s。没有未处理失败。

9. `python -m ruff check` 本分工修改的生产文件与测试文件通过；包括 main/startup、global_logs、local_log、runtime_client、log_dialog/main_window/device_workspace、preview/runtime_logs、workflow_debug_window、operator_runtime main/controller、runtime main/app_log、workflow_debug Worker 及对应扩展测试。最终 14 日志同样包含重点文件 `All checks passed!`。未为低影响修改另建测试框架。

## 实际界面与未执行边界

- 上述 windows 组为真实 Windows Qt 平台自动化，不等同人工桌面验收；offscreen 组单独标识。
- 提供 [desktop_mock_probe.py](desktop_mock_probe.py)：真实 LogDialog 与两台临时 RuntimeService、模拟设备/流程记录，仅 tempfile 数据，无硬件访问。运行 `python docs/testing/2026-11-03-logging-consolidation/desktop_mock_probe.py --seconds 600`，需使用 Windows Qt 平台。可看 old-project / old-job-A / mock-camera 历史，实时每两秒产生 node.log，选择 Designer 本机启动失败日志，以及模拟 Runtime 切换/查询暂停重连。该 probe 是模拟日志可见窗口，不能宣称它实际连接或执行真实硬件流程。
- 主代理已实际启动并通过 computer-use 看到原生日志窗口、实时记录、应用/模块及控件。可见窗口发现来源列宽不足，已局部修正。操作输入被当前桌面工具拒绝，错误为 `GetCursorPos failed` / `0x80070005`；重试后仍相同，故未宣称 computer-use 人工交互走查完成。历史/本机/详情/导出/取消/重试/重连/切换由上述真实 Qt 自动化验证。
- 不访问真实相机/PLC，不改网络地址，不修改现场工程、凭据或生产日志。没有真实设备验收；冻结打包/协议交付由主代理/协议分工统一记录，不把本记录的源码/Qt检查当成打包或部署验收。
- 本分工没有剩余实现项或代码阻塞。独立审查仍应基于当前未提交工作区，不用基线 SHA 冒充实现提交。
