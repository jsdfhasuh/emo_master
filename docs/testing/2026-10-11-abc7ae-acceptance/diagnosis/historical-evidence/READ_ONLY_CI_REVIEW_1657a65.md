# 1657a65 GitHub Actions 只读复核

核查时间：2026-10-11 04:15:42 UTC。三个适用 run 全部终态，均为 attempt 1。结论：标准 CI 失败；冻结 Windows 包验证成功。不能把本地 Debian 全通过表述为 GitHub CI 全绿。

## 精确版本

- 已发布 head：1657a65e5700b963000090964bd1b54227e1d550
- 原 parent：012081423dff794a1e7bf69202b55af8076b90dd
- 分支：agent/runtime-workflow-architecture-v1；PR #1
- PR base：7f141ce627da39d9e174e4a79344fc468607ebef
- 普通 PR CI 的实际 checkout：db2014401017a04c832f69fa0fa4d208bccbbe53（合成 merge）
- Push CI 的两个 job、冻结包 prepare/verify 的实际 checkout：1657a65e5700b963000090964bd1b54227e1d550
- checkout 均有完整日志中的 git fetch / git log 输出佐证。API run.head_sha 对 PR 仍为 source head，不等同于实际 checkout。

## 终态和计数

- Push CI：https://github.com/jsdfhasuh/emo_master/actions/runs/38109140024
  - Ubuntu job 114380850823：5 failed、4783 passed、19 skipped、29 subtests；959.33s。
  - Windows job 114380850977：1 failed、4801 passed、5 skipped、29 subtests；1931.38s。
- PR CI：https://github.com/jsdfhasuh/emo_master/actions/runs/38109143323
  - Ubuntu job 114380859795：6 failed、4782 passed、19 skipped、29 subtests；1054.75s。
  - Windows job 114380859609：2 failed、4800 passed、5 skipped、29 subtests；1722.94s。
- 四个普通 CI job 的 protobuf drift、Ruff、mypy 都通过，失败发生在 pytest。
- 冻结包：https://github.com/jsdfhasuh/emo_master/actions/runs/38109143881
  - prepare、package / Build emo-master for Windows、verify 成功；不适用的 vision_train job skipped。
  - 合约测试 11 passed、2 subtests；冻结包验证 JSON status=PASS、source_commit=head。

## 首次失败定位

1. 两个 Ubuntu job 共同出现五项小窗口回归：
   - tests/designer/test_small_window_layout_repair.py:37：PLC read/write × 640×474 / 800×478 四项。
   - debug_connectTimeoutMs 在 ensureWidgetVisible 后仍越出 viewport：640 宽为 QRect(154,361,435,28) / viewport QRect(0,0,604,382)；800 宽为 QRect(154,361,595,28) / viewport QRect(0,0,764,386)。底部分别越出 7px / 3px。
   - 同文件 :90：testOverflowButtonHasPaintedArrowAndSharedTailActions[800]，展开 overflow 后某尾部按钮 isVisible() 为 false。日志未记录该循环中具体哪个 action 失败。
   - 这些断言在本轮两个 Windows job 中未失败。尚不能仅据跨环境差异判断产品或测试哪一侧需要修改。

2. Push Windows：
   - tests/runtime/test_parallel_workflows.py:26，testMoreThanTwoActualWhileJobsWithImages[None-5]。
   - 启动成功且等待五个 While jobs 各至少四周期后，all(status == RUNNING) 为 false。
   - 同失败 stderr 子进程 traceback 经 operator_executor.py:170 抛出 WorkflowExecutionError: global variable database is busy。日志未给完整各 job 终态字典，不延伸归因。

3. PR Ubuntu 额外一项：
   - tests/runtime/test_plc_debug.py:1817，test_service_same_endpoint_rejected_and_released_by_project_reload[INPROCESS]。
   - duplicate endpoint 已被 E_RESOURCE_BUSY 正确拒绝；紧接着 peer.connections == 1 实际为 0。尚未证明是 fixture 观测时序或生产连接问题。

4. PR Windows 两项：
   - tests/p0/test_continuous.py:7 → prototypes/runtime_pages_p0/scenarios.py:340：continuous 子进程 exit=1，correctness_status=FAIL，两个客户端 coverage=[0.975,0.975]。
   - 原始 P0_CONTINUOUS JSON：ordinal 20 为 INCOMPLETE，image UNAVAILABLE、reason=TimeoutError；incomplete=1、client_errors=[null,null]。cleanup 的 memory/staging/cache/lease 均为 0、owners=0。不能据此声称泄漏或确定超时根因。
   - tests/runtime/test_production_boundaries.py:261：testBusinessNgDoesNotStopAndSharesInvocationWithImageAndCount；latest ordinal≥3 后 result.status=INCOMPLETE，期望 COMPLETE。该断言日志没有提供缺失原因。

## 冻结包验证范围

- PASS：icons、原生启动选择器可见和干净取消、Runtime Job 完成、OperatorView 连接该 Job、关闭界面保留独立 Runtime、显式停止、worker 退出、owner 锁回收。
- forced_cleanup_pids=[]。
- 未测：Designer 主编辑、OperatorView 图像像素保真、实体相机、GPU、生产现场验收；安装器仅 hash 校验，未执行。
- 验证 evidence artifact：https://github.com/jsdfhasuh/emo_master/actions/runs/38109143881/artifacts/11691412339
- 包 artifact：https://github.com/jsdfhasuh/emo_master/actions/runs/38109143881/artifacts/11691031538
- 二者只保存元数据/链接；本次监控没有下载二进制 artifact。JSON 报告直接从完整 verify 日志提取。

## 证据与复核入口

- ci-summary-1657a65.json：机器可读版本、run/job 状态、测试计数、实际 checkout、首次失败清单。
- final-exact-head-runs.json：head_sha 精确筛选、per_page=100、total_count=3 的最终 API 结果。
- run-*-terminal.json / run-*-terminal-jobs.json：最终 run/job 原始 API 数据。
- job-*-first-failure.log：四个普通 CI job 的完整首次日志。
- job-114380861789-prepare.log、job-114380949296-package.log、job-114381656782-verify.log：三个成功冻结包 job 的完整日志。
- frozen-windows-validation-report-from-log.json：日志内完整冻结包 JSON。
- pr-windows-continuous-first-failure.json：日志内完整 continuous JSON。
- EVIDENCE_MANIFEST.json / SHA256SUMS.txt：本目录证据清单和校验值；manifest 自身及 SHA256SUMS 不自包含。

日志是 GitHub connector 返回的完整解码文本，统一为 LF。没有改源码、断言、workflow、超时或测试顺序，没有重跑、dispatch、新提交、发布或部署。未做对照实验，不把任何失败称为已知旧问题或 flaky。此摘要仅供后续 Codex 只读核查；没有推送到仓库。

