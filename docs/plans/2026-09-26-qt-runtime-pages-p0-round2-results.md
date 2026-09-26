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

## 批次三：测量口径与基线修复

测量使用3轮、每组预热8件+测量96件；五组为原功能、无presentation、采集零客户端、一个客户端、两个客户端。每轮轮换起始组，按本轮原功能P95配对计算回退。绝对5Hz输入不等待导出，保留Runner串行语义；scopeEnd仍来自Runner实际workflow终态。模型P95只统计实际完整解码，同时以全部96件为覆盖率分母；任何缺失/INCOMPLETE都使正确性和性能门槛失败，不能用有效样本P95掩盖缺失。

单任务导出/读图/封闭500ms、回收1s全部不变；外层benchmark watchdog根据固定窗口长度计算，默认432s，其他故障场景90s。证据同时记录执行、正确性、性能、兼容性；写入起止HEAD/status、diff SHA256及受测源码SHA256，禁止覆盖已有证据。

私有预览基线修复独立提交 `323f35d`：原测试长路径下临时PNG文件超过Windows传统路径限制，事件记录preview.snapshot.failed；私有存储使用扩展I/O路径。同时屏障复现GetJobStatus在终态资产promotion前返回COMPLETED，改为promotion完成后发布状态，回调异常仍发布终态。旧测试断言不变，新增长路径和回调屏障/异常回归。Runtime/core/e2e：356 passed、1 skipped（既有无符号链接权限）。完整CI的proto/Ruff/mypy通过，pytest仍原生访问冲突3221225477。

Qt基线隔离：全部Designer在workflow-tabs import用例的processEvents路径崩溃；workflow-tabs单文件10 passed，main-window组34 passed；前置Designer文件拆成两半分别加workflow-tabs，61/46 passed。说明是组合运行/生命周期相关，尚无足够证据定位到某个Qt对象；没有据此猜测性改动Qt生产代码或调整原断言。后续独立进程结果与原失败分列。

补充故障回归：在Designer分文件回归同时运行时，短窗口首次出现33/40与40/40不对称覆盖（`final-p0.txt`保留FAIL）。原客户端读图超时会结束订阅；修复为本件UNAVAILABLE/INCOMPLETE并继续接收，禁止旧OK继续占live。增加真实网络慢读超时后下一件恢复断言，保持500ms读/解码截止与完整率原断言不变。39项P0复验通过；新增3轮不重建AssetStore的120件缓存/租约周转（88次淘汰），以及上传发送中断、上传执行中取消、停服API异常重试。spawn时钟诊断历史也限制为64条，避免长期重复回收积累。

Designer逐文件独立进程验证49个文件、333 passed；原完整CI的组合原生崩溃仍未解决。这是补充回归，不是全量CI通过。性能首组三轮（`final-evidence.json`）完整保留；客户端故障分支修改后再做完整三轮，使用另一证据文件，不择优替换。
