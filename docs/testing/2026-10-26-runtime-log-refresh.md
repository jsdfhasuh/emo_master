# 2026-10-26 运行日志浏览位置与筛选刷新修复

## 范围与根因

- 基准源码 SHA：`633de0e197f5553168a7786440199f68f3410bed`。验证对象是保留已有未提交修改的当前工作区，不是该 SHA 的干净检出。
- 用户截图中的“运行日志”属于 Designer 的 `StructuredLogView`，不是独立 Runtime 的文件日志窗口。
- 原实现每个日志批次都清空并重建 Job、工作流、节点和事件筛选选项，即使内容不变。Qt 下拉列表的悬停项与滚动状态随之丢失；只阻断 QComboBox 信号不能阻止其模型被重置。
- 勾选自动滚动时，追加日志无条件跳到底部，向上阅读不会暂停跟随。缓存淘汰旧行时，未跟随的表格也没有补偿被删除行数，导致当前阅读内容前移。

本次只修改 `src/emo_master/apps/designer/ui/log_dialog.py`，新增 `tests/designer/test_runtime_log_refresh.py` 及本记录和验证输出。未改动其他已有工作区修改、现场工程、设备配置或运行任务。

## 行为变更

1. 筛选选项内容未变时不重建模型；下拉框展开期间不替换其选项，保持鼠标悬停项和滚动位置。
2. 关闭下拉框后使用现有批处理定时器更新选项，包括选择和 Esc 取消两条路径；即使之后没有新日志，也会补齐新选项。按身份保留已有选择和未知历史筛选。
3. 向上滚动日志表格自动取消“自动滚动”，新日志仍正常接收。重新勾选后立即回到底部并继续跟随；窗口尺寸变化也保留跟随行为。
4. 暂停跟随时，缓存淘汰旧行会补偿滚动位置，保留仍在缓存内的阅读位置和选中日志详情。更新表格期间阻断中间选择信号，避免重写仍在查看的详情文本。
5. 不匹配当前筛选的新日志只更新统计，不重绘表格内容。日志保留上限及批处理间隔不变。

## 验证

环境：Windows、Conda `emo_master` Python 3.10.21、PySide2 5.15.2.1、pytest 9.1.1。使用合成日志及既有模拟客户端，不连接真实相机或 PLC。

| 检查 | 结果 | 原始输出 |
| --- | --- | --- |
| 修改前原生 Qt 反例 | 14 failed，2 passed | [before.txt](2026-10-26-runtime-log-refresh/before.txt) |
| 第一轮修复定点复验 | 4 failed，12 passed | [first-fix.txt](2026-10-26-runtime-log-refresh/first-fix.txt) |
| 第一轮 150% 日志回归 | 1 failed，83 passed | [first-native-150.txt](2026-10-26-runtime-log-refresh/first-native-150.txt) |
| 最终原生 Qt，100% | 85 passed，17.40 秒，无跳过 | [final-native-100.txt](2026-10-26-runtime-log-refresh/final-native-100.txt) |
| 最终原生 Qt，150% | 85 passed，33.02 秒，无跳过 | [final-native-150.txt](2026-10-26-runtime-log-refresh/final-native-150.txt) |
| Ruff | 源码与新增测试通过 | `ruff check` |
| mypy | 1 个源码文件通过 | `mypy --follow-imports=silent` |
| Git 空白检查 | 修改的源码通过 | `git diff --check` |

两种缩放执行同一套 85 项测试，不应相加为 170 个独立测试。新增 20 项覆盖四个动态筛选的重复内容、展开期间持续日志、实际鼠标选择、取消后的补更新、暂停和恢复跟随、窗口缩放、缓存淘汰、详情和行对象保留、重命名后的身份保留。其余 65 项为原有日志解析、过滤、名称、历史偏好、隐藏错误提示与安全节点导航回归。

首轮失败保留如下：

- 第一轮修复的 3 个下拉框失败来自选项点击后表格刷新取消了刚安排的选项刷新，现保留该单次定时任务。
- 第一轮修复的阅读锚点断言读取发生在 Qt 延迟布局前。辅助检查确认补偿后的滚动值已正确，处理下一轮布局后原阅读行恢复；测试现处理布局事件后断言，原位置和详情断言未弱化。修改前的实际锚点偏移仍由基线反例记录保留。
- 第一轮 150% 失败暴露了窗口显示后可视区尺寸变化、跟随位置尚未到达新底部的问题。已在滚动范围变化时保持已启用的跟随，并新增窗口尺寸变化回归。
- 首轮定点 mypy 报告 PySide2 `Signal` 的 `emit/connect` 属性类型错误，按仓库现有方式标注 Qt 信号类型后复验通过。

复验命令（两轮分别设置 `QT_SCALE_FACTOR` 为 `1` 与 `1.5`）：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = '1'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest -q `
    tests/designer/test_runtime_log_refresh.py `
    tests/designer/test_log_dialog.py `
    tests/designer/test_runtime_log_names.py `
    tests/designer/test_runtime_log_usability.py `
    --tb=short -rs --timeout=60
```

## 交付边界

已完成源码修复和上述原生 Qt 自动化，未执行全仓库测试、人工现场走查、冻结 EXE、打包、部署或真实设备验收，也未提交或推送。

当前已打开的 Designer 不会自动加载修改。源码版需用户按正常流程关闭后重新启动，已有 EXE 需要重新构建才包含修复；本次未替用户停止任务、关闭窗口或重启程序。
