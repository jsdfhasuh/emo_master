# TCP 设备化 v1.5：最后一轮局部收尾

日期：2026-10-21。范围为附件指定的三个代码问题、两处指南修订及计划状态更新。沿用 [v1.5 计划](../plans/2026-10-20-tcp-device-integration-plan-v1.md) 的 A1—A6，不重做主体架构，不扩展旧工程迁移或故障矩阵。[第二轮审核](2026-10-21-tcp-device-review-round2.md)及所有历史原始证据保持原样。

结论：**本轮三个问题已修复；固定源码上的 A1—A6 为 50 passed，受影响回归为 219 passed、2 deselected，无本轮范围内未解决的软件问题。** 软件要求已落实到本轮覆盖的正常工程链；真实硬件、人工完整 UI、冻结产品、Linux 和跨机器验收仍分别为 NOT_RUN。

后续复核补记（2026-10-21）：上述日志用例覆盖 Server `_record()` 分支，没有覆盖单次设备 `device.action.completed.result` 的折叠。随后确认单次业务响应正确，但结构化诊断预览丢失，且 1025/1026 字节截断标记不准确。本报告保留当时的固定源码和原始结果；后续局部修复及重新执行结果见[单次设备日志补齐记录](2026-10-21-tcp-once-log-preview.md)，不能以本报告的历史通过数替代。

## 1. 基线、被测源码与工作区范围

开始时，本地和实际远端 HEAD 均为 `31b5de4a604e4facdf636ad39795da0869d908d4`，分支为 `agent/runtime-workflow-architecture-v1`，工作区干净。适用指令为根目录 AGENTS.md，没有后续提交已提前解决本轮问题。

源码与测试提交：**`325f60e4fe7b9210eaf50abddc13d0b2086e8bcb`**，`fix(devices): close TCP preview, draft test and validation focus gaps`。

被测 tree：**`0df69ebe7a7eeee7d631009de8e78cc2091cc2fe`**。独立 `git archive` 快照位于 `C:/Users/JSDFHA~1/AppData/Local/Temp/emo-f-5_r7z73q/source`；实际导入为该快照的 `src/emo_master/__init__.py`，以 `os.path.samefile()` 核对。10 个受改 Python 文件逐一校验导出内容的 Git clean blob 与固定提交一致，详见 [final/validation.json](../evidence/2026-10-21-tcp-device-final-closure/final/validation.json)。

实施期间共享工作区出现了视频算子相关并行改动。它们没有被回退、暂存或纳入本轮源码提交。首次反例属于共享工作区定向证据，记录保留了当时的 status 和 blob；三个问题涉及的五个产品文件均与审查基线一致。最终通过结论来自固定提交的独立快照，不依赖其他未提交改动。

后续提交仅包含指南、计划、报告、证据入口、原始结果和换行保护。交付时核对其 `src/`、`tests/` 与被测提交一致；最终文档提交可通过 `git log -1 --format=%H -- docs/testing/2026-10-21-tcp-device-final-closure.md` 定位，完整交付 SHA 与远端核验结果记录在交付答复和本地 Git 回执中。

## 2. 三项修复与最终行为

