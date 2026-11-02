# Designer 共用性能探针

本轮实现用于手动记录流程设计、页面设计中的卡顿。开发目录为 `D:/jsdfhasuh/documents/my_project/emo_master_designer`，分支 `codex/designer-resize-zoom-fix`，基线 HEAD 为 `8df8599fc026c424a49e87cc53fb45f6b371f16f`。本轮为工作区修改，包含此前尚未提交的页面改动；没有创建提交、推送或合并。

## 复用范围

历史流程探针位于另一工作区的 `build/canvas_drag_20261012/profile_drag.py`，以 `cProfile` / `pstats` 和分段计时记录拖动。这是绑定特定流程的诊断脚本，不是产品中的开关。

本轮沿用它的函数分析机制，新增共用服务 `src/emo_master/apps/designer/services/performance_probe.py`，并将正式流程验证脚本 `scripts/validate_flow_edge_readability.py` 的 median / P95 统计提取为共用 `durationStatistics`。原有统计口径保留，额外提供 max；历史报告不改写。

流程和页面工作区使用同一个控制器和“视图 → Designer 性能探针…”入口，没有各写一套探针。不替换拖动、布局或绘制逻辑，不修改工程和撤销历史，不连接设备。

## 手动使用

1. 从本工作区的 `start_designer.cmd` 启动更新后的 Designer。已运行的旧进程不会自动加载这次新增代码；先自行保存当前工作。
2. 打开待验收页面，选择“视图 → Designer 性能探针…”。默认记录 60 秒，可设为 5～300 秒；默认开启“记录函数耗时”。
3. 点击“开始记录”，回到画布重复一种操作约 20～60 秒。建议一次只复现一个问题，例如移动组件、调整尺寸、切换选中项、组属性修改、窗口缩放或跨容器拖动。
4. 感到卡顿后可按 `Ctrl+Alt+F9` 或点击“标记卡顿”。如果界面正在阻塞，快捷键需等事件循环恢复才会送达，所以标记通常位于阻塞之后；同时核对标记之前的延迟和采样栈。
5. 点击“停止并导出”，或等待自动停止。关闭探针窗口会隐藏窗口并继续记录；从原菜单可重新打开。关闭 Designer 的正常退出流程也会停止并尝试导出。
6. 点击“打开报告目录”。每次生成独立时间戳目录，包含 `report.json` 与 `summary.md`。提供这两个文件，并说明刚才复现的具体动作即可。
7. 建议对同一操作再采集一次，取消勾选“记录函数耗时”，作为较轻量的对照。此模式仍有延迟与 Python 栈采样。

默认保存位置为当前 Designer 日志目录下的 `designer-probes`；窗口直接显示实际目录。`EMO_DESIGNER_LOG_DIR` 已有配置会继续生效，也可以用“更改保存目录…”单独选择位置。导出失败后记录留在内存，允许换目录重试；成功导出前不覆盖这次记录。关闭进程会失去未导出的内存记录。

## 报告怎样读

| 数据 | 用途与边界 |
| --- | --- |
| `heartbeat` | 20ms Qt 定时器相邻回调间隔减去 20ms，作为事件循环额外延迟。包含 P95、最大值和时间线；不是屏幕 FPS。 |
| `stallStacks` | 后台线程每 50ms 检查一次；GUI 心跳缺失至少 100ms 时截取 Python 栈。用于定位阻塞时正在执行的调用链，不能看到 Qt/GPU 原生内部栈。 |
| `functions` | GUI 线程函数调用次数、自身 CPU 时间、含子调用 CPU 时间和 CPU 耗时最高的最多 8 个调用者。入口在分析器启动前已进入时，可能没有调用者记录。 |
| `areas` | 按源码模块归类的自身 CPU 时间，覆盖候选布局、响应式求解、Qt 布局、页面渲染、拖动/尺寸调整、属性、刷新、工程历史和流程路由等。 |
| `marks` | 手动标记时刻及当时工作区。 |
| `events` | 相关输入、布局、绘制事件数量；包含 Qt 的转发，不等同于操作次数。 |
| `metadata` | 应用/Python/Qt 版本、开始和结束时的视图尺寸、缩放、组件数、终端模式、源码 HEAD/分支/dirty 和相关文件 SHA256。没有工程路径、页面名称或组件内容。 |

