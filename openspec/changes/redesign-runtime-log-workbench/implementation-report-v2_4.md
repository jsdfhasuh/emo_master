# Runtime 日志工作台 V2.4 实施与验收

## 工作区与版本

- 实际执行者为 Codex。工作树为 `D:/emo-runtime-log-workbench`，分支为 `codex/runtime-log-workbench`。
- 开始时本地和远端分支均在 `fa4b72d751189c2689882a0a0d127fd280a35fde`，该工作树干净，基线后没有差异。核对了既有任务目录、唯一全局读取器、有界事件窗口、日志原始数据、诊断、调试与导出链路。
- 本轮代码和测试提交为 `e7e78e0dd79f7d6a8e7d2d54f7c3979d63f96f4b`。提交前的综合回归使用当时的改动树。提交后从这个 SHA 重跑了 Q1、Q2 和状态／目录回归，工作树只有本 change 文档与新证据待提交。
- 将附件更新到同名 OpenSpec change，保留原第 1–6 节的 38 项、旧实现报告、8 张原生证据及包外文件。当前完整规格含 21 项要求。本轮进度按第 7 节记录。
- 浏览器打开了 V2.4 的参考导航及可操作原型，检查首页终态分组、关联展开、高级筛选和时间视图。下文的验收来自变更后的实际 Qt 窗口。

## 修改与原因

| 文件 | 改动 |
| --- | --- |
| `src/emo_master/apps/designer/ui/runtime_job_logs.py` | 首页按任务自身状态分组，终态仍 active 的任务与最近历史合并，实时 RunState 优先。关联按当前 Runtime、工程、运行组及来源 Job 匹配。当前快照无法解释原因时提示“结束处理未完成”。关联查看和返回使用一个不含事件载荷的来源书签，按原有固定游标有限重查，恢复筛选、视图、展开、选择及详情页。保留 Runtime 授权的停止操作和各任务自己的计时。 |
| `src/emo_master/apps/designer/ui/log_filters.py`、`src/emo_master/apps/designer/ui/log_dialog.py` | 三处共享可空日期输入，Designer 本机历史沿用相同控件。日期按操作机本机时区换算为原有整数毫秒参数，区间包含起点、排除终点。倒置只显示行内提示，下一页与选定范围导出使用已提交条件。常用与技术条件独立展开，精确 ID 和原始嵌套轮次路径保留，候选只来自当前可用记录。 |
| `src/emo_master/apps/designer/state/job_log.py`、`src/emo_master/apps/designer/presenters/node_run_presenter.py` | 主行用名称、轮次、状态和时间，完整调用身份及时间证据放入提示或详情，树键仍是真实结构身份。仅将已知框架默认消息和明确状态转为中文显示。 |
| `src/emo_master/apps/designer/ui/log_record_detail.py`、`src/emo_master/apps/designer/ui/operator_debug_window.py` | 保留生产任务与独立调试共用详情。易读界面显示已记录的输入输出摘要、状态及诊断，原始 JSON、复制值、业务文本、未知值、日志归属和调试结果身份沿用真实数据。 |
| `tests/designer/test_job_log.py`、`test_log_history_instance.py`、`test_log_json_tree.py`、`test_operational_log_integration.py`、`test_operator_debug_window.py`、`test_runtime_job_workbench.py` | 在已有测试中补充目录合并、真实关联条件、单窗口书签、日期接线、原值保护和 Q1/Q2 操作。 |

运行时服务、gRPC 协议、存储和调度算法沿用基线。普通子工作流、循环和连续根调用仍在各自 Job 的执行结构中。

## Q1 原生走查

