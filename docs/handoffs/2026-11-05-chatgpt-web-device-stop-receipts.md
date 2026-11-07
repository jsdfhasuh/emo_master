# ChatGPT Web：PR #11 设备停止回执修复与 Dot 桌面验收交接

日期：2026-11-05。仓库：`jsdfhasuh/emo_master`，PR：#11。

本交接记录源码修复、本地软件验证，以及待执行的真实可见桌面验收。修复交付 SHA 由本次 PR 交付评论提供；执行者必须使用评论中的完整 40 位 SHA，不能把原问题基线或移动中的分支 HEAD 当成交付版本。

## 1. 问题与证据边界

原问题基线为 `15dc5d3d907c630aaa7c714aa2b4035fb770a7b4`。用户提供的 Linux 可见桌面、真实软件 Runtime、仓库模拟相机场景中，第一次正常 graceful Stop 后，Dispatch root `a33af144-a353-4104-b0e5-88fee274cbe2` 为 `FAILED / E_DEVICE_OUTCOME_UNKNOWN`；第二次相同入口停止为 `ABORTED`。

首次原始堆栈落在模拟相机 `capture` 的设备 IPC 回复读取：`DeviceProxy._call → receiveBytes → _readExact(handle, 4) → os.read` 抛出 `ConnectionResetError(104)`，随后包装成 `E_DEVICE_OUTCOME_UNKNOWN`。这定位了 worker 等待 DeviceBridge 回复时的传输中断，不能单凭堆栈断言当时驱动已经执行、没有执行，或已经返回明确取消。

后续软件诊断分别复现了两个可达机制：请求已写入而 Bridge 尚未读取时，Job 取消关闭了带有未读请求的通道；驱动已有明确取消结果时，共用的取消条件又会阻止回执送达。原 `DeviceExecutor.wait` 还可能在驱动实际退出前立即返回未知结果。worker 因而无法区分“已明确取消”与“真实设备结果未知”。

完整 Runtime 对照在原 head 和 base `7919f9a5b5335f55f6eeb0fb1e77109653e4f32c` 均复现采集中停止的问题；列出的相关停止源码在两版相同。因此这次修复针对既有设备停止链，不把它归因于 PR #11 的页面预览改动。

原证据中 `timestampMs` 保留事件发出时间，SQLite 文本 `timestamp` 是追加路径另取的时间，`sequence` 是同 Job 的持久化次序。Dispatch source 的取消标志可以早于 root 的 `job.stopping` 事件设置，Web stop audit 也在控制结果返回后才写入。不能混用这些字段推导“设备先失败、用户后点击”。原包缺少首次失败的完整 request/Bridge trace，无法恢复那一次运行的全部字节级交错。

## 2. 三个生产文件的局部改动

### `src/emo_master/apps/runtime/devices/executor.py`

`DeviceExecutor.wait` 增加可选 `cancelReceiptTimeout`，默认 `0.0`，保留其他调用方的原行为。调用方选择等待明确回执时，取消请求先传给原 operation，再有限等待**同一个真实 future**；future 完成后返回其原成功结果或原异常。超时仍保留未知结果，原生操作、设备串行队列和资源所有权不会因此被提前释放，也不补执行被取消的操作。

### `src/emo_master/apps/runtime/devices/manager.py`

`DeviceManager.execute` 仅透传上述可选参数到原 executor 等待入口。Job 的 DeviceBridge 入口选择 **1 秒**收尾窗口，等待真实驱动给出结果；没有新增全局锁、第二套结果账本或后台补偿机制。

### `src/emo_master/apps/runtime/devices/ipc.py`

