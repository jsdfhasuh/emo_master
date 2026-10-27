# TCP 单次设备结果日志：有界预览补齐

日期：2026-10-21。本轮只处理 `client_once.exchange` 和 `receive_once.receive` 的完成诊断预览，承接 [v1.5 计划](../plans/2026-10-20-tcp-device-integration-plan-v1.md)与[上一轮报告](2026-10-21-tcp-device-final-closure.md)。上一轮 Server 字节日志、草稿端点测试、连接字段焦点及 R01—R03 的结论保留；本次发现的是上一轮 Server 用例未覆盖的单次完成结果分支，**诊断预览丢失，业务响应没有丢失**。

结论：**本轮单次设备预览与截断遗漏已修复，没有本轮范围内未解决的软件问题。** 固定源码独立快照的 A1—A6 及既有补充为 59 passed，受影响回归为 219 passed、2 deselected，静态与协议检查通过；硬件、人工完整 UI、冻结产品、Linux 和跨机器仍为 NOT_RUN。

## 1. 基线与修复

开始时本地及 `git ls-remote` 返回的目标分支均为 `4550d46e34e508cbb2774f7bcda8863d293541e6`，没有后续提交预先解决此项。读取根目录 AGENTS.md，未发现下级指令文件。工作区已有视频加载器等并行改动，实施期间另有图片保存相关改动；没有回退或纳入本轮提交。

固定被测源码及测试提交：**`cdd690065bd629d0294151cb0310965aa90b9c71`**，tree 为 **`2208902c1658ddc332d4dd5966c8560b34407fbd`**。最终验证从该提交 `git archive` 导出，四个受改 Python 文件的实际导入目录、Git blob 及 SHA-256 均已记录。后续文档交付需与此提交的 `src/`、`tests/` 一致，完整交付 SHA 与远端核验记录在交付答复及本地 Git 回执中。

实际快照为 `C:/Users/JSDFHA~1/AppData/Local/Temp/emo-f-5di5sg_j/source`，`emo_master.__file__` 位于该目录的 `src/emo_master/__init__.py`，入口用 `os.path.samefile()` 核对。两个批次均为 0 failed、0 error、0 skipped；两个 deselected 只属于既有迁移范围排除。

| 根因 | 修改及最终行为 |
| --- | --- |
| `compactResult()` 把 Base64 字符数大于 1368 当作超过 1024 个原始字节；1025/1026 字节仍可能只有 1368 个 Base64 字符 | `apps/runtime/devices/diagnostics.py` 依据编码长度和尾部 padding 计算准确的原始字节数 `bytes`，按该计数设置 `dataTruncated`。仅解码有界前缀，最多保留 1024 个原始字节，不额外解码完整响应 |
| `DeviceDiagnostics.append()` 折叠超过 1024 字节的元数据时删除整个 `result`，单次设备没有其他字节事件代替 | 折叠时只保留嵌套 `result` 中受限的 HEX、字节截断标记和整数 `bytes`/`sentBytes`。HEX 再限制为 1024 字节；任意嵌套结果、冗长文本及其他字段仍折叠，1000 字节元数据 preview 上限不变 |
| `recordDetails()` 支持嵌套 HEX，但只检查顶层 `truncated` | `ui/communication_panel.py` 同时识别单次结果的 `dataTruncated`。原始字节截断和 `fieldsTruncated` 分别提示；沿用从 HEX 解码 UTF-8、非法字符替换的现有显示逻辑 |

| 原始响应 | client_once / receive_once 业务返回 | 诊断预览 | 字节提示 | 元数据提示 |
| --- | --- | --- | --- | --- |
| 512 字节普通文本 | 逐字节完整 | 完整 512 字节，HEX/UTF-8 均准确 | 不显示原始字节截断 | 字段 JSON 超过阈值时独立显示字段折叠 |
| 1025 字节普通文本 | 逐字节完整 | 前 1024 字节，原始计数仍为 1025 | 显示原始字节截断 | 同上，与字节截断独立 |

没有修改驱动、实际收发、事务生命周期、Worker 或 protobuf。每设备 512 条/1 MiB、工程 8192 条/16 MiB、128 条分页继续沿用；节点投影仍 `write=False`。没有完整 Base64 入日志，也不解析元数据 preview 恢复字节。

## 2. 直接反例与测试范围

