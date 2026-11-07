# a051874 验收问题修复与复验边界

执行者：**ChatGPT Web**。日期：2026-11-07。

基线为 PR #11 的 `a051874d21a1872b5068091622933cdda4c95c2d`。本目录与修复一起提交，最终提交 SHA、远端 CI 状态由 PR 的 ChatGPT Web 评论记录。本次没有合并 PR、发布 Release、接触真实设备或修改现场工程。

## 1. 本轮解决什么

| 已确认原因 | 局部修复 | 对应回归 |
| --- | --- | --- |
| 发布 App 收到某个 Job 的 `capture_unavailable` 后全局取消图片候选，连消费者在途图片一起取消；Editor 对应入口已经隔离 | 发布 App 只失效目标 Job 的 scope、候选、图片请求和持有结果 | 实际 App 中 root 通知保留 consumer 在途图；consumer 自身不可用仍取消本图并拒绝迟到提交 |
| 共享字符串 notice 无 Job/view 归属，恢复新图或切换 Job 后仍保留旧采集提示 | 采集提示独立按 Job/view 保存；恢复只清对应 Job，切 view 清旧提示；控制回执独立保留 | 同 Job 恢复、切 Job、多 Job 提示各自恢复 |
| 查询和导出共用 error，成功重试未清旧错误，查询成功又可吞掉导出失败 | 查询/导出错误分别归属；新一次导出清自己的旧错误；取消和目标/实例/筛选切换退场旧请求 | 实际 RuntimeLogs 的失败重试、错误隔离、取消、三种目标切换 |
| 模拟相机只在曝光循环内部检查取消/期限，最后一次等待结束时可能跳过检查 | 每次醒来按取消 → deadline → 曝光完成的顺序检查，生成图及递增 sequence 前决定结果 | 最后一次等待中超时、取消，以及正常曝光对照 |

没有修改 2 秒 IPC 时限、5 秒设备/心跳预算、UNKNOWN 语义或取消不重放行为。

## 2. 直接反例与测试结果

三个直接反例集合合计 14 条：修复前 11 条失败、3 条正常对照通过；修复后 14 条通过。

- 发布 App：新增 5 条真实 React 组件检查，修复前 4 失败/1 通过，修复后 5 通过。
- RuntimeLogs：新增 6 条真实 React 组件检查，修复前 5 失败/1 通过，修复后 6 通过。
- 模拟相机：新增 3 条受控时钟检查，修复前 2 失败/1 通过，修复后 3 通过。调用未改造的生产方法，仅替换该驱动模块的时钟，不替换全局时间或执行器。
- 全前端 Node 测试：194 通过、0 跳过；TypeScript/Vite 构建及正式前端打包通过。
- `web_panel/tests/test_product_runtime.py`：5 通过，包括 3 项实际软件 Runtime 检查及 2 项控制结果核对辅助检查。
- 本地最终 Runtime/取消/设备/CI 配置组合回归的命令和精确计数见 [validation.json](validation.json)。
- 全源 Ruff、mypy 和生成协议一致性检查通过。mypy 原有 untyped-body 提示仍如实保留，未通过关闭检查解决。
- 现有 CI recovery 步增加模拟相机末次等待及连续 Dispatch 回归，后续全仓检查保持不变。

### 新增真实 Dispatch 组合回归

`tests/runtime/test_dispatch_continuous_recovery.py` 使用真实 RuntimeService、回环 gRPC 和 spawn worker：

1. 正式模型生成 2.9/1.2 工程，保存重读、加载和编译验证。
2. 模拟相机曝光 1 秒，root+一个 consumer 占满容量 2。
3. 每组读取至少 6 个不同、递增的消费者结果；逐条读取真实图片资产，核对 descriptor/响应/内容 SHA-256，并实际解码尺寸。
4. 明确 Stop 后要求 root 为 ABORTED/E_CANCELLED，执行资源、Dispatch 组、帧和配置周期退场；通过正式 ListWorkflows 确认准入恢复。
5. 在相同 Runtime 实例及相机驱动上启动新组，再读取至少 6 个结果。任意 Job FAILED 都使测试失败。

这是两组至少 12 个真实结果与图片读取的正常集成回归，不是长时间稳定性指标，也不代替可见浏览器或真实设备验收。示例 count 是常数 1，不能扩展为所有算法业务语义已验收。

本机 Chromium 下载返回 Site Unavailable HTML，未取得浏览器，未把该环境阻碍写成产品失败或本机浏览器通过。远端现有 Chromium/Windows Runtime 浏览器检查以本次 PR 提交的实际 CI 为准；仍不能冒充 Dot 人工桌面验收。

## 3. 本轮没有把尚未归因的延迟写成已修复

原始 a051874 证据包：

- 文件：`emo_master_PR11_a051874_visible_acceptance_20261106.zip`
- 大小：24,689,673 字节
- SHA-256：`f63f7904e2f597a86c38a18fe7fe10ba02910c2477d7ccc63dc487e6bdc8c4a8`

本轮原始样本为 9 个 Dispatch 组，其中 4 个失败，涉及 6 个 FAILED Job。普通和 Dispatch 各 5 个有效准入恢复样本不抵消这些失败。第 9 组先出图后失败，说明单张新图不能证明后续稳定。

已证明的具体失败机制：

