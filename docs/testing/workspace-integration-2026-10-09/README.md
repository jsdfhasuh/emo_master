# 工作区整理与集成提交验证

日期：2026-10-09。分支：`agent/runtime-workflow-architecture-v1`。

## 提交范围

- `7ebac95af42c7b17ac4015c1bf60061a422c50a7`：集成相互依赖的全局变量 2.4、While 布尔条件、控制流执行与关系显示、自定义工程文件名、批量图片、参数中文名、相机草稿预览和 PLC 调试源码、协议、测试及使用说明。共享文件的改动已交织，因此没有把它们拆成无法独立使用的功能片段。
- `67258caa7b3f0d44492f8cffbb5ada0656382954`：补齐可离线批量图片示例的端口契约；保留最小参数 schema，以覆盖晚到目录的元数据补全。
- 后续文档提交只整理验证资料，不改变上述源码。

## 本地工作保留

- 原 `examples/image_batch_while/image-batch-while.emoproj` 含本机相机配置，连同 `.bak` 原样保留，不纳入共享示例。没有覆盖、搬走或删除这些文件。
- 共享示例为 `examples/image_batch_while_portable/image-batch-while.emoproj`，基于原有离线示例备份，使用相对图片目录，不调用相机。测试与验证脚本显式使用该独立路径；没有放宽原测试断言。
- 475 个仅本地保留的文件包括现场示例和重复／中间验证产物，通过本仓库 `.git/info/exclude` 的精确文件列表排除，不增加会隐藏未来源码的宽泛规则。清单及整理前 SHA-256 见 [local-artifacts.json](local-artifacts.json)，全部经整理后字节校验。
- 整理前的 1030 个修改／未跟踪文件已备份到本地 `.git/workspace-cleanup-20261009/before-20261009-105001.zip`，包括 Git 状态、基线提交及原始文件哈希。备份不会随提交发布。
- 保留历史最终证据及必要的失败记录；重复中间截图和临时补丁留在本地。历史报告描述的是当时快照，不应将其中的旧失败数、源码数量或 NOT_RUN 状态当成本次最终源码结论。

## 日志原始性

为通过 Git 空白检查，108 份历史日志只统一了编码、换行和行尾空白，不更改测试结果。每份原始字节另存为同名 `.txt.raw.gz`；[normalized-evidence.json](normalized-evidence.json) 同时记录原始和整理后 SHA-256，所有原始压缩文件已解压校验。已有带原始压缩文件的相机验证资料保持不变。

## 最终源码的验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| protobuf 生成漂移检查、全 `src/tests` Ruff、Mypy | PASS；Mypy 355 个源码文件；后续修复仅改示例端口 | [首轮 CI 日志开头](ci-initial.txt) |
| 完整 Designer 测试组 | **879 passed** | [designer-final.txt](designer-final.txt) |
| 从 Git 提交导出的干净快照，不含任何忽略文件或现场示例 | **111 passed** | [clean-snapshot-final.txt](clean-snapshot-final.txt) |
| 可离线示例、元数据补全、控制流与新旧 While 编辑回归 | **54 passed** | [portable-example-final.txt](portable-example-final.txt) |
| staged diff 空白检查 | PASS | 提交前 `git diff --cached --check` |

测试集存在重叠，不相加作为独立测试总数。干净快照验证覆盖了可离线示例、全局变量、文件绑定拒绝、管理窗口并发设值和变量选择刷新。

[source-manifest.json](source-manifest.json) 记录最终源码提交以及本地 857 个源码、协议、测试、脚本和示例文件的字节哈希，最终验证期间未发生源码漂移。Git 导出快照使用 Git 的规范化换行；本地字节哈希不承诺与其他平台 checkout 的行尾格式一致。

### 首轮整套 CI 的失败与修复

首轮整套 CI 发现 `testLegacyWhileShowsBooleanOutputAndCanExplicitlySwitchToBooleanMode` 失败：新建的最小示例没有声明普通算子的端口，尚未加载目录的 Designer 在切换 While 模式时无法保留全部数据连线。补齐示例端口后，该用例及完整 Designer 组通过；没有修改生产运行代码或弱化测试断言。

首轮最终结果：**1 failed, 3977 passed, 8 skipped, 28 subtests passed**，耗时 1068.39 秒。唯一失败就是上述用例，完整诊断保留在 [ci-initial.txt](ci-initial.txt)。

这次示例补齐发生在首轮 CI 已完成 Designer 部分、仍在执行后续测试时。因此首轮日志是诊断记录，不是最终源码的一次不可变、全通过 CI 证明；最终源码另由上述完整 Designer 和干净提交快照验证。最终源码的整套 CI 未再次完整运行（**NOT_RERUN**），不能声称全套 CI 已通过；机器可读记录见 [verification.json](verification.json)。

本次新增的四份测试日志也保留原始压缩字节；规范化哈希另见 [integration-log-normalization.json](integration-log-normalization.json)。仅规范化编码和空白，不改变结果。

## 复现与边界

使用现有 Conda `emo_master` 环境，无依赖升级：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTEST_ADDOPTS = 'tests'
& "$env:USERPROFILE/.conda/envs/emo_master/python.exe" scripts/ci_check.py
```

`tests` 限定的是仓库正式测试集合，避免收集本地 `manual_test_workspace` 历史副本；没有跳过正式测试。

只有本地 Git 提交。未推送、发布、构建便携包、操作相机／PLC 硬件或重启用户正在运行的 Designer／Runtime。历史原生 Qt 截图只证明其对应源码快照的模拟界面验证，不代表硬件或现场验收。
