# Runtime 后台任务服务实施记录

执行角色为 Codex。产品提交为 `b4df667267f15de3dba3690eed978b2681f9394a`。测试使用临时工程、合成图像和测试插件。复核起点为计划提交 `028400a573a38690f2518d97c97aff44d565b3a4`。与计划源码基准 `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd` 相比，实施前已纳入 `main@82a1aa6d8499ee203ea8e452c2f016db37ea7b3f` 的四个提交，包括文件资源绑定和调试路径更新。

## 运行行为

- manifest 可以声明 `runtimeCapabilities: ["backgroundTasks"]`。Runtime 根据已验证的声明注入节点客户端。旧算子仍可按原方式构造和注册。
- 一个 Job 进程共用一条 FIFO 线程。首版内部限额为 32 个未完成任务、估算输入合计 128 MiB，包含正在运行的任务。终态通常保留最近 128 项。未消费的文件依赖固定记录，查询过期任务会得到明确错误。
- ImageSaver 默认同步。后台模式要求显式设置 `saveMode=background` 和 `namingMode=unique`。提交回执带 `taskId`、`state=SUBMITTED`、`saved=false`。任务在后台编码，将结果写入同目录临时文件并关闭后，通过发布门禁调用 `os.replace`。
- Runner 在执行线程上登记文件、发布事件并捕获已保存图片。SQLite 的文件绑定在写入节点前等待，继续使用既有采集和事务代码。
- 正常结束使用内部 30 秒 drain 上限。用户 Stop 先禁止最终发布、取消排队任务，给运行中的任务留出协作清理机会。Supervisor 沿用 5000 毫秒默认宽限。超时强杀可能遗留临时文件。

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