在既有 `tests/designer/test_tcp_closure_ui.py` 中参数化两个设备类型及 512/1025 两种长度，使用真实 DeviceManager、TCP 驱动和 localhost socket。client_once 实际发送 4 字节 PING；receive_once 实际临时监听并接收。测试在业务返回逐字节相等和资源退出断言之后，检查真实完成事件经过 append/query 的结构化预览，再通过现有 `DeviceDiagnosticsView` 多次切换格式，分别断言准确 HEX 与 UTF-8。

同时检查 Runtime/工程加载代次/设备 generation、owner、cycle、requestId、source 保留，`TRANSACTION_READY`、`ownsResources=false`、socket 集合为空及 owner 退出。捕获实际 writer 的入队事件并原样转交异步 JSONL writer，断言入队诊断与 query 完全相等，查询和格式切换后入队及落盘均恰好一次。

`tests/runtime/test_device_feedback_closure.py` 补入 1024/1025/1026/1027 的精确计数边界，以及任意大结果只保留受限字节与整数计数的保护用例。其他用例不改，复用原 CHAIN / REGRESSION。

现有 operational JSONL writer 会对所有 `*.completed` 载荷做摘要，单次完成事件的预览供设备诊断查询使用。首次修复后测试曾错误要求该落盘摘要等于完整诊断；核对 writer 源码后改为分别验证完整入队内容与单次持久化，没有改变 JSONL 策略、增加写入路径或削弱预览断言。这次测试假设错误的失败输出也完整保留。

## 3. 本轮实际执行

环境：Windows `10.0.26200`；`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`；Python **3.10.21**、PySide2 **5.15.2.1**、Qt **5.15.2**；`QT_QPA_PLATFORM=windows`、`PYTHONIOENCODING=utf-8`。所有工程和设备均为临时工程、模拟设备及 localhost。

本轮独立入口直接加载上一轮支持 `--output-dir` 的 runner，替换本轮基线、产品文件和反例列表，复用其 archive、导入核对、CHAIN、REGRESSION、静态检查与产物保留。旧入口及历史目录不改。新入口补充保留单次设备的 JSONL 和结构化展示证据。

```powershell
$env:QT_QPA_PLATFORM='windows'
$env:PYTHONIOENCODING='utf-8'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  docs/evidence/2026-10-21-tcp-once-log-preview/run_validation.py `
  --repo 'D:/jsdfhasuh/documents/my_project/emo_master' `
  --source-sha cdd690065bd629d0294151cb0310965aa90b9c71 `
  --output-dir docs/evidence/2026-10-21-tcp-once-log-preview/final
