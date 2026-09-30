# P2 显式结果通道

本通道属于正式模块 `apps/runtime/presentation`。R3 同任务接入见下节；原 P2 debug/release 通道仍保留。
`PresentationService` 复用已有 Runtime、JobManager、Supervisor 和 spawn Worker，
`prepare` 只生成稳定副本和编译记录；只有 `start(preparedId)` 创建任务。
所有客户端读取操作均不得调用 StartJob 或 StopJob。

## R3：原 StartJob 可选绑定采集（2026-09-30）

这是正常运行入口的增量接点，不是把 debug Prepare/Start 改名。原 RuntimeService.StartJob
新增 `capture_presentation`（默认 false）、`start_request_id`、`expected_runtime_instance_id`；
旧字段号不变。没有请求采集的旧请求保持原执行路径，2.0/2.1 项目不自动升级。
默认服务器复用有分类额度的 AioRuntimeServer 同时提供原 RuntimeService 与 DisplayService；
嵌入式 Designer 只在明确开始操作中建立所需通道，观看不创建 Runtime 或 Job。

正常采集使用 `freezeNormalCapture` 校验当前已安装可信 registry 的实际端口与绑定，冻结只被
控件引用的 source ID、完整来源/作用域定义和修订。节点参数、文件输出路径、原 Job 快照、
`runtimeDbPath` 和生产计数保持原 StartJob 语义；不复制资源到 debug，不初始化隔离 DB，
不重写资源/站点参数。已声明资源在启动时校验根目录、大小和摘要；设备执行仍属于用户明确
启动原工程的原有权限/互斥，不因页面采集获得额外许可。执行身份 `mode=runtime` 明确区别
`debug` 和 `release`。executionRevision 描述本次正常流程与已安装插件，capturePlanRevision
仍由共享 captureDefinition 计算；它不承诺普通文件运行路径拥有 debug 的资源副本隔离。

本批准入限制仍是同一调用作用域、一个不同图像来源、最多16个source ID；不支持来源逐项
拒绝（跨 scope、global_counter/runtime_status 平台来源仍拒绝；计数算子的类型化输出可用）。
使用注册端口元数据，不复用测试交付四算子白名单。两页引用相同来源只编码一次；不同source ID
指向完全相同图像地址时也共享一次冻结/编码与结果自有资产。单图8 MiB和既有额度/期限不变。

Capabilities 追加 `normal_start_capture`、`project_jobs`、`source_coverage`、`start_request_lookup`。
`DisplayEmpty.project_id` 用于 ListJobs 的精确项目 UUID 过滤；带项目过滤返回当前 Runtime
会话内该工程的普通和展示 Job（包括未采集任务），空过滤仍只返回已有展示 Job，兼容旧客户端。
DisplayJob 追加 project/workflow/mode、capture_enabled、source_ids、sources_json、
capture_definition_json、两类revision、runtime_instance_id、start_request_id、accepted_at_ms
和resources_released。元数据只读、没有隐式采集或启动；未采集与已释放来源不能补数据。
P5 TestReleaseChannel 不声明 normal_start_capture 并明确拒绝原StartJob附加采集，保留测试
宿主经准备记录明确单次启动的边界；新增普通入口不借测试宿主绕过其保护。
关闭观察者/重连继续只断开或恢复已有Job的会话。

同次明确开始生成一个有界请求ID，并先协商Runtime代际。带请求ID的Start必须匹配代际；
同ID同参数返回原Job，改参数复用ID拒绝。`GetStartRequest(StartRequestLookup)` 只查询原
请求，返回原job_id及当前状态；已知未创建任务的拒绝为REJECTED，启动失败保留FAILED Job。
缺失请求为UNKNOWN、代际变化为RESET_REQUIRED，均不能解释为“未执行”而自动重发Start。
记录最多1024个请求摘要/答复，达到额度拒绝新ID，不淘汰旧ID以免延迟重试造成重复执行。
重启会更换代际；旧代际请求不可在新Runtime重放。此机制不使跨重启未知结果自动变为已确认。

