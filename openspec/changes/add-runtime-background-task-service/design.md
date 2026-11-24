# 设计

## 1. 核心决定

增加的是 Runtime 执行能力，并对插件注册描述做一项最小扩展，用来声明算子是否可以使用这项能力。

- manifest 增加可选字段 `runtimeCapabilities`，首版唯一支持值为 `backgroundTasks`。
- 缺少该字段的旧 manifest 等同于空列表；重复值、非字符串值和未知能力值在注册校验阶段拒绝。
- `PluginManifest` 保存解析后的 `tuple[str, ...]`；`PluginDescriptor` 通过 manifest 暴露声明。
- 新字段作为 `PluginManifest` 最后一个带默认值的字段（默认空 tuple），避免破坏现有位置参数构造和旧 manifest。
- `OperatorMeta` 不增加 `runtimeCapabilities`，避免把运行时能力与端口、参数等算子元数据重复维护。
- `PluginRegistry.scanRoots()` 的扫描和注册流程不变，只扩展 manifest 校验和解析结果。
- 算子仍通过 `validateParams()` 和 `executeNode(inputs, params, runtimeContext)` 执行，不新增基类或必需方法。
- `OperatorExecutor` 读取当前算子的 manifest 声明：只有声明 `backgroundTasks` 时才向 `runtimeContext` 注入客户端；未声明的算子拿不到该入口。

示例 manifest：

    {
      "operatorId": "vision.io.image_saver",
      "runtimeCapabilities": ["backgroundTasks"]
    }

该声明表示算子可以提交、等待和查询 Runtime 后台任务，不代表整个 `executeNode()` 会被 Runtime 自动放到后台线程。算子具体何时提交、是否等待，仍由算子自身逻辑和参数决定。需要等待任务的下游算子也必须声明 `backgroundTasks`。

## 2. 所有权与进程边界

Runtime 的 Job 由 `JobSupervisor` 使用 multiprocessing spawn 启动独立进程。线程不能跨这些进程共享，因此采用以下归属：

- 每个 Job worker 进程创建一个 `BackgroundTaskService`，内部一条工作线程、一个有界 FIFO 队列。
- 同一 Job 的普通节点、循环、子流程和连续运行周期共用该服务。
- 不同 Job 各自拥有服务；Dispatch 的 source 与 consumer 也是独立 Job，各自拥有服务。
- Job 退出时由 worker 关闭服务。Supervisor 不直接持有或操作后台线程。
- 算子不创建、销毁或直接访问 `threading.Thread`。

    Runtime 主进程
      └─ JobSupervisor
           ├─ Job A worker process
           │    ├─ WorkflowRunner / OperatorExecutor
           │    ├─ 已声明能力的 runtimeContext.backgroundTasks
           │    └─ BackgroundTaskService → FIFO queue → worker thread
           └─ Job B worker process
                └─ 独立 BackgroundTaskService / worker thread

## 3. 注册声明与运行时注入

manifest 是能力声明的唯一来源。首版支持值：

- `backgroundTasks`：允许算子通过 RuntimeContext 提交、等待和查询后台任务。

注册处理：

1. `validateManifestFields()` 解析 `runtimeCapabilities`。
2. 只接受已知能力、字符串元素和无重复列表；错误沿用 manifest validation issue 流程，算子进入 `rejectedOperators`。
3. `PluginManifest.runtimeCapabilities` 保存规范化元组。
4. `PluginRegistry` 仍生成原有 `PluginDescriptor`，不新增第二套注册表。
5. 正式 Job 的 `OperatorExecutor` 根据当前 `PluginDescriptor.manifest.runtimeCapabilities` 决定是否注入客户端。

调试入口目前以 class 而非 `PluginDescriptor` 构造临时 registry。单算子调试和工作流调试必须由 Runtime 侧 `trustedEntries()` 从已校验的 manifest 提取能力，并通过调试 worker spec 传入；worker 不能相信客户端提交的能力列表。为此，执行器允许显式接收由 Runtime 校验后生成的能力映射。纯预览和 clone-safe trial 不注入服务，即使 manifest 声明了 `backgroundTasks` 也不能绕过预览隔离。

## 4. 算子接口与任务状态

建议模块名：`apps/runtime/background_tasks/`；具体文件拆分由实施时沿用现有目录习惯，不新增注册层。

已声明能力的算子使用：

    background = runtimeContext["backgroundTasks"]

    task = background.submit(
        save_image,
        image.copy(),
        output_path,
        taskName="image.save",
    )

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

接口语义：

