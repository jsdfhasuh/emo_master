# 验收标准

本文件定义本次能力验收范围，不代表任何源码实现或测试已经完成。

## A. 注册能力声明

| 验收项 | 预期行为 | 验证方法 |
|---|---|---|
| A1 旧 manifest | 缺少 runtimeCapabilities 时正常注册，能力为空 | 复用现有 manifest 注册用例 |
| A2 声明能力 | ImageSaver 的 manifest 声明 backgroundTasks，PluginManifest 保留该值 | Registry scan 后检查 descriptor.manifest |
| A3 未知能力 | 未支持值、重复值、非字符串值导致对应算子被拒绝并给出明确 issue | 参数化校验测试 |
| A4 注入隔离 | 声明算子收到 backgroundTasks；未声明算子没有该键 | 两个最小测试算子在 executeNode 中检查 context |
| A5 下游等待能力 | 需要 wait 的消费者必须声明 backgroundTasks；未声明消费者不能访问客户端 | 一个声明消费者和一个未声明消费者分别验证 |

## B. 后台任务基本行为

| 验收项 | 预期行为 | 验证方法 |
|---|---|---|
| B1 多算子共享 | 同一 Job 内不同已声明算子提交的任务进入同一 FIFO 服务 | 使用两个测试算子记录任务提交和执行顺序 |
| B2 Job 隔离 | 两个并发 Job 不共享任务对象、队列或线程 | 两个 spawn worker 使用不同任务 ID / 线程身份验证 |
| B3 非阻塞提交 | 队列有空位时 submit 返回任务 ID，不等待任务函数结束 | 用 Event 阻塞后台函数，确认 executeNode 可先返回 |
| B4 显式等待 | wait(taskId) 等待目标任务并返回其结果 | 先提交后由后续算子等待，验证结果和时序 |
| B5 失败可见 | 后台异常有 task.failed 事件；wait 能抛出原始错误 | 使用确定性失败函数 |
| B6 背压 | 队列满时提交方等待；释放一个队列位置后继续，取消时可退出 | 用 Event 控制单线程和有限队列容量 |
| B7 身份关联 | 完成/失败事件保留提交时 nodeId、nodeRunId 和 workflowRunId | 在算子返回后再释放后台任务，检查事件字段 |
| B8 记录有界 | 终态记录超过上限时只淘汰最早终态；运行中任务及 pending artifact/binding 引用不被淘汰；查询已过期 ID 返回明确错误 | 使用小型测试上限和确定性任务序列 |
| B9 字节背压 | 大图像提交受待处理字节预算限制；单项超限明确失败，不无限等待 | 使用可控图像字节权重验证额度释放和超限错误 |
| B10 禁止递归等待 | 后台任务函数不能再次 submit/wait 同一服务，不会单线程自锁 | 在测试任务中尝试递归调用，验证明确拒绝 |

## C. 生命周期与 Runner 完成处理

| 验收项 | 预期行为 | 验证方法 |
|---|---|---|
| C1 正常结束 | Job terminal event 晚于所有已接收任务完成和延迟产物登记 | 记录 task.completed、artifact.created 与 job.completed 顺序 |
| C2 未观察失败 | 未 wait 的任务失败不能被当作 Job 成功 | 后台任务抛错，验证 Job 以 E_BACKGROUND_TASK_FAILED 结束 |
| C3 取消 | 排队未启动任务取消，活动任务按现有停止宽限收尾 | 用可控 Event 验证协作停止；另验证超出 5000ms graceful stop 后由现有 Supervisor 终止 |
| C4 原错误优先 | 工作流已有错误时，后台收尾错误不覆盖主错误 | 同时制造确定性节点错误和后台错误，检查主错误及附加诊断 |
| C5 连续运行 | 多个 cycle 共用同一个 Job 服务，Job 停止时统一收尾 | 两个 cycle 提交任务，验证线程身份相同及终态顺序 |
| C6 预览 writer 顺序 | 后台任务完成和 artifact capture 发生在 AsyncPreviewSnapshotWriter 关闭之前 | 用可控任务记录 close/capture 顺序 |
| C7 失败及时发现 | 未 wait 的后台失败在下一个节点边界、下一次 workflow run 开始或 workflow 完成前被发现，不等待连续 Job 终止 | 连续 Job 中注入失败任务，验证下一轮开始前 Job 已失败 |

