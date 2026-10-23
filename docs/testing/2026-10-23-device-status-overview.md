# Designer 设备状态栏验证

日期：2026-10-23。

最新语义：按用户后续明确要求，状态栏时间已改为当前选中流程的本次 Job 耗时，不再显示 Runtime 服务启动时长。下面初版及首次修正记录保留为历史证据，最终行为见末节。

## 范围与基线

- 开始源码 SHA：`23f33b33e5290257bb67aec21b9d00b8e4c4f37f`，分支 `agent/runtime-workflow-architecture-v1`。
- 验证结束时 HEAD：`21b9ad6aa4c24b8dafa01a5e6d78a69cc9396e84`。期间另有三次提交，差异只涉及参数表单销毁、其回归测试、PLC 测试清理和 CI 报告，未重复实现或回退这些修改。
- 本轮改动未提交；保留开始时已有的视频、网络相机等工作区修改。
- 将右下角仅 TCP 摘要改为所有在线设备、TCP 监听与接入客户端、Runtime 本次启动时长。点击打开设备管理，悬停展示设备和接入方信息。
- 在线设备来自 `GetDeviceStates`，不把本地未发布草稿当成在线配置。TCP 监听不等于客户端已连接，按次连接设备单独标明。
- Runtime 时长通过向后兼容的可选字段 `runtime_uptime_ms` 提供；未加载工程也可读取，旧 Runtime 未提供时显示未知。
- 后台只读查询间隔约 2 秒，单个在途查询；状态超过 6 秒未刷新即显示未知。切换工程或 Runtime 时取消原查询并丢弃旧上下文结果。窗口关闭后取消查询并退出观察器。
- 本轮接入方指托管 TCP 服务当前接纳的客户端，包含通道名称、IP、端口和接入时间；不表示 gRPC/Designer 操作员列表或历史连接审计。

## 验证

