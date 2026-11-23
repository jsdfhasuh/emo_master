# 设计

## 1. 核心决定

增加的是 Runtime 执行能力，并对插件注册描述做一项最小扩展，用来声明算子是否可以使用这项能力。

- manifest 增加可选字段 `runtimeCapabilities`，首版唯一支持值为 `backgroundTasks`。
- 缺少该字段的旧 manifest 等同于空列表；重复值、非字符串值和未知能力值在注册校验阶段拒绝。
- `PluginManifest` 保存解析后的 `tuple[str, ...]`；`PluginDescriptor` 通过 manifest 暴露声明。
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

- `submit(callable, *args, taskName=...)`：提交任务并立即返回任务引用；不承诺文件已落地。
- `task.id`：本 Job 内唯一任务 ID，适合放进 JSON 输出并传给后续算子。
- `background.wait(taskId)`：等待指定任务结束；成功返回任务函数结果，失败抛出带原始错误信息的后台任务异常。等待过程响应当前节点取消。
- `background.status(taskId)`：查询 `QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED`。
- 任务记录只保留有界数量的终态摘要；达到上限时只淘汰最早的终态记录，不淘汰运行中任务。等待已淘汰任务返回明确的 `E_BACKGROUND_TASK_EXPIRED`，不能伪造成功。
- 服务自动捕获提交时的 Job、Project、Workflow、WorkflowRun、Node、NodeRun、iterationPath、cycle 等身份；算子不能自行伪造归属。
- 服务只传递轻量任务元数据和结果摘要，不把图片字节放入 Runtime 事件。
- 通用服务不擅自复制任意 Python 参数。提交方必须保证传入参数在任务完成前不可变；图像算子在提交前复制图像，避免上游继续修改同一 ndarray。
- 任务函数返回值应是小型结果（例如最终路径、字节数）；不得把图片 ndarray 或大型缓存放入任务结果记录。

后台任务不复用 `executeNode()` 返回后即关闭的 `OperatorLogger`。任务开始、完成和失败事件由服务记录完成结果，`WorkflowRunner` 在节点边界和 Job 收尾时取出完成记录，在 Runtime 执行线程中发布事件及处理产物，避免后台线程直接操作 Runner、ResultCollector 或 Qt 对象。

## 5. 队列与执行规则

- 首版一条工作线程，FIFO 顺序执行；先服务文件编码、文件写入等 I/O 工作。
- 队列有界，不允许无限堆积，也不允许静默丢弃已接收的业务任务。
- 队列满时提交方等待可用位置，并响应当前节点取消；这是磁盘持续落后于生产速度时的背压。
- 不自动重试任务。失败由任务句柄和 Runtime 事件明确报告。
- 任务函数不能依赖已关闭的算子 logger，也不应在后台线程中访问设备 proxy、修改工作流变量或操作 Qt UI。
- 不提供通用 CPU 线程池；CPU 密集型推理和图像计算继续使用现有执行机制。

## 6. 任务结果与 Job 终态

任务是 Job 的一部分，不能在 Job 已结束后继续偷偷写文件。

| 场景 | 行为 |
|---|---|
| 提交成功 | 返回 taskId 和 SUBMITTED，不代表任务完成 |
| 任务成功 | 状态变为 SUCCEEDED，完成记录进入 Runner 的完成队列 |
| 任务失败且调用方执行 wait | wait 抛出任务异常；由算子决定处理或继续向上抛出 |
| 任务失败但调用方未 wait | 仍发布失败事件；正常 Job 收尾时发现未处理失败，将 Job 终态置为 FAILED，错误码 E_BACKGROUND_TASK_FAILED |
| Job 正常完成 | 停止接收新任务，排空已接收队列；Runner 处理全部完成记录和延迟产物后，才发布 job.completed |
| Job 取消或已有执行错误 | 停止接收；尚未开始的排队任务取消；正在执行的任务尝试收尾，不补执行、不重试 |
| 后台收尾同时遇到原始工作流错误 | 保留原始错误为主错误，后台清理错误放入诊断，不覆盖原始失败原因 |

正常 Job 终态顺序必须是：停止接收 → 等待/取消后台任务 → Runner 处理完成记录与延迟产物 → 关闭预览快照 writer → 发布 Job terminal event。不能先关闭预览 writer，再让后台完成回调尝试捕获产物。

现有 Supervisor 的 graceful stop 有超时，默认 5000ms；如果正在执行的后台任务超过现有停止宽限，Supervisor 仍可能按现有规则终止整个 Job 进程。首版不延长全局停止时间，也不承诺能强制中断任意 Python 文件操作。被强制终止的任务不发布成功或 artifact.created；可能留下的临时文件必须由后台保存实现清理，正式目标文件只能在写入完成后发布。

