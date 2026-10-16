# Designer 窄工具栏修复与验证（2026-10-07）

本轮修复完整 CI 中 `testNarrowToolbarKeepsRunActionAndHasOverflow` 的真实失败：640×500 窗口的运行按钮被挤入工具栏溢出区域。运行、停止按钮现在优先于撤销、重做和其它流程工具，按实际 Qt 尺寸收缩为图标，窗口拉宽后恢复文字。原测试及其可见性断言未修改。相关专项、原生 Windows Qt 和本次完整 CI 均通过；pytest 为 3007 passed、8 skipped、28 subtests passed，跳过项不计为通过。

## 代码身份与范围

- 分支：`agent/runtime-workflow-architecture-v1`。
- 起始 HEAD：`f19e88050a198536558a989397541d0822b0f8e9`，起始工作区干净，远端独立查询与本地一致。
- 修复提交：`b697ff864774ea922fe0bd676cb8020476fe86d2`。
- 完整 CI 受测 HEAD：上述修复提交；代码、测试和开发文档已提交，dirty 项仅为本轮新增证据目录。各次验证的实际 HEAD、dirty 列表和逐文件 SHA-256 均在相应 `candidate.json` 中。
- 最终受测源码摘要：`de1fc06a113f446022207642cde8ecb657e69ef5979503e90a7283515c158f91`。摘要覆盖 `src`、`tests`、`proto` 和 `scripts` 中的源码／配置，按实际文件字节计算，不将 CRLF/LF 差别抹去。
- 仓库外原件备份：`D:/jsdfhasuh/documents/emo-master-backups/toolbar-width-20261007-002955-281239/`。没有 reset、stash、force push、修改 main 或重写旧提交。

正式修改集中在 `src/emo_master/apps/designer/page_designer/chrome.py`：

1. 将运行／停止放在撤销／重做前，保留同一 QAction、菜单、快捷键和任务启停逻辑。
2. 使用工作区选择器、按钮 `sizeHint`、最小宽度、Qt 间距、边距和溢出预留宽度计算核心动作所需空间。先压缩打开／保存，仍不足时压缩运行／停止，放宽时恢复文字。
3. 响应 Resize、LayoutRequest、FontChange、StyleChange；尺寸键与重入保护避免样式变化造成重复布局循环，没有增加轮询或延迟定时器。
4. 为图标按钮保留 accessibleName、真实启用状态和提示。页面设计继续隐藏流程执行动作。

新增 `tests/designer/test_toolbar_width.py` 的 14 项测试，覆盖 480／640／800／900／960／1280 宽度、空闲／运行控制状态、往返缩放、动作身份、项目状态不变和同宽度字体变化。`scripts/validate_designer_toolbar.py` 使用正式 MainWindow 和主题生成原生截图；开发指南同步工具栏顺序和窄窗行为。

## 环境与执行口径

- Windows build 26200（Python 平台字符串为 `Windows-10-10.0.26200-SP0`），Python 3.10.21。
- 解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。
- PySide2 5.15.2.1、grpcio 1.78.0、protobuf 6.33.6、NumPy 1.26.4、OpenCV 4.10.0.84；未安装或升级依赖。
- 专项／CI：`PYTHONPATH=src`、`QT_QPA_PLATFORM=offscreen`、`HUARAY_CAMERA_SMOKE=0`。
- 沿用上一轮 `PYTEST_ADDOPTS=--ignore=manual_test_workspace`，排除手动工作区中的历史代码拷贝；没有排除正式 `tests` 中的用例，也没有修改 CI 脚本或验收断言。
- Runtime 测试数据目录在上述仓库外备份目录下，未操作用户正在运行的 Designer、正式数据库或设备。
- 所有命令均保留 `candidate.json`、`raw.log` 和外层监督 `result.json`。只对验证进程树应用外层 watchdog；本轮没有通过放宽任务期限或取消资源断言达标。

## 实际结果

| 检查 | 状态 | 结果／原始证据 |
| --- | --- | --- |
| 原缺陷与初版回归测试 | FAIL，保留 | [red/raw.log](toolbar-width-2026-10-07/red/raw.log)：15 failed；含原 640×500 失败及新夹具错误，不能解释为 15 个生产缺陷。 |
| 第一轮修复验证 | FAIL，保留 | [focused-first/raw.log](toolbar-width-2026-10-07/focused-first/raw.log)：6 failed、9 passed；6 个失败均为停止按钮空闲提示的错误测试预期，核心按钮可见性已通过。 |
| 最终相关专项 | PASS | [focused-final/raw.log](toolbar-width-2026-10-07/focused-final/raw.log)：104 passed，pytest 17.38 秒。 |
| 125% 离屏窄窗专项 | PASS | [scaled-125/raw.log](toolbar-width-2026-10-07/scaled-125/raw.log)：15 passed，5.33 秒。 |
| 150% 离屏窄窗专项 | PASS | [scaled-150/raw.log](toolbar-width-2026-10-07/scaled-150/raw.log)：15 passed，5.33 秒。 |
| 200% 离屏窄窗专项 | PASS | [scaled-200/raw.log](toolbar-width-2026-10-07/scaled-200/raw.log)：15 passed，5.44 秒。 |
| Ruff | PASS | [ruff/raw.log](toolbar-width-2026-10-07/ruff/raw.log)：检查 `src tests scripts/validate_designer_toolbar.py`。 |
| Mypy | PASS | [static/raw.log](toolbar-width-2026-10-07/static/raw.log)：334 个正式源码文件，无错误，保留既有未检查函数体提示。 |
| 首次原生截图脚本 | FAIL，保留 | [native-100/raw.log](toolbar-width-2026-10-07/native-100/raw.log)：PySide2 `QTest.qWait` 不存在；属于本轮验证脚本问题。 |
| 原生 Qt 100%／125%／150%／200% | PASS／部分 NOT_RUN | 共 26 个实际窗口状态通过，详细尺寸与限制见下一节。 |
| 完整 CI | PASS，含 8 SKIP | [ci/raw.log](toolbar-width-2026-10-07/ci/raw.log)：protobuf drift／Ruff／Mypy／pytest 全步骤 exit 0；pytest 3007 passed、8 skipped、28 subtests passed，875.18 秒；整条命令 878.015 秒，无外层超时。 |