```

首次反例和修复后定向使用相同入口，加 `--working-tree --source-sha 4550d46e34e508cbb2774f7bcda8863d293541e6`，输出到各自独立目录。此时 SHA 表示工作区基准，不冒充该提交已包含新增测试或修复。

| 批次 | 本轮实际结果 | 证据 |
| --- | --- | --- |
| 修复前反例 | **9 failed，2.82s；exit 1** | first-counterexamples：四个真实收发用例在确认业务正确及清理后因 result 丢失失败；边界用例另证实计数缺失及 1025/1026 截断错误 |
| 首次修复后 | **4 failed、5 passed，2.69s；exit 1** | fixed-counterexamples：预览、原生 Qt 和事务断言已通过，四项失败均来自错误地要求 operational 完成摘要等于完整诊断 |
| 修正持久化断言后 | **9 passed，2.39s；exit 0** | fixed-counterexamples-corrected：完整入队、实际落盘次数及全部预览/身份/清理断言通过 |
| 最终固定源码 CHAIN | **59 passed，73.68s；exit 0** | final/chain.log、chain.xml：原 A1—A6/既有补充，加本轮 9 项 |
| 最终固定源码 REGRESSION | **219 passed、2 deselected，100.17s；exit 0** | final/regression.log、regression.xml：原 14 文件受影响集合，无新增排除 |
| 静态与协议 | **全部 exit 0** | 四个受改 Python 源码/测试 Ruff、两个源码 mypy、`scripts/gen_proto.py --check`、`git diff --check 4550d46 cdd6900`；新证据入口另执行 Ruff |

最终批次原始 argv、工作目录、导入路径、环境、被测 SHA/tree、各命令退出码见 [validation.json](../evidence/2026-10-21-tcp-once-log-preview/final/validation.json)。仅保留之前已排除的 `testUpgradedDraftResourceFailureCanBeFixedAndRevalidated` 两个旧迁移参数分支，不新增排除项，不将其记为通过。上一轮 50 passed、219 passed 是历史证据，不能替代此表结果。

| 保护范围 | 本轮固定快照证据 |
| --- | --- |
| A1—A6 连续链 | 创建、保存/另存、纯校验/显式写入、正式运行/停止、模拟相机→处理→PLC、三类 TCP/四内置/第三方/16 MiB、维护/配置更新、三类包新目录导入后正式 Job、第三方持续服务全部通过。实际工程、导入路径和 Job 见 final/acceptance/acceptance-results.json |
| Server 日志 | `testTcpByteLogsKeepChannelAndExactBoundedPreview[32/512/1025]` 全部通过：通道筛选、准确 HEX/UTF-8、原始计数、连接/服务代次、独立截断及单写 |
| 回执与 tcpTransaction | 16 MiB 正式 Job 的实际发送回执及字节断言、F09 两种大小的冻结日志投影断言全部通过，原单次取消及未知发送不重放测试仍通过 |
| 已关闭端点/焦点 | Server 改名复用、非 TCP 连接保护与连接测试清理通过；外层对话框四个地址/通道焦点参数均通过 |
| R01 | `testConsumerDeviceLogsKeepRetainedCycleIdentityAndPersistOnce[False/True]` 通过；真实 spawned Dispatch、冻结/历史 generation、成功及失败日志、JSONL 唯一持久化与清理的原始材料保留在 final/dispatch |
| R02 | 选择性通道删除保留 workflow/node/slot/selector 定位，补选改绑后正式发布用例通过 |
| R03 | 0/1/2 owner 与 64 owner 有界摘要、服务权威活动及退出清理用例通过 |

## 4. 证据、未执行项及复验

独立证据目录：[2026-10-21-tcp-once-log-preview](../evidence/2026-10-21-tcp-once-log-preview/)。首次失败、修复过程和最终快照分目录，原始日志及 JUnit XML 保留；初始两次入口的 pytest 目录名 glob 过长，随后从仍保留的原临时目录补收四个原始 JSONL 及修复后展示结果，记录在各目录 recovered-artifacts.json，没有改写首次输出。最终入口已修正该产物保留路径。

逐项核对结果见 [completion-audit.json](../evidence/2026-10-21-tcp-once-log-preview/completion-audit.json)。[SHA256SUMS.txt](../evidence/2026-10-21-tcp-once-log-preview/SHA256SUMS.txt) 包含 89 个证据文件，不包含自身；清单本身 SHA-256 为 `137e1560791f60a90b3fc622f318ccf07008a7f498f9d46eca3d65f362fc8418`。acceptance 中保存工程、设备/流程/运行包导入路径及真实 Job，dispatch 保存 R01 原日志，byte-logs 和 once-byte-logs 分别保存 Server 与单次设备证据。共享工作区状态和保护文件初始摘要见 scope-audit.json。

| 验收层级 | 本轮状态与原因 |
| --- | --- |
| Windows 原生 Qt 自动化 | 使用 windows 平台插件和实际 QWidget / DeviceDiagnosticsView；结果以本轮测试输出为准 |
| 真实硬件 | **NOT_RUN**；限定模拟及 localhost，没有访问真实相机、PLC 或 TCP 硬件 |
| 人工完整 UI | **NOT_RUN**；未由人员执行完整桌面走查，原生 Qt 自动化不等同人工验收 |
| 冻结 EXE/安装产品 | **NOT_RUN**；未构建、安装或运行新冻结产品，现有 EXE 不会自动加载源码修复 |
| Linux/跨机器 | **NOT_RUN**；本轮执行环境为 Windows 本机，未准备或运行对应部署 |
| 旧工程迁移/压力/强杀/多故障矩阵 | 不在本轮范围；不新增排除项或验收框架 |

源码复验：在临时工程绑定 client_once（exchange，发送 PING）或 receive_once，让 localhost 对端返回 512 字节普通文本；动作结束后在设备日志选择完成事件并切换 HEX/UTF-8，应看到完整且准确的 512 字节，没有原始字节截断提示。改为 1025 字节，应保留 1024 字节预览、原计数 1025，并独立显示原始字节截断与元数据折叠提示；设备回到 TRANSACTION_READY 且不持有资源。保存/写入/导出及导入运行继续使用原指南，不重写 session_send 或相机硬触发说明。
