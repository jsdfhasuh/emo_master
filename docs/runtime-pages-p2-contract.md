# P2 显式结果通道

本通道属于正式模块 `apps/runtime/presentation`，默认应用入口不创建它。
`PresentationService` 复用已有 Runtime、JobManager、Supervisor 和 spawn Worker，
`prepare` 只生成稳定副本和编译记录；只有 `start(preparedId)` 创建任务。
所有客户端读取操作均不得调用 StartJob 或 StopJob。

## 首批契约

- `ClosedSource.reasonCode` 兼容新增；旧记录可只有 reason。新采集器始终输出稳定代码。
  OPTIONAL_ABSENT、BRANCH_SKIPPED、NODE_FAILED、EXECUTION_CANCELLED、EXPORT_TIMEOUT、
  EXPORT_FAILED、RESOURCE_EXPIRED、BUDGET_EXCEEDED、INVALID_VALUE、SOURCE_MISSING、IPC_ERROR。
- 执行 COMPLETED 且所有来源 AVAILABLE 才 COMPLETE；可选未输出也记 INCOMPLETE，
  不丢清单项。执行 FAILED/CANCELLED 优先；以上状态均不是产品 OK/NG。
- 非有限指数、字面量、超 256 KiB 来源、4096 元素、12 层、16384 值节点拒绝，
  0/false/空集合/null 保留。每结果值 1 MiB。
- callPath 包含 subflow / loop_body / loop_condition；每个显式 scope 的每次调用独立封闭。
  本轮只接收本调用作用域的 node_output / workflow_output。跨作用域混合来源和
  global_counter/runtime_status 绑定明确拒绝准备，不能静默读最后值；实际计数节点输出支持。
- 检测开始分配业务序号。最新开始序号立即清除旧 latest；旧封闭不能覆盖新的指针。
  消息游标另行递增。保留至多 32 结果及 8 MiB 元数据；并非完整历史。
- 单 Runtime 至多 2 个展示 Job、8 个准备记录。每 Job 至多 8 个 OPEN；
  超额采集拒绝计入共享计数器；不会结束检测任务。终态 Job 需显式 release 释放保留状态。
- debug SQLite/outputs 使用 P1 的隔离命名空间，不迁移生产 SQLite，不创建第二个 Runtime。
- 新 protobuf DisplayService 与旧 RuntimeService 并存；不替换旧客户端与无页面项目。

## 图像与资源

两个固定 spawn 编码单元，无待处理队列；每个展示 Job 独占一个槽，允许两个 Job 并发。
这使 Worker 在复制中死亡且未发出描述符时仍能确定资源归属；同一 Job 忙时图像明确
BUDGET_EXCEEDED，不等待展示，不增加线程。单来源原图 8 MiB、结果 16 MiB 上限仍保留，
不承诺同一结果的多个图像同时可用。检测线程在槽位预留后 copyto 自有共享内存；仅小描述符
进入原事件通道，标量在路由前序列化冻结。旧 PreviewSnapshotWriter 仍执行。

任务提交后导出 500 ms；等待超时后 terminate/join/kill/join，最多 1 s 回收确认。
未证明退出则隔离该槽，不归还额度。新进程启动预热独立于运行任务期限，期间仍占槽。
封闭期限从 Worker 的 workflow 终态单调时钟起计 500 ms，不从客户端收到时重新计时。
缺图进入本次 INCOMPLETE；迟到导出清理孤儿，不改已经封闭的结果。Supervisor 为没有
workflow 终态的失败/强杀兜底。正常 Job 终态不等待导出线程、不提前抛弃封闭任务。

结果只引用已原子移入资源库的 ID、摘要和大小。按 ID 读图核验 Job 所有权、摘要和大小。
读图期限 500 ms，读取引用在真实读操作 finally 才释放；客户端取消不能提前回收。
历史淘汰后，租约/读引用继续保留资产。租约最多 16 个、30 s、64 MiB；cache 256 MiB，
两槽 staging 最多 16 MiB（既定上限 64 MiB），每图编码最多 8 MiB。

资源固定计账：共享内存容量 16 MiB、导出含临时副本 2×6×8=96 MiB，
每 Job 标量 OPEN/IPC/快照/历史保守预留 32 MiB，两读线程预留 16 MiB；
两个 Job 合计预留 192 MiB，小于 Runtime 256 MiB，每 Job 32+48=80 MiB 小于 128 MiB。
`resourceStats()` 同时报保留对象数量、字节及拒绝数量。固定预留是审计模型，不能代替
Python/Qt/OpenCV 整进程 RSS、句柄和长期稳态实测；此项仍阻塞稳定性验收。

每次冻结产生独立 frameIdentity / coordinateSpaceId；现阶段图像自身可显示，几何叠加
来源统一 unknown，不按同尺寸或文件路径推断逐帧 lineage。可信算子链适配尚未完成，
不能宣称 B09 的完整可信叠加已通过。原生 Qt 组合崩溃仍独立跟踪。
