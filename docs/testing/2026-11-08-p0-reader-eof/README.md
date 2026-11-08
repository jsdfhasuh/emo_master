# P0 图片 RPC 收尾等待无关文件操作的修复

执行者：**ChatGPT Web**。日期：2026-11-08（北京时间）。
基线：`c897db8f48fda255a0853caa3ba538ddaf210502`。

## 当前失败与证据

[原 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/42387051487) 的实际 checkout 是 PR 合并候选 `10262f2ec087e744c773e2c62e9a767b9f7dba6f`。Windows 全仓只有 `tests/p0/test_continuous.py::test_long_lived_runner_to_two_network_image_consumers` 失败，其余 9266 项通过、5 项跳过，另 42 个子测试通过。Linux 全仓 9241 项通过、31 项跳过；两边 Runtime 恢复专项各 57 项通过。[同期 Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/42387051507) 全部成功。

失败的两个消费者均为 40 个测量样本中 39 个成功，覆盖率分别为 0.975；同一 ordinal 35 图片读取失败。两边异常时均记录已收到完整的 6,232,951 字节，却没有成功的流结束、哈希验证或解码结果，最终为 `DEADLINE_EXCEEDED`。RPC 期限为 500 ms，记录耗时分别为 506.9945 / 512.4123 ms。不能把“收齐字节”计作显示成功，也不能由现有时间戳推断最后一块实际到达的时刻。

下一张 ordinal 36 的 `Assets.adopt` 耗时 380.4177 ms；两个消费者的异常时间均落在该接管区间内。前后相邻接管耗时约 1.6–4.8 ms。原报告没有逐段记录获取锁、unlink、replace 和最后一块/EOS 的时间，因此无法把这次历史等待唯一归因于某个系统调用、磁盘、杀毒软件或调度器。

本次确认并修复的是：**现有代码存在能产生该症状的共享锁依赖，而且已经用确定的并发反例证明。** 历史时序与这个机制一致；并不把未采集到的 Windows 系统原因写成已证实事实。

## 可复现的因果链

1. `Assets.adopt` 持 `Assets.lock` 做旧文件淘汰和新文件 `Path.replace`。
2. 已有 reader 发完最后一批字节后，在 `_read` 的 `finally` 中也要取得 `Assets.lock`，才能减少 reader 计数并释放读缓冲 token。
3. `IsolatedServer._stream` 要等下一次 iterator 调用正常结束，才能结束 gRPC 流。字节发完并不等于已经取得 EOF。
4. 因此，无关新图片的文件操作可以阻塞旧图片的 EOF，使已收齐字节的消费者越过原 500 ms RPC 期限。

新增测试只使用一个受控交错：以 Event 卡住无关新图片的真实 `Path.replace`，并在旧图片最后一批已交给读取方之后触发接管。没有修改网络期限、读取实现或成功判定。

- 直接 reader 反例：最后一次 yield 期间仍有 reader/token；接管受阻时，要求旧 reader 可以独立完成 EOF 和资源归还。
- 真实双消费者 gRPC 反例：两个消费者都收齐原始字节后，必须在原 500 ms 期限内取得正常 EOF，且此时文件接管仍被门闩暂停。
- 未修改的 c897 上，两条均失败；第二条精确出现“两边完整字节 + 两边 DEADLINE_EXCEEDED”。修复后两条均通过。
- 门闩只控制顺序，最终仍调用原文件操作；finally 放开门闩、收束 worker/RPC。既有 fixture 检查所有 memory/staging/cache/lease 和 token 归零。

## 最小改动与保护条件

只改 `prototypes/runtime_pages_p0/resources.py` 的读取所有权同步，复用已有 `Resources.lock`：

- 读取准入继续持 `Assets.lock` 校验资产和 Job，再在短资源锁内预留缓冲 token 并增加 reader 数。
- 文件句柄退出后，只在资源锁内同时减少 reader 数和释放 token，不再等待资产文件操作。
- 淘汰和删除仍持资产锁，短取资源锁检查 reader；释放资源锁后才操作文件。新 reader 因准入锁无法插入删除窗口。
- 最后一批数据悬挂于 RPC 传输期间，reader 与完整缓冲额度仍然保留；活跃 reader、lease、latest 的保护和实际删除后才归还资产配额的顺序均不变。
- 锁顺序为 `Assets.lock → Resources.lock`；收尾只取 `Resources.lock`。复核 P0 没有反向持锁路径；资源锁内不进行文件 I/O。

没有增加锁、第二本资源账或后台补偿；没有改动 500 ms RPC / 550 ms 客户端总预算、100% 覆盖率断言、图片校验、文件接管事务或 CI 工作流。

## 本地验证与适用范围

Python 3.10.22，Linux，`QT_QPA_PLATFORM=offscreen`：

- 新直接反例：修复前 **2 失败**，修复后 **2 通过**。
- 完整 `tests/p0`：**72 通过、0 跳过**，包含原连续运行失败项，以及最后一批、deadline/cancel、lease/history/quota 等既有检查。
- 修改文件 Ruff 与 whitespace 检查通过。测试和资源锁设计经独立复核。

精确命令、摘要、文件哈希见 `validation.json`。本提交的新远端结果以 PR 中的 **ChatGPT Web** CI 收尾评论为准，不能借用旧提交成功结果。

当前失败使用真实 WorkflowRunner 和图像算子，但 display/asset server 与消费者属于 P0 原型链路。此修复不修改正式 Web/Runtime/Dispatch 产品路径，不据此宣称历史 Error 9、Windows 首次空图的唯一原因已查明，也不要求 Dot 重做已经通过的全流程。c897 可见退场复验的成功范围保留；缺少逐次网络响应正文的取证限制单独记录，不再当作新软件故障。

没有合并 PR、发布 Release、部署现场或进行新的人工/真实硬件验收。
