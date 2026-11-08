# PR #11：状态同步、同结果读图恢复与清理修正

执行与记录：**ChatGPT Web**。基线为 `504c1c35db4f80a55b236a2f51adda78a38b1d28`。
本次使用隔离工作树，开发验证针对基线加本文所述修改，不能记为基线干净版本通过。
推送到原 PR 分支后的固定提交、双平台 CI 结果在 PR #11 的对应 ChatGPT Web 评论记录。

本轮修正可以直接复现的正常使用缺陷，并保留历史故障的证据边界。
没有合并、发布、部署，也没有操作真实相机、PLC 或现场工程。

## 1. Web 正式状态没有同步到运行列表与原控制卡片

### 根因

Start/Stop 受理后，立即读取目录得到 STARTING/STOPPING 是合法的。
但原 `main.tsx` 收到正式 `job_state` 时仅更新 renderer 的 `runtimeState`，
两个运行下拉使用的 `runs` 没有更新；原控制卡片又优先展示受理时的
`lastCommand.reply.run`。因此同一个 Job 的结果区与控制区域能够长期显示不同状态。

Dispatch 的消费者查看订阅不会替代对原控制 root Job 的观察，不能把消费者的状态写入 root。
进一步的实际 App 反例还证明：首次目录读取失败时新 Job 无法进入 `runs`；
一个较慢的目录响应可能覆盖已经到达的正式终态，即使该 Job 的最终观察已停止。

### 修正

- 将正式 `job_state` 按 Runtime 实例、工程、Job 身份合并回既有 `runs`。
  两个下拉、renderer 与原控制卡片使用同一份当前状态；新观察到的 Job 可以进入列表。
- 查看订阅未覆盖原控制 Job 时，仅读取该 Job 的现有状态接口。
  使用既有 `GetRunState`，不扫描全目录、不重新打开 view、不改变查看或停止选择、不重发命令。
- 终态仍持有执行或展示资源时继续观察；两类资源均退出后停止额外轮询。
  注销、实例/工程变更、卸载及订阅接管时取消请求，拒绝晚回包串写。
- 目录刷新保留请求期间已经更新的正式状态，同时继续提供名称等目录信息。
  不建立第二份 Job 状态账本。
- `/api/runs/{id}` 保留原会话、目标 ACL 与 HTTP 错误语义；旧 Mock 适配器仍可使用原回退路径。

回归使用实际 App/React、受控 HTTP/WebSocket 边界以及真实 ASGI 路由。
现有真实软件 Runtime + Chromium 测试去掉 Stop 后的手动刷新，
直接检查两个下拉及原控制卡片自动更新为 ABORTED。

### 真实浏览器回归发现的只读准入拒绝

加强后的真实 Runtime 浏览器检查首次在 Stop 后暴露了另外一条实际失败链：
`GET /api/runs/{id}` → `_info()` → `GetRuntimeInfo` 被短暂拒绝，
错误为 `RESOURCE_EXHAUSTED / control`，网关直接返回 HTTP 500。
测试辅助函数又假设 Playwright `APIResponse` 有 `request` 属性，遮住了原错误；
现在错误助手保留真实 HTTP 状态和响应，首次失败记录未丢弃。

网关仅对 GetRuntimeInfo/GetRunState 的明确 control 执行前准入拒绝短暂重试。
一次 `run_state` 的两次读取和退避共享原 `self.timeout` 截止时间，
不提高服务端并发额度、不增加轮询频率、不延长验收等待，不重试 Start/Stop 写操作或其他错误。
真实 loopback gRPC 的两条失败反例及持续拒绝、总预算、取消、写命令不重发均有定向验证。

## 2. 原生页面一次暂时读图失败后，同一完成结果一直空白

### 已复现的机制

在真实 Runtime、gRPC 和资产存储上，仅让第一次 `ReadAsset` 返回一次超时。
任务已经 COMPLETED，结果及图片来源仍为 AVAILABLE，后续服务恢复正常。
原客户端仍只读取一次，`decoded=0`；连接恢复为 CONNECTED、健康快照继续更新，
隐藏/重新显示窗口也不能恢复同一个结果。

原因由三处共同构成：

1. `_decodeLive` 把第一次读取错误记入该结果的 `failures`。
2. `_accept` 用 `seen` 跳过重复的同一个 `resultKey`。
3. `_enqueueDemand` 排除已经在 `failures` 中的来源。

连续任务可能被下一份新结果掩盖这个问题；只产生一个结果的完成任务没有下一份结果可替换。

### 修正边界

在后续成功接纳的权威健康快照确认同一当前结果和图片引用仍有效时，
使用原有有界队列及解码线程，允许暂时失败来源补读一次。
每个 RPC 原有的 0.5 秒上限不增加，不重新采集或执行任务。

暂时错误限定为 `UNAVAILABLE`、`DEADLINE_EXCEEDED`，以及明确的 `RESOURCE_EXHAUSTED / asset`。
摘要/PNG 错误、已过期资源、取消和本地缓存额度失败不自动重读。
第二次仍失败时保留真实错误，不无限重试。
无图像需求、换 Job/Runtime、结果更新、过期及关闭均按现有生命周期处理，不能恢复旧图。
恢复成功只更新已有结果的图片，不重复发布正式业务结果。
本次恢复限于当前 live 结果；冻结 pin 沿用原有快照和租约语义，不自动替换冻结内容。

