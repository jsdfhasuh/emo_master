# Web 浏览器 CI 标签与发布入口修复

执行与记录：dot（OpenAI AI 助手），2026-11-04。此为 PR #11 的增量修复，
针对远端 head `6a5e2dae7393a7cbc87b89ebefd0cf0d28755426` 的
[Web CI run 41829116842](https://github.com/jsdfhasuh/emo_master/actions/runs/41829116842)。
独立云端副本的对应集成树为本地 `9ce2974`；未操作用户电脑或真实设备。

## 失败证据与最小修改

- Ubuntu job `126571389844` 在 `editor_draft_recovery_browser.py:170` 失败。
  `restart`、`expire_and_logout`、`project_switch` 已通过；CAS 比较断言报
  `get_by_label("本地草稿源", exact=True)` 未找到元素。
  CI 的 aria snapshot 同时保留了两份正确的源内容，未显示草稿丢失。
- 根因：React 将受控 textarea 的源同步为文本节点，而 textarea 嵌在 label 内，
  label 的文本变成“本地草稿源 + 完整 JSON”。Playwright 的精确标签查询不再匹配。
  改为独立的 `label htmlFor` 与 textarea `id`，保留两个字段的完整源和只读属性，
  不改原浏览器断言，也不绕过 CAS 或减少测试。
- 在既有 React/jsdom CAS 测试增加关联标签的精确文本断言，并继续检查两份源。
  同一新增断言在修复前失败，实际值确为标签文字后带完整 JSON；修复后通过。
- 顺查后续包/网格脚本发现，它查找名为“发布”的按钮，但真实工具栏按钮是“发布页面”。
  修正该精确入口定位，保留 HTTP 成功、`PUBLISHED` 和 Runtime 零控制调用断言。
  Dispatch 浏览器脚本静态复核未发现本次相关定位错误，未改其断言。

## 实际验证与边界

- 定向 React/jsdom CAS 回归：修复前 1 项失败；修复后 1 项通过。
- `npm --prefix web_panel/frontend test`：123 项通过，0 失败、0 跳过。
- `npm --prefix web_panel/frontend run build`：TypeScript 与 Vite 通过。
- `python web_panel/scripts/package_frontend.py`：通过。
- 三项新增 Python 浏览器脚本语法编译与 `git diff --check`：通过。
- 本地重试 Chromium 仍在进入页面前失败：
  `process_singleton_posix.cc:297: socket() failed: Operation not permitted (1)`。
  因而此处不声称三组浏览器回归已通过；最终需以修复提交的 Ubuntu/Windows CI 为准。
  不以 jsdom 通过替代浏览器、实际 Runtime、原生 Qt 或现场设备验收。

## Dispatch 夹具回调注册修复

执行与记录：dot，2026-11-04。后续
[run 41830732268](https://github.com/jsdfhasuh/emo_master/actions/runs/41830732268)
基于远端 `26cd85d9`（本地同树 `6509dfb`），Ubuntu job `126576936936` 与
Windows job `126576936612` 的草稿恢复均已通过，随后在 Dispatch 脚本同一行失败：

```text
context.route_web_socket('**/ws/views/dispatch-view', sockets.append)
AttributeError: 'builtin_function_or_method' object has no attribute '_pw_impl_instance_'
```

这是页面打开前的测试夹具错误，不是 Dispatch 产品分组逻辑失败。Playwright 的
Python 回调包装器会在 handler 上缓存包装元数据，而内置 `list.append` 不能被
设置属性。改用普通 Python lambda 追加相同的 socket，保留所有捕获隔离断言。
未改变产品代码、消息格式、捕获范围或测试等待条件。

增加 `frontend/tests/test_browser_harness.py` 并纳入同一 CI 步骤：读取真实浏览器
脚本中的回调表达式，用当前安装 Playwright 的真实包装器注册和调用，核对两条
route 的顺序与包装缓存复用，不需要启动浏览器。此为夹具兼容回归，不替代浏览器。
本地包装器对旧 `sockets.append` 复现同一 AttributeError；新回调测试 1 项通过。

顺查同脚本后续路径：HTTP 预览返回的 runIds、WebSocket URL 与实际 Editor 匹配；
结果 DTO、来源 ID 与 scope 对应；普通 Job、无捕获根 Job、单个 consumer 的断言
均保留。未发现另一个确定错误，不据静态检查声称这些浏览器路径已通过。

本轮 `npm --prefix web_panel/frontend test` 为 124 项通过、0 失败、0 跳过；
两项 Python 脚本语法编译和 `git diff --check` 通过。本轮仅修改夹具与 CI，未改
产品源，未重复构建。此前本地 Chromium 已确认启动前 IPC socket EPERM，未再次
尝试绕过限制；本轮完整浏览器执行仍以新 CI 为准。