| 项目 | 根因与修改位置 | 修复结果与验证 |
| --- | --- | --- |
| 日志截断 | `DeviceDiagnostics.append()` 在字段 JSON 超过 1024 字节时只保留部分身份字段，丢失 TCP 通道、字节和连接信息，并把元数据折叠统一写成字节截断。修改 `apps/runtime/devices/diagnostics.py` 和 `ui/communication_panel.py` | 保留 channelId、bytes、dataHex、服务/连接代次、请求、source、发送回执及既有冻结归属。字节预览最多 1024 个原始字节；`truncated` 表示字节预览截断，新增 `fieldsTruncated` 表示元数据折叠。512 字节仍可按 a 筛选并显示完整 HEX/UTF-8；1025 字节保留原始计数及准确截断提示。每设备 512 条/1 MiB、工程 8192 条/16 MiB、128 条分页和 1000 字节元数据 preview 上限不变。保留结构化字段后整条 JSON 可以超过 1 KiB；原始字节预览与缓存仍有界 |
| 在线 Server 草稿测试 | `DeviceManager.testConnection()` 找到物理 owner 后仍要求整个 connection 相同，通道显示名称变化也被拒绝。修改 `apps/runtime/devices/manager.py` 和 `devices/tcp.py` | 纯校验后的候选和 owner 均为 TCP Server 且物理端点一致时，可以借用原后端测试。其他设备保留连接一致性保护；receive_once 不能因 listener identity 相同而借用 Server。原 definition、driver、executor、lifecycle、generation、listener 和后台线程保持不变。新空闲端点的临时资源完整清理；原监听仍可接入。结果明确不应用草稿通道规则、不验证外部客户端 |
| 连接字段焦点 | `TcpConnectionForm.validationMessage()` 将所有 TCP 模型错误统一定位 channels。修改 `apps/designer/ui/tcp_device_form.py` | 从 Pydantic `ValidationError.errors()[0].loc` 选择现有 focusField；bindAddress 定位地址，channels 路径及当前通道唯一性根错误定位表格。通过实际创建对话框 accept 外层校验验证：非法/组播地址被拒绝后连接页可见、最终焦点在地址输入框；输入保留、不提交草稿、不调用 Runtime。非法通道和重复路由仍定位通道表 |

新增定向测试复用现有文件：`test_tcp_closure_ui.py`、`test_device_plan_completion.py`、`test_device_definition_feedback.py`。日志测试从真实 DeviceManager 工厂建立的 TCP 后端调用 `_record`，经实际 append/query 进入原生 Qt 的 DeviceDiagnosticsView，不手工构造修复后的日志行；分别断言 HEX 和 UTF-8 的准确内容，并在真实异步 JSONL writer 关闭后检查 RX 事件只持久化一次。

现有 `testF09ProjectionPreservesFrozenIdentityAndOriginalEvent` 补入 tcpTransaction，与 tcpReceipt 一起检查大小字段折叠后的完整保留。现有 16 MiB 正式 Job 链补充真实 TX 诊断断言：原始字节计数、发送回执、Job、通道、连接/服务代次及 1024 字节预览一致。没有增加一套新的大报文场景。

节点投影仍使用 `write=False`；原 JSONL 单次持久化路径保持。没有更改实际 TCP 数据、连接生命周期、protobuf、驱动 API、设备描述符版本或 Worker 协议。

## 3. 文档与当前计划状态

- [TCP 指南](../guides/project-tcp-service.md)修正创建说明和算子表格：session 静态选择 Server/channelId；session_send 只绑定 Server，通道来自输入 session。补充草稿端点测试和两种日志截断提示的含义。
- [设备使用指南](../guides/device-dispatch-online-configuration.md)补入 §3.3 的实际硬触发边界：connect 不代表采集就绪；首次 capture 才执行当前准备；同步流程不能先等待触发再靠后续 PLC 节点产生触发；软件控制需要实际就绪与 PLC 握手。普通取图、处理判定、PLC 写结果继续用流程依赖，不新增 arm/trigger/wait。
- v1.5 首页和 §12.1 记录已实现的主体、三轮修复及当前证据。原始 `59f3f63b` 审查 SHA、设计要求和固定源码依据保留，原“待实施”叙述明确标为设计时状态，未把未执行的产品/现场验收写成通过。

## 4. 实际验证、首次失败与证据

环境：Windows `10.0.26200`，`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python **3.10.21**、PySide2 **5.15.2.1**、Qt **5.15.2**，`QT_QPA_PLATFORM=windows`、`PYTHONIOENCODING=utf-8`。PYTHONPATH 指向固定快照，`EMO_TEST_SOURCE_SHA` 指向 `325f60e`。所有工程、相机/PLC 模拟及 TCP 端点均为临时目录和本机回环。

本轮实际最终验证命令如下；再次执行时必须换用新的输出目录，原始目录保留不覆盖：

```powershell
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  docs/evidence/2026-10-21-tcp-device-final-closure/run_validation.py `
  --repo 'D:/jsdfhasuh/documents/my_project/emo_master' `
  --source-sha 325f60e4fe7b9210eaf50abddc13d0b2086e8bcb `
  --output-dir docs/evidence/2026-10-21-tcp-device-final-closure/final
```