环境：Windows，`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10.21、PySide2 5.15.2.1、Qt offscreen。

1. 修改前：`test_project_communication_ui.py` 与 `test_runtime_connection_ui.py`，17 passed。
2. 新增覆盖：设备状态只读性、未加载/不同工程、单调运行时长、旧 Runtime 缺失字段、真实回环 TCP 接入/断开、回环 gRPC、Qt 旧结果隔离/过期/恢复/计时、窗口外持续刷新与通用设备入口。
3. 最终聚焦回归：90 passed，64.58 秒，无跳过。

```powershell
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest tests/designer/test_device_status.py tests/designer/test_project_communication_ui.py tests/designer/test_runtime_connection_ui.py tests/designer/test_device_workspace_usability.py tests/runtime/test_device_overview.py tests/runtime/test_device_runtime.py tests/runtime/test_device_lifecycle.py tests/runtime/test_tcp_device_closure.py tests/runtime/test_tcp_device_integration.py -q
```

- 涉及的手写源码与新增测试 Ruff 通过；新 UI 模块及共享设备客户端定向 mypy 通过；`scripts/gen_proto.py --check` 通过；`git diff --check` 通过。
- 初次截图缺字源于 Windows offscreen 未发现系统字体；复用现有 `readableFont` 夹具后重拍并检查。初次 Ruff 的测试夹具 F811 注释位置错误已修正，未修改产品字体或弱化断言。
- 1024 和 1920 宽度截图检查摘要文字完整、位于状态栏内；真实 QToolTip 抓图可读。截图使用固定模拟状态，不代表现场连接。
- 临时测试工程使用 `WorkflowStore().toPayload()`、2.7 模型校验、保存重读与工作流编译；通过 Designer 实际加载入口。没有生成或改写现场工程。

截图：[1024 宽度](../evidence/2026-10-23-device-status-overview/status-1024.png)、[1920 宽度](../evidence/2026-10-23-device-status-overview/status-1920.png)、[悬停详情](../evidence/2026-10-23-device-status-overview/status-tooltip.png)。

## 验收边界

已执行源码、原生 Qt 离屏自动化、模拟相机、回环 TCP/gRPC 验证；不是全仓测试、真实相机/PLC、运行中的桌面程序或冻结包验收。未发布、打包、提交、推送或重启现场程序。源码修改需在后续启动的新 Designer 进程生效；独立 Runtime 的时长字段需使用更新后的 Runtime。

## 后续显示修正（2026-10-23）

本轮基线 HEAD：`e8c24fcfd7ebf964f0f506a7d73a42405069dff9`，在上面的未提交状态栏实现上继续修正。仅修改 `device_status.py`、其 Designer 测试及本验证记录，保留其他工作区修改；没有改动设备 I/O、Runtime 协议或连接控制。

- 通过真实 `QHelpEvent(QEvent.ToolTip)` 接管本状态栏的提示，仅在它仍可见且属于本控件时同步更新。时间、客户端断开、观测失败与工程切换不再留下旧详情。离开、点击或关闭时收起本提示，不重新弹出已关闭提示，也不覆盖其他控件的提示。
- 明确显示 TCP 未启动、启动中、监听中、停止中、服务失败和未知状态，读取现有 `driverState.error`，并保留 HTML 转义。
- 当前异常优先，之后是已连接设备和其他设备，仍保持有界的 12 项详情；摘要在有当前异常时显示数量。第 13 台设备故障的反例已覆盖，不会再被前 12 台正常设备挤出。
- 悬停分类汇总包含已连接、未连接、按次连接、已停用和等待接入。正常停止、禁用、按次连接和暂停不是故障；执行器保留的历史错误显示为“上次错误”，已恢复设备不因此一直计入当前异常。
- 时间明确标为“Runtime 已运行”，悬停显示“状态更新于 N 秒前”，不混同流程执行耗时。

验证经过：

1. 修改前原有定向测试：11 passed。
2. 先新增直接反例、未改产品代码：8 failed、2 passed、8 deselected。失败分别覆盖 TCP 状态文案、故障优先与分类、历史错误标记、真实可见悬停框未更新；保留这次首次失败记录，不将其作为环境失败。
3. 修正后定向测试：25 passed，含本轮新增 14 项。
4. 与上文相同的九个测试文件执行最终回归：**104 passed，70.80 秒，无跳过**；命令使用 `python.exe -X utf8 -m pytest ... -q`，避免 Windows 控制台编码影响输出阅读。
5. 本轮源码和测试 Ruff 通过，`device_status.py` 定向 mypy 通过，`scripts/gen_proto.py --check` 与 `git diff --check` 通过。
6. 1024 / 1920 宽度原生 Qt 离屏截图已检查，增加异常数量与时间说明后摘要完整可读；悬停截图使用模拟故障与连接状态，不是现场设备验收。

追加截图：[1024 宽度](../evidence/2026-10-23-device-status-overview/followup/status-1024.png)、[1920 宽度](../evidence/2026-10-23-device-status-overview/followup/status-1920.png)、[悬停详情](../evidence/2026-10-23-device-status-overview/followup/status-tooltip.png)。上一轮截图保留不覆盖。

## 当前作业耗时修正（2026-10-23）

本轮源码基线仍为 `e8c24fcfd7ebf964f0f506a7d73a42405069dff9`。用户明确这里应显示当前运行的时间，因此将时钟改为当前选中流程的本次 Job；原有设备、监听、接入客户端与悬浮刷新行为保留。

- `RuntimePanelState` 保存当前 Job 的开始、结束时间，复用既有 `job.started` / 终态事件，以及 `GetJobStatusReply.started_at_ms` / `ended_at_ms`。运行中的秒数由本地单调时钟从观察到开始时推进；最终耗时优先使用同一 Runtime 的起止时间差，不将两台机器的墙上时间相减。没有时间戳的状态回包使用本地观测耗时并在终态冻结。
- 未执行显示“本次运行 未开始”，启动和接受状态不借用服务存活时间。运行中显示“本次运行 HH:MM:SS”；成功、失败和取消分别显示“完成耗时”“失败耗时”“取消耗时”，终态保留最终时间，不继续增长。停止请求尚未完成时继续计时，状态待确认时不冒充仍在正常运行。
- 新 Job / 工程重置时清除旧计时；并发流程使用各自已有的 `RuntimePanelState`，摘要跟随当前选择。悬停最上方显示当前流程、Job、状态和本次运行时间，下方继续显示设备连接详情。离开悬停后不会自动重新弹出。
- 修改范围为 `runtime_panel.py`、`device_status.py`、`runtime_controller.py` 的状态回包时间传递、三个对应测试文件及本记录。未修改 Runtime 协议、设备 I/O 或执行控制；初版添加的可选 uptime 字段保留兼容，但本 UI 已不再使用它计时。保留其他人的未提交修改。

验证经过：

1. 修改前四个聚焦测试文件：56 passed。
2. 先添加当前 Job 计时测试：5 failed、32 deselected，均因尚未引入计时 API / `monotonic` 测试入口而失败。这是新增接口的测试先行结果，不将其描述成五个独立旧产品故障。
3. 初版新实现四文件：62 passed；补充未开始状态与当前流程名称测试后，扩展回归曾得到 150 passed、2 skipped。
4. 随后把窗口用例加强为实际调用流程选择入口，发现新增名称读取误将 `WorkflowState` 当作字典，产生 `AttributeError`：1 failed、149 passed、2 skipped、1 error。关闭窗口的同一读取也引发清理错误。本轮修正为读取 `.name`，保留该真实窗口断言，不绕过选择入口。
5. 最终十文件回归：**150 passed、2 skipped，60.00 秒**。两项跳过均为已有 `testFailurePopupNativeLayout` 参数化用例，需要 Windows Qt 平台，本次 offscreen 未执行，不记为通过。
6. 最终状态栏测试在 100% 和 `QT_SCALE_FACTOR=1.5` 下分别 **28 passed**；检查 1024 / 1920 宽度截图与真实 QToolTip，包含失败后固定耗时和流程名称，文字未挤出状态栏。时间推进、重复终态、下一次运行、切换当前流程、其他 Job 事件隔离和连接丢失后终态耗时保留有断言覆盖。
7. 六个改动源码 / 测试文件 Ruff 通过，三个源码文件定向 mypy 通过，`git diff --check` 通过。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -X utf8 -m pytest tests/designer/test_runtime_panel.py tests/designer/test_runtime_controller.py tests/designer/test_workflow_runtime_controller.py tests/designer/test_device_status.py tests/designer/test_runtime_worker.py tests/designer/test_runtime_qt_ownership.py tests/designer/test_runtime_connection_ui.py tests/designer/test_run_failure_feedback.py tests/designer/test_project_communication_ui.py tests/designer/test_workflow_navigation.py -q -ra
```

本次截图：[1024 宽度](../evidence/2026-10-23-device-status-overview/current-run/status-1024.png)、[1920 宽度](../evidence/2026-10-23-device-status-overview/current-run/status-1920.png)、[悬停详情](../evidence/2026-10-23-device-status-overview/current-run/status-tooltip.png)、[150% 窄窗口](../evidence/2026-10-23-device-status-overview/current-run/scale150-status-1024.png)。截图为固定模拟状态，历史截图未覆盖。

这些证据属于源码及原生 Qt 离屏验证，不是当前已打开的桌面窗口、冻结包或真实设备验收。本轮未重启用户进程，未访问真实相机 / PLC，未打包、提交或推送；已打开的 Designer 需要后续正常重开才会加载源码修改。