## D. ImageSaver、产物与 SQLite 文件引用

| 验收项 | 预期行为 | 验证方法 |
|---|---|---|
| D1 同步兼容 | 默认 saveMode=sync，既有 saved/path 结果与覆盖规则不变 | 复用 ImageSaver 现有测试和原路径断言 |
| D2 后台提交 | saveMode=background 立即返回 accepted、SUBMITTED、taskId，不能返回 saved=true | 阻塞写入函数后检查算子返回 |
| D3 数据快照 | 上游在提交后修改原 ndarray，不影响落盘内容 | 提交后修改源数组，对比文件解码结果 |
| D4 最终保存 | 任务完成后文件内容可正常解码，wait 返回成功结果 | 临时目录写入 PNG/JPEG 并重新读取 |
| D5 产物延迟登记 | 文件写入成功前不发布 artifact.created；成功后沿用原 ArtifactStore/预览链路 | 观察任务事件、registerPath 与 artifact.created 的先后关系 |
| D6 失败不造假 | 写入失败不发布 artifact.created；未观察失败使 Job 失败 | 以可控写入函数抛出 OSError 验证 |
| D7 SQLite 文件引用等待 | pending ImageSaver 输出绑定 FILE_REFERENCE 时，Runner 在 SQLiteWriter 执行前等待，成功后再形成正常 BoundValue | 用阻塞任务确认中间节点可先运行，SQLite 节点直到文件成功后才启动 |
| D8 SQLite 文件引用失败 | 保存任务失败时不生成有效 FILE_REFERENCE，不执行数据库 insert，并按现有 SQLite failurePolicy 产生失败 receipt | 注入确定性保存异常并检查 insert 未调用 |
| D9 普通消费者等待 | 普通 JSON 输出可携带 taskId；声明能力的下游 wait 后读取文件成功，未声明能力的算子不能访问服务 | 两个下游算子对同一输出分别验证 |
| D10 异步唯一命名 | 后台模式使用 namingMode=unique；fixed 模式返回明确参数错误；同步 fixed 模式不变 | 对比 background/fixed、background/unique、sync/fixed 三条路径 |
| D11 原子文件发布 | 最终路径只在完整编码/写入后出现；写入异常或取消不留下半成品正式文件 | 在临时写入中途注入异常/终止，检查最终路径不存在、临时文件可清理 |
| D12 同名并发安全 | 多个 Job/周期异步保存时不会把一个任务的产物登记成另一个任务的内容 | 并发保存不同图像并校验每个 artifact 的 SHA-256 与对应图像 |

## E. 执行模式边界

- 正式 Job 只向已声明 backgroundTasks 的算子注入客户端。
- 单算子调试和工作流调试只接受由 Runtime trustedEntries() 从已校验 manifest 生成的能力声明，并在结果冻结/最终快照前按计划 drain。
- PurePreviewExecutor 不注入可提交的服务；previewMode=pure 不允许延迟文件副作用。
- clone-safe trial 不继承正式工作流服务。
- 未声明能力的直接 OperatorExecutor 调用不影响现有同步算子。

## F. 范围与证据边界

本次不要求真实相机/PLC、现场节拍、磁盘极限吞吐或全平台性能专项。可以记录一组本机同步/后台保存耗时作为参考，但不能把单机微基准当成生产性能保证。

必须区分：单元测试、spawn Job 集成测试、调试模式测试、人工 Designer 验收和真实设备验收。未执行项明确标记，不以模拟任务代替真实设备结论。
