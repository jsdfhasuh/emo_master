# 全仓 CI 的异步测试同步修正

执行者：dot（OpenAI AI 助手）。由 dot 调查、修改及推送，不代表其他开发者操作。
基线为 PR #11 的 `f308c87b620461af0b48a00948f9b916b4d399d1`，在独立云端副本处理；
不操作用户电脑、不合并、不发布 Release。固定旧 SHA 的可见验收独立保留。

## 原始失败与证据边界

[全仓 CI #445](https://github.com/jsdfhasuh/emo_master/actions/runs/42038140412)
实际检出的合成 merge 为 `43f287b31c38af5cedd1884a8ebfcf7879824474`。

- Linux：9154 passed、1 failed、31 skipped。`test_online_graph.py` 中相机已继续拍摄，
  直接读取 Job 仍为 `STARTING`，而测试期望 `RUNNING`。
- Windows：9179 passed、2 failed、5 skipped。捕获寿命测试用人为编码 gate
  等待主线程完成检查；该次首图的 500ms completion wait 到期，排队第二图也超时。
  原日志没有记录 gate 解除时间，不能仅据超时确认子进程是否仍在等该 gate。
  双 Job 资源测试中，首图在约一个时钟刻度内完成编码，但从 callback 开始到
  adoption 开始相隔 516ms；第二图尚未 dispatch 就已耗尽其 500ms 排队预算。
- 516ms 只能定位至回调入口和接纳之间。现有日志不能区分锁争用、调度暂停等因素，
  也没有确定具体锁持有者。本次不据此声称发现或修复了产品锁缺陷。

## 最小改动

1. 在线历史测试等待 Job 自身的 `RUNNING` 状态再断言，而非把设备帧数当作事件落库屏障。
   设备 IPC 与 worker 事件桥相互独立。原状态、连续生产、断连后更新及旧历史断言均保留。
2. 捕获寿命测试对真实 private-snapshot ACK 做显式同步，保留真实 spawn encoder 和像素检查。
   仅在测试主动阻塞编码、检查共享槽及第三次捕获拒绝时暂停该测试的 exporter 时钟；
   首次派发/快照和解除 gate 后的真实编码、排队仍消耗原有预算。
   owner 的 completion poll 也在测试检查完成后再开始，不让测试自身的 gate 冒充编码超时。
   生产实现及另行验证真实超时、队列超时和回收的测试不变。
3. 双 Job 资源测试在两个真实 worker 均完成启动、编译及资源准备后同时放行工作流。
   gate 位于真实 `runJobProcess` 内的 `WorkflowRunner.run` 前，心跳保持运行；finally 放行。
   新增两进程仍存活、两 Job 非终态且尚未捕获的检查，保留双 slab、256MiB 预算和完整结果，
   增加每 Job 两张图、共四次成功导出。捕获仍使用生产 500ms 截止时间，未串行化两个 Job。
   该屏障排除了“第一个正在捕获时第二个仍在启动”的交错；不据它的通过证明原 Windows
   516ms 延迟根因已查明，也不把此测试作为该启动交错下的延迟保证。

## 验证记录

- 原样 Linux 定向 capture 两文件：32 passed。此结果没有重现 Windows 的偶发延迟。
- 在线状态确定性反例：人为阻塞 `job.started` 消费，拍摄达到第 6 帧时仍为 STARTING；
  放行事件后才变为 RUNNING。在线历史与生产结果 4 项随后通过。
- gate 辅助反例：仅让测试的共享槽检查额外等待 600ms，旧夹具的两种 lane 数均出现
  completion_wait / queue_wait 的 EXPORT_TIMEOUT，新夹具两种均通过。
  旧夹具反例额外避免向已被杀死子进程拥有的 Event 发通知，只为安全回收，未改变超时判定。
  该反例证明测试自己的观察成本可以制造同类失败，不证明原 CI 的延迟恰为此来源。
- 最终组合定向回归：53 passed in 45.36s，包含三个原失败及原有真实超时/回收测试。
- Ruff 全 src/tests、protobuf 漂移检查通过；mypy 614 个源码文件通过。
- 一轮并发复验期间出现 4 failed / 49 passed；后续单路但资源仍繁忙时出现
  3 failed / 50 passed，都是启动/ready前超时或 Job 启动等待，未把它们记为通过。
  云端负载曾升至约 86；不能凭同时发生就断定因果。暂停后在低负载下串行复测，
  并仅对本地测试进程设置 OPENBLAS_NUM_THREADS、OMP_NUM_THREADS、MKL_NUM_THREADS、
  OPENCV_FOR_THREADS_NUM 为 1；没有修改系统设置、产品默认值或 CI 配置。

- 完整 presentation 加在线/生产范围：647 passed、2 failed（649 项，289.74s）。
  两项是双 Job 启动 ready 等待、已有网络分类测试等待 RUNNING，未再出现原图像导出断言失败。
  后续单独复测两项仍失败；新增诊断确认双 Job 连首个心跳都未发出便被
  E_HEARTBEAT_TIMEOUT 终止，网络测试则出现 0.2s RPC 超时。当时负载再次升至约 23。
  这些结果保留为本地验证限制，不能把完整 presentation 范围写成通过。

因此本次提交是等待独立 Linux/Windows CI 复验的测试同步修正，不是全仓已经通过的声明。
远端最终结果以本次提交对应 SHA 的检查为准，不以旧 SHA 的 Web CI 代替。
不把定向测试、软件模拟或辅助反例当作 Windows 原生人工界面、真实相机或 PLC 验收。