- Windows Qt Widgets 用真实软件 Runtime、临时工程、模拟相机与 SQLite，执行了 While Job `8433195e-2750-40c2-80cd-a9a0d0d638f8`。告警事件为 `241`，整数加法完成摘要为 `235`，SQLite 主动日志为 `242`。验证精确调用 ID、真实轮次路径、按已加载身份候选筛选，折叠后仍生效。窄窗中拖动原生分隔条，右侧详情和关键按钮保持可用。 [执行树与常用条件](evidence/v2_4/q1-while-warning.png) · [窄窗分隔条](evidence/v2_4/q1-narrow-split.png)
- 同一 Runtime 使用明确标识的 JobRecord／RunGroup 状态夹具。来源任务 `3be027de-7abd-4d69-aa67-4b6706b6eb9b` 已完成、耗时固定 20 秒，超出常规最近页仍保留在“最近已结束”。两条独立成员分别为运行中和停止中。具有同名及同一来源字段的错组任务未计入。Runtime 返回的停止授权仍可用。成员结束后，超出普通最近页的来源退出首页。 [任务首页](evidence/v2_4/q1-task-catalog.png) · [关联任务](evidence/v2_4/q1-related-jobs.png)
- 从来源的时间顺序视图打开关联任务日志，再返回原来源，恢复告警筛选、关键词、节点 ID、选中事件 `768` 和详情。夹具通过原有 Runtime gRPC 读取。 [返回原记录](evidence/v2_4/q1-related-return.png)
- 原有模拟设备诊断往返、节点摘要值与原始记录复制均通过。同一 Designer 原入口调试两次，从第一次结束通知仍能打开第一次的结果，显示中文成功状态且保留原始状态与身份。 [节点执行摘要](evidence/v2_4/q1-execution-summary.png) · [设备缓存](evidence/v2_4/q1-device-cache.png) · [调试易读](evidence/v2_4/q1-operator-debug-result.png) · [调试原始记录](evidence/v2_4/q1-operator-debug-raw.png)

## Q2 原生走查

- 软件 Runtime 执行真实整数加法 Job `fe6e70e9-e9de-46cc-afb2-0df0e7e8b31b`，使用已标记的历史任务和事件夹具形成分页。关闭生产者 Runtime 后，新实例连接原 SQLite。历史目录以实际结束时间 `1795314571492` 为起点，下一页沿用已提交的单侧范围。倒置时未发新请求。 [历史任务结束时间](evidence/v2_4/q2-directory-date.png)
- Job 本地 WARN 使用 `[1795314572123, 1795314572128)`，日期控件显示实际本机时区 `Asia/Shanghai`。服务端查询命中近期窗口外恰在起点的事件，排除起点之前及恰在终点的事件。等值返回空结果，清空恢复不限。倒置未改变旧结果，按旧范围导出仍只包含匹配记录。返回 Job 保留本地条件。Designer 本机历史按日期读取带 Job ID 的客户端错误，来源保持为 Designer。 [毫秒历史查询](evidence/v2_4/q2-history-date.png)
- 通过原有文件导出按钮完成 Job 全量范围导出。测试只替换了文件选择器返回的路径。当前窗口有 200 条，实际导出以固定上界 `223` 前向读取两页，共 221 条，包含本地日期、等级和节点条件之外的记录。逐条对照实际 Runtime 查询。 [完整记录导出](evidence/v2_4/q2-whole-job-export.png)

## 命令及结果

使用 Windows、Python 3.10.21、PySide2 5.15.2.1 和 gRPC 1.78.0。`python` 指向 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，命令从实施工作树运行。

| 检查 | 结果 |
| --- | --- |
| OpenSpec strict、proto 生成一致性、Ruff、mypy、Git 差异校验 | 通过，mypy 共 648 个源文件 |
| Designer 与 Runtime 受影响回归 | 183 通过。随后新增打开不同 Job 时筛选默认折叠的调整，下列提交后走查及状态／目录回归覆盖了该入口 |
| 提交后执行树与目录定向回归 | 17 通过 |
| 提交后 Windows 原生 Qt Q1、Q2 | 2 通过，15.71 秒 |

```powershell
openspec validate redesign-runtime-log-workbench --type change --strict --no-interactive
python scripts/gen_proto.py --check
python -m ruff check src tests
python -m mypy --config-file mypy.ini src
git diff --check

$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q --tb=short tests/designer/test_job_log.py tests/designer/test_log_dialog.py tests/designer/test_log_history_instance.py tests/designer/test_log_categories.py tests/designer/test_log_json_tree.py tests/designer/test_log_export.py tests/designer/test_node_result_panel.py tests/designer/test_runtime_log_names.py tests/designer/test_operational_log_integration.py tests/designer/test_operator_debug_window.py tests/designer/test_operator_debug_resource_ui.py tests/designer/test_device_workspace_usability.py tests/runtime/test_run_catalog.py --maxfail=3
python -m pytest -q --tb=short tests/designer/test_job_log.py tests/runtime/test_run_catalog.py

$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONIOENCODING = 'utf-8'
$env:EMO_LOG_WORKBENCH_EVIDENCE_DIR = 'D:/emo-runtime-log-workbench/openspec/changes/redesign-runtime-log-workbench/evidence/v2_4'
$env:EMO_LOG_WORKBENCH_V2_4_EVIDENCE_DIR = $env:EMO_LOG_WORKBENCH_EVIDENCE_DIR
python -m pytest -q -s --tb=short tests/designer/test_runtime_job_workbench.py::testNativeWhileLogsWarningDeviceReturnAndTimeline tests/designer/test_operational_log_integration.py::testNativeWorkbenchHistoryAndWholeJobExportAfterRestart
```

