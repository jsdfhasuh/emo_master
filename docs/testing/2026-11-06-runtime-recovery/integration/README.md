# 最终 Runtime 集成回归

执行身份：**ChatGPT Web**。2026-11-06 在 Linux、Python 3.10.22、仓库声明依赖、`QT_QPA_PLATFORM=offscreen` 下执行完整 `tests/runtime`。基线为 `cdf1250ccc41b78f79b25b87e6db197809875384`，修复代码与本目录一同提交；固定交付 SHA 见 PR #11 的 ChatGPT Web 评论。

## 最终结果

- **2970 passed、7 skipped、0 failed / errors**，总计 2977 个用例。
- pytest 报告耗时 782.02 秒；外围进程及收据计时 784.449 秒。
- UTC 开始时间 `2026-11-06T01:19:27.353547+00:00`，结束时间 `2026-11-06T01:32:31.802166+00:00`。
- [结构化收据](runtime-suite.json) 中 `exitCode=0`、`sourceChangedPaths=[]`。
- 19 个修改的源码/测试文件在运行前后 SHA-256 一致，见 [源码指纹](runtime-suite-source-hashes.json)。
- [完整控制台记录](runtime-suite.txt) 与 [gzip 压缩原始 JUnit](runtime-suite.xml.gz) 一并保留。解压后的 JUnit SHA-256 为 `5a65bb5831d289a2b01e9d248767f9bb537e9fa2477bef5a1e53dab0e532b903`。

原命令见结构化收据，核心为：

```text
python -m pytest tests/runtime -q --tb=short --timeout=120 --timeout-method=thread -o faulthandler_timeout=60 --junitxml=build/recovery-runtime-results.xml
```

## 未执行的 7 项

| 类别 | 数量 | 原因 |
| --- | ---: | --- |
| managed IMV | 4 | 现有声明仅支持 Windows Runtime。 |
| Windows 路径语义 | 1 | 当前运行平台是 Linux。 |
| Operator 1000 次执行 / 50 次会话专项 | 1 | 需要显式开启的专项验收，本轮未启用。 |
| Designer 65 秒 live lease 专项 | 1 | 需要 `EMO_DEBUG_STRESS=1`，本轮未启用。 |

具体用例全名与原始 skip reason 均见 JSON；跳过不计为通过。本次是 Runtime 自动化与真实软件子进程验证，不是全仓 9000 多项回归、可见人工 Web 验收、Windows 原生桌面验收或真实相机 / PLC 验收。

## 保留的失败记录及修正

1. [首次收集失败](runtime-suite-collection-failure.json)：工作树采用稀疏检出，缺少仓库已有的 `prototypes` / `field_tests` 夹具。补齐原仓库目录后重跑，没有删除或跳过用例。
2. [首轮执行](runtime-suite-integration-failure.json)：2966 passed、7 skipped、1 failed。失败是既有 `testCleanupCancellationCannotReplaceOriginalCycleFailure`，指出新实现给普通取消追加 `cycleCleanup` 改变了主异常诊断。产品修复恢复普通 `CancellationRequested` 的旧行为，仅在确有 `DeviceError` 清理失败时追加诊断；原测试及正确断言保持不变。原控制台、JUnit 和当时源码指纹保留在同名文件中。
3. 最终审查发现诊断改动使回复序列化时间被提前计入既有 2 秒发送预算，已恢复先完成 `encode_reply` 再开始发送计时。使用真实 Pipe 分别对照序列化暂停与实际发送暂停，见 [原对照](../device-observability/reply-deadline-review.json) 和 [修复回执](../device-observability/reply-deadline-review-restored.json)。没有延长原发送预算。

两处修正后的受影响回归为 **76 passed**，之后执行本页所述最终完整 Runtime 套件。

## 静态检查

[quality-checks.json](quality-checks.json) 记录全源 ruff、mypy（614 个源码文件）、protobuf 生成一致性与 diff 检查均通过。这些检查不替代 [Dot 可见人工复验](../DOT_ACCEPTANCE.md)。

提交打包时，原始 pytest / Node 失败转录中的空白行和源码回显会触发 `git diff --cached --check` 的 trailing-whitespace 提示。原始记录按字节保留，不为格式检查改写失败证据。最终源码、测试、脚本、JSON 和 Markdown 的检查另行通过；完整提示及检查范围见 [packaging-checks.json](packaging-checks.json)。
