# Runtime 后台任务服务实施记录

执行角色为 Codex。产品提交为 `b4df667267f15de3dba3690eed978b2681f9394a`。测试使用临时工程、合成图像和测试插件。复核起点为计划提交 `028400a573a38690f2518d97c97aff44d565b3a4`。与计划源码基准 `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd` 相比，实施前已纳入 `main@82a1aa6d8499ee203ea8e452c2f016db37ea7b3f` 的四个提交，包括文件资源绑定和调试路径更新。

## 运行行为

- manifest 可以声明 `runtimeCapabilities: ["backgroundTasks"]`。Runtime 根据已验证的声明注入节点客户端。旧算子仍可按原方式构造和注册。
- 一个 Job 进程共用一条 FIFO 线程。首版内部限额为 32 个未完成任务、估算输入合计 128 MiB，包含正在运行的任务。终态通常保留最近 128 项。未消费的文件依赖固定记录，查询过期任务会得到明确错误。
- ImageSaver 默认同步。后台模式要求显式设置 `saveMode=background` 和 `namingMode=unique`。提交回执带 `taskId`、`state=SUBMITTED`、`saved=false`。任务在后台编码，将结果写入目标文件系统的受管暂存位置并关闭后，通过发布门禁原子创建目标文件。
- Runner 在执行线程上登记文件、发布事件并捕获已保存图片。SQLite 的文件绑定在写入节点前等待，继续使用既有采集和事务代码。
- 正常结束使用内部 30 秒 drain 上限。用户 Stop 先禁止最终发布、取消排队任务，给运行中的任务留出协作清理机会。Supervisor 沿用 5000 毫秒默认宽限，受管子进程退出后回收该进程遗留的暂存图片。

## Windows 实际观察

以下两行来自干净产品提交上的 RuntimeService 和 multiprocessing spawn 子进程。每个 Job 中两个独立节点各自提交了一个探针任务，表中编号取自运行日志。

| Job ID | 进程 PID | 工作流线程 | 两个探针的后台线程 | artifact.created → job.completed 序号 |
| --- | ---: | ---: | --- | --- |
| `901cca23-958a-4474-981c-1d328cb87a88` | 179840 | 117260 | 22900、22900 | 20 → 21 |
| `dd9e59f1-369e-49eb-ba67-7084fec71e94` | 13532 | 82660 | 115640、115640 | 20 → 21 |

两次后台保存生成不同的唯一文件名。每个 PNG 为 129 字节，产物副本和输入图的 SHA-256 均为 `b4e70fe355e54c989a4f3bc700cb55cda0c351397c24152feffadb652d5ac504`。测试还解码并逐像素比较。另一个子进程在任务进行中收到 graceful Stop，清理活动标记后发布 `job.aborted / E_CANCELLED`，Job ID 为 `6dee3c97-7745-44e7-9845-c07dbcb8e20b`。

## 五组验证证据

| 范围 | 实际验证 |
| --- | --- |
| 注册与注入 | 旧 manifest、有效和非法声明、未声明节点缺少客户端、调试草稿伪造能力无效。 |
| 服务 | Event 控制 FIFO、成功/失败、显式 wait、数量与字节背压及取消。检查原始异常和提交身份、记录过期、多个依赖固定保留、取消和 drain 超时后的迟到发布。 |
| Job 与 Runner | 上述真实进程验证共享和隔离、预览收尾及终态顺序。另用可控 worker 入口测试正常 drain 超时、连续间隔和 Dispatch IDLE 的错误发现。超时用例临时缩短内部等待期限。 |
| ImageSaver、Artifact、SQLite | 在后台编码中阻塞，后续节点已运行且修改了源图。直到 SQLite 等待入口才放行编码，最终文件逐像素等于修改前的图。校验产物副本及 SHA-256，断言产物事件、预览捕获均早于 SQLite 节点开始，实际数据库读取到已存在的路径。注入写入错误后，stop 和 continue 策略均没有插入行或创建成功产物。 |
| 调试与预览 | 实际调试子进程覆盖单算子后台保存和编码错误。工作流调试使用服务器端测试声明验证会话收尾、子流程错误归属与克隆试跑隔离。PurePreview 验证没有可提交任务的客户端。 |

