# 单算子调试 A0 A1 实施与验证记录

日期：2026-10-09 开始，2026-10-10 完成验证。对应 [实施计划](../../plans/2026-10-09-operator-step-and-workflow-debugger-v1.md) 和 [执行契约](../../operator-step-contract-v1.md)。证据目录沿用计划日期。本批交付公共节点调用基础，不包含可点击的单步调试入口。

本文件是 A0-A1 当时的阶段记录：A0 COMPLETE；A1 COMPLETE，公共调用与正式流程接入已完成源码验证。当时 A2-A5、B 阶段均未实现，完整 CI 仍有既有 mypy 失败。后续已完成 A/B 阶段，见 [最终审查](B0-B3-review.md)；本次全部工作区联合提交及类型问题修复见 [联合交付记录](../2026-10-10-joint-delivery.md)。以下历史结果不改写为新版本验证。

## 工作区与隔离

- 分支：`agent/runtime-workflow-architecture-v1`。
- HEAD：`61a42bf3ab181c3b2339f0425edfb40a60d0ec7b`；验收对象包含既有 dirty 工作区，不能仅靠 HEAD 复现。
- 入口目录：`C:/Users/jsdfhasuh/my_scripts/emo_master`；解析后的物理目录为 `D:/jsdfhasuh/documents/my_project/emo_master`。
- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，3.10.21；依赖版本见每轮 `source-before.json`。
- 每轮验证保存 `src/`、`proto/`、`tests/`、`scripts/` 的文件 SHA256、Git 状态和前后对照。只有 `sourceChangedDuringRun: []` 的轮次才作为固定源码证据。
- 未提交、推送、打包、部署或重启现有 Designer/Runtime；未主动连接真实相机、PLC 或生产数据库。

## 实现内容

| 文件 | 本批内容 |
| --- | --- |
| `src/emo_master/apps/runtime/execution/operator_executor.py` | 共享算子获取、变量绑定、类型校验、调用、服务注入、日志及生命周期；独立调用不需要 CompiledProject |
| `src/emo_master/apps/runtime/execution/errors.py` | 迁移 WorkflowExecutionError，避免独立导入执行器时触发 workflow 包循环导入 |
| `src/emo_master/apps/runtime/workflow/runner.py` | 接入共享执行器；保留拓扑、分支、循环、子流程、结果采集和路由；旧异常导入位置继续可用 |
| `tests/runtime/test_operator_executor_shared.py` | 独立调用与正式流程对照，以及输入输出、变量、服务、生命周期、取消和回执专项 |
| `scripts/operator_step_validate.py` | 不操作运行中应用的基线、能力清单、回归和静态检查证据采集 |
| `docs/operator-step-contract-v1.md` | 60 个内置算子的能力审查及后续 RPC、隔离、输入资产、容量和错误契约 |

共享层的 `invoke()` 保留正式 Runner 的调用时序；Runner 在取得节点诊断后再验证输出和检查取消，因此 SQLite 写入回执不会因后续失败被提前丢弃。独立 `execute()` 负责自身输出校验和调用前后取消检查，后续会话必须在构造/初始化前完成草稿、参数、能力和资源准入。

AST 对照确认 `_operatorForNode`、`disposeOperators`、`closeSession`、清理诊断/事件，以及构造和生命周期辅助函数与改动前代码一致。正式缓存键仍是 workflowId/nodeId；公共层不无条件复制图像输入，不改变生产运行的内存开销。

## 验证记录

| 轮次 | 状态和范围 | 原始证据 |
| --- | --- | --- |
| 基线 | PASS：2407 passed，2 skipped；Runtime、Core、Plugins、SQLite | [baseline/pytest.log](baseline/pytest.log)、[结果](baseline/result.json) |
| 首轮专项 | PASS：213 passed；其中共享执行器专项 28 个参数化用例 | [focused/pytest.xml](focused/pytest.xml) |
| 全量入口首试 | FAIL：87 个 collection errors；误收集 manual_test_workspace 内旧工程，不能视为应用回归结果 | [full-after/pytest.log](full-after/pytest.log)、[结果](full-after/result.json) |
| 指定 tests 全量 | PASS：4138 passed，8 skipped，另有 28 subtests passed；1043.05 秒 | [full-tests-after/pytest.log](full-tests-after/pytest.log)、[结果](full-tests-after/result.json) |
| 独立入口补测红灯 | EXPECTED_FAIL：5 failed、28 passed，准确复现执行后取消与日志回调问题 | [review-red/pytest.xml](review-red/pytest.xml) |
| 最终专项 | PASS：218 passed，含公共执行器 33 个用例；51.91 秒 | [focused-final/pytest.xml](focused-final/pytest.xml) |
| 初次静态对照 | Proto PASS；Ruff 全 src/tests PASS；mypy BASELINE_FAIL，前后相同 10 条错误 | [static-before/result.json](static-before/result.json)、[static-after/result.json](static-after/result.json) |
| 最终静态 | Proto PASS；Ruff 全 src/tests PASS；mypy BASELINE_FAIL，仍与改动前相同 | [static-final/result.json](static-final/result.json)、[mypy](static-final/mypy.log) |