- **Bridge 分开处理业务取消与通道关闭。** Job Stop 阻止新业务动作准入，但允许读完已经发出的请求并发送一次明确回执。空闲通道在观察到 Job 取消后保留最多 **2 秒**的收尾窗口；显式通道关闭仍可中断 I/O。取消前未准入的请求返回 `E_DEVICE_CANCELLED / NOT_EXECUTED`，不交给驱动执行。
- **Proxy 为在途设备动作保留有限接收窗口。** 第一次在设备动作 I/O 中观察到 Job 取消后，最多保留 **3 秒**，由 1 秒真实驱动收尾预算和 2 秒 IPC 预算组成。无限采集等待也会在停止后有限退出。这些是不同层的预算，不表示每次停止必须等待满 1、2、3 秒，也不改变原请求已有的传输期限。
- **只有三个条件同时满足才转换为正常工作流取消：** 本 Job 的取消标志已置位；设备错误码是 `E_DEVICE_CANCELLED`；设备结果明确为 `NOT_EXECUTED` 或 `CANCELLED`。转换为 `CancellationRequested / E_CANCELLED` 时保留原 outcome 和 diagnostics，worker 按现有路径得到 `ABORTED`。
- **未知结果和已完成动作保持原语义。** 真实断连、缺失回执、驱动仍未退出或其他故障不因用户点击 Stop 就伪装成成功取消；`UNKNOWN` 保留。动作已经完成且成功回执到达时，设备调用仍返回成功，不改写成“未执行”，也不重放请求。
- **增加传输未知的关联信息。** `E_DEVICE_OUTCOME_UNKNOWN` 的 diagnostics 带 `requestId`、`deviceId`、`cycleId`、`action`、`phase`（`send` / `receive`）和 `transportError`，便于下一次现场把错误与具体请求关联。这些字段不是额外重试权限。

## 3. 已完成的本地软件验证

环境为 Linux、CPython 3.10.22；遵循现有 Python 3.10 / PySide2 技术栈。测试生成工程使用 `schemaVersion: "2.9"`、`presentation.schemaVersion: "1.2"`，经模型校验、保存重读和 Runtime 工作流校验。

### 先红后绿的定向证据

| 验证层 | 原实现 | 当前修复 | 验证含义 |
| --- | --- | --- | --- |
| 真实 Runtime：`test_device_stop_receipts.py` 两个参数实例 | 2 failed，9.24 秒；原驱动明确取消，root 却为 `FAILED / E_DEVICE_OUTCOME_UNKNOWN` | 2 passed，14.47 秒 | 普通 Job 与 Dispatch 均通过正式 gRPC 启停；每例在同 Runtime、同驱动上重启后再产生两帧并正常停止；终态回执保留 `CANCELLED`，资源退场且不重放。 |
| IPC 组件定向组合，含“已取消 Proxy 不发送”回归 | 5 failed、2 passed | 7 passed | 覆盖未准入取消、明确取消回执、真实 peer loss、无限等待的有限退出，以及动作已完成时保留成功。 |
| `test_device_executor.py` | 本轮以新增边界断言验证 | 12 passed | 真实 future 的取消、完成、原异常均保留；收尾期限到达时不提前释放仍在执行的设备 owner。 |

真实 Runtime 测试只用原驱动的观测包裹和 `Event` 在原取消检查处同步停止边界，没有注入相机异常。它们是 gRPC / spawn 进程的软件集成测试，不是可见桌面验收或硬件验收。上述分组结果是红绿证据，不能与下表重复累加。

### 最终定向回归与静态检查

| 执行范围 | 实测结果 |
| --- | --- |
| 四个新增或修改文件：`test_device_executor.py`、`test_device_ipc_cancellation.py`、`test_device_ipc_retirement.py`、`test_device_stop_receipts.py` | **27 passed，22.38 秒** |
| 九个相关既有文件：`test_device_contract_closure.py`、`test_dispatch_queue_state.py`、`test_startup_cancellation.py`、`test_device_manager.py`、`test_device_lifecycle.py`、`test_device_feedback_closure.py`、`test_cancellable_device_wait.py`、`test_dispatch_queue_integration.py`、`test_device_network.py` | **109 passed，112.61 秒** |
| 两组互不重复的针对性用例 | **共 136 项通过**；不是全仓库测试总数。 |
| `ruff check src tests` | 通过。 |
| `mypy --config-file mypy.ini src` | 按仓库现有配置通过，检查 614 个文件；输出含已有 untyped notes。 |
| `scripts/gen_proto.py --check` | 通过；本次未修改协议或生成文件。 |