再次运行由下一次明确开始驱动：先停止/确认旧Job终态，显式ReleaseJob且确认Worker、IPC、
待封闭结果与导出槽真实结束，再创建新Job。终态标签不代替进程退出；无法kill的Worker仍
保留占用、阻止释放与并发额度复用。普通ReleaseJob只清展示状态，保留原普通工作目录和输出。
Runtime.close 在监督器真正结束后关闭PresentationService；失败不冒充资源已释放。
DiscardPrepared 只在关联Job释放后删除准备副本及该debug快照自有SQLite/output树，保留release
持久命名空间。沿用的旧中间图快照仍执行，没有宣称重复编码成本或历史性能失败已经解决。

## 首批契约

- `ClosedSource.reasonCode` 兼容新增；旧记录可只有 reason。新采集器始终输出稳定代码。
  OPTIONAL_ABSENT、BRANCH_SKIPPED、NODE_FAILED、EXECUTION_CANCELLED、EXPORT_TIMEOUT、
  EXPORT_FAILED、RESOURCE_EXPIRED、BUDGET_EXCEEDED、INVALID_VALUE、SOURCE_MISSING、IPC_ERROR。
- 执行 COMPLETED 且所有来源 AVAILABLE 才 COMPLETE；可选未输出也记 INCOMPLETE，
  不丢清单项。执行 FAILED/CANCELLED 优先；IPC 损坏无法确认执行终态时为
  INCOMPLETE + UNKNOWN，不谎报检测 Job 已停止。以上状态均不是产品 OK/NG。
- 非有限指数、字面量、超 256 KiB 来源、4096 元素、12 层、16384 值节点拒绝，
  0/false/空集合/null 保留。每结果值 1 MiB。
- callPath 包含 subflow / loop_body / loop_condition；每个显式 scope 的每次调用独立封闭。
  本轮只接收本调用作用域的 node_output / workflow_output。跨作用域混合来源和
  global_counter/runtime_status 绑定明确拒绝准备，不能静默读最后值；实际计数节点输出支持。
- 检测开始分配业务序号，分别维护 latestStarted 与 latestClosed 水位。
  latest 表示最新已封闭结果；待执行/导出的下一件不抹去最新已确认结果。
  按 ordinal 取所有已封闭结果中的最大值，旧封闭不能覆盖新的 COMPLETE/INCOMPLETE/FAILED。
  客户端也按已收到封闭结果的水位栅栏迟到解码；开始水位单独用于表示进行中的检测。
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
进入独立展示 IPC，标量在路由前序列化冻结。旧 PreviewSnapshotWriter 仍执行。

正式 IPC 使用单写/单读共享内存邮箱：每 Job 16 个固定槽，每槽 1 MiB+64 KiB，
状态字发布写入完成、连续包序号和 CRC 复核内容。Worker 在写入中被强杀时，读取线程
不会卡在部分 Pipe/Queue 帧上；只轮询到 Supervisor 栅栏。满额立即拒绝，捕获异常只计
展示诊断，不改变检测执行。原生命周期事件/心跳队列保持原语义。

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
每 Job 标量 OPEN/IPC/快照/历史/重放保守预留 64 MiB（包含固定邮箱），两读线程预留 16 MiB；
两个 Job 合计预留 256 MiB，每 Job 64+48=112 MiB，不提高原 256/128 MiB 上限。
相比第二批的 32 MiB 元数据预留，第三批把新增独立邮箱和重放保留也计入既定额度；
没有通过提高预算使原性能测试通过。单一 Job 仅占一个导出槽，忙时拒绝；不是两个槽
对单个 Job 的性能保证。额外网络/旧预览/库基线开销仍需结合实测评估，不能把此账本当整进程上界证明。
`resourceStats()` 同时报保留对象数量、字节及拒绝数量。固定预留是审计模型，不能代替
Python/Qt/OpenCV 整进程 RSS、句柄和长期稳态实测；此项仍阻塞稳定性验收。

