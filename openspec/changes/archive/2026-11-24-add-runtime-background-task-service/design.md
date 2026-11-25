# 设计

## 1. 注册能力声明

manifest 是能力声明的唯一来源，首版新增可选字段：

    {
      "operatorId": "vision.io.image_saver",
      "runtimeCapabilities": ["backgroundTasks"]
    }

规则：

- `backgroundTasks` 表示算子允许提交、等待和查询 Runtime 后台任务，不表示整个算子自动在后台运行。
- 旧 manifest 缺少该字段时按空 tuple 处理。
- 未知值、重复值、非字符串元素按 manifest 校验失败处理。
- `PluginManifest.runtimeCapabilities` 保存解析结果；新字段放在 dataclass 最后并提供空 tuple 默认值，保持现有构造兼容。
- 不给 `OperatorMeta` 增加重复字段，不增加算子基类或必需方法。
- `OperatorExecutor` 仅对 manifest 声明该能力的算子注入 `runtimeContext["backgroundTasks"]`。
- 单算子调试和工作流调试的能力声明必须由 Runtime 的 `trustedEntries()` 从已校验 manifest 传入，不能相信客户端自报。
- PurePreview 和 clone-safe trial 即使算子声明了能力，也不注入可提交任务的客户端。

## 2. 服务归属

- 每个 Job worker 进程创建一个 `BackgroundTaskService`，一条 FIFO 工作线程。
- 同一 Job 的节点、循环、子流程和连续运行周期共用服务。
- 不同 Job 是独立进程，各自拥有服务，不跨进程传递 Python 对象或图片。
- 服务由 Job worker 创建和关闭；算子只拿到轻量客户端，不管理线程。
- 不复用现有 `AsyncPreviewSnapshotWriter` 或日志线程，它们有不同的缓存和丢弃策略。

## 3. 算子接口

已声明能力的算子使用：

    client = runtimeContext["backgroundTasks"]
    client.checkSize("image.save", image.nbytes)
    task = client.submit(
        save_image,
        image.copy(),
        output_path,
        taskName="image.save",
        estimatedBytes=image.nbytes,
    )

    # 任务已接收，不代表文件已经保存
    return {
        "status": "ok",
        "outputs": {
            "result": {
                "accepted": True,
                "saved": False,
                "state": "SUBMITTED",
                "path": str(output_path),
                "taskId": task.id,
            }
        },
        "metrics": {},
        "diagnostics": {},
    }

接口约定：

- `checkSize(taskName, estimatedBytes)` 在调用方创建大对象副本前检查单任务大小。实际接收任务及队列容量控制仍由 `submit` 负责。
- `submit(callable, *args, taskName, estimatedBytes)`：提交成功返回任务引用；服务执行时向任务函数注入首个参数 `TaskControl`，即 `callable(taskControl, *args)`。
- `wait(taskId)`：等待任务完成；成功返回小型结果，失败抛出保留原始原因的任务异常。异常交给调用方后视为已观察，算子可以捕获处理，也可以继续向上抛出。
- `status(taskId)`：返回 `QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED`。
- 任务函数只做后台 I/O，不访问设备、Qt、工作流变量、算子实例或已关闭的 OperatorLogger；不在后台任务里递归提交/等待同一服务。
- 图片在提交前复制，防止后续节点修改原数组。服务限制已接收任务的数量和估算字节数；单任务超过字节上限明确返回 `E_BACKGROUND_TASK_TOO_LARGE`。队列满时提交方等待容量并响应 Job 取消。
- 任务终态记录保留最近有限数量；Runner 尚未处理的 artifact/SQLite 依赖固定保留对应记录。记录过期后查询或等待返回 `E_BACKGROUND_TASK_EXPIRED`，不能推断成功。
- 未被算子 wait 观察的失败由 Runner 在下一节点边界或空闲轮询中发现；保留首个未观察失败即可触发当前执行失败，不需要另建复杂失败历史系统。

### TaskControl

Runtime 为每个任务注入 `TaskControl`。

- `raiseIfCancelled()`：任务开始和最终发布前检查 Job 是否已取消/服务是否已放弃。
- `publish(finalize)`：在服务生命周期门禁内执行短小的最终发布动作。服务 close 与 publish 使用同一把锁；服务进入 abandoned 后，迟到的 publish 必须拒绝。
- `temporaryFile(directory)` 在目标目录独占创建可写的暂存文件，并负责退出上下文时清理。在受管子进程中，Runtime 还会记录其归属，供进程退出后回收。