## 验证边界

- 窄窗走查初次使用的移动事件没有保持鼠标按下状态。改用原生 Qt 的带左键状态移动事件，确认分栏宽度实际改变，并重新捕获界面。
- 真实硬件、冻结安装包、全仓 pytest、全部 82 个算子及全时区矩阵未执行。现有 CI 工作流的推送分支范围不包含本分支，远端 CI 未作为本轮通过证据。

## 2026-11-22 审查修复与重新验收

执行者为 Codex。工作树为 `D:/emo-runtime-log-workbench`，分支为 `codex/runtime-log-workbench`。开始时本地和远端分支均为 `993a8796c4510cf5fa1850cd205b973caf0dbd57`，工作树干净。以下回归实测代码提交为 `d0c1df946750c803c2d9cf082549eceb7bbe1865`。运行时只存在同一 OpenSpec change 的文档修改，代码和测试与该提交一致。历史实施记录和原截图保留在本报告前文及 `evidence/v2_4`。

- 关联返回补齐单个来源书签的临时轮次、临时定位及原筛选位置。在有界窗口重新查询并重建执行结构后，先恢复临时范围，再生成列表。右侧详情按列表实际选中项恢复。原范围或事件不可再定位时，显示可用记录并给出提示。
- 修复 V2.4 之前已存在的历史等级遗漏。Job 已加载窗口与高级历史入口共用告警快捷条件和所选等级的交集。历史展示、分页及选定范围导出沿用已提交条件。冲突形成明确的空范围，查询和空范围导出均无需发历史 RPC。Runtime 继续接收已有 `levels` 字段，完整 Job 导出继续以原有任务身份和固定上界查询。

### Q1 关联任务往返与临时阅读

Windows 原生 Qt Widgets 通过 QTest 操作现有 Designer，连接真实软件 Runtime。保留原 While、SQLite、模拟设备和调试路径，关联任务及用于滚动的轮次事件由已标识夹具写入 Runtime。来源 Job 为 `cdcc2a10-0b81-4268-91f2-ca83bc6cf6f8`，两个关联成员为 `170cf940-c1ba-4337-9dae-fed37d4a9062` 和 `7ede3037-b2fc-4650-9649-b31724669603`。

- 先启用告警 / 错误，选择 WARN `626`，再进入本轮上下文，选中原筛选隐藏的 INFO `627`。进入关联任务并返回后，时间顺序模式、临时范围和“返回原筛选”仍可用。列表选择与原始 JSON 都对应 INFO `627`，顶部可见事件为 `578`。 [关联返回的 INFO 与上下文](evidence/v2_4-review-fixes/q1-related-return.png)
- 点击“返回原筛选”，重现原告警条件、关键词及节点 ID。列表选择与详情回到 WARN `626`，顶部可见事件恢复为 `574`。两处顶部行均与选中行不同。定向测试另覆盖临时定位往返和原事件已不可查询时的提示与抽屉关闭。 [退出临时范围后原告警](evidence/v2_4-review-fixes/q1-related-return-to-filter.png)

### Q2 历史等级与两种导出

沿用软件 Runtime 重启和持久数据路径。实际整数加法 Job 为 `b2497310-b596-4271-8f86-58df007da4ba`，结束时间 `1795317753058`。在该 Job 同一节点 `early` 的 `[1795317754123, 1795317754128)` 插入 INFO、WARN、ERROR 和分页夹具，并在边界外加入对照。保留历史目录结束时间校验、毫秒半开区间、等值空区间和本机日志来源检查。

