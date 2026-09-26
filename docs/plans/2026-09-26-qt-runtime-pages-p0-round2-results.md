# Qt 运行页面 P0 第二轮执行记录

起点 `e19a9202e9d904cd07a6165ecc00d76b71639a7d`，分支 `agent/runtime-workflow-architecture-v1`，开始时 clean。保留第一轮报告与全部失败证据。本轮只做 P0，不启用生产入口或项目 2.2。

## 批次一：持续异步链路

`pipeline.py` 在一个窗口内保持 Results、Job 身份、业务序号、有限历史；只选择根流程 `load.image` 和 `source.items`。真实 WorkflowRunner 保留旧 PreviewSnapshotWriter 开销。检测只复制并非阻塞提交，两个固定 owner 线程管理原有两个 spawn 导出单元；待处理额度仍为零。导出成功/失败作为真实结果来源参加封闭，成功文件 rename 到独立缓存后才发布 assetId。

`resources.py` 用同一个令牌账本预留 Collector、快照、图像、共享内存、编码、读流、暂存、缓存和租约；物理缓存只计一次，pin 是受 64 MiB 限制的缓存子集。历史32，租约句柄16、最长30秒；实际 unlink/关闭读引用后才归还。客户端另有单张16 MiB传输+解码+8 MiB原图预算，不以服务端账本代替 RSS。

`continuous.py` 两个独立长驻 gRPC channel 经 loopback 按 ID 获取 PNG，分别校验结果身份、PNG摘要、解码后原始像素摘要和冻结 count=7。业务序号跨预热和测量连续。40次测量跨历史淘汰，窗口中不 close/重置账本；窗口结束才停订阅、停服务、回收导出并释放缓存。

第一批验证：`pytest tests/p0 -q`（36项），原型 Ruff；最终原始日志与后续测量统一放第二轮证据目录。当前结果仅证明正常短窗口和资产边界；RPC异常清理、两Job故障饱和及稳定性能由后续批次补齐，不能据此宣称 P0 通过。

## 批次二：网络兼容与异常所有权

真实旧 stub 的 UploadPreviewImage → StreamPreviewAsset → 解码比对通过；保留64 MiB累计上限、1 MiB传输消息上限，另限制1024分块和68 MiB带元数据暂存，超过返回明确错误。接收阶段在 bulk 池写临时 spool，完整接收后才调用原服务，不在 aio 循环做文件操作；部分上传取消不生成资产。原服务的上传解码/临时资产仍是旧路径，不能把展示账本当作它或整个 Runtime RSS 的限制。

构造、next、close 在所属分类池中；取消回调使用固定13线程上限的清理池（仅有准入中的 RPC 可提交），槽位覆盖回调、未完成工作及close全部寿命。close异常记录后归还已完成的工作；close阻塞仍占额度，控制请求保持可用。停服4秒后仍未结束则明确失败，保持loop/池/额度供监督处理，不伪称已回收。

外层监督实测：构造抛错/阻塞、首条前取消、next中取消、close抛错/延迟、序列化异常、回调阻塞、启动失败、停服超时后释放门闩并重试。另用两个真实 Runner Job共享同一Pipeline，重复hang/IPC故障、第三导出拒绝、INCOMPLETE封闭、恢复后两网络客户端解码、慢读取消保留资产引用、500ms读期限、运行中close和最终子进程归零。

`pytest tests/p0 -q`：38 passed（38.07s）；R09六排列/重复/换Job、R10真实spawn回收、R11临时debug隔离及C1—C4均重跑。正式长流若第三方next永久不响应取消，Python线程不能强杀，槽位不会复用；此情况被报告为不可回收并由测试外层进程树监督结束，不能当作正式设备驱动已验收。
