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