- 从 Job 勾选告警 / 错误、等级保持全部，进入高级历史。实际请求携带 `levels=["WARN", "ERROR"]` 和工程、Job、节点、时间条件，首屏显示 200 条，下一页 1 条。同范围 INFO 未返回。倒置日期和未提交的 INFO 等级编辑未改变当前范围，下一页与选定范围导出使用原条件和固定上界，选定范围共导出 201 条。 [合并等级查询](evidence/v2_4-review-fixes/q2-history-warning-levels.png) · [保留已提交条件的下一页](evidence/v2_4-review-fixes/q2-history-applied-page.png)
- 快捷条件加 WARN 时，实际请求只含 WARN。快捷条件加 INFO 时显示“无匹配等级”，不发新查询，选定范围导出 0 条，JSON 保留显式 `levels=[]`。 [冲突空结果](evidence/v2_4-review-fixes/q2-history-empty-levels.png)
- 返回 Job 后，在本地条件仍互斥、已加载 200 条的情况下，完整 Job 导出以固定上界 `225` 读取两页，共 223 条，包含 INFO 和本地节点、时间范围之外的记录。测试替换文件选择器的返回路径，逐条对照真实 Runtime 查询。 [完整 Job 导出](evidence/v2_4-review-fixes/q2-whole-job-export.png)

### 本次命令及结果

Windows 原生 QPA 为 `windows`，桌面 1920 × 1080，Python 3.10.21、PySide2 5.15.2.1、gRPC 1.78.0，本机时区 `Asia/Shanghai`。`python` 指向 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。六张截图均来自本次 QTest 运行，已在归档前核对。

| 检查 | 实际结果 |
| --- | --- |
| Windows 原生 Qt ＋软件 Runtime Q1、Q2 | 2 通过，17.25 秒 |
| Designer 定向回归 | 100 通过，29.09 秒。筛除的 Q2 已在原生 Qt 命令单独执行 |
| OpenSpec strict、proto 生成一致性、Ruff、mypy、Git 差异校验 | 通过，mypy 检查 648 个源文件 |

```powershell
openspec validate redesign-runtime-log-workbench --type change --strict --no-interactive
python scripts/gen_proto.py --check
python -m ruff check src tests
python -m mypy --config-file mypy.ini src
git diff --check 993a8796c4510cf5fa1850cd205b973caf0dbd57 HEAD

$env:PYTHONPATH = 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q --tb=short tests/designer/test_job_log.py tests/designer/test_log_dialog.py tests/designer/test_log_history_instance.py tests/designer/test_log_categories.py tests/designer/test_log_export.py tests/designer/test_operational_log_integration.py -k 'not testNativeWorkbenchHistoryAndWholeJobExportAfterRestart'

$env:QT_QPA_PLATFORM = 'windows'
$env:EMO_LOG_WORKBENCH_EVIDENCE_DIR = 'D:/tmp/emo-log-workbench-v24-review-native-1795317703891'
$env:EMO_LOG_WORKBENCH_V2_4_EVIDENCE_DIR = $env:EMO_LOG_WORKBENCH_EVIDENCE_DIR
python -m pytest -q -s --tb=short tests/designer/test_runtime_job_workbench.py::testNativeWhileLogsWarningDeviceReturnAndTimeline tests/designer/test_operational_log_integration.py::testNativeWorkbenchHistoryAndWholeJobExportAfterRestart
```

本次两条验收由 QTest 驱动实际 Qt 窗口。未另做人工点击验收、真实硬件、全部算子、全时区、冻结安装包、全仓 pytest 或远端 CI。

## 2026-11-22 合并与 CI 交付

执行者为 Codex。开始时工作分支干净，本地与远端 `codex/runtime-log-workbench` 均为 `3f3c3b4b1a301ab2bb807b5fcd669dff72ed46b3`，远端 `main` 为 `9d59fd16a1d0d3ec0c60725c839ff38183d6916b`。核对了这两个远端提交之间的全部 122 个文件、7 个提交，以及 `d0c1df94` 之后仅有的同一 OpenSpec 文档与证据差异。独立的本地 `main` 工作树保留用户修改。

### 合并前集成修正

