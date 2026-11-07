# Windows CI 测试兼容性补充

执行身份：**ChatGPT Web**。本补充基于 PR head `6372ecfb8eaab7e0a8d9df687848915788b79109`。产品源码没有变化；本次修正三个测试文件的跨平台假设，并把本轮四个回归文件安排在原全仓 CI 之前执行，单独上传原始 JUnit。

## 保留的失败结论

[原全仓 CI 42144922848](https://github.com/jsdfhasuh/emo_master/actions/runs/42144922848) 的实际 checkout 是 PR merge `9eb1ad7193aab10f3350e47fb118924fd4330287`，与 head 仅 `AGENTS.md` 不同，产品代码和测试一致。

| 平台 | 实际结果 |
| --- | --- |
| Linux | 9205 passed、31 skipped、42 subtests passed、3 warnings，pytest 2200.81 秒。 |
| Windows | **5 failed**、9226 passed、5 skipped、42 subtests passed、3 warnings，pytest 3864.63 秒。 |

两平台 protobuf、ruff、mypy（614 个源码文件）均通过。Windows 原生 Qt 环境为 `windows`、1920×1080、scale 1.0。原失败日志、JUnit、artifact ZIP 与摘要均保留；没有把这次 Windows 全仓结果改写为通过。JUnit 的 tests 属性包含 42 个子测试，不能与 9236 个 testcase 元素混为同一计数。

## 五项失败的具体原因与修改

| 用例 | 原因 | 最小修改与保留的验证 |
| --- | --- | --- |
| `testRetirementFailurePersistsFinalProofInExistingEventStoreAndJsonl` 两个参数 | 产品固定写入 UTF-8 JSONL，测试 `read_text()` 使用 Windows 默认 CP1252，遇到 byte 0x8f 解码失败。 | 只给测试读取指定 `encoding='utf-8'`。保留 EventStore 与 JSONL payload 完全一致的断言。对真实测试输出用 CP1252 读取可复现原位置 449/450 的错误。 |
| `testFrameWriteEOFKeepsOriginalUnknownReceiptThroughNestedCleanup` 两个参数 | 原测试只接受 EOFError；Windows 的同一 peer-close 在直接 ReadFile 与 overlapped 完成阶段分别可能表现为 WinError 109 或 errno 109。 | 保留 EOFError，并且仅在 Windows 接受明确 ERROR_BROKEN_PIPE=109 的 OSError；新增 transportError 与原 cause 类型相符断言。原异常对象身份、UNKNOWN、requestId、cycle、cleanup 顺序及不重放等断言继续保留。 |
| `testReplySendBudgetStartsAfterSerialization[0.0-2.25-False]` | 测试在桥已按预期关闭后先调用 `poll()`；Windows 的 PeekNamedPipe 在此处抛 WinError 109，尚未执行原 EOF 断言。 | 成功回包分支保留 poll；超时分支直接严格检查 `recv_bytes()` 的 EOFError。保留 parent.closed、恰好一次执行/发送、精确 2 秒预算及唯一 TimeoutError 回执。 |

没有改变产品异常分类、取消行为、日志编码、设备/图像所有权、发送超时或 UNKNOWN 的处理。

## 本地验证

四个文件联合运行：**50 passed in 6.66s**，无失败或跳过。测试期间四个文件的 SHA-256 未变化，见 [结构化收据](combined-recovery-linux.json)、[原始输出](combined-recovery-linux.txt) 与 [JUnit](combined-recovery-linux.xml)。三个独立定向验证也分别通过，但与联合结果重叠，不累加总数。

Ruff 与 `git diff --check` 通过。工作流已解析并核对：新增早期回归步骤在原 `Run project checks` 之前，失败会阻断后续步骤，失败 JUnit 仍上传；原完整命令 `python scripts/ci_check.py` 继续保留，未跳过全仓测试。早期产物名称带平台和 run attempt，保留每次运行的结果。

这是 Linux 本地验证。补充提交的 Windows 原生结果，以其 Actions 和对应的 **ChatGPT Web** 评论为准，不把本地 50 项通过写成 Windows 已复跑。

## 验收边界

产品实现仍与 `6372ecf` 一致；该提交的双平台 Web 和 Windows 软件 Runtime 自动化已通过，见 [Web CI 42144922863](https://github.com/jsdfhasuh/emo_master/actions/runs/42144922863)。本次没有增加人工桌面或真实设备验收结论，也没有改变剩余历史 F2/F4 触发因素尚未唯一确定的边界。Dot 应使用最新 ChatGPT Web 评论明确给出的固定提交 SHA。