全量入口已改为 `pytest ... tests`，只选择当前仓库测试根；没有删除旧工作区，没有修改 pytest 配置、CI 门禁或忽略失败用例。原始失败证据保留。

首次提取中的 workflow 循环导入和未使用导入问题已修正，并在随后专项及静态检查中通过。最终修正补齐独立入口执行后取消检查及默认日志回调适配；日志实际送达、关闭后迟到日志丢弃，以及取消后仅保留本次 SQLite 提交回执都有断言。

全量回归发生在最终两处独立入口修正之前；修正后重跑 218 项专项和静态检查，没有再次跑全部 Designer/UI 测试。全量后的源码变化仅有 `operator_executor.py` 和其专项测试，正式 Runner 未再修改。不得把 4138 的全量结果描述为最终修正后的再次全量结果。两轮专项仅保存 JUnit；全量及静态检查同时保存原始日志和源码快照。

全量 8 个 SKIP 分别为：1 个未开启的真实相机 smoke、6 个当前 Windows 无创建符号链接权限的用例、1 个仅适用于非 Windows 的用例。无真实相机验收声明。

最终哈希复核：基线中已存在文件只有本批证据脚本和 `workflow/runner.py` 改变；新增源码/测试仅 `execution/errors.py`、`execution/operator_executor.py` 和 `test_operator_executor_shared.py`，无删除。其余原有 `src/proto/tests/scripts` 文件哈希保持不变。`git diff --check` 对 Runner 通过，本批脚本和源码/测试的定向 Ruff 通过。

### 原有 mypy 问题

`static-before` 使用保存的原 Runner 做 `--shadow-file` 对照，`static-after` 与 `static-final` 使用抽取后的 Runner。三轮 `: error:` 行逐条相同，涉及 5 个本批未修改文件：

- `_communication_operators.py:802`，1 条。
- `designer/ui/runtime_panel.py:102`，1 条。
- `core/workflow/compiler.py:182`，1 条。
- `runtime/presentation/exporter.py:51`，1 条。
- `designer/ui/main_window.py:2973/4356/4372/4392`，6 条。

详细错误保留在 [修改前 mypy](static-before/mypy.log) 和 [修改后 mypy](static-after/mypy.log)。不为本批通过而关闭类型检查或修复无关并行改动。不能宣称完整 CI 通过。

## 复跑

在仓库根目录，使用一个尚未存在的标签；脚本拒绝覆盖历史证据：

```powershell
$env:PYTHONPATH = 'src'
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe scripts/operator_step_validate.py new-backend-label
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe scripts/operator_step_validate.py new-full-label --full
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe scripts/operator_step_validate.py new-static-label --static-only
```

脚本为测试设置 `QT_QPA_PLATFORM=offscreen`。Offscreen 通过不等于原生 Windows UI 点击、文本像素、缩放矩阵或真实设备验收。

## 后续边界

- A2 NOT_STARTED：草稿准入、调试会话、spawn 监管、幂等、取消期限、租约、资源互斥及 RPC。
- A3 NOT_STARTED：完整多端口输入、资产预算、固定输入副本、结果及有效参数快照、隔离变量。
- A4 NOT_STARTED：Designer 入口与可用交互；当前不会出现新的“单步执行”按钮。
- A5 NOT_STARTED：新能力的集成、故障注入、资源长测和支持范围验收。
- 实机、原生 UI 新功能、冻结包、发布、部署：NOT_RUN。
- B 流程调试器：NOT_STARTED；须先完成 A 阶段出口，不提前添加断点/暂停控制。