生产代码改动仅限上列三个文件。这里记录的是本地执行结果，不声称远端 CI 已通过，也不声称修复后的 Dot 桌面验证已经执行。后续 CI、Windows 和现场硬件结论应各自记录。

## 4. 可复制给 Dot 的真实可见桌面验收提示词

将下列提示中的 SHA 填成**本次 PR 交付评论给出的修复提交 SHA**后执行。不能填原问题基线 `15dc5d3...`，也不能自动采用 PR 后续变化的 HEAD。

```text
请在真实可见桌面上验收 emo_master PR #11 的设备正常停止回执修复。

固定修复 SHA：<粘贴本次交付评论中的完整 40 位 SHA>
仓库：https://github.com/jsdfhasuh/emo_master

执行边界：
1. 先读取仓库 AGENTS.md 和工程格式规范，拉取并在独立 worktree 或测试副本检出
   上面的固定 SHA，再核对 git rev-parse HEAD。保留原有未提交修改和现场目录，
   不用移动中的最新 HEAD 替代固定提交，也不合并或重置他人的工作区。
   记录 OS、Python/Qt、启动命令、Runtime instance、源码 SHA 和工作区状态。
   无法取得指定提交或环境确实不可用时才记录受阻，不拿另一版本的结果替代。
2. 只做验收和证据收集，不改产品源码、不提交修复、不合并、不发布部署。
   在隔离测试目录复制或生成测试工程，不覆盖现场工程。新工程必须是 2.9，
   presentation 1.2，按正式入口保存、重读、校验和写入 Runtime。
3. 使用仓库原模拟相机，不连接真实相机/PLC。通过可见 Designer/Web 正常 UI
   配置、启动、停止和预览；不靠 mock Runtime、修改取消逻辑、注入 IPC 错误或
   截图脚本伪造结果。可只读采集权威状态与日志，不直接改数据库或运行状态。

准备两个可追溯的最小工程：
A. Dispatch：原模拟相机 → 图像处理 → Dispatch → consumer 图像/count 输出。
   页面请求仅绑定 consumer；保留 root、consumer、runGroup 的真实关系。
B. 普通 Job：同一原模拟相机采集循环，没有 Dispatch/consumer。
两类都把相机曝光设为 exposureUs=200000（200000 微秒，即 200 毫秒），使用
设备声明允许的图像尺寸。记录其他相机参数、队列策略和实际工程文件。

核心验收——两类各至少 5 个有效“采集在途时停止”样本：
每类最多尝试 10 轮；若仍未收集到 5 个有证据的在途样本，按实际数量报告部分
覆盖，不无限重复，也不把未命中窗口的轮次补算为通过。
1. 正常启动后先观察至少两帧新图像/结果，确认本次 Job 正在正常采集。
2. 在采集进行中使用正常停止按钮，只发一次 graceful Stop。不要强杀进程。
3. 记录点击附近的 UTC 时间、root jobId、consumer jobId、runGroupId、cycleId。
   结合 camera node/device 事件及回执区分“采集在途”“尚未准入”“队列等待”
   “已经完成”。无法确认命中采集窗口的样本单列，不计入五个有效样本。
4. 等待权威终态与执行资源退场。期望明确正常取消为 root ABORTED/E_CANCELLED；
   在途驱动已明确取消时，日志应保留 CANCELLED。Stop accepted 提示不等于
   Job 已终止，必须截图权威状态、持有资源字段，并保存对应完整日志。
   不要求所有 consumer 都显示同一种终态：已完成的 consumer 可 COMPLETED；
   但不能残留运行进程、执行资源或把错误隐藏成正常取消。
5. 同一个 Runtime 不重启，等待执行资源退场后立即再正常 Start，证明新 Job
   至少产生两帧新图像/新结果，再进行下一轮。这个 Start 可作为下一轮的开始。
   每轮确认 jobId 已变化、没有复用上一轮结果、没有旧取消标志污染新任务。
   完成五个样本后也验证一次再次启动，最后正常停止并保留收尾证据。

保留以下相关回归，逐项说明执行/未执行/受阻：
6. 队列容量等待：采用合法、可在界面配置的慢消费者场景，queueCapacity=1、
   queueFullPolicy=wait。在队列界面确实看到 producer 等待容量后正常停止，
   确认 source 正常退出、排队项按取消处理、group 与 frame 资源最终释放。
   如果现有场景未能进入等待，不得仅凭参数配置就记为该项通过，也不要用
   源码 hook 制造一个“桌面通过”；保留工程并把未命中的阶段单列。
7. 连续启停：保持同 Runtime，在前一任务权威退场后立即再次启动，观察控制
   按钮、任务目录、旧/新 Job 选择和下一轮图像。核心五轮可同时提供此项证据。
8. 消费者预览：Dispatch 选择 root 时仍能显示本组 consumer 的持续更新结果；
   图像应实际变化，count 若工程定义恒为 1，不能把恒值当作冻结。停止与重新
   启动后不冒充旧结果，重新选择、刷新或重新进入预览仍关联正确的新 group。
   如果自然遇到 root capture_unavailable，保留原通知和画面；已显示的有效
   consumer 结果不应因此被误清空。未遇到该通知交错就标为未覆盖，不注入通知
   后声称自然桌面验收通过。兄弟 consumer 隔离仅在实际配置并运行后才可计入。
9. 日志导出：通过正常菜单导出这轮启动、采集、停止、终态和退场的完整范围。
   至少验证 Runtime JSONL，核对可解析、记录数、时间范围、目标 Job 和错误详情
   没被截掉；如执行 JSON/TXT、覆盖/取消等操作，分别记录结果，不把一种格式
   的成功扩展为所有格式和原生保存框都通过。

异常证据要求：
10. 如果出现 FAILED、UNKNOWN、资源不退场、再次启动失败、旧图像冒充新结果，
    先保留第一个失败窗口、原工程副本和完整原始日志，再做下一次尝试。
    不用重启、第二次成功、删日志或把 UNKNOWN 改标 ABORTED 来覆盖首个失败。
    已发停止且资源尚未退场时，不继续启动新的业务任务；把当前状态原样记录。
11. 若有 E_DEVICE_OUTCOME_UNKNOWN，保留原始异常链和 diagnostics 中 requestId、
    deviceId、cycleId、action、phase、transportError。区分设备 requestId 与 Web
    Stop 命令 requestId，不把二者混为一谈。若字段缺失也如实记录，不补造值。
    关联 root/consumer 的 node/device/job/dispatch 事件以及 Runtime stdout/stderr，
    不仅保存终态截图或最后十几秒的单 root 导出。
12. 同时保留 timestampMs、timestamp、sequence 和审计时间；不要用混合时间列
    宣称请求顺序。图片证据应标实际拍摄内容，文件名不能代替画面本身的证明。

交付：
13. 给出每类至少五行的逐轮结果表：固定 SHA、Runtime instance、root/consumer/
    group 身份、停止阶段证据、权威终态/code/outcome、资源是否退场、重启是否
    产生新帧、日志/工程/截图路径，以及首次失败位置（如有）。
14. 将“源码/单元或集成测试”“真实可见 Linux 桌面”“Windows 原生桌面/保存框”
    “真实相机/PLC”分开。没有 Windows 或硬件条件时明确未测，不能用本轮软件
    模拟结果替代。历史尚未实际执行的网络节流、兄弟 consumer、保存覆盖矩阵等
    仍各自保留未测边界；本次修复不会自动使这些历史项目变为通过。
15. 最终结论只覆盖实际执行的版本、平台和场景。可以报告本轮正常停止样本
    全部符合预期，但有限样本不等于证明不存在任何竞态，也不代表 PR 所有功能
    或远端 CI 已通过。
```

## 5. 交付后更新位置

负责人在 PR 交付评论中填写包含本修复的实际提交 SHA，并把后续 CI 和 Dot 桌面报告作为独立证据补充。此文档不包含下载 token、签名下载地址或整套原截图；历史现场证据与修复后的验证结果应继续分开保存。