首次反例及修复后定向命令使用同一入口，加 `--working-tree --source-sha 31b5de4a604e4facdf636ad39795da0869d908d4`，分别输出到 first-counterexamples、corrected-counterexamples、fixed-counterexamples。该 SHA 表示共享工作区的基准提交；修复后工作区批次明确为 dirty，不冒充基线代码通过。[counterexample-runs.json](../evidence/2026-10-21-tcp-device-final-closure/counterexample-runs.json)及各目录 validation.json 保存完整 argv、工作目录、文件 blob、原始输出和退出码。

| 批次 | 本轮实际结果 | 解释与原始证据 |
| --- | --- | --- |
| 首次反例 | **5 failed、5 passed，4.50s；pytest exit 1** | 两个报文长度、Server 改名和非法地址复现产品问题；另一失败来自测试向 QSpinBox 调用不存在的 setText，不作为产品缺陷。原始日志/XML 保存在 first-counterexamples |
| 修正夹具后的基线反例 | **5 failed、5 passed，3.17s；pytest exit 1** | 去除不可输入的端口夹具，改为合法可输入但不允许监听的组播地址。五个失败全部对应本轮三个缺口；产品文件仍为基线 blob。见 corrected-counterexamples |
| 修复后定向 | **10 passed，2.96s；pytest exit 0** | 原生 Qt、32/512/1025 字节日志、在线 owner/临时清理、非 TCP 保护和外层焦点；见 fixed-counterexamples |
| 最终 A1—A6 与既有补充 | **50 passed，78.49s；exit 0** | 固定提交独立快照，沿用 CHAIN 的五个文件；[chain.log](../evidence/2026-10-21-tcp-device-final-closure/final/chain.log)及 chain.xml |
| 最终受影响回归 | **219 passed、2 deselected，115.33s；exit 0** | 上轮 REGRESSION 的 12 个文件，加设备连接与表单反馈两个文件；[regression.log](../evidence/2026-10-21-tcp-device-final-closure/final/regression.log)及 regression.xml |
| 静态及协议 | **全部 exit 0** | 10 个源码/测试 Python 文件 Ruff、5 个源码文件 mypy、`scripts/gen_proto.py --check`、`git diff --check 31b5de4 325f60e`；见 final 中同名日志。mypy 使用项目现有配置，对未标注函数体的默认限制保留 |

独立证据入口自身另执行 Ruff。精确 pytest 文件列表、完整静态检查 argv 和运行环境以 final/validation.json 为准；运行器中的 CHAIN/REGRESSION 与原证据脚本分离，旧脚本和目录未执行写入。清单见 [SHA256SUMS.txt](../evidence/2026-10-21-tcp-device-final-closure/SHA256SUMS.txt)，不自包含。

两项 deselected 仍仅来自 `testUpgradedDraftResourceFailureCanBeFixedAndRevalidated` 的两个旧 2.1→2.6 迁移参数化分支。范围和原因沿用第二轮记录，原测试不改、不计为通过，没有新增排除项。此前 47 passed、196 passed 等均为历史证据，未作为本轮通过数。

