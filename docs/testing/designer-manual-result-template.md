# EmoMaster Designer 人工验收结果模板

请由实际执行 Windows 桌面测试的用户填写。自动化测试结果不能替代窗口、交互、进程和 DPI 的人工观察。

~~~text
FRONTEND_MANUAL_TEST=PENDING
BRANCH=
HEAD=
TEST_DATE=
TESTER=

AUTOMATED_GATE=
WINDOWS_DESKTOP_SMOKE=
PROJECT_CREATE_SAVE_LOAD=
WORKFLOW_BOUNDARY_NODES=
SUBFLOW_DATA_FLOW=
REPEAT=
FOREACH_CONFIG=
WHILE_CONFIG=
LIVE_EVENTS=
RUNTIME_VISUAL_STATE=
GRACEFUL_STOP=
FORCE_STOP=
CLOSE_WHILE_RUNNING=
EMBEDDED_RUNTIME=
EXTERNAL_RUNTIME=
DPI_100=
DPI_125=
DPI_150=
UNHANDLED_EXCEPTION=
RESIDUAL_PROCESS=
RUNTIME_LOCK_RESTART=
FINAL_RESULT=
~~~

## 问题清单

| 编号 | 用例 | 严重度 | 结果 | 截图/日志 |
|---|---|---|---|---|
|  |  | Blocker/Major/Minor | OPEN/PENDING/FIXED |  |

## 判定规则

- 只有全部关键项完成实际观察且没有阻断缺陷，才填写 FRONTEND_MANUAL_TEST=PASS 和 FINAL_RESULT=PASS。
- 存在阻断缺陷时填写 FRONTEND_MANUAL_TEST=FAIL 和 FINAL_RESULT=FAIL，并填写问题清单。
- 未执行或无法判断的项目保持 PENDING，不得猜测结果。
- WINDOWS_DESKTOP_SMOKE=PASS 只能由用户在真实 Windows 桌面观察后填写。
- DPI_100=PASS、DPI_125=PASS、DPI_150=PASS 只能由对应缩放级别的实际观察后填写。
- 自动化门禁、桌面 smoke 和 DPI 结果分别记录，不得互相替代。

## 证据位置

- 指南：docs/testing/2026-08-25-designer-manual-acceptance.md
- 工作区：manual_test_workspace/
- 截图：manual_test_workspace/screenshots/
- 日志：manual_test_workspace/logs/
- 环境：manual_test_workspace/evidence/environment.txt
- 问题记录：manual_test_workspace/issue-template.md
