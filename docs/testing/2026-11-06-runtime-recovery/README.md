# PR #11：复验缺陷修复与剩余根因边界

执行身份：**ChatGPT Web**。本轮从 `cdf1250ccc41b78f79b25b87e6db197809875384` 建立独立工作树，原 `f308c87b620461af0b48a00948f9b916b4d399d1` 工作树保持干净。`cdf1250` 相对被测 `f308c87` 仅修改测试和文档，本轮涉及的产品实现相同。

原始复验证据包 SHA-256：`1486ee1604ae2f380b190512cabb5ff328f6e9b9121183730950a7f8e6ac0d9c`。本目录保存新的回归用例、红绿结果和真实进程重放，不覆盖原验收证据。

## 已经确认并修复的代码缺陷

| 缺陷 | 因果链与最小修改 | 证据 |
| --- | --- | --- |
| 共享心跳被观察者停顿误判 | 原实现先读心跳、后读当前时间，父进程恰在两者之间停顿时，会拿过时快照判定持续发心跳的 worker 超时。改为用读取开始时的时钟判断，用读取结束时的时钟验证样本；不修改 5 秒超时或 500 毫秒间隔。真正停发仍超时，进程退出仍独立识别。 | [heartbeat-scope](heartbeat-scope/README.md)，真实 spawned producer 的 5.35 秒读后停顿反例和独立控制用例。 |
| 清理错误覆盖原始 UNKNOWN | `frameBeginWrite` 连接中断产生 `E_DEVICE_OUTCOME_UNKNOWN / UNKNOWN` 后，scope 的 `endCycle` 通过已关闭代理又抛取消错误，覆盖原错误。现在保留主异常对象、请求身份与 outcome，追加清理诊断；没有主异常时清理失败仍传播。 | [heartbeat-scope](heartbeat-scope/README.md)，真实 Pipe/DeviceProxy 的单层及嵌套 scope。 |
| 编码器替代启动失败导致后继永久隔离 | ready 失败已经成功回收替代进程，但外层仍无条件 quarantine。ordinal 33 的 raw credit 实际已归还，被卡住的是回调期间获准进入的 ordinal 34。现在根据真实回收结果区分隔离与可退休，后继独立归还自己的 credit；新请求可发起一次本地启动。 | [exporter](exporter/README.md)，保留原 500 ms/10 s 预算的真实 spawn/共享内存故障链重放，恢复后的两张 PNG 像素均核对。 |
| 双标签页 cookie 更新后旧 CSRF 无恢复出口 | cookie 跨标签共享，CSRF 缓存按页面模块保存。另一标签重新进入后，旧标签的合法操作变为 403，而客户端只处理 401。现在后端用专用错误码表示真正 CSRF 不匹配，前端走现有显式重新进入流程；普通权限/Origin 403 保持原行为。 | [session](session/README.md)，实际后端 HTTP cookie jar、实际 App/Editor/API 组件、sessionStorage 不可用时的内存草稿及身份隔离。 |

重新进入不自动保存、发布或重复运行命令，不注销另一标签的有效共享会话；草稿恢复仍按实际账号、Runtime 目标、工程隔离。

## 没有宣称已经确定的历史原因

原始 F2 的约 93.601 秒间隔、F4 的双任务启动无心跳，以及 F3 首次变慢的最初触发条件，不能凭现有历史记录唯一归因。已复现的心跳竞态证明产品存在误判路径，不等于证明历史每一次 heartbeat timeout 都经过该路径。已修复的导出退场错误解释了为什么一次失败会变成持续不可用，不等于证明最初一次编码为何变慢。

[剩余因果复核](remaining-causality-review.md) 已重新回查原 SQLite/JSONL：F2 是同请求执行等待超时后又有晚完成，F4 是两个独立 watchdog 的终止路径。父侧事件序号连续不证明生产者从未丢弃诊断心跳；数据库 timestamp 文本也不等于 commit 完成时间。当前没有可复核产物的旧控制试验不纳入本轮确定排除清单。

为后续定位补齐了 [设备失败观测](device-observability/README.md)：

