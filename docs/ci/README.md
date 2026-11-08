# CI 执行范围

日常 CI 按变更范围决定是否执行完整源码检查。检查状态会明确说明是否执行，不把未执行的测试记为已通过。

## 主 CI

`CI` 工作流先通过 `scripts/ci_scope.py` 检查本次完整差异，再保留原来的 Windows / Linux、Python 3.10 两个检查名称。

| 变更 | 完整源码检查 |
| --- | --- |
| 根目录 `AGENTS.md`、`README.md`、`LICENSE` / `LICENSE.md` / `LICENSE.txt` | 不运行 |
| `docs/` 下的 Markdown、RST、TXT、常见图片和 PDF | 不运行；下述特殊文档除外 |
| 仅 `web_panel/`，或同时包含上述普通文档 | PR 到 main、main push 时由 Web CI 接管；旧架构分支 push 仍运行主 CI |
| `docs/operator-development-guide.md` | 运行；测试会执行其中的算子示例 |
| `docs/development/panel-contract-v1.md` | 运行；属于交付 SDK 的输入 |
| 文档目录中的 JSON、YAML、Python 等数据或代码 | 运行；不按文档目录一概跳过测试夹具 |
| 源码、测试、协议、公共依赖、CI 配置、脚本及其他未分类路径 | 运行 |
| 手动执行、没有有效基线、无法取得差异 | 运行 |

PR 使用当前检出的合并结果与事件中 base 提交的差异；push 使用 `before` 到当前检出提交的整个范围。删除和重命名会同时考虑旧路径，避免将源文件移入文档目录后误判成文档改动。

只要混合变更中有一个文件需要完整检查，就执行整套主 CI。修改范围判定脚本、其测试或工作流本身，同样执行完整检查。

范围判定失败或缺少明确结果时，两个平台的检查均失败。确认可以跳过时，只执行状态说明步骤，不安装项目依赖或运行 pytest；GitHub job 完成不代表完整测试已经执行，应以检查摘要为准。

完整检查仍通过 `scripts/ci_check.py` 执行 protobuf、Ruff、mypy 和默认完整 pytest。恢复测试仍在默认收集范围内，只移除了工作流中重复执行的那一轮；失败日志、JUnit 和双任务观察结果继续保留。

## 并发与手动运行

主 CI 和 Web CI 对同一个 PR 或同一个 push 分支取消过时的普通运行。不同 PR、不同工作流分别分组，不跨 push / PR 事件互相取消。

手动运行使用独立的 run ID 分组，不被后续普通提交取消。主 CI 的手动入口始终执行完整源码检查。

## Web 两个平台的分工

Web CI 保留 `web-panel (ubuntu-latest)`、`web-panel (windows-latest)` 和 Windows 软件 Runtime 集成三个检查名称。每次触发 Web CI 时，Linux 执行全部检查；Windows 日常运行重点保留实际平台集成。手动 `workflow_dispatch` 始终在两个平台执行完整 Web 检查。

| 检查 | Linux 自动 / 手动 | Windows 自动 | Windows 手动 |
| --- | --- | --- | --- |
| 完整 Python 后端测试、SDK 版本检查 | 运行 | 运行 | 运行 |
| 前端 Node 逻辑测试 | 全部运行 | 保留下述两个平台相关测试文件 | 全部运行 |
| 前端依赖安装、TypeScript 检查和 Vite 构建 | 运行 | 运行 | 运行 |
| 桌面 / 移动浏览器入口、控制按钮、停止确认与图片身份 | 运行 | 运行 | 运行 |
| spacer / 画布几何、编辑器剪贴板 / 拖拽 / 图片上传 / 保存导出 | 运行 | 运行 | 运行 |
| 真实 HTTP / WebSocket、上传下载、目标隔离、无密码本地模式、官方预览 CLI | 运行 | 运行 | 运行 |
| 模拟 API 的目录刷新、草稿恢复、Dispatch 捕获隔离三个浏览器脚本 | 运行 | 不重复运行 | 运行 |
| Playwright 夹具回归 `test_browser_harness.py` | 运行 | 运行 | 运行 |
| 独立 wheel 构建、离开源码目录安装、静态资源与无 Qt 依赖检查 | 运行 | 运行 | 运行 |

Windows 自动任务保留 `editor_draft_recovery.test.mjs` 和 `editor_round3.test.mjs` 两个完整文件：前者覆盖 Windows 原生模块路径及缓存清理，后者包含 Node 调用 Python 的组件目录校验。同文件中的草稿恢复等业务回归一并保留，不使用测试名称过滤，避免改名后零匹配却成功。

Windows 单独的 `Owning Runtime integration (synthetic data, Windows)` job 沿用原有全部步骤，包含软件 Runtime 的回环连接、同 Runtime 两个 Job 的图片与自动重启验收。这里使用合成数据，不代表真实设备验收。

不在 Windows 自动任务中重复的浏览器脚本为 `catalog_refresh_e2e.py`、`editor_draft_recovery_browser.py`、`editor_dispatch_browser.py`。它们通过 Playwright 拦截 HTTP / WebSocket 验证前端状态逻辑；完整场景仍在 Linux 每次执行，未删除测试或业务断言。涉及平台字体、几何、系统交互、文件与进程入口的检查继续在 Windows 执行。检查摘要会列出本次选择范围，不把未运行的测试记为通过。

## 界面测试的等待方式

`tests/designer/test_workflow_navigation.py` 的 picker 点击、键盘导航和窗口尺寸循环使用已有 `waitUntil` 等待真实状态：先处理 Qt 事件，再等待弹窗已暴露、筛选项可点击或当前标签完整可见，超时仍使测试失败。保留 29 个标签、三种宽度、左右布局方向和原有全部断言，避免每次操作固定等待 50 ms。

一次性建窗、截图和像素检查仍保留原有等待。其他验证真实轮询间隔、停止与恢复时序的测试没有批量缩短等待。

## 其他保留的覆盖

主 CI 的 Windows 原生 Qt、Linux offscreen 和完整源码回归不减量。千次导航 / 显隐、300 次 Join、P0 原型与正常停止重启测试均保持原有执行频率和次数。

历史一次性诊断入口移至 [archived-workflows](archived-workflows/README.md)，原文和固定提交条件保留。其余诊断和打包工作流沿用既有触发条件。

## 局部验证

范围规则使用 Python 标准库即可验证，不依赖 Qt：

```bash
python -m unittest discover -s tests -p test_ci_scope.py -v
```

完整项目环境中的相关回归：

```bash
python -m pytest tests/test_ci_scope.py tests/test_ci_check.py tests/test_ci_pytest_reports.py tests/test_release_workflow_contract.py -q
```

前端组件目录一致性检查读取 Python AST 中的字面常量，不执行 Designer 模块；允许等价格式调整，组件类型缺失或名称不一致仍失败。

导航等待修改可在项目 Python 3.10 环境中单独验证：

```bash
python -m pytest tests/designer/test_workflow_navigation.py -q --durations=10 --timeout=120 --timeout-method=thread
```

Linux 使用 `QT_QPA_PLATFORM=offscreen`，Windows CI 仍使用原生 `windows` 平台。局部耗时对照只说明同环境中该文件的变化，完整 CI 的耗时以对应固定提交的托管运行记录为准。