**该反例证明的是一个具体产品缺陷。原 Windows `job_2_image` 超时缺少当时的读图错误及缓存状态，
目前不能据此宣称历史故障的触发原因已经唯一确定。**

## 3. 故障桩持续到 teardown，窗口拒绝关闭却被原测试忽略

`testObserverOpeningRejectsInvalidPendingFormWithoutRefresh` 使用 fixture 共用的 monkeypatch，
把 `commitPending` 改成始终抛异常。fixture teardown 先于 monkeypatch 恢复，
因此实际 `window.close()` 被拒绝，关闭路径没有进入 worker 退休；原 fixture 没检查返回值。

现在把该故障桩限制在 `monkeypatch.context()` 中，并断言真实窗口关闭成功、
Runtime controller 已关闭、icon/catalog worker 均已退出。
没有用强制销毁掩盖拒绝关闭。

前后对照：只加强断言、保留原桩作用域时出现 `1 passed, 1 error`，
错误明确是 `designer fixture refused close`；限定桩作用域后该模块 22 项通过，
再与原 two-job 用例在同一 pytest 进程执行，23 项通过。
这些是 Linux Qt offscreen 自动化证据，不能代替 Windows 人工验收。

## 4. 原 two-job 失败点现在保留可分流的现场信息

仅在原断言失败后、清理前输出 `TWO_JOB_OBSERVATION DIAGNOSTICS` JSON。
记录 Runtime 来源状态与图片引用、exporter ownership、客户端读取/解码统计及错误、
当前需求、GUI 图像是否为空以及 widget/result 身份是否一致。

原 25 秒等待、原图像就绪谓词和断言不变。成功路径不新增持续采样。
锁获取有界，各段独立容错，不读取图像字节、不发 RPC；诊断异常不能遮盖原失败。
辅助故障注入已检查正常输出、session 锁忙及整个 writer 异常三种诊断出口。

## 5. 浏览器 Error 9 仍然未闭环

此前三条原导出路径回放成功，不能证明原可见标签崩溃已经修复。
本轮继续检查结果订阅、图片 object URL 释放、日志折叠/重查，
没有找到与原现场相吻合的无界积累链，因此没有添加猜测性的产品改动。

原证据未记录实际浏览器/renderer PID、退出事件、资源域和崩溃报告，
不能从画面 Error 9 或辅助校验程序的内存峰值直接推断浏览器的死因。
原包最后两份逐行校验报告没有附产生它们的流式实现，也不能把方法自述等同于完整源码审计。

后续如要关闭该问题，应沿原数据量做一次定点取证：导出、下载、校验、打开日志并重查，
关联真实 browser/renderer/校验进程及其资源域，记录退出/crash 通道；失败时先保留现场再重载。
取不到这些字段就标明取证受阻，不能将另一个浏览器的通过写成原故障已修复。

## 6. 验证及交付记录

本地最终结果如下；各集合有重叠，不相加为独立总数。

| 验证 | 结果与范围 |
| --- | --- |
| 前端完整 `npm test` | 171 通过，0 跳过 |
| TypeScript / Vite / 静态资源打包 | 通过 |
| Web backend 完整 unittest discover | 226 项：219 通过、7 跳过；跳过为 Windows 专属 3 项、未启用的 owning Runtime 集成 4 项 |
| Chromium → Web HTTP/WS → 真实软件 Runtime | 通过；Stop 后没有手动刷新，两个下拉及原控制卡片自动 ABORTED；Start/Stop 各 1 次 |
| 新增读图恢复及既有相关回归 | 95 项通过，其中新增 25 项；包含原 two-job 用例 |
| Qt session views / LegacyReader | 14 项通过 |
| 原健康失败后同图对象复用合同 | 1 项通过；已解码图恢复不增加读取/解码/转换 |
| fixture 清理模块 + two-job 同进程 | 23 项通过；修复前关闭断言明确失败 |
| Ruff / mypy / diff check | 通过；mypy 检查 614 个源文件 |

本地为 Linux、Python 3.10.22、Qt offscreen；浏览器为 headless Chromium 153。
这是自动化软件验证，不能改写成可见桌面人工验收、Windows 验收或真实设备验收。
完整命令、固定提交和后续双平台 CI 链接见本次 PR 评论。
新用例覆盖本次正常路径及直接反例；自动化、Qt offscreen、Windows CI、
可见桌面人工验收和真实设备验收分别记账。

Dot 后续可见验收重点：

1. 固定本次修复 SHA，记录是否有工作区改动。沿用已授权的本机免密模式进入 Web。
2. 创建/保存/发布页面，启动普通任务，核对结果、查看下拉、停止下拉和原控制卡片；
   Stop 后不点刷新，等待真实终态和资源退场，再在同一 Runtime 明确启动新任务并出图。
3. 查看 Dispatch 消费者时保留原 root 为控制对象，检查两者状态和图片不会串写。
4. 原生 Designer 连续完成两次普通任务，分别打开当前结果、冻结/恢复、关闭观察者；
   第二次必须显示第二个 Job 的图像身份，不使用重新启动应用作为恢复步骤。
5. 日志显示与导出继续核对数量、字段和范围；若重现浏览器崩溃，按第 5 节采集退出证据。
6. 各项如实区分通过、失败、受阻和未执行。软件模拟通过不代表真实设备验收。