第三批增加限量弱引用 sidecar：锁定版本 ImageLoader 1.1.0 每次输出产生独立帧身份，
Blob 1.2.0 的 mask/overlay 可继承真实输入帧的坐标空间并记录父帧。其他算子、失效引用、
超出 16 个 sidecar、版本不符均 unknown；不按同尺寸、文件名或 sourceId 猜 lineage。
本轮显示算子自身 overlay，不实现独立几何叠加渲染。Qt 组合崩溃仍独立跟踪。

## 网络与只读客户端

显式 `grpc_server.aio_entry.AioRuntimeServer` 同时注册原 RuntimeService 与增量 typed
DisplayService。公共接口：Capabilities、Prepare、Start、ListJobs、Snapshot（可重放）、
Subscribe、ReadAsset、AcquireLease、ReleaseLease、ReleaseJob、DiscardPrepared。
Display Start 创建隔离/交付准备任务，R3 原 Runtime StartJob 也可附带采集；ReleaseJob 仅释放已终止且 IPC/导出已收尾的展示状态，
不是 StopJob；DiscardPrepared 要求关联 Job 已释放。默认 Runtime 入口在R3复用此有界适配；旧RPC签名保留。
同一 Runtime 只能有一个展示所有者；关闭恢复原回调。显式 ReleaseJob 也清理该 Job
拥有的旧预览工作目录和重放引用，有限租约所持资产继续存活；不删除正式输出或生产 DB。

分类额度：control=4、events=2、display=2、camera=1、asset=2、bulk=2，cleanup=13。
超过分类额度返回 RESOURCE_EXHAUSTED。旧上传保持 64 MiB 内容上限，另限制分块数和
framing 暂存；旧 RPC/客户端路径不变。所有同步构造、next、close、取消回调、上传临时
文件操作均离开 aio loop。额度持有到真实工作、close/回调和 gRPC 完成回调全部结束。
停服发现尚未结束的阻塞旧代码会明确报未收尾，保留额度，不用 shutdown(wait=False) 冒充回收。

`clients/runtime/display_session.DisplaySession(address, jobId)` 显式选择已有任务。
固定三个线程分别负责长期元数据订阅、500 ms 健康快照补取、单读图/解码工作单元；
元数据队列 8，已见结果 32，诊断/测量记录有限。断线可重连，RESET_REQUIRED 清除代际；
快照补取同时带有限增量和权威 latest，恢复最后一次载荷丢失。`selectJob` 不启动任务。
多观察者复用同一会话，`close` 只断开客户端，不关闭外部 Runtime。图像 live 缓存总计
16 MiB；超额明确 CLIENT_BUDGET，失效资源明确 RESOURCE_EXPIRED，不沿用上一件 OK。

## 演示和测量

仓库根目录：`python scripts/p2_demo.py`。需要项目 Python 3.10 环境；演示使用临时项目、
数据库和本地图像，自动清理，一个真实 spawn Job、两个真实 loopback 客户端，无设备。

`python scripts/p2_validate.py --suite measure --output <新目录> --timeout 360`：固定种子
20260926、1080p、5 Hz 调度、96 次/预热 8 次、三组交替配对；保留旧预览写出。
受控 Pace 仅用于测试调度，图像/Blob/Count 仍是现有真实算子。基线与启用组使用相同图和
工作流，基线只开限量计时、关闭采集。耗时采用同机 perf_counter_ns，性能回退按执行 P95；
模型提交从 scope 终态计时，丢失/未应用不删除分母。数据年龄保留直到 Job 终态的尾部。
原 P0 是不同原型算子图；这些是正式图的配对结果，不冒充同一二进制的历史直接对比。
短窗口的 RSS/句柄/CPU 原始曲线不等于长时间稳态或工控机/Qt/冻结包验收。
