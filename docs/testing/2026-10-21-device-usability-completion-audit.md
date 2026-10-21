# 设备易用性计划完成复核补记

日期：2026-10-21。依据：[计划 v1](../plans/2026-10-21-device-usability-improvement-plan-v1.md)，
关联[实施记录](2026-10-21-device-usability-improvement.md)及
[独立证据目录](../evidence/2026-10-21-device-usability-improvement/README.md)。

完成复核发现 §8.2 尚有一个未落实细节：草稿模式移除了所有缺失 selector 的默认值，
连可选 selector 的既有默认规则也一起改变。前次 `e2a9d86` 的软件完成结论因此补充修正，
此次以 `236d600` 的直接反例修复和完整独立快照验证作为最终源码依据。
目前 U01—U10 及 C1—C6 在本轮软件范围内已落实。

## 缺口、直接反例与修正

源头为 `src/emo_master/core/plugin/devices.py::validateDeviceBinding` 的
`allowMissingSelectors=True` 分支。原逻辑对所有未填写的 properties 移除 default；
当第三方算子同时声明必需 channelId 和有默认值的可选 backupChannelId 时，草稿结果为 `{}`，
计划要求的结果应为 `{'backupChannelId': 'a'}`。

在 `tests/designer/test_device_usability_drafts.py` 增加
`testPendingSelectorsPreserveOptionalDefaultsWithoutFillingRequiredSelector`：

- 必需 channelId 即便有 default 也不代填，保持待选择状态。
- 可选 backupChannelId 保留默认值，并按真实设备候选检查。
- 严格调用仍沿用原有默认值规则；不修改调用方 requirement 或 binding。
- 可选默认值指向不存在的通道时仍拒绝，错误保留 backupChannelId 位置。

未修正源码的首次结果为 **1 failed**，完整保留 `selector-default-first.log/xml`。
产品修正只将移除 default 的遍历范围改为 schema.required，随后清除草稿模式的 required 检查；
所有已填字段与可选默认值继续经过原共享校验，没有设备 I/O。
定向批次 **25 passed in 2.63s**，保留 `selector-default-fixed.log/xml`。

```powershell
$env:QT_QPA_PLATFORM='windows'
$env:PYTHONIOENCODING='utf-8'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' -m pytest -q `
  tests/designer/test_device_usability_drafts.py tests/core/plugin/test_device_registry.py `
  --junitxml=docs/evidence/2026-10-21-device-usability-improvement/selector-default-fixed.xml
```

源码提交：`236d6008651a6f1bde5daebc999f83065555940a`，
tree：`ecdb1063bdd6da2f85f30cfd2fe0b4b97c514d08`。
该提交只有共享校验和上述反例测试两个文件。

## 最终源码验证

使用 conda Python **3.10.21**、PySide2 **5.15.2.1**、Qt **5.15.2**，Windows `10.0.26200`，
`QT_QPA_PLATFORM=windows`、`PYTHONIOENCODING=utf-8`。所有设备为模拟驱动或 localhost，工程位于临时目录。
从固定源码提交 `git archive` 导出，实际导入位置为：

`C:/Users/JSDFHA~1/AppData/Local/Temp/emo-f-a5vawtih/source/src/emo_master/__init__.py`。

完整命令位于实施记录；本次入口参数为 `--source-sha 236d6008651a6f1bde5daebc999f83065555940a`
和全新 `--output-dir .../completion-audit-snapshot`。每条真实 argv、cwd、时间、退出码及
29 个改动文件的提交 blob 对比保存在该目录的 `validation.json`。

| 批次 | 实际输出 | 退出码 |
| --- | --- | --- |
| 环境／导入／改动 blob | 指定环境，导出文件全部等于固定提交 blob | 0 |
| CHAIN | **61 passed in 85.29s** | 0 |
| REGRESSION | **349 passed, 2 deselected in 137.50s** | 0 |
| Ruff | 23 个受改 Python 文件，含两个已提交证据入口 | 0 |
| mypy | 13 个受改源码文件 | 0 |
| `scripts/gen_proto.py --check` | 协议一致 | 0 |
| `git diff --check 756e526c… 236d6008…` | 无差异格式问题 | 0 |

JUnit 分别确认 61／349 个用例，failures、errors、skipped 均为 0，总计 **410 passed**。
两个 deselected 仍仅为旧迁移用例 `testUpgradedDraftResourceFailureCanBeFixedAndRevalidated`
的原参数化分支，没有新增排除。pytest 时间取标准输出，runner 的命令耗时另保留在 JSON，未混用。
定向 25 项属于此前批次，不累加为最终总数。

本次实际重跑 C1—C6：禁用空通道设备与待绑定草稿保存重开、恢复与统一历史、未保存草稿的显式发布、
局部候选隔离、不完整节点准确拒绝、补齐后正式 Job 的真实 TCP 字节和模拟图像、模板及设备／流程／运行包再次运行。
原 A1—A6 及 R01—R03 同批次回归通过；包含停止、维护、服务健康与活动、冻结身份与 JSONL 单次持久化，
以及 Server／单次 TCP 的有界字节预览。断言和产物依据仍见实施记录的逐项映射。

三类包本次导入正式 Job：设备包 `7ad260c7-9609-4fc6-ae29-bee2db52163d`，
流程包 `a9552fb0-4640-44d8-a834-12ef5c4ab730`，运行包 `0d6edd1f-22d1-4f59-a845-2046ddc76149`。
原工程、包、导入工程、实际诊断和截图均保留在新快照，未覆盖前一轮产物。

## 证据层次与交付核对

前次 `e2a9d86` 的 409 项继续保留于 `final-snapshot-delivery/`，其 SHA-256 清单与索引
在此次更新前先完整校验，再保留历史副本；首次 selector 失败与新的全部输出纳入更新后的清单。
`completion-audit-checks.json` 记录本次清单校验、文档链接、差异与并行工作保护。

真实桌面走查仍绑定 `848ad46` 和 `e2a9d86` 两个实际源码快照。此次局部校验修正重跑了
原生 Windows Qt 自动化、正式 Runtime 连续链和受影响回归，没有再次执行桌面输入走查。
人工完整 UI、真实相机／PLC／外部 TCP 设备、冻结 EXE、Linux 和跨机器均为 **NOT_RUN**，
具体原因沿用实施记录；它们不转记为软件通过，也不扩展本轮正常路径验收范围。

此次文档与证据另行提交；提交前后分别核对索引／提交中的 `src/` 与 `tests/` 等于被测源码 `236d600`。
10 个无关已跟踪文件保留原始哈希，其他并行未跟踪文件不暂存。仅正常推送目标分支，
实际最终文档提交 SHA、远端 `git ls-remote` 及 ahead／behind 核验由交付回复提供，避免自引用 SHA。

用户可按实施记录的六步复验；第三方 selector 补充检查为：保留必需通道未选，保存待绑定节点，
确认可选通道使用其合法默认值；改为不存在的可选默认通道则仍需准确拒绝。补齐必需绑定后再显式发布和运行。