ImageSaver 在暂存文件中写完并关闭后，才进入 `publish` 原子创建最终文件。Windows 使用同一文件系统上拒绝覆盖的重命名，Linux 优先使用 `renameat2(RENAME_NOREPLACE)`。不支持该操作时，可用硬链接原子创建最终目录项。若文件系统无法执行这两种操作，任务返回写入错误。编码和写入均在发布门禁外完成。[Python 3.10 的重命名行为](https://docs.python.org/3.10/library/os.html#os.rename) · [Linux renameat2](https://man7.org/linux/man-pages/man2/rename.2.html)

## 4. 队列与生命周期

- 首版一个 FIFO worker；队列按任务数量和估算数据字节数双重限额，额度覆盖排队和正在执行的任务，完成/失败/取消后释放。
- 不自动重试，不静默丢弃已接收任务。
- 后台任务失败后，Runner 在节点边界、workflow run 开始/完成前检查；连续运行间隔和 Dispatch consumer IDLE 等待也定期检查，不能等到用户手动停止。
- 正常 Job 完成时停止接收并 drain 已接收任务；默认 drain 上限为 Runtime 内部常量 30000ms，不新增工程配置或 JobProcessSpec 字段。超时以 `E_BACKGROUND_TASK_DRAIN_TIMEOUT` 失败，不发布 `job.completed`。
- 用户主动 Stop 仍使用现有 Supervisor 5000ms graceful-stop 规则；不延长停止时间。取消时停止接收、取消未开始任务，活动任务尽量在现有停止宽限内收尾。
- 服务 close 超时后标记 abandoned；TaskControl.publish 拒绝迟到的最终发布。后台线程使用 daemon 生命周期，Job 进程退出后不会留下仍运行的线程。
- 正常 Job terminal 顺序：关闭提交入口并 drain → Runner 处理剩余完成记录和产物 → 关闭预览快照 writer → 发布 Job terminal event。已有工作流错误仍为主错误，后台清理错误作为附加诊断。

## 5. ImageSaver 与文件产物

### ImageSaver

- 在 manifest 中声明 `backgroundTasks`。
- 增加 `saveMode=sync/background`，默认 `sync`。
- 同步模式完全保持现有 fixed/unique 命名、覆盖规则、返回结构和错误语义。
- **后台模式首版只允许 `namingMode=unique`；选择 fixed 时返回明确参数错误。** 不自动把 fixed 改成 unique。
- 后台模式先确定唯一最终路径，复制图像后提交任务；立即返回 `accepted=true`、`saved=false`、`state=SUBMITTED`、`path` 和 `taskId`。
- 受管后台任务在目标目录的私有子目录中写入独立临时文件，完整关闭后通过 TaskControl 发布到回执指定的路径。若该路径在提交后被占用，保存节点报 `E_OUTPUT_EXISTS`，原文件内容保留，不登记成功产物。
- 一个 Job 或调试进程可以在不同的输出目录写图。每个在写的临时文件有独立的归属记录。进程强制停止或清理超时退出后，父进程根据所属记录逐一删除遗留暂存文件；同目录其他文件和已经发布的图片不在回收范围内。
- 任务完成结果包含最终路径和必要的小型元数据，不返回图像数组。

### 重启后清理暂存图片

Runtime 为每个用过的输出目录分配一个随机命名的 `.emo-image-stage-<标识>` 私有子目录。第一次使用前，SQLite 必须先提交输出目录和标识，提交成功才允许创建暂存图片。登记随同一个 Runtime 数据目录跨进程和重启保留。即使断电后只剩下暂存文件、单张图片的工作区登记已经丢失，下次仍有定位依据。目录登记使用 `PRAGMA synchronous=EXTRA`。[SQLite 对持久提交的定义](https://sqlite.org/pragma.html#pragma_synchronous)

写入进程从创建暂存文件起到完成发布和清理为止持有共享锁。启动时，取得数据目录主锁的 Runtime 只在已登记的私有子目录内尝试取得独占锁并清理受管暂存文件。仍有写入进程时整目录跳过。已发布图片、输出根下的其他文件及未登记位置不参与扫描。登记和锁文件保留供下次复用，删除受阻时记录诊断并保留下次启动重试的依据。

上一版正式 Job 的工作区可能保留按文件登记的路径。只有在能够确认原进程退出、输出目录可访问时，才按旧登记回收。没有可靠归属的旧文件保留。

2.2 本地页面测试发布通过展示服务启动 Job，使用展示服务自己的工作区。这个入口和常规 StartJob 都将所属 Runtime 的暂存登记库交给写入进程，重启时由同一处启动清理逻辑回收。

在 Windows 上，业务输出路径可用时，Runtime 追加的私有目录和文件名仍可能超过传统路径长度限制。创建和回收这些受管文件时使用扩展路径，返回给工作流的文件路径和写入登记库的位置沿用常规表示。

### Artifact 与预览

- Runner 识别明确的 pending 输出（`state=SUBMITTED`、`taskId`、`path`、`saved=false`），不立即调用 `ArtifactStore.registerPath()`。
- 任务成功后，Runner 在 Runtime 执行线程调用现有 `registerPath()`、发布 `artifact.created` 并调用预览快照捕获；失败/取消不登记成功产物。
- 任务完成记录与 pending artifact 在登记完成前保持关联。同步输出继续使用原有即时登记路径。

### SQLite FILE_REFERENCE

当前 Runner 在消费者节点执行前调用 `captureBoundValue()`，它会立即调用 SQLite `toStorage()`；FILE_REFERENCE 要求文件已存在。因此不能只在 SQLiteWriter 算子内部 wait。

- 当 pending ImageSaver 输出连接到 SQLite `FILE_REFERENCE` 时，Runner 暂存 taskId 和绑定信息，不立即采集。
- 到 SQLiteWriter 节点执行前，Runner wait 对应任务。
- 成功后先处理该任务完成记录和 artifact 登记，再用 `{"saved": true, "path": finalPath}` 调用现有 `captureBoundValue()`，最后交给 SQLiteWriter。
- 失败时生成错误 BoundValue，沿用 SQLiteWriter 现有 failurePolicy；不插入不存在的文件。
- 其他普通下游算子若需要文件，必须声明 `backgroundTasks` 并显式 wait；不依赖文件的节点无需等待。

DetectionArchive、ResultWriter、SQLiteWriter 的执行和事务语义保持不变；首版不把它们改成异步。

## 6. 执行模式

### 配置切换、分发和节点结果

- 在线配置选择 `drainAffected` 时，受影响 Job 的活动周期、已投递的消费任务和已受理图片的产物登记均纳入原有 30 秒配置预算。旧 Job 在成功图片完成登记并发出 `artifact.created` 后进入停止流程。普通连续周期仍可与前一轮的后台编码重叠。
- 后台任务编号归创建它的 Job 所有。在 Dispatch 输入和消费者输出跨进程前，发送方解析其中的待保存文件，等待写入并处理产物，将已保存文件的回执交给另一方。普通 JSON、标量和图像沿用现有编码链路。
- ForEach 汇集多个文件后再分发时，仍在工作流内流转的回执随值保留已登记文件的证明。这样即使最早的任务已从有限记录中淘汰，仍可交付曾经确认的文件；单独查询过期编号继续返回 `E_BACKGROUND_TASK_EXPIRED`。
- 每个 Dispatch 消费者串行处理投递。当前投递启动的任务及产物处理完成后才确认该次投递；后台失败使用当前投递身份，并保留提交节点的诊断。活跃投递的等待可被 Stop 中断，Join 沿用配置的等待时间。30 秒正常收尾上限只用于 Job 或调试执行关闭服务。
- While 的迭代间隔同时检查取消和后台失败。Designer 将迟到的产物按 Job、工作流执行和节点执行身份写入仍保留的历史记录，按原有规则限制记录数量和大小。
- 普通 Job 失败时先关闭最终文件发布入口，取消排队任务，给活动任务有限时间清理临时文件，并保留原始业务错误。用户主动 Stop 继续使用 Supervisor 的现有宽限时间。
- ImageSaver 在复制图像前检查单任务字节上限。后台文件按 UTC 时间和 UUID 命名，最终发布操作负责在并发写入时兑现“唯一命名不覆盖”的约定。

### 能力准入

| 模式 | 能力 |
|---|---|
| 正式 Job | 已声明的算子可使用服务 |
| 单算子调试 | Runtime trustedEntries 传递声明；本次调试结束前 drain |
| 工作流调试 | 调试会话共用服务；最终快照/关闭前 drain，暂停期间任务可完成 |
| PurePreview | 不提供提交能力 |
| clone-safe trial | 不继承父会话后台服务 |

## 7. 明确不做

本计划的重启恢复仅清理受管暂存图片。未完成的保存不会重放。跨 Job 全局线程池、多 worker 并发、任务持久化、优先级调度、通用资源事务、后台设备调用和新 RPC 不在本次范围内。