## 复现和范围

Windows 使用 Python 3.10.21 与原生 Windows Qt 平台。OpenSpec CLI 2.0.0 的 `openspec validate add-runtime-background-task-service --strict`、Ruff、mypy 和生成协议一致性检查均通过。开发指南的三个可执行示例也已验证。

```text
python -m pytest -q tests/runtime/test_background_tasks.py tests/runtime/test_background_task_job.py tests/runtime/test_background_task_debug.py tests/plugins/test_image_saver_operator.py tests/sqlite_writer/test_background_file_references.py
```

Windows 受影响回归覆盖 32 个测试文件。同一干净产品提交首次运行 410 通过、1 失败、0 跳过。`testTwoConcurrentGroupsStayDistinctOnQueueAndDisplayRPC` 在报表导出子进程的 ready 握手处报 `exporter startup failed`。单独复跑通过，随后保留原顺序和断言完整复跑，411 通过、0 失败、0 跳过（216.60 秒）。加上单独执行的开发指南示例，最终共验证 414 个不同用例。该启动失败未稳定复现。

<details><summary>Windows 扩展回归命令（PowerShell）</summary>

```powershell
$tests = @(
    'tests/core/plugin/test_registry.py'
    'tests/plugins/test_image_saver_operator.py'
    'tests/plugins/test_detection_archive_operator.py'
    'tests/plugins/test_unicode_image_io.py'
    'tests/runtime/test_background_tasks.py'
    'tests/runtime/test_background_task_debug.py'
    'tests/runtime/test_background_task_job.py'
    'tests/runtime/test_operator_executor_shared.py'
    'tests/runtime/test_operator_error_propagation.py'
    'tests/runtime/test_operator_lifecycle.py'
    'tests/runtime/test_operator_registration.py'
    'tests/runtime/test_image_saver_filename.py'
    'tests/runtime/test_artifacts.py'
    'tests/runtime/test_worker_preview_finalization.py'
    'tests/runtime/test_worker_heartbeat_finalization.py'
    'tests/runtime/test_startup_cancellation.py'
    'tests/runtime/test_production_continuous.py'
    'tests/runtime/test_dispatch_queue_integration.py'
    'tests/runtime/test_job_supervisor_contracts.py'
    'tests/runtime/test_runtime_job_lifecycle_integration.py'
    'tests/runtime/test_workflow_loop_contracts.py'
    'tests/runtime/test_workflow_loop_contracts_v2.py'
    'tests/runtime/test_workflow_subflow_contracts.py'
    'tests/runtime/test_operator_debug_rpc.py'
    'tests/runtime/test_operator_debug_resources.py'
    'tests/runtime/test_workflow_debug_rpc.py'
    'tests/runtime/test_pure_preview_cancellation.py'
    'tests/sqlite_writer/test_background_file_references.py'
    'tests/sqlite_writer/test_relative_file_references.py'
    'tests/sqlite_writer/test_dependencies.py'
    'tests/sqlite_writer/test_review_encoding.py'
    'tests/sqlite_writer/test_review_paths.py'
)
$env:QT_QPA_PLATFORM = 'windows'
python -m pytest -q @tests --tb=short --timeout=120
python -m pytest -q tests/core/plugin/test_operator_development_examples.py
```

</details>

Linux/amd64 在固定提交的 git archive 上运行。Python 3.10.22、OpenCV 4.10.0、pytest 9.1.1。上面五个文件 67 项通过，另运行 `tests/core/plugin/test_registry.py` 和 `tests/runtime/test_operator_executor_shared.py`，65 项通过，合计 132 通过、0 失败、0 跳过。容器首次准备时缺少 Pillow 和夹具依赖的 examples 源文件，补齐后在同一提交完整复跑。

