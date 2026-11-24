# 实施任务

实施与测试证据见[交付记录](delivery.md)。

## 1. 注册与注入

- [x] 1.1 在 PluginManifest 增加末尾可选字段 runtimeCapabilities，默认空 tuple；validator 校验 backgroundTasks 声明。
- [x] 1.2 通过现有 PluginDescriptor.manifest 保留声明；旧 manifest 兼容；不改 OperatorMeta、算子基类和 executeNode 签名。
- [x] 1.3 ImageSaver manifest 声明 backgroundTasks；OperatorExecutor 只向已声明算子注入客户端。
- [x] 1.4 调试能力由 Runtime trustedEntries() 传递；PurePreview 和 clone-safe trial 不开放。

## 2. 后台任务服务

- [x] 2.1 新增 Job-scoped BackgroundTaskService、节点级客户端和任务引用；一条 FIFO worker。
- [x] 2.2 实现 submit、wait、status、任务状态/结果、来源身份和任务完成/失败记录。
- [x] 2.3 实现任务数量/字节双重限额、队列背压、单任务超限错误及有限任务记录保留；pending artifact/binding 依赖期间固定保留记录。
- [x] 2.4 实现 TaskControl 取消检查和最终发布门禁；实现 drain、取消排队任务、内部 30 秒 drain 上限及幂等 close。

## 3. Runtime / Runner 接入

- [x] 3.1 Job worker 创建服务并沿 WorkflowRunner → OperatorExecutor → runtimeContext 注入；同一 Job 的循环/子流程/连续周期共用。
- [x] 3.2 Runner 在节点边界、workflow run 开始/完成前处理任务完成记录、失败和 pending artifact；ArtifactStore、ResultCollector、预览对象只由 Runtime 执行线程调用。
- [x] 3.3 连续运行间隔和 Dispatch consumer IDLE 等待定期调用 Runner poll。
- [x] 3.4 单算子调试、工作流调试传递已验证能力声明并在输出冻结/最终快照前 drain；保持 PurePreview 和 clone-safe trial 隔离。
- [x] 3.5 Job terminal 前先关闭提交入口并 drain、处理剩余产物，再关闭 AsyncPreviewSnapshotWriter，最后发布 terminal event；保留现有 5000ms Stop 规则。

## 4. ImageSaver 与现有文件链路

- [x] 4.1 增加 saveMode=sync/background，默认 sync；后台模式仅允许 namingMode=unique，fixed 明确报错；同步模式不变。
- [x] 4.2 后台模式复制图像、提交带 estimatedBytes 的任务，写入任务专属临时文件并通过 TaskControl 原子发布最终文件；返回 SUBMITTED/taskId，不返回 saved=true。
- [x] 4.3 Runner 延迟登记 pending artifact；成功后 registerPath、SHA-256、artifact.created 和预览捕获，失败不登记。
- [x] 4.4 Runner 延迟 SQLite FILE_REFERENCE 绑定：SQLiteWriter 执行前 wait，成功后完成 artifact 处理并调用现有 captureBoundValue/toStorage；失败交给现有 failurePolicy。
- [x] 4.5 更新算子开发指南，说明 manifest 能力声明、提交/等待、任务函数边界和图像数据所有权。

## 5. 验证与交付

- [x] 5.1 运行 manifest 注册与注入测试：旧 manifest、有效声明、非法声明、未声明算子不可访问。
- [x] 5.2 运行后台服务测试：非阻塞提交/FIFO/wait/失败/背压/取消/任务记录保留/来源身份；使用 Event 控制时序，不新增长时间 sleep 或压力测试。
- [x] 5.3 运行 Job 集成测试：多算子共享、不同 Job 隔离、连续/Dispatch 空闲期间失败发现、Job drain 与 terminal 顺序。
- [x] 5.4 运行 ImageSaver 和 Runner 集成测试：同步回归、后台 unique 命名、源图快照、临时文件原子发布、artifact 延迟登记、SQLite FILE_REFERENCE 成功/失败。
- [x] 5.5 覆盖调试模式能力传递和 PurePreview/clone-safe trial 隔离。
- [x] 5.6 运行受影响的现有回归及 OpenSpec strict validation；记录固定源码 SHA、实际结果和未执行项。不新增全算子遍历、现场设备或极限性能测试。
- [x] 5.7 更新计划状态和交付记录；推送时附实际执行角色的 ChatGPT Web / Codex / dot 评论。

## 6. 独立审查后的修正

- [x] 6.1 配置排空覆盖连续 Job 已受理的后台保存，并验证真实配置切换后目标文件和 Job 收尾状态。
- [x] 6.2 在 Dispatch 输入和消费者返回结果跨进程前解析文件回执，并验证双 Job 的保存、转发和 Join。
- [x] 6.3 在确认 Dispatch 投递前处理该次后台任务和失败，覆盖相邻两次投递的错误归属。
- [x] 6.4 将迟到产物关联到 Designer 保留的对应执行，复验节点面板的“保存结果图”和旧参数表单。
- [x] 6.5 While 迭代间隔轮询后台失败，验证保存失败、循环超时和主动停止。
- [x] 6.6 普通失败时为活动文件任务提供有限清理时间；ImageSaver 在复制超限图像前报错。
- [x] 6.7 复跑受影响回归及 OpenSpec strict 校验，记录修正后的验证范围和剩余边界。详见[修正后的工作区回归](delivery.md#修正后的工作区回归)。

## 7. 后台保存的目标冲突与强制退出

- [x] 7.1 原子创建回执指定的最终文件。验证其他写方抢先占用时文件内容保持完整，原任务报告 `E_OUTPUT_EXISTS`。
- [x] 7.2 在正式 Job 和调试子进程退休时，回收该进程持有的临时图片。验证强制停止和普通失败清理超时，保留已发布图片、其他写方文件和原始 Job 错误。
- [x] 7.3 在 Windows 和 Linux 复跑受影响用例，更新交付记录并执行 OpenSpec strict 校验。[修正和验证记录](delivery.md#目标冲突与强制退出)

## 8. Runtime 重启后清理暂存图片

- [x] 8.1 在首次写图前持久登记每个输出目录的私有暂存位置，涵盖正式 Job、单算子调试和工作流调试。
- [x] 8.2 主 Runtime 在启动时按照登记和进程锁清理，保留已发布图片和其他写方文件。旧 Job 有精确登记且原进程已退出时兼容回收。
- [x] 8.3 验证父子进程异常退出、旧写入进程仍活跃、删除受阻后再次启动和重复启动。记录 Windows、Linux 源码回归及断电验证边界。[完整验证记录](delivery.md#下次启动时清理暂存图片)

## 9. 配置排空与页面测试发布收尾

- [x] 9.1 让配置排空等待已受理图片的产物登记完成，覆盖连续 Job 等待下个周期的情形。验证登记完成后才停止旧 Job，显式 Stop 继续使用原有宽限。
- [x] 9.2 为 2.2 页面测试发布入口传入所属 Runtime 的暂存登记库。验证同一数据目录重启后能清理中断的图片并保留旁边的文件，并覆盖 Windows 私有暂存路径超过 260 字符时的正常发布。
- [x] 9.3 复跑两项反例及受影响回归，记录 Windows、Linux 和干净 HEAD 的对照结果。两项反例已关闭，Linux 静态页面资源替换用例的既有超时单独保留。[收尾验证](delivery.md#配置排空与页面测试发布收尾)