- `058022c80171915d06d8dce8d05f258f48e07a37` 修复切换工程或 Job 时，新历史查询失败可能沿用旧任务列表、分页游标和导出范围的问题。同一任务的已提交查询及日期草稿仍保留。两项直接反例在修复前失败，修复后通过。
- `da0b8e25675a3bb4ad88f3f7bd96537ea5ee80ea` 根据 Linux 源码 CI 的四项失败做局部修正。单算子调试首次获得图像时显示输出端口，切换到历史结果时保留用户所选详情页。旧 Runtime 已收到的无全局事件 ID 记录可在现有高级历史页查看，范围标明受当前缓存限制，沿用既有读取与缓存，切换目标时更新入口。设备弹窗测试通过原生窗口展开「技术条件」后继续核对原来的模态释放、焦点和草稿断言。 [Linux 失败证据](https://github.com/jsdfhasuh/emo_master/actions/runs/44981996455/job/137084431998)
- 随后的 Windows 非 Runtime 分片初次运行仅有双范围页面预览用例在初始图像等待时超时。该用例在 `9d59fd16` 基线和 `da0b8e25` 的本机 Windows 原生 Qt 上均通过，远端超时根因未确定。保持代码、测试和门禁不变，在同一提交上重跑失败分片并通过。 [超时记录](https://github.com/jsdfhasuh/emo_master/actions/runs/44984472142/job/137092022300) · [Windows 分片重跑](https://github.com/jsdfhasuh/emo_master/actions/runs/44984472142/job/137100205590)

### 合并和远端运行

PR 检查结束后核对了开放状态、源码与 Web 检查、评审要求、最新目标 SHA 和 GitHub 测试合并树 `10c00f38ab604abd06f6b92eae9130abd488ae47`。按普通合并提交保留历史。远端 `main` 的产品合并提交为 `9a9cc511d2c2eec588b5602405cd364e40ae313a`，两个父提交为原 `main` 与 `da0b8e25`。重新拉取后确认完整包含交付分支，提交树与通过 PR 验证的树一致。 [产品 PR #16](https://github.com/jsdfhasuh/emo_master/pull/16) · [产品合并提交](https://github.com/jsdfhasuh/emo_master/commit/9a9cc511d2c2eec588b5602405cd364e40ae313a)

| 阶段 | 对应提交 | GitHub Actions 运行 |
| --- | --- | --- |
| PR 源码 CI | `da0b8e25` | Linux、Windows 各两个分片及汇总通过。 [CI 第 2 次尝试](https://github.com/jsdfhasuh/emo_master/actions/runs/44984472142/attempts/2) |
| PR Web panel | `da0b8e25` | Linux、Windows 及软件 Runtime 集成通过。 [Web panel](https://github.com/jsdfhasuh/emo_master/actions/runs/44984472233) |
| 主分支推送后源码 CI | `9a9cc511` | 该合并提交的独立 [源码 CI 结果](https://github.com/jsdfhasuh/emo_master/actions/runs/44990323997) |
| 主分支推送后 Web panel | `9a9cc511` | 该合并提交的独立 [Web panel 结果](https://github.com/jsdfhasuh/emo_master/actions/runs/44990324003) |

### 本机集成核对

`da0b8e25675a3bb4ad88f3f7bd96537ea5ee80ea` 的干净工作树上，Windows 原生 QPA `windows`、1920 × 1080、Python 3.10.21 和 PySide2 5.15.2.1。软件 Runtime 的 Q1、Q2 与八项直接相关回归共 10 项通过，29.13 秒。未合并前文各批次数量。OpenSpec strict、proto 生成一致性、Ruff、mypy（648 个源文件）和完整 Git 差异检查通过。

```powershell
openspec validate redesign-runtime-log-workbench --type change --strict --no-interactive
python scripts/gen_proto.py --check
python -m ruff check src tests
python -m mypy --config-file mypy.ini src
git diff --check 9d59fd16a1d0d3ec0c60725c839ff38183d6916b...da0b8e25675a3bb4ad88f3f7bd96537ea5ee80ea

$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -q -s --tb=short tests/designer/test_runtime_job_workbench.py::testNativeWhileLogsWarningDeviceReturnAndTimeline tests/designer/test_operational_log_integration.py::testNativeWorkbenchHistoryAndWholeJobExportAfterRestart tests/designer/test_operational_log_integration.py::testPinnedHistoryRetiresOtherJobScopeEvenWhenQueryFails tests/designer/test_operational_log_integration.py::testHistoryFailedFilterKeepsSuccessfulPagingAndExportScope tests/designer/test_debug_image_results.py tests/designer/test_device_workspace.py::testDeviceRuntimeLogsKeepDraftAndReleaseModalInput tests/ui/page_designer/test_presentation_logs.py::testObservedJobAppearsInExistingLogWindowWithDetails
```

保留原 QTest 界面证据。此次未另做人工点击验收、真实硬件验证、全部算子、全时区、冻结包和本机全仓测试。冻结包工作流的路径过滤条件未命中此次改动。