- `submit(callable, *args, taskName=..., estimatedBytes=...)`：提交任务并立即返回任务引用；服务执行时以首个参数注入 Runtime 创建的 `TaskControl`，任务函数签名为 `callable(taskControl, *args)`；不承诺文件已落地。
- `task.id`：本 Job 内唯一任务 ID，适合放进 JSON 输出并传给后续算子。
- `background.wait(taskId)`：等待指定任务结束；成功返回任务函数结果，失败抛出带原始错误信息的后台任务异常。等待过程响应当前节点取消。失败被 wait 抛给调用方后视为已观察；算子可明确捕获并处理。
- `background.status(taskId)`：查询 `QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED`。
- Runtime 注入的 `TaskControl` 提供 `raiseIfCancelled()` 和 `publish(finalize)`；后者在服务生命周期门禁内执行短小的最终发布操作。任务函数先写临时文件，再通过 `publish()` 原子发布正式路径。
- 任务记录只保留有界数量的终态摘要；达到上限时只淘汰最早的终态记录，不淘汰运行中任务。等待已淘汰任务返回明确的 `E_BACKGROUND_TASK_EXPIRED`，不能伪造成功。
- Runner 尚未处理的 pending artifact、SQLite pending binding 必须固定保留对应任务结果，不能被普通终态淘汰策略提前清除；释放依赖后才允许淘汰。
- 任务记录淘汰不能抹去未观察失败：服务另行保留有界失败摘要/计数，Job 收尾仍能识别是否存在未处理失败。
- 服务自动捕获提交时的 Job、Project、Workflow、WorkflowRun、Node、NodeRun、iterationPath、cycle 等身份；算子不能自行伪造归属。
- 服务只传递轻量任务元数据和结果摘要，不把图片字节放入 Runtime 事件。
- 通用服务不擅自复制任意 Python 参数。提交方必须保证传入参数在任务完成前不可变；图像算子在提交前复制图像，避免上游继续修改同一 ndarray。
- 任务函数返回值应是小型结果（例如最终路径、字节数）；不得把图片 ndarray 或大型缓存放入任务结果记录。

ImageSaver 后台写入的任务函数约定：

    def save_image(taskControl, image, finalPath):
        tempPath = write_complete_temp_file(image, finalPath)
        taskControl.raiseIfCancelled()
        taskControl.publish(lambda: os.replace(tempPath, finalPath))
        return {"path": str(finalPath)}

`publish()` 与服务 close 使用同一生命周期门禁；close 一旦进入 abandoned，任务即使从阻塞 I/O 恢复，也不能再发布最终文件。
- `TaskControl.raiseIfCancelled()` 检查任务是否因 Job 关闭而取消；`TaskControl.publish(finalize)` 在服务生命周期提交锁内执行短小的最终发布操作。服务关闭/放弃后，publish 必须拒绝执行；ImageSaver 用它包住临时文件到最终路径的原子 rename/replace，避免 close 超时后任务又迟到发布文件。

后台任务不复用 `executeNode()` 返回后即关闭的 `OperatorLogger`。任务开始、完成和失败事件由服务记录完成结果，`WorkflowRunner` 在每个节点边界、每次 workflow run 开始处、workflow.completed 发布前以及 Job 收尾时取出完成记录，在 Runtime 执行线程中发布事件及处理产物，避免后台线程直接操作 Runner、ResultCollector 或 Qt 对象。连续 Job 的 cycle 间隔等待和 Dispatch consumer 的 IDLE 等待也必须定期调用同一 poll 入口，避免后台失败在长时间空闲期间一直无人发现。任务失败若已被算子显式 wait 并捕获，视为已处理；Runner 为 SQLite FILE_REFERENCE 做的内部 wait 也视为已观察，再由 SQLite 现有 failurePolicy 决定是否继续。

## 5. 队列与执行规则

- 首版一条工作线程，FIFO 顺序执行；先服务文件编码、文件写入等 I/O 工作。
- 队列同时限制待执行任务数量和估算的待处理数据字节数，避免少量大图像造成内存无界增长。
- 队列满或字节预算不足时提交方等待可用额度，并响应当前节点取消；单任务超过单项上限时明确返回 `E_BACKGROUND_TASK_TOO_LARGE`，不能永久等待。
- ImageSaver 提交时提供图像字节权重；队列额度在任务完成/取消后释放。
- 不自动重试任务。失败由任务句柄和 Runtime 事件明确报告。
- 任务函数不能依赖已关闭的算子 logger，也不应在后台线程中访问设备 proxy、修改工作流变量或操作 Qt UI。
- 任务函数是后台 I/O 叶子任务，不得再次向同一服务 submit 或 wait，避免单工作线程自我等待死锁；不得捕获算子实例或 RuntimeContext。
- 不提供通用 CPU 线程池；CPU 密集型推理和图像计算继续使用现有执行机制。

