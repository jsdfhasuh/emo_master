# 日志视口滚动：实际失败、局部修复与回归

记录日期：2026-11-03 / 04 UTC。基线为 `9079e444c3aeb7ab7f5aa645c9c055b418b2ba1b`，修改位于独立云 worktree。原检出、之前的失败证据及 SDK 来源未覆盖，没有操作用户电脑、真实设备或现场工程。

## 真实现象与根因

正式云 Linux Designer 用鼠标/键盘打开 SDK 示例、运行软件 Runtime 后，首次打开日志时发现：滚动条数值已到底，渲染视口仍显示最早几行。操作记录及 [原现场截图](dot-ci-evidence/scroll-viewport-visible-before.png) 保留；该截图位于继续操作之后，不以其中复选框状态替代最初观察。

独立 PySide2 几何反例直接证明：15 行时 scrollbar=11/11，实际首行仍是 0，最后一行矩形 `(0, 504, 127, 35)` 完全在 161px 视口之外；追加至 20 行时 scrollbar=16/16，最后一行仍不可见。见 [修复前几何输出](dot-ci-evidence/scroll-viewport-before-geometry.log)。这些是 offscreen Qt 几何和渲染证据，不冒充人工点击。

原因是 `_refreshTable()` 和 `_applyPendingEntries()` 在改行数、滚动到底或恢复历史位置时，对整个竖向 scrollbar 调用了 `blockSignals(True)`。Qt 自身也依赖这些信号同步视口/header offset；之后解禁不会补发已错过的移动。该问题在本轮基线确实存在，不声称必然由本次日志整合引入。

## 最小修改

保持 Qt scrollbar 信号正常，仅在已有两处表格更新期间设置 `_updatingTable`，让本视图自己的自动跟随回调暂时不改变跟随状态。既有表格选择信号保护、历史锚点、选中详情和行数上限不变。未改超时、业务数据、依赖或 workflow。

新增 9 个确定性回归，断言真实 `visualRect` / `rowAt` 视口位置，而不只看 scrollbar 数值：首次与隐藏时填充、连续追加和正常淘汰、筛选/resize/重开、暂停历史锚点及详情树选择、手动滚动暂停与恢复跟随。旧测试和正确断言保留。

## 自动化证据

Python 3.10.22 / PySide2 5.15.2.1，云 Linux `QT_QPA_PLATFORM=offscreen`。各组存在交叠，不相加。

- 未修代码上运行新 9 项：**5 failed、4 passed**。保留 [原失败输出](dot-ci-evidence/scroll-viewport-before.txt)，未以新结果覆盖。
- 修改后新 9 项加原有 refresh 24 项：**33 passed / 5.04s**，见 [focused](dot-ci-evidence/scroll-viewport-focused-after.txt)。
- 先前日志相关扩展组：**156 passed / 35.14s**，见 [broader](dot-ci-evidence/scroll-viewport-broader-after.txt)。
- 11 月 4 日恢复后重新执行 `tests/designer/test_log_scroll_viewport.py tests/designer/test_runtime_log_refresh.py tests/designer/test_runtime_log_usability.py`：**66 passed / 10.61s**，见 [恢复后 focused](dot-ci-evidence/scroll-viewport-focused-resumed.txt)。
- 恢复后 Ruff `src tests`、mypy 全 **612** 源码文件、`scripts/gen_proto.py --check` 以及源码 `git diff --check` 均通过，见 [恢复后静态检查](dot-ci-evidence/scroll-viewport-lint-resumed.txt)。
- [修改后几何输出](dot-ci-evidence/scroll-viewport-after-geometry.log)：初次 15 行、追加 20 行以及替换 25 行，最新行均在视口内；手动上滚仍退出跟随，重新启用后回到底部。

## 正式可见桌面复测边界

旧运行与修复运行采用不同临时数据/设置目录。修复源码 `log_dialog.py` SHA256 为 `06eace5e07d42b47983bb57f73cd3650ab02b1218e3008097e08ec5701e8fc49`。

修复后正式 Designer 重新运行 SDK 示例，首次打开日志时自动滚动勾选，最后四条包括 workflow/job completed 正常显示；见 [修复后正式窗口截图](dot-ci-evidence/scroll-viewport-visible-after.png)。11 月 4 日 00:27–00:29 UTC 的正式鼠标键盘复测另已完成：清筛选后 40 条显示尾部；关闭跟随并 Ctrl+Home 到首条，实际再次运行使记录 40→53，视口及选中首条保留；恢复跟随后显示新一轮 completed 尾部；Escape 关闭再 Ctrl+Shift+L 重开仍显示最新尾部。见 [逐步操作与截图](dot-ci-evidence/scroll-visible-recheck/README.md)。完整八组桌面流程仍继续单独记录，本段不标为全部通过。

## CI 与冻结边界

基线 `9079e444...` 的 [Web 41651429170](https://github.com/jsdfhasuh/emo_master/actions/runs/41651429170) 是 SUCCESS；[主 CI 41651429886](https://github.com/jsdfhasuh/emo_master/actions/runs/41651429886) 是 CANCELLED，Windows job 是 SUCCESS，Ubuntu job 为 CANCELLED（即使其 steps 显示 success，也不改写 job 终态）。这些不是后续修复 SHA 的结果。

本补丁提交后要跟踪该精确完整 HEAD 的自动 CI/Web 至终态；不以基线或未提交源码测试冒称新 HEAD 已通过。Designer/Runtime 手动冻结 workflow 仍没有已认证 dispatch 入口，**BLOCKED / NOT_RUN**；没有改触发条件、创建凭据或用用户电脑绕过。未合并、未强推、未发布。