- `deviceWait`：原始 deadline、开始时间、观察时间及执行状态。
- `deviceAction`：驱动、frame publication、observer、cleanup 等阶段与已完成边界。
- `device.ipc.failed`：父桥真实异常、收发阶段和请求身份，进入现有全局事件与 JSONL。
- heartbeat timeout：读前/读后时钟、原始样本、lastGood、超时门槛与 PID。
- `presentation.export.retirement_failed`：结果回调之后发生的替代启动/回收错误，避免最终退场证据只留在内存。

这些记录不重试 UNKNOWN，也不改变设备和图像原始内存的所有权门槛。同步日志发布仍可能延迟相关 owner 线程后续处理；独立评审未发现新增锁环，该限制在对应 review 中保留。

`heartbeat.seen` 仅表示父侧消费过诊断心跳事件，不等于共享 cell 曾发布或采纳过样本；不能只靠 `seen=false` 判定子进程尚未启动。

## 验证记录

| 范围 | 结果 |
| --- | --- |
| 心跳与 scope 新增回归、既有定向回归 | 32 + 36 通过；新增回归在基线上 8 失败、24 通过。8 个失败包含诊断字段断言，不代表 8 个独立根因。 |
| 心跳/scope 独立复核 | 3 个独立反例 + 11 个 scope 用例通过。 |
| 图像导出定向回归 | 60 通过、5 deselected；被 deselect 的真实 encoder 回归随后单独通过，其余 RuntimeService 项纳入集成套件。 |
| 图像导出真实进程重放 | 健康替代进程对照和一次 ready timeout 故障链两组通过，13.01 秒；下一正常请求及再下一请求均出新图，进程、线程、共享内存全部退场。 |
| 最终设备诊断与取消/退场/日志、startup/cycle 受影响回归 | 76 通过；另有 5 个独立分支验证及原发送时限对照。 |
| Web 前端完整 Node 测试 | 153 通过。 |
| Web 后端定向测试 | 72 通过，另 134 个子测试通过。 |
| TypeScript/Vite 构建 | 通过。 |
| 全源 ruff、mypy、protobuf 一致性 | 通过；mypy 覆盖 614 个源码文件。 |
| Runtime 完整集成回归 | **2970 通过、7 跳过、0 失败**，pytest 782.02 秒，运行期间源码指纹未变化。明细见 [integration/runtime-suite.json](integration/runtime-suite.json) 和 [集成说明](integration/README.md)。首次收集因稀疏工作树缺夹具未执行；首轮执行 2966 通过、7 跳过、1 失败。该失败揭示普通取消被追加诊断，已恢复既有行为，原测试断言保留。两份失败记录及原始 JUnit 均保留。 |

不同组有重叠，不能将上表数字相加冒充独立测试总数。所有结果是开发验证，未执行完整人工 Web 桌面流程、原生 Windows 桌面、真实相机或 PLC 验收。

最终审查还恢复了 IPC 原有的回复序列化后再开始发送计时的边界。序列化暂停与发送阶段暂停分别使用真实 Pipe 做对照：前者保留完整 2 秒发送预算，后者照常超时；没有通过放宽超时或重试消除问题。最终完整 Runtime 回归固定源码执行，开始/结束指纹核对见 integration 目录。

## 后续人工复验

Windows 全仓 CI 后续发现五项新增测试的跨平台假设问题，具体失败、最小修正和原断言保留情况见 [Windows CI 补充记录](windows-ci-followup/README.md)。原失败结果继续保留，Windows 复跑以对应固定提交的 Actions 和 ChatGPT Web 评论为准。

按 [DOT_ACCEPTANCE.md](DOT_ACCEPTANCE.md) 在 PR 评论给出的固定提交复验。优先完成 Web 创建、保存、发布、真实软件 Runtime 订阅、停止/重启、日志查看与导出闭环，并单独检查双标签页恢复。历史长停顿若再次出现，保留新诊断与原始日志，不通过刷新、重启或覆盖报告把失败样本隐藏。

本轮不合并 PR、不发布或部署、不修改现场工程、不访问真实设备。