## 6. 任务结果与 Job 终态

任务是 Job 的一部分，不能在 Job 已结束后继续偷偷写文件。

| 场景 | 行为 |
|---|---|
| 提交成功 | 返回 taskId 和 SUBMITTED，不代表任务完成 |
| 任务成功 | 状态变为 SUCCEEDED，完成记录进入 Runner 的完成队列 |
| 任务失败且算子调用 wait | wait 抛出任务异常并标记为已观察；算子可捕获并处理，也可继续向上抛出 |
| 任务失败但未被 wait 观察 | Runner 在下一个节点边界、下一次 workflow run 开始处或 workflow 完成前检查失败记录；未处理失败立即以 E_BACKGROUND_TASK_FAILED 终止当前执行，不等到无限连续 Job 结束 |
| Job 正常完成 | 停止接收新任务，排空已接收队列；Runner 处理全部完成记录和延迟产物后，才发布 job.completed |
| Job 取消或已有执行错误 | 停止接收；尚未开始的排队任务取消；正在执行的任务尝试收尾，不补执行、不重试 |
| 后台收尾同时遇到原始工作流错误 | 保留原始错误为主错误，后台清理错误放入诊断，不覆盖原始失败原因 |

正常 Job 终态顺序必须是：停止接收 → 等待/取消后台任务 → Runner 处理完成记录与延迟产物 → 关闭预览快照 writer → 发布 Job terminal event。不能先关闭预览 writer，再让后台完成回调尝试捕获产物。

现有 Supervisor 的 graceful stop 默认 5000ms；用户主动 graceful stop 超时后仍由 Supervisor 按现有规则终止整个 Job 进程，不延长该时间。JobProcessSpec 增加内部 `backgroundTaskDrainTimeoutMs`（默认 30000ms），用于正常结束或工作流失败时有限等待后台任务。close 超时后服务停止接收、取消未开始任务、标记活动任务 abandoned，并通过 TaskControl 发布门禁阻止迟到提交；后台 worker 线程采用 daemon 生命周期，Job 进程退出后不允许后台任务继续存活。正常 Job 完成但 drain 超时，必须以 `E_BACKGROUND_TASK_DRAIN_TIMEOUT` 失败，不能发 job.completed；已有工作流错误仍为主错误，用户取消仍保持 ABORTED。被终止任务不发布成功或 artifact.created；可能留下的临时文件必须清理，正式目标文件只能在完整写入后原子发布。

## 7. 算子接入与现有文件链路

### ImageSaver（首个接入者）

- 在 manifest.runtimeCapabilities 中声明 `backgroundTasks`。
- 在现有参数中增加 `saveMode`：`sync / background`，默认 `sync`。
- 同步模式保持现有返回结构、覆盖规则、唯一命名和异常语义。
- 为避免不同 Job、循环周期或同一工作流的异步任务覆盖同一路径，首版后台模式要求 `namingMode=unique`；选择固定命名时明确返回参数错误，不静默改变用户路径。同步模式仍完整支持固定/唯一命名。
- 后台模式先确定唯一目标路径，再复制图像并提交编码/写入任务；返回 `accepted=true`、`saved=false`、`state=SUBMITTED`、`path` 和 `taskId`。
- 后台模式不能返回 `saved=true`，也不能把入队成功报告成文件保存成功。
- 工作线程先写入目标目录内的任务专属临时文件，完整写入并关闭后才通过 `TaskControl.publish()` 原子发布到唯一最终路径；异常/取消/服务放弃时清理临时文件。不能直接逐步写入最终路径，避免进程被强制终止后留下看似正式的半张图片。
- 任务成功结果包含真实写入完成的路径；失败保留原始异常。ImageSaver 不在工作线程中写 RuntimeContext、日志对象或工作流变量。

### Artifact 与预览

当前 `WorkflowRunner._publishArtifacts()` 会立即处理输出 JSON 中带 `path` 的值；`ArtifactStore.registerPath()` 会复制文件并计算 SHA-256。后台模式文件尚未存在，不能沿用立即登记路径。

- ImageSaver 后台输出使用明确的待完成标记（`state=SUBMITTED`、`taskId`、`path`、`saved=false`）。Runner 将它登记为 pending artifact，不立即调用 `registerPath()`，不发布 `artifact.created`。
- 后台完成记录由 Runner 在自己的执行线程中处理。任务成功后，Runner 才调用现有 `ArtifactStore.registerPath()`、发布 `artifact.created` 并调用预览快照的 `captureArtifact()`；任务失败不发布 artifact.created。
- Runner 在每个节点边界处理完成记录，并在 Job 收尾 drain 后处理最后一批，保证完成事件和 artifact.created 都早于 Job terminal event。连续运行的间隔等待和 Dispatch consumer 的空闲轮询也必须检查未观察失败。
- pending artifact 在 ArtifactStore 登记完成前固定保留任务结果；任务完成文件必须是该任务的唯一最终路径，避免后续写入覆盖它再被错误登记。
- 同步输出仍走原有立即登记路径。

