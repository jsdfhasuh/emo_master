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

    task = runtimeContext["backgroundTasks"].submit(
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

- `submit(callable, *args, taskName, estimatedBytes)`：提交成功返回任务引用；服务执行时向任务函数注入首个参数 `TaskControl`，即 `callable(taskControl, *args)`。
- `wait(taskId)`：等待任务完成；成功返回小型结果，失败抛出保留原始原因的任务异常。异常交给调用方后视为已观察，算子可以捕获处理，也可以继续向上抛出。
- `status(taskId)`：返回 `QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED`。
- 任务函数只做后台 I/O，不访问设备、Qt、工作流变量、算子实例或已关闭的 OperatorLogger；不在后台任务里递归提交/等待同一服务。
- 图片在提交前复制，防止后续节点修改原数组。服务限制已接收任务的数量和估算字节数；单任务超过字节上限明确返回 `E_BACKGROUND_TASK_TOO_LARGE`。队列满时提交方等待容量并响应 Job 取消。
- 任务终态记录保留最近有限数量；Runner 尚未处理的 artifact/SQLite 依赖固定保留对应记录。记录过期后查询或等待返回 `E_BACKGROUND_TASK_EXPIRED`，不能推断成功。
- 未被算子 wait 观察的失败由 Runner 在下一节点边界或空闲轮询中发现；保留首个未观察失败即可触发当前执行失败，不需要另建复杂失败历史系统。

### TaskControl

Runtime 为每个任务注入 `TaskControl`，只提供两个必要操作：

- `raiseIfCancelled()`：任务开始和最终发布前检查 Job 是否已取消/服务是否已放弃。
- `publish(finalize)`：在服务生命周期门禁内执行短小的最终发布动作。服务 close 与 publish 使用同一把锁；服务进入 abandoned 后，迟到的 publish 必须拒绝。

ImageSaver 任务先写入临时文件并关闭，再调用 `taskControl.publish(lambda: os.replace(tempPath, finalPath))`。不要在 publish 回调中执行编码或普通文件写入。

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
- 后台任务写入同目录任务专属临时文件，完整关闭后通过 TaskControl 原子发布最终文件。失败、取消或服务放弃时尽力清理临时文件；正式最终路径不能暴露半张图片。
- 任务完成结果包含最终路径和必要的小型元数据，不返回图像数组。

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

| 模式 | 能力 |
|---|---|
| 正式 Job | 已声明的算子可使用服务 |
| 单算子调试 | Runtime trustedEntries 传递声明；本次调试结束前 drain |
| 工作流调试 | 调试会话共用服务；最终快照/关闭前 drain，暂停期间任务可完成 |
| PurePreview | 不提供提交能力 |
| clone-safe trial | 不继承父会话后台服务 |

## 7. 明确不做

不做跨 Job 全局线程池、多 worker 并发、任务持久化、断电恢复、自动重试、优先级调度、通用资源事务、后台设备调用或新 RPC。若实际使用证明一条线程成为瓶颈，再单独评估并发度。