| 连续链/既有行为 | 本轮覆盖证据 |
| --- | --- |
| A1 | 同一 2.7 工程创建、表单、绑定、保存重开、首次纯校验/显式写入、已发布后另存为；检查资源和 Runtime 后端 |
| A2 | 同工程模拟相机→处理/判定→脚本 SLMP PLC，检查实际结果和各设备独立线程 |
| A3 | 三类 TCP、四个内置算子和第三方算子正式 Job，连续接收、16 MiB、会话/回执、失效会话；新增真实大报文 TX 诊断内容断言 |
| A4 | 单次等待 Stop、维护/恢复、活动与配置更新、新 Server 和已有通道的选择性发布、更新后运行、COMMITTED 后连接失败 |
| A5 | 同一工程设备包、流程包、运行包在新目录导入后正式运行；精确依赖缺失与版本提示。实际导入路径/Job/设备日志见 acceptance-results.json |
| A6 | 第三方持续服务、业务动作/管理来源、状态与设备日志、关闭重开窗口、合法六秒动作预算及取消退出 |
| R01 | `testConsumerDeviceLogsKeepRetainedCycleIdentityAndPersistOnce[False/True]` 在最终回归中通过：真实 spawned Dispatch、冻结设备身份与历史 generation、成功/失败投影、原始 JSONL 唯一持久化及资源回收；原始日志保留在 final/dispatch |
| R02 | 选择性通道删除用例在最终链中通过：保留工程/流程/节点/slot/selector 定位、草稿与选择，补选改绑后正式提交 |
| R03 | 0/1/2 owner、HTML 转义与 64 owner 有界摘要在最终链中通过，服务并发权威状态及退出清理用例同步通过 |

主工程及包副本、截图、设备日志和导入后的实际 Job 见 [final/acceptance/acceptance-results.json](../evidence/2026-10-21-tcp-device-final-closure/final/acceptance/acceptance-results.json)与 final/artifacts.json。保留副本没有重写结果内的原始绝对路径；源码 SHA 与实际 importPath 均在结果内。

## 5. 未执行项与用户复验

| 项目 | 状态与具体原因 |
| --- | --- |
| 真实相机/PLC/TCP 硬件 | **NOT_RUN**。本轮限定模拟与 localhost，没有提供现场联调条件，没有访问真实设备或修改物理网络 |
| 人工完整 UI | **NOT_RUN**。本轮运行原生 Windows Qt 自动化并断言最终焦点、控件文本与受控弹窗，没有操作人员完整手工走查 |
| 冻结 EXE/安装包 | **NOT_RUN**。未构建或安装产品；运行中的旧 EXE 不会自动加载源码修复 |
| Linux/跨机器 | **NOT_RUN**。本轮准备和执行的是 Windows 本机快照及回环工程，没有准备对应被测部署或运行这些批次；不把旧环境库存当作本轮检查 |
| 旧工程迁移、压力、强杀、多故障矩阵 | 不在本轮范围；保留两个旧迁移排除和此前深层 Windows 路径限制，不新增平台或故障专项 |

原生 Qt 结果属于软件自动化证据；直接调用 `_record` 的日志反例属于真实生产构造/投影/显示链验证，不能冒充真实网线报文验收。在线端点测试确实使用本机 socket/listener；A1—A6 使用真实 Runtime 与正式 spawned Job 配合模拟端。

用户复验步骤：

1. 从源码启动 Designer，打开临时 2.7 工程，保存并另存到新目录；核对资源与 Runtime 状态。
2. 创建 TCP Server 和通道 a，给 session 节点绑定设备/channelId，给 session_send 节点只绑定设备并连接 session 输入；显式写入 Runtime 后运行。
3. 在线 Server 仅改通道显示名称，点击“测试当前连接草稿”：应成功提示仅测试端点，原监听继续可用，在线通道名称仍为已发布值；通过 Runtime 明确写入后才生效。
4. 将监听地址改为 127.0.0.999 后创建/校验：应拒绝、显示连接页、聚焦地址并保留输入；通道地址错误仍聚焦通道表。
5. 向 Server 发送 512 字节普通文本，按通道 a 筛选该记录；HEX/UTF-8 应显示准确完整预览。超过 1024 字节时显示原始计数和字节截断；部分字段截断提示与字节截断分别显示。
6. 运行/停止任务，查看多 owner 状态及历史设备日志；导出设备包、流程包和运行包，在新目录导入并按既有入口运行。硬件、冻结产品和人工现场结果单独记录。