### SQLite FILE_REFERENCE 绑定

这是现有链路的特殊依赖：`captureBoundValue()` 会立即调用 `toStorage()`；SQLite 的 `FILE_REFERENCE` 只接受 `saved=true` 且文件已经存在的路径。因此仅让下游 SQLiteWriter 在自身执行时调用 wait 是不够的——当前 Runner 在 SQLiteWriter 执行之前就已经采集绑定值。

首版必须在 Runner 增加延迟绑定处理：

1. 当 ImageSaver 的 pending artifact 输出连接到 SQLiteWriter 的 `FILE_REFERENCE` 映射时，Runner 暂存 taskId、原始输出、CompiledBinding 和来源运行身份，不立即调用 `captureBoundValue()`。
2. 在 SQLiteWriter 节点真正执行前，Runner 等待该 taskId。
3. 成功后，Runner 使用等价于 `{"saved": true, "path": finalPath}` 的已完成引用调用现有 `captureBoundValue()`，再交给 SQLiteWriter；SQLiteWriter 和其事务实现不改。
4. 失败时，生成带明确错误码的 BoundValue，让 SQLiteWriter 按现有失败策略处理；不得插入一个不存在的文件引用。该内部 wait 将任务失败标记为已观察，SQLiteWriter 的 `stop / continue` 策略继续决定工作流结果。
5. 普通 JSON 输出仍可携带 taskId。需要读取文件的普通下游算子必须声明 backgroundTasks 并显式 wait；不依赖文件的节点无需等待。
6. pending binding 在 SQLiteWriter 消费完成前固定保留任务终态记录，避免任务记录淘汰导致合法绑定随机过期。

这样图片保存可以与中间计算节点重叠；只有真正消费该文件的 SQLite FILE_REFERENCE 节点才等待保存完成。

### 暂时保持同步的算子

- DetectionArchive 目前逐个裁剪并同步调用 ImageSaver，首版保持同步，避免同时改变部分完成进度、路径和冷却语义。
- ResultWriter 保持序列化与原子写入的同步契约。
- SQLiteWriter 保持事务提交和 receipt 同步语义；只由 Runner 延迟准备 FILE_REFERENCE 绑定。
- 文件读取和目录扫描仍同步执行；后台能力不是把所有文件 API 自动搬走。

## 8. 不同执行模式

| 执行模式 | 是否开放后台提交 | 处理方式 |
|---|---|---|
| 正式 Job | 仅 manifest 声明后开放 | Job worker 创建服务，OperatorExecutor 按声明注入 |
| 单算子调试 | 仅已校验声明后开放 | `trustedEntries()` 将已校验能力传入 debug worker；每次执行返回前 drain 已提交任务，再冻结输出；原始算子输出保留提交时的 SUBMITTED 回执，最终状态通过调试任务事件/状态记录展示，不伪造改写原始输出 |
| 工作流调试 | 仅已校验声明后开放 | 调试会话持有服务；运行期间共享，工作流最终快照/关闭前 drain；暂停期间任务可以继续完成并产生调试事件 |
| 纯算子预览（previewMode=pure） | 否 | 继续保持无副作用；不注入可提交任务的服务 |
| 工作流调试中的 clone-safe trial | 否 | 试运行保持纯计算语义，不继承正式会话的后台任务服务 |
| 直接构造 OperatorExecutor 的测试/工具 | 默认否 | 只有显式注入已授权服务与能力声明时才开放 |

调试 worker 当前使用 class-only registry。实施时必须让能力声明从 Runtime 侧 `trustedEntries()` 生成并随 worker spec 传递，不能让用户工程或调试请求自行声明能力。

## 9. 兼容边界与明确排除

旧 manifest 缺少 `runtimeCapabilities` 时默认为空列表，现有算子不受影响。PluginRegistry 仍是唯一注册入口，不新增第二套注册表；OperatorMeta 不新增字段；不新增算子基类或必需方法。

不做跨 Job 全局线程池、多个 worker 的通用线程池、优先级调度、自动重试、任务持久化、断电恢复、后台设备调用、任意对象深拷贝框架、通用资源事务或新 RPC。若未来实际场景证明单线程成为瓶颈，再单独评估并发度，不在首版预设。