## 7. 算子接入与现有文件链路

### ImageSaver（首个接入者）

- 在 manifest.runtimeCapabilities 中声明 `backgroundTasks`。
- 在现有参数中增加 `saveMode`：`sync / background`，默认 `sync`。
- 同步模式保持现有返回结构、覆盖规则、唯一命名和异常语义。
- 后台模式先确定目标路径和命名，再复制图像并提交编码/写入任务；返回 `accepted=true`、`saved=false`、`state=SUBMITTED`、`path` 和 `taskId`。
- 后台模式不能返回 `saved=true`，也不能把入队成功报告成文件保存成功。
- 任务成功结果包含真实写入完成的路径；失败保留原始异常。ImageSaver 不在工作线程中写 RuntimeContext、日志对象或工作流变量。

### Artifact 与预览

当前 `WorkflowRunner._publishArtifacts()` 会立即处理输出 JSON 中带 `path` 的值；`ArtifactStore.registerPath()` 会复制文件并计算 SHA-256。后台模式文件尚未存在，不能沿用立即登记路径。

- ImageSaver 后台输出使用明确的待完成标记（`state=SUBMITTED`、`taskId`、`path`、`saved=false`）。Runner 将它登记为 pending artifact，不立即调用 `registerPath()`，不发布 `artifact.created`。
- 后台完成记录由 Runner 在自己的执行线程中处理。任务成功后，Runner 才调用现有 `ArtifactStore.registerPath()`、发布 `artifact.created` 并调用预览快照的 `captureArtifact()`；任务失败不发布 artifact.created。
- Runner 在每个节点边界处理完成记录，并在 Job 收尾 drain 后处理最后一批，保证完成事件和 artifact.created 都早于 Job terminal event。
- 同步输出仍走原有立即登记路径。

### SQLite FILE_REFERENCE 绑定

这是现有链路的特殊依赖：`captureBoundValue()` 会立即调用 `toStorage()`；SQLite 的 `FILE_REFERENCE` 只接受 `saved=true` 且文件已经存在的路径。因此仅让下游 SQLiteWriter 在自身执行时调用 wait 是不够的——当前 Runner 在 SQLiteWriter 执行之前就已经采集绑定值。

首版必须在 Runner 增加延迟绑定处理：

1. 当 ImageSaver 的 pending artifact 输出连接到 SQLiteWriter 的 `FILE_REFERENCE` 映射时，Runner 暂存 taskId、原始输出、CompiledBinding 和来源运行身份，不立即调用 `captureBoundValue()`。
2. 在 SQLiteWriter 节点真正执行前，Runner 等待该 taskId。
3. 成功后，Runner 使用等价于 `{"saved": true, "path": finalPath}` 的已完成引用调用现有 `captureBoundValue()`，再交给 SQLiteWriter；SQLiteWriter 和其事务实现不改。
4. 失败时，生成带明确错误码的 BoundValue，让 SQLiteWriter 按现有失败策略处理；不得插入一个不存在的文件引用。
5. 普通 JSON 输出仍可携带 taskId。需要读取文件的普通下游算子必须声明 backgroundTasks 并显式 wait；不依赖文件的节点无需等待。

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
| 单算子调试 | 仅已校验声明后开放 | `trustedEntries()` 将已校验能力传入 debug worker；每次执行返回前 drain 已提交任务，再冻结输出并反映最终任务状态 |
| 工作流调试 | 仅已校验声明后开放 | 调试会话持有服务；运行期间共享，工作流最终快照/关闭前 drain；暂停期间任务可以继续完成并产生调试事件 |
| 纯算子预览（previewMode=pure） | 否 | 继续保持无副作用；不注入可提交任务的服务 |
| 工作流调试中的 clone-safe trial | 否 | 试运行保持纯计算语义，不继承正式会话的后台任务服务 |
| 直接构造 OperatorExecutor 的测试/工具 | 默认否 | 只有显式注入已授权服务与能力声明时才开放 |

调试 worker 当前使用 class-only registry。实施时必须让能力声明从 Runtime 侧 `trustedEntries()` 生成并随 worker spec 传递，不能让用户工程或调试请求自行声明能力。

## 9. 兼容边界与明确排除

旧 manifest 缺少 `runtimeCapabilities` 时默认为空列表，现有算子不受影响。PluginRegistry 仍是唯一注册入口，不新增第二套注册表；OperatorMeta 不新增字段；不新增算子基类或必需方法。

不做跨 Job 全局线程池、多个 worker 的通用线程池、优先级调度、自动重试、任务持久化、断电恢复、后台设备调用、任意对象深拷贝框架、通用资源事务或新 RPC。若未来实际场景证明单线程成为瓶颈，再单独评估并发度，不在首版预设。
