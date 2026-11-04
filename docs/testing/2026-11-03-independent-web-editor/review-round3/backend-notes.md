# Round 3: selected package recovery

审查源码 HEAD：`d92ffd37960e8b8f527136151ab9ccb80054e78f`。工作分支为 `codex/logging-consolidation-20261103`，同时包含此前未提交的独立编辑器、发布和审查修复。本项未提交、推送、合并或部署。

## 修前反例

用户提供的整个反例目录原样复制到 `reconnect-before/`。原始报告另存为 `browser-report-supplied.json` 和 `backend-report-supplied.json`，之后先执行两个原脚本，再修改产品。日志为 `reconnect-browser-before.log`、`reconnect-backend-before.log`。

- Edge + 用户提供的构建产物 + 模拟 HTTP/WS：发布通知仍保留旧版，但一次普通 WebSocket 断连后，无刷新、切包或 Start/Stop 操作即自动选择新包。Job 未变，新来源显示 `SOURCE_NOT_CAPTURED`。
- 正式 FastAPI/EditorStore + MockRuntime：旧 `.emoweb` 可下载，既有旧 view 保留旧包；旧包读取、新建旧 view 均返回 404。任务冻结请求保持不变，仅有一次明确 Start。
- 新回归 `test_selected_package_recovery.py::test_recover_old_release_same_job_and_frozen_coverage` 在产品修改前执行失败：`404 != 200`，保存于 `selected-package-before.log`。

## 局部修复

`main.tsx` 将目录/控制同步、连接恢复与明确采用新包分开。默认同步和 `quiet` 刷新继续查询已选的不可变包；工具栏明确刷新、包管理明确激活才采用当前启用版本。下拉切包是明确用户意图，并用已有目录 generation/abort 机制取消在途同步，避免旧响应覆盖用户刚选的版本。当前启用目录里仍列出新版，同时保留已选旧包选项。真正的实例/工程变化继续清空旧查看与控制选择。

`app.py` 继续只在目录返回当前启用包。精确读取旧包和创建旧 view 可复用已持久化的 `package_targets` 历史关联，且要求该包曾在当前目标启用、工程匹配、明确 Runtime 实例匹配、当前会话及目标查看权限仍成立。管理员也不能绕过目标关联而恢复任意上传包。缺少历史恢复所需身份返回 404，错误工程/实例返回 409，撤销权限返回 403。新建 view 在读取任务后再次核对实例与工程。前端精确读取和新建 view 均携带当前 `runtimeInstanceId`。

没有新增页面版本注册表、采集请求变更或 Start/Stop 调用。历史页面可恢复并不代表新增来源已被旧 Job 采集；覆盖继续由所选 Job 的冻结定义判断。

MockRuntime 的 `source_problems` 原先比较原始字典：请求包含 `name:null`，不可变包省略 null，导致同一来源也被误报未采集。本轮保持严格同源断言，将其改为正式 `DataSource` 模型比较。真实 Runtime 的覆盖判断未因此改变。这个中间失败仅出现在新测试的 Mock 覆盖断言，后续模型比较修正后通过；完整后端首轮还暴露缺身份旧包状态码 409/原回归期待 404 的差异，已保留 `backend-full-before-context-status.log` 并恢复 404，未改动原正确断言。

## 执行结果与层级

- `selected-package-after.log`：5 passed，12 subtests passed。恢复旧版本/同 Job/冻结配置，当前目录只列新版，新增来源未采集，错误目标/工程/实例、缺少明确身份、实例替换、当前查看权限撤销，以及从未启用包不能恢复。
- `selected-package-browser-final/browser-report.json`：实际安装的 Edge headless、当前构建的 React、模拟 HTTP/WS。查看者：通知保留旧包 → 一次自动重连仍旧包/修订/同 Job → 用户明确点击刷新才采用新包；管理员：包导入触发 quiet 同步仍旧包 → 通知/自动重连仍旧包 → 用户明确下拉切新版。新增来源显示 `SOURCE_NOT_CAPTURED`，请求记录没有 `/api/runs/*`。真实实例/工程身份替换时清除旧 Job。每阶段报告保存包 ID、footer 修订、view 请求、Job、用户动作、请求上下文、页面错误及构建产物 SHA-256，并提供截图。此前仅明确刷新两角色的通过记录仍保留于 `selected-package-browser/`。
- `catalog-refresh-browser.log`：既有实际 Edge/React 目录回归通过，包含跨目标延迟响应、同 ID 的新实例、控制结果 quiet 同步、会话失效与未确认请求不重放。
- `backend-full.log`：183 passed，230 subtests passed，62.51 秒，无 skip，包含临时实际软件 Runtime 检查。第一轮失败记录保留于 `backend-full-before-context-status.log`。
- `frontend-node.log`：前端 Node 测试 105 passed，无 skip；`frontend-build.log`：TypeScript/Vite 通过。最终统一构建、Web wheel/安装后的完整闭环及其摘要由主交付记录补充。
- `backend-ruff.log`：`--select F` 仅报告 `app.py` 已有的 `urlsplit` 未使用、logout 的 `who` 未使用；这两条在原 HEAD 已存在，未删除。Mock、专属后端测试和浏览器脚本没有新增 F 类问题。

完整后端命令（PowerShell，在仓库根目录执行）：

```powershell
$env:PYTHONPATH='src;web_panel/backend;web_panel/tests'
$env:PYTHONUTF8='1'
$env:EMO_WEB_PRODUCT_RUNTIME='1'
$env:EMO_WEB_PRODUCT_PYTHON='D:\Conda\envs\emo_master\python.exe'
$env:EMO_WEB_PRODUCT_EVIDENCE_DIR='docs/testing/2026-11-03-independent-web-editor/review-round3/product-runtime'
$env:EMO_WEB_ROUND2_EVIDENCE='docs/testing/2026-11-03-independent-web-editor/review-round3/backend-isolation'
$env:EMO_WEB_REVIEW_EVIDENCE_DIR='docs/testing/2026-11-03-independent-web-editor/review-round3/backend-isolation'
& web_panel/.venv/Scripts/python.exe -m pytest web_panel/tests -q
```

专属浏览器命令：先在物理 D: 的 `web_panel/frontend` 执行 `npm run build`，再在仓库根目录执行：

```powershell
$env:EMO_WEB_RECONNECT_EVIDENCE='D:\jsdfhasuh\documents\my_project\emo_master\docs\testing\2026-11-03-independent-web-editor\review-round3\selected-package-browser-final'
& web_panel/.venv/Scripts/python.exe web_panel/frontend/tests/selected_package_recovery_browser.py
```

这份记录没有声称原生 Qt 人工操作、跨机器 LAN、安装后 Runtime wheel 完整检测、真实设备或现场验收通过。安装后 Web → 源码软件 Runtime 的完整闭环另由主交付执行，不能与安装后 Runtime wheel 的实际运行混同。
