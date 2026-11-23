# 设计

## 1. 核心决定

增加的是 Runtime 执行能力，不是插件注册能力。

- PluginRegistry 继续只扫描 manifest、导入算子类并校验 manifest/meta。
- 算子仍通过 validateParams() 和 executeNode(inputs, params, runtimeContext) 执行。
- Runtime 在每次 executeNode() 调用的字典型 runtimeContext 中注入 backgroundTasks 客户端。
- 客户端绑定本次节点运行身份；底层服务由 Job worker 持有，多个算子共享同一个服务。

RunContext 是 Runtime 内部的运行身份对象；本次不把线程、队列或任务状态塞进 RunContext，也不把它改造成插件 API。

## 2. 所有权与进程边界

Runtime 的 Job 由 JobSupervisor 使用 multiprocessing spawn 启动独立进程。线程不能跨这些进程共享，因此采用以下归属：

- 每个 Job worker 进程创建一个 BackgroundTaskService，内部一条工作线程、一个有界 FIFO 队列。
- 同一 Job 的普通节点、循环、子流程和连续运行周期共用该服务。
- 不同 Job 各自拥有服务；Dispatch 的 source 与 consumer 也是独立 Job，各自拥有服务。
- Job 退出时由 worker 关闭服务。Supervisor 不直接持有或操作后台线程。
- 算子不创建、销毁或直接访问 threading.Thread。

    Runtime 主进程
      └─ JobSupervisor
           ├─ Job A worker process
           │    ├─ WorkflowRunner / OperatorExecutor
           │    ├─ RuntimeContext.backgroundTasks
           │    └─ BackgroundTaskService → FIFO queue → worker thread
           └─ Job B worker process
                └─ 独立 BackgroundTaskService / worker thread

## 3. 算子接口

建议模块名：apps/runtime/background_tasks/；具体文件拆分由实施时沿用现有目录习惯，不新增注册层。

算子使用方式：

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

- submit(callable, *args, taskName=...)：提交任务并立即返回任务引用；不承诺文件已落地。
- task.id：本 Job 内唯一任务 ID，适合放进 JSON 输出并传给后续算子。
- background.wait(taskId)：等待指定任务结束；成功返回任务函数结果，失败抛出带原始错误信息的后台任务异常。等待过程响应当前节点取消。
- 可查询任务状态：QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED。
- 服务自动捕获提交时的 Job、Project、Workflow、WorkflowRun、Node、NodeRun、iterationPath、cycle 等身份；算子不能自行伪造归属。
- 服务只传递轻量任务元数据和结果摘要，不把图片字节放入 Runtime 事件。
- 通用服务不擅自复制任意 Python 参数。提交方必须保证传入参数在任务完成前不可变；图像算子在提交前复制图像，避免上游继续修改同一 ndarray。

后台任务不复用 executeNode() 返回后即关闭的 OperatorLogger。任务开始、完成和失败由服务使用捕获的运行身份通过 Runtime eventPublisher 发布。

## 4. 队列与执行规则

- 首版一条工作线程，FIFO 顺序执行；先服务文件编码、文件写入等 I/O 工作。
- 队列有界，不允许无限堆积，也不允许静默丢弃已接收的业务任务。
- 队列满时提交方等待可用位置，并响应当前节点取消；这是磁盘持续落后于生产速度时的背压。
- 不自动重试任务。失败由任务句柄和 Runtime 事件明确报告。
- 任务函数不能依赖已关闭的算子 logger，也不应在后台线程中访问设备 proxy、修改工作流变量或操作 Qt UI。
- 不提供通用 CPU 线程池；CPU 密集型推理和图像计算继续使用现有执行机制。

## 5. 任务结果与 Job 终态

任务是 Job 的一部分，不能在 Job 已结束后继续偷偷写文件。