初版夹具在首次显示之前 resize，离屏平台会将大窗口压缩到屏幕宽度。修正为显示后再 resize，保留精确宽度断言；字体测试改为对选择器应用局部字体，保留其真实 sizeHint 增长断言。停止按钮空闲时的正式提示为“没有可停止的当前任务”，测试改为按空闲／运行状态精确校验，没有修改正式文案。截图脚本使用 Qt 事件循环等待布局，修正缺失的 `QTest.qWait` 调用；原始失败日志仍保留。上述新问题没有归为基线。

## 原生 Qt、尺寸与截图

截图由正式 QWidget 抓取，并检查窗口边框位于屏幕内。`QT_SCALE_FACTOR` 分别为 1、1.25、1.5、2，实际 DPR 与设定一致；这属于 Qt 应用缩放验证，不冒充 Windows 系统 DPI 修改或多屏验收。

| Qt 缩放 | 屏幕可用逻辑尺寸 | 实际通过的窗口（空闲／运行各一次） | NOT_RUN／限制 |
| --- | --- | --- | --- |
| 100% | 1920×1032 | 480／640／960／1280 × 500，共 8 个 | 无 |
| 125% | 1536×826 | 480／640／960／1280 × 500，共 8 个 | 无 |
| 150% | 1280×688 | 480／640／960 × 500，共 6 个 | 1280 宽连同窗口边框超屏，NOT_RUN。 |
| 200% | 960×516 | 480／640 × 483，共 4 个 | 960／1280 宽超屏，NOT_RUN；500 高连同边框超屏，原生测试按实际可用高度 483 记录。 |

运行中／空闲只是受控按钮状态夹具，标题明确标记“工具栏验证（未启动任务）”，`startJob`、`stopJob` 调用均为 0。没有创建 Runtime、订阅或检测 Job，不将这些截图当作设备执行验收。正式的 640×500 原失败断言和新增精确窗口尺寸测试在离屏平台通过，包括各应用缩放条件。

- [100% 结果](toolbar-width-2026-10-07/native-100-final/screens/result.json)、[125% 结果](toolbar-width-2026-10-07/native-125/screens/result.json)、[150% 结果](toolbar-width-2026-10-07/native-150/screens/result.json)、[200% 结果](toolbar-width-2026-10-07/native-200/screens/result.json)。
- [640 宽、运行控制状态](toolbar-width-2026-10-07/native-100-final/screens/640-running.png)：运行和停止均保留在主工具栏。
- [1280 宽、空闲控制状态](toolbar-width-2026-10-07/native-100-final/screens/1280-idle.png)：打开／保存与运行／停止恢复文字。
- [200% 下 640 宽、运行控制状态](toolbar-width-2026-10-07/native-200/screens/640-running.png)：实际逻辑高度 483，截图物理尺寸随 DPR 增大。

## 复现命令

在仓库根目录使用已配置的 Python 3.10 环境：

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
python -m pytest tests/designer/test_visual_layout.py tests/designer/test_toolbar_width.py tests/designer/test_designer_shortcuts.py tests/designer/test_node_context_menu.py tests/ui/page_designer/test_workspace_modes.py tests/ui/page_designer/test_workspace_switch.py -q
```

原生窗口验证用一个尚不存在的输出目录，输出原生截图和实际代码身份：

```powershell
$env:QT_SCALE_FACTOR = '1'
python scripts/validate_designer_toolbar.py --output "$env:TEMP\emo-toolbar-$(Get-Date -Format yyyyMMdd-HHmmss-fffffff)" --platform windows
```

完整 CI 沿用正式测试集合及上一轮排除手动工作区的口径：

```powershell
$env:PYTEST_ADDOPTS = '--ignore=manual_test_workspace'
$env:QT_QPA_PLATFORM = 'offscreen'
Remove-Item Env:QT_SCALE_FACTOR -ErrorAction SilentlyContinue
python scripts/ci_check.py
```

运行实际 Designer 前保存已有窗口并退出，再执行 `python scripts/dev.py run-designer --local`；正在打开的进程不会自动加载源码修改。

## 保留的限制与交付边界

上一轮完整 CI 的 2992 passed／1 failed／8 skipped 原始日志仍在 [designer-shortcuts-2026-10-06/ci](designer-shortcuts-2026-10-06/ci/raw.log)，未改写为 PASS。本轮通过只证明本次受测代码的结果，原 A18、1080p／5 Hz 性能、读图完整率、资源稳态和 Qt 间歇／组合崩溃证据仍保留；本轮没有重新验收这些项目，也没有制作或发布现场版本。

本次完整 CI 没有最终 FAIL 或 ERROR；8 个原有跳过项保持 SKIP。本轮最终日志未出现 Windows `access violation`，一次 CI 成功仍不足以宣称旧 Qt 间歇崩溃已修复。设备、现场、系统 DPI 切换与超屏原生窗口仍属 NOT_RUN 或上述明确限制。

本轮原始证据目录采用局部 `.gitattributes` 禁止文本行尾转换，`manifest.json` 记录实际文件大小和 SHA-256；提交前逐个核对暂存 Git blob 与工作区原始字节。仅提交本任务文件，最终远端 SHA 以正常推送后的独立 `git ls-remote` 查询为准。
