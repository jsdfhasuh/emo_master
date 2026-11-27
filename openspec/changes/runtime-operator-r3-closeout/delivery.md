# Operator Runtime R3 收尾交付记录

执行者为 Codex。远端 `codex/runtime-overall-r3` 与指定基线 `1f35861a22f4e5bd553fe210db989e28c3c4a0d1` 一致。实施工作区从该提交单独创建，R3 提交及归档验收记录保留。

## 实施

- 起始页与生产页的常规日志入口打开「运行任务」。起始页入口清除上次 Job 和本机日志的筛选。设备窗口仍可定向打开本机 JSONL。
- Job 实时读取跟随来源和工具可见性。离开时请求取消，Qt 主线程继续响应。旧 reader 退出并排空队列后才允许重开，使用已确认的 Runtime 实例和游标。现有事件 ID 去重及阅读位置继续生效，主动导出独立完成。
- `LIGHT_THEME` 继续用于 Operator 工具。统一字体回退、控件状态、来源标签、筛选和详情，收紧起始设置、工具栏及设备诊断的布局。保留工程生产页 QSS 和 Designer 共享视图的默认外观。
- 共享连接诊断使用 `runtime.logs.connection`；等级、错误码、负载及重连合同沿用原有实现。高级历史范围检索仍按 OpenSpec 范围排除，历史任务目录、事件分页、筛选与导出均保留。

## 定向验证

被测源码和测试提交为 `6253c4b60a02e081eb3960e01b606895ce90ec11`，测试启动时工作区干净。环境为 Windows build 26200、Python 3.10.21、PySide2 5.15.2.1、gRPC 1.78.0、pytest 9.1.1。所有 Runtime 项目和设备来自临时软件夹具。

| 验证 | 结果 |
| --- | --- |
| 定向 pytest（`QT_QPA_PLATFORM=offscreen`） | 79 通过，0 失败，0 错误，0 跳过。覆盖 Operator 日志和设置、独立入口及 Designer 共享读取。 |
| `python -m ruff check src tests`（0.15.6） | 通过 |
| `python -m mypy --config-file mypy.ini src`（2.3.1） | 通过，677 个源码文件 |
| `openspec validate runtime-operator-r3-closeout --strict --json`（2.0.0） | 通过，1 项变更，0 个问题 |
| `git diff --check 1f35861a22f4e5bd553fe210db989e28c3c4a0d1 HEAD` | 通过 |

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
$env:PYTHONPATH = 'src;.'
$pytestTargets = @(
    'tests/ui/operator_view/test_runtime_job_log_sources.py'
    'tests/ui/operator_view/test_runtime_log_view.py'
    'tests/ui/operator_view/test_startup_settings.py'
    'tests/ui/operator_view/test_basic_runtime_feedback.py'
    'tests/ui/operator_view/test_production_window.py::testSourceEntryImportsNoDesignerAndCheckNeverExecutes'
    'tests/designer/test_operational_log_integration.py::testNativeDeviceFailureAndRecoveryReachGlobalWindowAndJsonl'
    'tests/designer/test_operational_log_integration.py::testGlobalObserverDoesNotReserveRunStreamsOrBlockRuntimeSwitch'
    'tests/designer/test_operational_log_integration.py::testEmbeddedGlobalReaderCancelsWithoutClosingRuntime'
    'tests/designer/test_operational_log_integration.py::testReaderErrorRetryKeepsMatchingCursorAndInstance'
    'tests/designer/test_runtime_job_workbench.py'
)
python -m pytest -q --junitxml D:/tmp/emo-r3-closeout-final-pytest.xml @pytestTargets
```

## Windows 原生 Qt

用真实显示的 Qt Widgets、Windows QPA、软件 Runtime 和模拟相机跑代表性操作。QTest 点击现有入口并走真实逻辑，Win32 `PrintWindow` 从可见且已暴露的窗口句柄取图。复现取图使用 `pywin32` 312 和 Pillow 12.3.0。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:HUARAY_CAMERA_SMOKE = '0'
$env:PYTHONPATH = 'src;.'
python -m tests.ui.operator_view.native_runtime_r3_closeout_probe --output D:/tmp/emo-r3-closeout-native-6253-final
```

最终走查退出码为 0，报告记录的工作区无未提交修改。起始、设置、日志首页和详情均以 1060×800 逻辑窗口采集。展开筛选后为 1060×820，设备对话框为 920×789，返回生产页的实测窗口为 1060×913。截图尺寸包含 Windows 窗框。

两次从来源切换或生产页恢复时都从同一 Runtime 实例的游标 198 开始。原选中事件 127 和阅读位置保留，暂停期间的标记事件 407 恢复后只交付一次。软件设备产生两条诊断。生产页继续查看 B，实际停止 A 后 B 仍为 `RUNNING`。

早期标准环境复跑两次在切换到「时间顺序」时触发 `0xc0000005`，栈指向 Qt 事件树的 `clear()`。同机 R3 基线完成四次顺序切换。过程内预载系统 C++ 运行库的独立诊断也通过。代码修正为 Qt 清空树项后才释放 Python 引用，保留显示与选择逻辑。修正后两次开发复跑及最终干净提交完整通过，最终验收使用标准环境和原依赖。

- [起始页](verification/native-qt/01-startup.png) · [Runtime 设置](verification/native-qt/02-runtime-settings.png)
- [默认日志首页](verification/native-qt/03-log-default.png) · [日志详情](verification/native-qt/04-log-detail.png) · [展开筛选](verification/native-qt/05-log-filters.png)
- [设备管理](verification/native-qt/06-device-management.png) · [生产页](verification/native-qt/07-production.png) · [机器可读验证记录](verification/native-qt/native-runtime-r3-closeout.json)

## 审查及交付边界

独立源码复核核对了真实入口、旧线程退出后再创建 reader、运行实例和游标恢复、导出独立性、Designer 共享视图及生产页样式隔离。原生预检发现的字体回退、下拉指示和常用窗高可达性问题均修正并复验。历史 R3 归档、Designer 应用源码及仓库工作流经差异核对均保持原样。

常规 GitHub CI 的 PR 触发目标为 `main`。本次 PR 以 R3 分支为目标，未启动全量 CI 矩阵。人工桌面验收、真实硬件、现场工程、冻结包、Web 和发布未执行。

后续交付提交只包含 OpenSpec 状态、记录和截图。核对被测提交与交付提交之间的 `src` 和 `tests` 无差异。