本次验证覆盖源码、Runner 与真实 Runtime 子进程。人工 Designer 操作、打包或冻结产物及现场相机、PLC 均未执行。独立验收状态及托管 CI 结果在 [PR #19](https://github.com/jsdfhasuh/emo_master/pull/19) 跟踪。

## 2026-11-24 独立审查后的修正

修正基于 `50f240f9d47fcfa280ba1520153c8ecf46f55976` 的工作区，包含尚未提交的 Runtime、ImageSaver、Designer、测试和文档更改。[原始审查记录](../../../docs/testing/2026-11-24-runtime-background-task-independent-review.md)保留修正前的行为和证据。

| 原始问题 | 本轮验证 |
| --- | --- |
| 配置排空丢图 | Windows 真实生产模式连续 Job 保存 6000×6000 RGB 图片。收到提交回执时目标还不存在，选择排空后配置返回 `COMMITTED`，旧 Job 退休，业务图片完整。受控子进程回归另核对逐像素内容、产物登记的 SHA-256 和产物事件早于 Job 终态。 |
| Dispatch 两向交付文件 | Windows 启动两个不同 PID 的 Job。消费者收到主流程的文件，并把原文件结果和自己保存的图片交给 Join。两个 Job 均完成，两张图片与输入文件内容一致。另用 ForEach 实际写出 140 张图片，超过默认 128 条任务历史后，保留的 140 个工作流输出仍能转换为已确认的独立文件结果。 |
| 投递失败归属 | 在 A 的工作流返回后才让其后台写入失败，消费者将失败记入 A，尚未领取 B。显式处理过的错误沿用现有处理结果；SQLite 的 stop/continue 文件绑定仍覆盖成功与失败路径。 |
| Designer 节点结果与表单 | 原生 Windows Qt、回环 gRPC 和真实 Runtime 子进程联合走通“上一次执行”的保存图选择、身份及像素断言。历史记录测试另将上一轮的产物推迟到下一轮节点完成后，核对两个执行分别显示的结果及历史额度。文件选择器保留原有参数并返回默认 `saveMode=sync`。 |
| While 等待中的失败 | 在 10 秒迭代间隔、450 毫秒超时的受控时钟下，编码失败先于循环期限被发现，保留保存节点和 `E_BACKGROUND_TASK_FAILED`。原有节拍、超时与 Stop 用例继续覆盖。 |
| 普通失败收尾与超限图像 | 在临时文件关闭后触发下游失败，协作退出的真实 ImageSaver 清理了临时文件并保留主错误。活动任务超出普通失败的 2 秒清理预算时记录附加诊断。单任务超限先于复制被拒绝，144,000,000 字节原图的拒绝路径在本机测得额外内存峰值约 10 KB。 |

当时仍有两个问题。外部写方提前创建回执路径时，最终替换可能覆盖文件。写入进程超过清理预算或被强制终止时，可能留下临时图片。[后续修正及复验](#目标冲突与强制退出)

### 修正后的工作区回归

Windows 使用 Python 3.10.21、PySide2 5.15.2.1 和原生 Windows Qt。45 个文件的扩展回归共 518 项通过，0 失败、0 跳过，耗时 359.41 秒。这轮启动后又调整了活跃 Dispatch 的等待期限，最终源码另做定点复跑。

<details><summary>Windows 扩展回归的附加文件</summary>

在上节 32 个文件的 `$tests` 数组基础上执行：

```powershell
$additionalTests = @(
    'tests/core/plugin/test_operator_development_examples.py'
    'tests/runtime/test_configuration_transition.py'
    'tests/runtime/test_review_dispatch_lifecycle.py'
    'tests/runtime/test_source_relocation_owners.py'
    'tests/runtime/test_loop_cycle_configuration.py'
    'tests/runtime/test_dispatch_background_files.py'
    'tests/runtime/test_frame_dispatch.py'
    'tests/runtime/test_unlimited_while.py'
    'tests/runtime/test_workflow_while_boolean.py'
    'tests/runtime/test_live_node_results.py'
    'tests/designer/test_image_saver_form.py'
    'tests/designer/test_run_result_history.py'
    'tests/designer/test_node_image_viewer.py'
)
$env:QT_QPA_PLATFORM = 'windows'
python -m pytest -q @tests @additionalTests --tb=short --timeout=120
```

</details>

Linux/amd64 在隔离网络的本地容器中运行，使用 Python 3.10.22、OpenCV 4.10.0 和 pytest 9.1.1。最终工作区快照的 11 个文件共 116 项通过，0 失败、0 跳过，耗时 170.74 秒。测试前后 `src`、`tests`、`examples` 和 `configs` 共 1650 个文件的快照指纹均为 `f24a275cf43428233a7864b74e0bef67195336ece34b0b9a82872b8ea7b3f239`。

```sh
python -m pytest -q tests/runtime/test_background_tasks.py tests/runtime/test_background_task_job.py tests/runtime/test_background_task_debug.py tests/runtime/test_dispatch_background_files.py tests/runtime/test_dispatch_queue_integration.py tests/runtime/test_configuration_transition.py tests/runtime/test_unlimited_while.py tests/runtime/test_worker_heartbeat_finalization.py tests/plugins/test_image_saver_operator.py tests/sqlite_writer/test_background_file_references.py tests/sqlite_writer/test_relative_file_references.py --tb=short -ra --timeout=120
```

Windows 在最终工作区上复跑后台服务、真实 Job、Dispatch、配置切换、While、ImageSaver、SQLite、注册和 Designer 历史及表单相关的 12 个文件，138 项通过，0 失败、0 跳过，耗时 36.14 秒。用例与上面的扩展回归有重叠。活跃投递可以等待慢任务并响应 Stop，正常关闭服务仍保留 30 秒上限。

```powershell
python -m pytest -q tests/runtime/test_background_tasks.py tests/runtime/test_background_task_job.py tests/runtime/test_background_task_debug.py tests/plugins/test_image_saver_operator.py tests/sqlite_writer/test_background_file_references.py tests/core/plugin/test_registry.py tests/runtime/test_dispatch_background_files.py tests/runtime/test_configuration_transition.py tests/runtime/test_unlimited_while.py tests/runtime/test_worker_heartbeat_finalization.py tests/designer/test_image_saver_form.py tests/designer/test_run_result_history.py --tb=short --timeout=120
```

最终工作区的 OpenSpec strict 校验、Ruff（`src tests`）、mypy（`src` 中 653 个源文件）、协议生成一致性和 `git diff --check` 均通过。

本轮覆盖源码、真实 Runtime 子进程及原生 Qt 自动化。人工 Designer 操作、打包或冻结产物、现场相机和 PLC 未验收。

## 目标冲突与强制退出

本节使用基于 `50f240f9d47fcfa280ba1520153c8ecf46f55976` 的未提交工作区，包含上节的修正。原实现会覆盖另一写方在正式发布前创建的文件。在 Windows 真实 Runtime 上，强制 Stop 和下游失败清理超时都能重现暂存文件遗留。修复前，两个用例都在检查暂存文件已被删除时失败。

| 场景 | 修正后的验证 |
| --- | --- |
| 抢先创建目标文件 | Windows 的真实 Job 交付 `SUBMITTED` 回执后，测试进程在目标路径创建了 44 字节的文件。放行 ImageSaver 后，Job 以 `E_OUTPUT_EXISTS` 失败，来源节点和任务编号与回执一致。产物事件数为 0，输出目录只留下原文件，SHA-256 为 `bf919a1b825b6137e792be420805c63c84a6f19b7d789b42d106507c0d43c1d0`。 |
| 正式 Job 强制停止或普通失败超时 | 暂存图写完且已关闭时触发强制 Stop，或触发下游错误并阻止活动任务在 2 秒内清理。两种情况下子进程退休后，暂存文件均被回收，未发布的图片没有出现。已发布 PNG 逐像素正确，旁边其他写方的 `.tmp` 字节完整。普通失败保留 `E_DOWNSTREAM` 及附加清理超时诊断。 |
| 调试子进程仍在写入时被终止 | 单算子和工作流调试子进程写入部分字节并保持句柄打开。终止两种子进程后，会话均释放资源、暂存文件消失，同目录其他写方的文件保留。Windows 和 Linux 都复现过工作流调试在这个时刻的阻塞。在等快照确认的子进程被终止后，父进程卡在 `multiprocessing.Event.set()`。现在父进程每接收一份快照就释放一个信号量，子进程每次消费一个确认。正常快照、单步和试跑也已复验。 |
| Linux 原子发布 | 本机容器验证了实际重命名和硬链接两条路径各自的正常发布与冲突处理，并注入“两种操作均不可用”。最后一种情况下任务失败，正式路径没有生成。没有实际挂载 FAT 或网络共享盘。 |

这轮验证覆盖父 Runtime 仍在运行时的子进程清理。父子进程同时退出后的处理和断电验收边界见[下次启动时清理暂存图片](#下次启动时清理暂存图片)。

Linux/amd64 在本地隔离容器中运行最终工作区的 9 个相关文件，112 项通过，0 失败、0 跳过，耗时 99.88 秒。Python 3.10.22、OpenCV 4.10.0、pytest 9.1.1。测试前后核对 1649 个文件，快照指纹为 `5f197586b0b5549201ad09a11d0077c0bf295a9bb1ba6360b88cebe226f57528`。

```sh
python -m pytest -q --tb=short -ra --timeout=120 tests/runtime/test_background_task_job.py tests/plugins/test_image_saver_operator.py tests/runtime/test_background_task_debug.py tests/runtime/test_background_tasks.py tests/runtime/test_worker_heartbeat_finalization.py tests/runtime/test_runtime_job_lifecycle_integration.py tests/runtime/test_job_supervisor_contracts.py tests/runtime/test_dispatch_background_files.py tests/sqlite_writer/test_background_file_references.py
```

Windows 使用 Python 3.10.21。最终工作区的 17 个测试文件覆盖 ImageSaver、后台服务、Job 退休、调试、Dispatch 和 SQLite。默认运行 230 项通过、0 失败、1 项跳过，耗时 143.38 秒。随后单独启用并运行 65 秒 Designer 暂停续租用例，1 项通过，耗时 67.89 秒。该会话在暂停超过租期后仍能继续运行至成功。两次合计实际通过 231 个不同用例。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$boundaryTests = @(
    'tests/plugins/test_image_saver_operator.py'
    'tests/runtime/test_background_tasks.py'
    'tests/runtime/test_background_task_job.py'
    'tests/runtime/test_background_task_debug.py'
    'tests/runtime/test_worker_heartbeat_finalization.py'
    'tests/runtime/test_runtime_job_lifecycle_integration.py'
    'tests/runtime/test_graceful_finalization_retirement.py'
    'tests/runtime/test_dispatch_start_retirement.py'
    'tests/runtime/test_workspace_long_path_retirement.py'
    'tests/runtime/test_job_supervisor_contracts.py'
    'tests/runtime/test_operator_debug_resources.py'
    'tests/runtime/test_operator_debug_rpc.py'
    'tests/runtime/test_workflow_debug_rpc.py'
    'tests/runtime/test_dispatch_background_files.py'
    'tests/sqlite_writer/test_background_file_references.py'
    'tests/runtime/test_workflow_debug_control.py'
    'tests/runtime/test_workflow_debug_acceptance.py'
)
python -m pytest -q @boundaryTests --tb=short --timeout=120
$env:EMO_DEBUG_STRESS = '1'
python -m pytest -q tests/runtime/test_workflow_debug_acceptance.py::testDesignerConnectionRenewsWhilePausedBeyondLease --tb=short -ra --timeout=120
```

最终工作区通过 OpenSpec strict、Ruff（`src tests`）、mypy（654 个源文件）、协议生成一致性与 `git diff --check`。测试使用临时工程和合成数据。

## 下次启动时清理暂存图片

本次基于 `50f240f9d47fcfa280ba1520153c8ecf46f55976` 的未提交工作区，包含之前的后台保存修正。将 Runtime 与 Job 强制结束后，旧实现在三组重启用例中都遗留了已写完、尚未发布的临时 PNG。新实现首先持久登记目标卷上的私有暂存目录，主 Runtime 下次启动时根据登记和操作系统文件锁清理。

| 场景 | 观察结果 |
| --- | --- |
| Runtime 与正式 Job 同时不再运行 | 在真实 Job 发布前阻塞写入，强制结束 Runtime 和 worker。同一数据目录重新启动后，暂存图消失，未发布路径不存在。已发布 PNG 字节完整，业务目录中的外部同格式临时文件也保留。再次启动结果一致。 |
| 旧写入进程尚在运行，或无法删除文件 | 新 Runtime 保留活进程正在使用的暂存目录。删除被拒时能正常启动，事件中有 `E_IMAGE_STAGING_CLEANUP_DEFERRED`。进程退出或删除权限恢复后，再次启动完成清理。 |
| 单算子及工作流调试 | 两种真实调试子进程都在持有已写入部分字节的文件时被终止，原 Runtime 直接退出。新 Runtime 启动后清除了受管文件，旁边的外部文件保留。 |
| 输出卷和登记的恢复顺序 | 受控测试模拟单张图片的工作区记录已经消失，而输出卷恢复出临时文件目录项。持久目录登记仍能定位并回收该文件。另验证两个 Runtime 共用业务输出目录、复制登记库到另一个数据位置时均保留其他实例的数据。 |
| 上一版正式 Job | 旧工作区的 JSON 字符串登记仍可用。原工作进程在运行时暂缓处理，输出目录不可访问时保留记录。进程退出、目录重新可用后，仅删除旧登记指向的暂存图。缺少位置或归属证明的历史临时文件不按名称猜测删除。 |

测试使用临时数据目录和合成图像。进程中断经过实际强制终止验证。断电场景通过模拟磁盘重启后可见的目录项验证，没有让测试主机实际断电。恢复依赖 Runtime 登记库仍完好，以及存储设备兑现持久写入。

Windows 使用 Python 3.10.21、原生 Windows Qt 及真实 spawn 子进程。最终工作区的 15 个文件共 203 项通过、0 失败、0 跳过，耗时 147.60 秒。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$restartTests = @(
    'tests/runtime/test_background_image_restart.py'
    'tests/runtime/test_background_tasks.py'
    'tests/runtime/test_background_task_job.py'
    'tests/runtime/test_background_task_debug.py'
    'tests/runtime/test_runtime_persistence_contract.py'
    'tests/runtime/test_workspace_long_path_retirement.py'
    'tests/plugins/test_image_saver_operator.py'
    'tests/runtime/test_runtime_job_lifecycle_integration.py'
    'tests/runtime/test_job_supervisor_contracts.py'
    'tests/runtime/test_operator_debug_sessions.py'
    'tests/runtime/test_workflow_debug_rpc.py'
    'tests/runtime/test_workflow_debug_control.py'
    'tests/runtime/test_dispatch_background_files.py'
    'tests/runtime/test_configuration_transition.py'
    'tests/sqlite_writer/test_background_file_references.py'
)
python -m pytest -q @restartTests --tb=short -ra --timeout=120
```

Linux/amd64 使用 Python 3.10.22、OpenCV 4.10.0、pytest 9.1.1 及本地隔离容器。12 个测试文件共 141 项通过、0 失败、0 跳过，耗时 240.82 秒。运行前后核对 1652 个文件，快照 SHA-256 均为 `ca518a13314a736bda2035000b03564a9b29264bfd26b753adb8a869469cadbc`。

```sh
python -m pytest -q -p no:cacheprovider --tb=short -ra --timeout=120 tests/runtime/test_background_image_restart.py tests/runtime/test_background_tasks.py tests/runtime/test_background_task_job.py tests/runtime/test_background_task_debug.py tests/plugins/test_image_saver_operator.py tests/runtime/test_runtime_job_lifecycle_integration.py tests/runtime/test_worker_heartbeat_finalization.py tests/runtime/test_job_supervisor_contracts.py tests/runtime/test_configuration_resource_leases.py tests/runtime/test_dispatch_background_files.py tests/sqlite_writer/test_background_file_references.py tests/runtime/test_runtime_persistence_contract.py
```

OpenSpec strict 校验、Ruff（`src tests`）、mypy（656 个源文件）、协议生成一致性及 `git diff --check` 均通过。本轮未打包或部署。

## 配置排空与页面测试发布收尾

本轮基于 `50f240f9d47fcfa280ba1520153c8ecf46f55976` 及本地未提交的后台保存、Dispatch、Designer 和重启恢复修正。以下实测针对该工作区。

### 配置替换等待产物登记

在 Windows 上用同一个连续生产 Job 和真实 ImageSaver，让产物登记耗时 7 秒。Supervisor 采用默认 5 秒停止宽限，配置排空预算为 30 秒。

| 观察项 | 修复前 | 修复后 |
| --- | --- | --- |
| 配置结果 | 约 5.23 秒返回 `COMMITTED` | 约 7.44 秒返回 `COMMITTED` |
| 正式 PNG | 已生成 | 已生成 |
| 事件 | 缺少 `artifact.created`，旧 Job 以 `E_STOP_TIMEOUT` 被强制结束 | 先发布 `artifact.created`，再发布 `job.aborted / E_CANCELLED` |

自动回归用可控的写入和登记时机复现上述顺序。两种情况分别是 Job 处在周期间隔内、Job 已经在等待下一周期准入。修复前两种都在“登记结束前已报空闲”的断言失败。现在 Runner 在处理完最后一项待登记产物后才清除该状态。周期准入在排空期间可以让执行线程先处理已完成的写入，处理期间仍保留原周期边界。用例核对了正式 PNG 的像素、产物的大小、SHA-256 和终态顺序。第三种情况在等待准入时撤销配置更新，验证旧 Job 可以在原图片仍未写完时进入下一轮。

### 页面测试发布的重启与正常保存

兼容测试从已有 2.2 测试包构建、导入、激活，经真实 `ReleaseHost` 的 `DisplayService.Start` 启动内置 ImageSaver。在暂存 PNG 写完但尚未发布时结束写入进程和发布主机。修复前，同一数据目录重启后暂存图仍在。展示服务现为 Job 指定所属 Runtime 的登记库。重启后暂存图已删除，中断的目标 PNG 没有出现，预置的 PNG 和模拟另一写方的同格式 `.tmp` 字节完整。

Windows 定向回归还发现，169 字符的有效输出目录会生成 267 字符的私有暂存路径。在系统关闭长路径支持时，普通文件打开调用使后台保存失败。暂存文件的创建、发布和清理现复用 Runtime 的 Windows 扩展路径处理，业务回执和持久登记仍用常规路径。在真实 `DisplayService.Start` 入口复验正常保存时，输出目录为 175 字符，私有暂存路径为 273 字符，正式路径为 242 字符。Job 成功，654 字节的 PNG 可解码。产物 SHA-256 为 `02590659c189056bbcb8d7a0b1c6297a8a20563395d7600a9dbaedcab19196a3`，先收到第 19 条产物事件，再收到第 20 条 Job 完成事件。

### 最终源码回归

Windows 原生 Python 3.10.21 使用临时数据、真实 spawn 子进程及原生 Qt 运行了 26 个测试文件，264 项通过，0 失败、0 跳过，用时 219.98 秒。针对两个反例单独运行的重叠用例未重复计数。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python -m pytest -q -p no:cacheprovider `
    tests/runtime/test_background_image_restart.py tests/runtime/test_background_task_job.py tests/runtime/test_background_tasks.py `
    tests/runtime/test_background_task_debug.py tests/runtime/test_configuration_transition.py tests/runtime/test_loop_cycle_configuration.py `
    tests/runtime/test_cycle_plan_scope_cleanup.py tests/runtime/test_targeted_cycle_inheritance.py tests/runtime/test_device_ipc_cancellation.py `
    tests/runtime/test_device_ipc_retirement.py tests/runtime/test_worker_heartbeat_finalization.py tests/runtime/test_runtime_job_lifecycle_integration.py `
    tests/runtime/test_job_supervisor_contracts.py tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_workspace_long_path_retirement.py `
    tests/runtime/test_release_host.py tests/runtime/presentation/test_normal_restart.py tests/runtime/test_dispatch_background_files.py `
    tests/runtime/test_unlimited_while.py tests/plugins/test_image_saver_operator.py tests/sqlite_writer/test_background_file_references.py `
    tests/designer/test_run_result_history.py tests/designer/test_node_image_viewer.py tests/designer/test_image_saver_form.py `
    tests/core/plugin/test_registry.py tests/runtime/test_project_configuration.py `
    --tb=short -ra --timeout=120
```

Linux/amd64 在隔离容器中使用 Python 3.10.22、OpenCV 4.10.0 和 pytest 9.1.1。冻结 1655 个文件，快照指纹为 `fdec28eaad620d169b51df07e2d46ebba6c713b57428aa7c5ea5e04743e67949`。两个修复的全部用例均通过。12 个文件共 104 项通过、1 项失败、0 跳过，用时 361.42 秒。

```sh
python -m pytest -q -p no:cacheprovider --tb=short -ra --timeout=120 \
    tests/runtime/test_release_host.py tests/runtime/test_background_task_job.py tests/runtime/test_configuration_transition.py \
    tests/runtime/test_loop_cycle_configuration.py tests/runtime/test_background_image_restart.py tests/runtime/test_background_tasks.py \
    tests/runtime/test_runtime_job_lifecycle_integration.py tests/runtime/test_job_supervisor_contracts.py tests/runtime/test_worker_heartbeat_finalization.py \
    tests/runtime/test_page_publication.py tests/runtime/test_dispatch_background_files.py tests/runtime/test_runtime_persistence_contract.py
```

失败项是静态页面资源替换 `testPageAssetReplacementChangesPublicationAndRetainsPortablePaths[False]`。它使用无 ImageSaver 的工作流和默认配置更新策略，等待配置线程退出 5 秒后断言失败。同一快照单独复跑页面文件，3 项通过，1 项失败，这次超时的是 `[True]`。同一容器的干净 HEAD 对照有 2 项通过，两个参数都在同一断言失败。Windows 单独复跑此文件，4 项通过，用时 9.09 秒。

额外在同一 Linux 快照观察两个参数的配置线程和业务结果。纯静态资源替换在 3.811 秒完成，混合资源和参数替换在 4.137 秒完成，均为 `COMMITTED`。生产 Job 持续运行，发布版次、图片内容和可移植路径均符合原用例断言。本次观察未触发超时。原 pytest 失败仍按失败计，超时根因尚未确认。

本轮两个审查问题均已关闭。OpenSpec 严格校验、Ruff（`src tests`）、mypy（656 个源文件）、协议生成一致性和 `git diff --check` 通过。结果覆盖源码和实际子进程。未执行冻结包、现场设备或整机物理断电验收。改动仍在本地工作区。
