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

## 本轮保留的覆盖

Windows 原生 Qt、Linux offscreen、全部业务回归、Web 两平台浏览器检查以及真实 Runtime 集成保持原有执行内容。长循环、原型测试和平台矩阵没有在这一轮降频。

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
