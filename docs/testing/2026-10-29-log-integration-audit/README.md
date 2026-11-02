# 日志接入缺口审查

日期：2026-10-29。源码 SHA：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`，叠加当前未提交工作，包括此前页面展示日志接入。审查对象：“运行 → 打开日志”、Runtime EventStore / SQLite 事件历史及 JSONL 文件。

本轮新增本文、辅助探针和输出，没有修改产品源码、提交、推送或部署。以下结论不是全仓库无遗漏证明，也不是内网截图根因或真实设备验收。

## 优先补齐

### 1. 晚到的导出失败被新日志过滤

位置：`src/emo_master/apps/runtime/presentation/operational_log.py:45`，调用入口 `presentation/service.py:394`。

结果 1 尚在导出，结果 2 因同来源通道忙先闭合，随后结果 1 导出失败。正常异步完成允许这种顺序，但序号过滤把较旧失败也跳过了。既有 assembler 夹具的真实 `_consume()` / `_exported()` 路径已复现：结果历史有 `2/BUDGET_EXCEEDED` 和 `1/EXPORT_FAILED`，普通事件仅有 `INFO/IMAGE_BUSY`，两个 scope credit 均正常归还。ERROR 未进入 EventStore，因此也无法进入 SQLite、JSONL 或窗口。

最小改法：区分“当前状态/恢复必须按新旧顺序”与“保留晚到失败证据”；晚到失败仍去重汇总，晚到成功不能恢复较新错误。这是此前本会话新增接入的遗漏，不能据此前通过测试称这一顺序已覆盖。

### 2. Designer 本机展示失败只在页面或内存

位置：`clients/runtime/display_session.py:611` 读取图片，`:699` 只写 failures / 统计 / 有界 records；`:247` 只更新连接状态，`:484` / `:569` 处理 Subscribe / Snapshot 失败。上述路径均在 `src/emo_master/` 下。

Runtime 已返回 AVAILABLE，Designer 的 ReadAsset 仍可能随后超时、校验或解码失败；展示订阅也可能独立失败，而日志订阅仍正常。renderer（`:663`、`:748`、`:808`）仅显示占位、状态和 tooltip，没有普通日志出口。服务端无法知道客户端这一步失败。静态追踪已确认，本轮未执行该 UI/网络场景。

最小改法：本机诊断由 PreviewController 在 GUI 线程写入既有日志入口，带 job/result/source 与 RPC/解析原因；短暂通信失败 WARN、校验/解码失败 ERROR、恢复 INFO，按来源去重，主动停止/切换不报异常。

非阻断补充：`display_session.py:722` 的 CLIENT_BUDGET、`ui/presentation/hub.py:121` 的 UI_IMAGE_BUDGET 也仅显示在页面，可顺手接 WARN；本轮未重现正常项目触发，不作为已确认的现场容量问题。

### 3. 外部已有任务的业务 ERROR 被观察订阅过滤

位置：`apps/designer/page_designer/preview.py:563` 为未被普通 Run 管理的任务建立观察订阅；`runtime_logs.py:67` 只转发 presentation.*；`apps/runtime/presentation/operational_log.py:60` 有意忽略已有权威 node.failed 的 NODE_FAILED。

查看不是本 Designer Run 创建的 Job（包括 producer/consumer）时，node.failed/job.failed 可存在 Runtime 事件历史和 JSONL，但当前窗口收不到。观察器仍推进这些事件的游标。真实观察器 `_read()` 配合合成 protobuf 流已复现：三条 ERROR 输入 node.failed、job.failed、presentation.source.unavailable，只转发最后一条，游标到 3；静态确认页面无第二条业务订阅。

最小改法：这条仅服务外部任务的订阅同时转发必要 WARN/ERROR 和业务终态，保留普通 Run 排重；不额外制造重复 NODE_FAILED 展示事件。

### 4. 原生设备失败写文件时丢失顶层等级/错误码

位置：`apps/runtime/devices/diagnostics.py:90` 构造 RuntimeEvent 未设置 level/code，默认 INFO/空字符串；`devices/manager.py:639` 的 device.connect.failed、`:367` 的 device.action.failed 使用该入口，真实失败发起处也未传 level。Runtime 在 `grpc_server/service.py:230` 绑定 JSONL writer。

无设备调用 DeviceDiagnostics.append + writer spy 已得到 `device.connect.failed / INFO / code=''`，payload.code 为 E_DEVICE_CONNECT。子代理另一探针即使显式传 level=ERROR，顶层仍为 INFO。按 WARN/ERROR 或顶层错误码筛选会漏掉。原生诊断直接写文件，没有经过主 EventStore；设备管理器另有诊断页，不能说完全没记录。工作流已有 node.failed 不在此项漏等级的断言范围内。

最小改法：失败发起点明确等级，投影保留 level/code/有效归属；关键失败进入主日志窗口时保持设备诊断游标与 Job 序号独立，不转发所有高频设备数据。

## 随后补齐

### 5. 普通运行日志流断开/恢复无记录

位置：`apps/designer/services/runtime_worker.py:400`、`:425`、`:498`。streamError 只参与重试，getJobStatus 失败只返回 None；RuntimeClient 只包装/重抛。

假客户端先发 job.started 后断流，再发 job.completed，真实 `_followJobEvents()` 仅发两条任务事件及 COMPLETED 状态，没有中断/恢复/failed 信号。自动重连和游标补读仍在工作，不能据此称业务事件必然永久丢失；缺少的是连接时间线证据。

最小改法：中断一次 WARN，恢复 INFO，连续重试合并，主动停止取消静默。展示连接是独立通道，按第 2 项补齐。

### 6. 预览快照 WARN 被 JSONL 白名单漏掉

位置：`apps/runtime/grpc_server/service.py:2262`、`:2281` 发布 preview.snapshot.failed；`events/jsonl_writer.py:377` 的 `_isOperationalEvent()` 不接收它。

临时目录真实 RuntimeJsonlLogWriter 已验证：事件在 EventStore 中，enqueue 返回 False，文件没有此条；对照 node.failed 和 presentation.source.unavailable 均实际落盘，writer 正常退出。正常 Runtime EventStore 配有 SQLite 持久化，所以本项是文件出口缺口，不是完全未接日志。

最小改法：把这条失败纳入现有白名单；不无差别取消全部过滤。

### 7. Designer 部分操作只有弹窗，另有失败误记 INFO

以下均为静态调用链确认，未执行原生 UI 故障场景：

- 连接/读取 Runtime 失败：`apps/designer/ui/runtime_connection.py:149`、`:173`，仅标签或 `_error()` 弹窗，上游没有日志。显式操作失败应 ERROR，带目标和错误码。
- 工程配置读取失败/写入结果未知：`apps/designer/ui/project_configuration.py:1351` 及后续 `_notify()`，通知入口仅处理 UI。建议读取/操作失败 ERROR；结果未知 WARN，保留原 requestId，合并 300ms 观察轮询。UNKNOWN 不能当作确定失败或自动重放。
- 页面测试包导出 I/O 失败：`apps/designer/page_designer/delivery.py:20` 只有 QMessageBox，上游文件操作不补日志。建议 ERROR 带输出目录与原始原因，取消和未保存前置校验另分类。
- 资源绑定读取设备类型失败：`apps/designer/page_designer/coordinator.py:189` 调 preview.message 未传等级，`preview.py:170` 默认 INFO；已进窗口，但 ERROR/WARN 筛选会漏，应显式传 ERROR。

## 窗口与文件的持久化边界

`apps/designer/ui/main_window.py:4314` 的 `_appendLocalLog()` 只构造 StructuredLogEntry，写有界 logEntries/logBuffer 并更新 LogDialog，没有发送到 Runtime EventStore，也没有自动落盘。窗口混合展示 Runtime 事件与 Designer 消息，不代表二者持久化相同。

第 2、5、7 项即使补 appendRuntimeLog，首先得到的仍是当前 Designer 可检索/导出的记录。若要求关闭或重启后自动保留现场证据，还需要 Designer 本地持久化出口；网络中断时的记录不能仅依赖不可达的 Runtime。

## 证据与边界

- 可复跑 `probe.py`，实际输出 `probe-results.json`。解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。五项辅助探针验证第 1、3、4、5、6 项，不等于全量测试通过。
- 子代理曾用默认 Python 加载观察器，因缺 grpc 未执行成功；之后根代理在项目解释器完成观察器探针。这是辅助验证环境问题，不列产品缺陷。
- 全局预览 `apps/runtime/preview/live.py:375` 的 node.preview.failed 发布到 __runtime__，后端 ERROR 可持久化，但 Designer 未见该全局订阅。尚未核对全部预览专属 UI 的等价提示，记录为次级覆盖疑点，不升级到上面七项的确定优先级。
- 不把 OPTIONAL_ABSENT、BRANCH_SKIPPED、正常取消、逐帧成功列为漏日志；runtime.logfile.failed 不回写同一 JSONL 是防递归。OBSERVER_FAILED 未确认当前正常路径注册回调，暂不单列；pin 收尾异常已有上层 ERROR。
- 未执行全量测试、原生 Qt 人工验收、冻结 EXE、真实相机/PLC 或内网部署，未证明原截图根因。此前批次的通过、失败和未验证项仍见 `../2026-10-29-presentation-operational-logs/README.md`，本轮没有覆盖或抹去它们。