- 第 1/5/9 组主任务采集在 5 秒预算下分别观察到 11.065/8.952/6.004 秒，是真实跨过 deadline。
- 第 1 组 driverReturned 距 frame_publish 观测仅 5ms，不能把整个约11秒都说成帧发布卡顿。
- 第 2 组主任务/consumer 从未发布首心跳；观察时距启动基线约 6.7/6.3 秒。不能把它混同于历史“先读旧心跳后被延迟”的错误。
- 第 9 组 consumer 的 UNKNOWN 来自内部 dispatchNext 的发送 TimeoutError，不能写成已证实的真实相机断网。
- 第 5 组采集失败早于正式 job.stopping；UI 点击、HTTP 完成和 Runtime 事件不是同一个执行时刻。
- 原始首次日志导出 409/DEADLINE_EXCEEDED 仍是真实超时。修复旧提示残留不能证明该 RPC 延迟已经消失。

### 新推进的排查证据

原始进程采样显示，组1约11秒窗口内 root、consumer、Web 的 PID 主线程分别累计等待运行队列约9.011、8.696、8.770秒。原始 `/proc/PID/stat` 的进程总CPU增长主要在内核态，同时多个进程 major faults 增长：

- Web：组1 user/system ticks 为 0/759；组2为5/499；组5为0/859；组9为6/488。
- 原证据未保存 SC_CLK_TCK，保留 ticks，不用开发机常见值换算现场秒数。
- stat 的进程聚合CPU与 schedstat 的PID主线程范围不同，不能直接相减归因。

逐项计算与限制见 [system-time-deltas.json](system-time-deltas.json)。它推进了内核调度、回收和实际资源域的排查方向，但没有唯一证明 CPU 配额、OOM、外部进程或某个具体锁是根因。

新进程导入对照见 [import-observations.json](import-observations.json)：

- 当前 Python3.10 开发环境：正式 runtime.main 约0.407秒，按 __mp_main__ 重载验收脚本约0.405秒。
- 两者模块数691/692，约124/123MB RSS，均9线程；没有额外重复启动服务。
- 这是一次新进程对照，非冷文件缓存基准，也不能代表事故现场资源状态。
- image_batch_loader 按 Job/path 缓存路径，默认不是每轮重扫9000文件。
- Web 正常请求复用 Runtime 客户端/channel；同一 DeviceProxy 串行发送/等待，不存在默认无限并发积压请求的机制。
- 没有据此删除已有 Qt 兼容预载、改全局线程池、提高时限或制造极端压力。

## 4. 为剩余原因补什么诊断

仅在现有 trace 中增加标量时钟和身份，没有逐帧 /proc 读取、后台轮询线程或新监控框架。

### DeviceManager

- 进程：processId。
- 执行线程：executionNativeThreadId。
- 阶段开始：phaseStartedMonotonicMs、phaseStartedThreadCpuNs、phaseStartedProcessCpuNs。
- 观察线程：observationNativeThreadId、observedThreadCpuNs、observedProcessCpuNs。
- 同进程CPU差：phaseElapsedProcessCpuNs。
- 仅执行和观察 native TID 相同时，提供 phaseElapsedThreadCpuNs；等待桥不会把自身CPU伪装成驱动CPU。
- driver 正常返回后提供 driverPhaseThreadCpuNs；未返回时不编造该值。

### DeviceProxy

send/receive 失败、关闭通道之前记录 processId、nativeThreadId、phaseStartedMonotonicMs、deadlineMonotonicMs、observedMonotonicMs、phaseElapsedThreadCpuNs、phaseElapsedProcessCpuNs。

无限采集的接收期限为 null。process CPU 包含其他线程，不能称作驱动单线程CPU。未扩展 Pipe 字节进度；零字节/部分写入仍不明时 UNKNOWN 不自动重放。未扩展首心跳 bootstrap 共享状态，首次心跳之前的具体阶段仍未唯一定位。

新增/加强的诊断回归验证实际线程归属、单调时刻、无限期限、原始错误/UNKNOWN及不重放语义。一个原有精确诊断字典断言因新增字段失败，已保留原6字段和值并明确校验新增7字段集合，未删除或弱化原错误断言。

## 5. Dot 复验应如何判定

固定 PR 评论提供的完整提交 SHA，在可见 Web 和真实软件 Runtime、模拟相机上复验受影响流程。记录所有启动/停止尝试，失败后不从分母中删除；未执行的平台和设备继续标未执行。

优先复验：

1. 新建/绑定/保存/发布页面，运行取得消费者真实图片与数值。
2. root 未启用采集时，不应取消已有消费者图片；提示明确所属运行。切到新的有效 Job，旧 Job 提示应退出。
3. 普通与 Dispatch 的持续结果、满容量停止、资源退场、Start 自动恢复、明确重启及后继持续结果。记录实际组数、结果数、持续时长及所有失败。
4. 日志查询、历史完整导出、正常取消和筛选切换；若自然发生导出失败，保留首错，成功重试后旧导出失败应清除，独立查询失败不被吞掉。
5. 发现超时/UNKNOWN时保全首次失败的原始 Runtime/Web 日志和新 diagnostics，结合相同 PID/native TID 的只读调度采样；记录采样器自己的 SC_CLK_TCK 和单调时钟。实际资源域不可读就标不可读。
6. 首次心跳仍未发布时，不根据“重试成功”宣称原因已解决；保留启动状态、时间与相关进程记录。

Dot 的可见验收、Windows原生交互、真实相机/PLC各自需要独立证据。历史 Error9、Windows首次空图以及未保存关闭原因的 VIEW_NOT_FOUND，不能因本次修复自动关闭。

本轮保证范围是已修代码与实际执行的验证结果如实对应；不承诺下一轮所有场景必然一次通过。