| 场景 | 行为 |
|---|---|
| 提交成功 | 返回 taskId 和 SUBMITTED，不代表保存成功 |
| 任务成功 | 状态变为 SUCCEEDED，发布完成事件，句柄可取得函数结果 |
| 任务失败且调用方执行 wait | wait 抛出任务异常；由算子决定处理或继续向上抛出 |
| 任务失败但调用方未 wait | 仍发布失败事件；正常 Job 收尾时发现未被处理的失败，将 Job 终态置为 FAILED，错误码 E_BACKGROUND_TASK_FAILED |
| Job 正常完成 | 停止接收新任务，排空已接收队列，确认全部任务结束后才发布 job.completed |
| Job 取消或已有执行错误 | 停止接收；尚未开始的排队任务取消；正在执行的任务允许完成后再退出进程，不补执行、不重试 |
| 后台收尾同时遇到原始工作流错误 | 保留原始错误为主错误，后台错误放入诊断，不覆盖原始失败原因 |

wait(taskId) 成功等待到任务结果；若任务失败，异常被调用方观察。调用方可以明确捕获并处理该错误。未被观察的后台失败不能被静默当作 Job 成功。

运行中的文件写入不强行中断；这避免线程仍持有文件句柄时 Job workspace 已被清理。Job 取消的终态仍按现有取消契约表达。

## 6. 算子接入范围

### ImageSaver（首个接入者）

- 在现有参数中增加 saveMode：sync / background，默认 sync。
- 同步模式保持现有返回结构、覆盖规则、唯一命名和异常语义。
- 后台模式先确定目标路径和命名，再复制图像并提交编码/写入任务；返回 accepted=true、saved=false、state=SUBMITTED、path 和 taskId。
- 后台模式不能返回 saved=true，也不能把入队成功报告成文件保存成功。
- 如果下游必须立即读取该文件，应通过 taskId 调用 backgroundTasks.wait(taskId)；不依赖文件的后续节点可以继续执行。

### artifact 与预览

当前 WorkflowRunner 会把输出 JSON 中带 path 的值立即交给 ArtifactStore，并发布 artifact.created。后台模式的文件尚未存在，不能沿用这条立即登记路径。

Runner 应识别带 taskId 的待完成文件输出，将现有 artifact 登记、复制/哈希及 artifact 预览动作挂到该任务成功完成之后。任务失败时不发布已创建 artifact。同步输出仍走原有立即登记路径。

### 暂时保持同步的算子

- DetectionArchive 目前逐张调用 ImageSaver，并在节点返回值中记录已保存裁图数量和路径；首版保持同步，避免把部分完成进度、路径和任务依赖同时改造。
- ResultWriter 保持序列化与原子写入的同步契约。
- SQLiteWriter 保持事务提交和 receipt 同步语义。
- 文件读取和目录扫描仍同步执行；后台能力不是把所有文件 API 自动搬走。

## 7. 不同执行模式

| 执行模式 | 是否开放后台提交 | 处理方式 |
|---|---|---|
| 正式 Job | 是 | Job worker 创建并注入服务 |
| 单算子调试 | 是 | 调试 worker 持有自己的服务；在冻结调试输出前完成本次已提交任务 |
| 工作流调试 | 是 | 调试会话持有服务；结束/取消时按相同规则收尾 |
| 纯算子预览（previewMode=pure） | 否 | 继续保持无副作用；不注入可提交任务的服务，后台模式请求明确返回不可用 |
| 工作流调试中的 clone-safe trial | 否 | 试运行保持纯计算语义，不继承正式会话的后台任务能力 |
| 直接构造 OperatorExecutor 的测试/工具 | 可选 | 通过显式注入服务启用；未注入时原有同步算子继续运行 |

## 8. 注册机制与兼容边界

不改 PluginRegistry.scanRoots()、loadOperatorClass()、validateConsistency()，不新增必需方法、不要求继承基类、不新增 manifest 能力声明。

ImageSaver 的 saveMode 是普通算子参数，因此只需同步更新该算子的 manifest 与 OperatorMeta.paramSchema。现有注册校验继续按原规则比较声明和实现；这不是注册机制变更。

现有算子不读取 backgroundTasks，行为不变。新增异步行为默认关闭，旧工程无需迁移。

## 9. 明确排除

不做跨 Job 全局线程池、多个 worker 的通用线程池、优先级调度、自动重试、任务持久化、断电恢复、后台设备调用、任意对象深拷贝框架、通用资源事务或新 RPC。若未来实际场景证明单线程成为瓶颈，再单独评估并发度，不在首版预设。