先从明显延迟或手动标记定位时间点，再看附近采样栈；之后用函数自身 CPU、含子调用 CPU 和调用者判断是否频繁测量、创建控件、刷新、复制或校验模型。含子调用时间存在重叠，不能相加。自身 CPU 按模块归类也不会把 `deepcopy` 等库调用自动归到业务调用者，需沿调用者查看。

函数分析沿用 `cProfile`，本轮将其时钟设置为 GUI 线程 CPU 时间，避免把 `QDrag`、对话框事件循环和等待误计成 CPU 计算。代价是 Windows 的线程 CPU 时钟可能较粗，不能凭一次极短函数记录断定根因。分析器本身有开销；本功能不提供无探针帧率结论。

采样线程只读 Python 栈中的代码文件、行号和函数名，不读取局部变量，不从后台调用 Qt。报告不保存工程 payload、组件标题、绑定值、图片或凭据；源文件位置归一化，避免带出绝对用户名路径。函数分析覆盖 GUI 线程，不覆盖 Runtime 或其他工作线程。GIL 长时间被占用可能导致栈采样缺失。自动停止也要等待 GUI 事件循环可响应，不能强制中断业务函数。

最多保留 15000 个心跳、600 个采样栈和 100 个标记，报告显示省略计数。未开启时不安装应用事件过滤器、不运行采样线程或定时器、不启用函数分析。采集期间不逐事件写盘，停止后再导出。已有其他 Python profiler 时，拒绝覆盖它，可关闭函数分析使用轻量模式。

源码标识来自采集开始时磁盘上的代码，不证明长期运行的进程已经加载这些文件。更新代码后须重新从对应工作区启动。

## 本轮验证

- Python 3.10 / PySide2，`QT_QPA_PLATFORM=offscreen`；服务与 Qt 集成测试，以及现有工作区、尺寸调整、组属性入口回归：**32 项通过**。
- 验证包括：两个工作区的菜单、开始/停止、重复采集、自动停止、手动标记、关闭窗口后继续记录、关闭控制器后释放资源、导出失败重试、已有 profiler 不被覆盖、采样栈与缓冲计数。
- Qt `QWindow` 输入路径在开启和关闭函数分析两种模式下执行 75% 缩放后的尺寸拖动，验证一次有效拖动一条历史、撤销恢复、刷新和代理内部事件被记录。
- Ruff 检查本轮 Python 文件通过；共用服务和控制器通过 `mypy --follow-imports=silent --check-untyped-defs`；`git diff --check` 通过。
- 初次测试有 3 项失败：探针文件排除条件误排除同名测试函数、两处测试调用当前 PySide2 不提供的 `QTest.qWait`。随后发现顶层已进入函数的 caller 断言不成立，改为实际嵌套调用验证。均保留在本条记录，不记作首次通过。
- 临时页面烟测脚本 `scripts/validate_designer_performance_probe.py` 使用独立 QSettings、模拟 Runtime 客户端、内存中的当前格式页面，捕获主动加入的 180ms 阻塞及 `syntheticStall` 栈，并验证工程与历史未改变。此示例只验证探针，不是用户页面的卡顿根因证据。
- `probe-recording.png` / `probe-exported.png` 为真实应用样式下的 offscreen Qt 截图，已检查中文显示和控件布局。未执行真实鼠标人工验收、Windows 多 DPI 验收或真实工程性能对照，不宣称已定位或改善现场卡顿。

复现命令（在 Designer 工作区 PowerShell 执行）：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$python = "$env:USERPROFILE/.conda/envs/emo_master/python.exe"
& $python -m pytest -q tests/designer/test_performance_probe.py tests/ui/page_designer/test_performance_probe_ui.py tests/ui/page_designer/test_workspace.py tests/ui/page_designer/test_responsive_resize_layout.py tests/ui/page_designer/test_parent_navigation.py --timeout=60
& $python scripts/validate_designer_performance_probe.py --output build/designer-probe-smoke
```

此处设置的 offscreen 仅用于自动化；手动启动验收时使用普通桌面终端，或先移除这个环境变量。人工验收不运行上述故意制造阻塞的示例，直接使用菜单记录日常编辑动作。
