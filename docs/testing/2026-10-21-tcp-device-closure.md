# TCP 纳入统一设备管理：F01—F11 实施收尾记录

日期：2026-10-21，Asia/Shanghai。本记录追加于原 v1.5 实施报告之后，不覆盖历史验收资料。

本轮完成 F01—F11、共享客户端动作预算和三个界面遗漏的源码修复。交付范围为 Windows / Python 3.10 / PySide2 的模拟设备、回环网络、正式 spawned Job 和原生 Qt 自动化。最终快照结果见下文；Linux、真实设备、冻结产品、人工桌面走查及跨机器交付不能由这些结果替代。**不把 v1.5 标为所有环境和设备均已验收。**

## 1. 固定版本、后续差异与提交边界

| 项目 | 固定值 |
| --- | --- |
| 仓库 / 分支 | `jsdfhasuh/emo_master` / `agent/runtime-workflow-architecture-v1` |
| 计划 | [`2026-10-20-tcp-device-integration-plan-v1.md`](../plans/2026-10-20-tcp-device-integration-plan-v1.md)，v1.5，提交 `f47a9e584904e098f43c9431d461f093594ceb60` |
| 原计划基线 | `59f3f63b653d6c021ceb7c33700bdfcd3e7e4e6a` |
| 本轮起点 | `d307b9fbe828ea600e8586f8f72f71a9d721f5f0`，原 TCP 实施源码为 `ba779fe356e61997b1b776802aa06b363412d037` |
| 最终被测源码 SHA | **`24fcc827de18085f432fababa679acb0d382e940`** |
| 最终被测 Git tree | `4dd9d4dd3ebcb5dfcff1a4ddc110dca05c658319` |
| 独立源码目录 | `D:/jsdfhasuh/documents/my_project/emo_master/build/tcp-device-closure/final-24fcc827` |
| 实际导入 | 上述目录下 `src/emo_master/__init__.py`，由独立进程实际导入并断言，见 [`final-environment.log`](../evidence/2026-10-21-tcp-device-closure/final-environment.log) |
| 本报告及证据提交 | 独立的最后一笔 `docs(devices): record TCP closure verification and retained failures` 提交；最终 SHA 见本地 `.git/tcp-device-closure-delivery.json` 与本次交付答复。也可执行 `git log -1 --format=%H -- docs/testing/2026-10-21-tcp-device-closure.md` 解析该不可变提交 |

先读取适用根 `AGENTS.md`、v1.5 计划，并核对基线之后的历史：`59f3f63b → f47a9e5（计划）→ ba779fe（实现）→ d307b9f（原证据）`。本轮开始时受跟踪工作区干净、目标分支正确；本轮没有需要重复实现的其他后续提交。保留已有 DeviceManager、设备窗口、菜单、首次写入、COMMITTED 回执及包结构，只处理剩余链路缺口。固定历史、变更清单及 39 个源码/测试文件与快照的 Git blob 对比见 [`history-and-source-audit.json`](../evidence/2026-10-21-tcp-device-closure/history-and-source-audit.json)。

`git archive` 在本机 `core.autocrlf=true` 下导出 CRLF；第一次直接逐字节比较因此在 `project_controller.py` 停止。随后使用同一路径的 Git clean 转换核对，每个导出文件都与固定提交的 blob 一致；清单同时保留原 blob 和导出文件 SHA-256，没有因此修改源码。

| 顺序 | 本地源码提交 | 内容 |
| --- | --- | --- |
| 1 | `1915dbd11de47e726ef07d47ddeb69faf538c77d` | F01—F05：受管工程另存为、依赖闭包、第三方 TCP 桥、单次 Stop、监听 socket 选项；含直接反例 |
| 2 | `bae00c584b33f4ce654f0ec3739d8ec700106c94` | F06—F08：IPC 分类、服务健康准入、表单选择；含直接反例和受影响框架测试更新 |
| 3 | `24fcc827de18085f432fababa679acb0d382e940` | F09—F11、共享客户端超时及三个 UI 遗漏；补充 F02 原 API/正式选中发布、F03 子流程、日志身份与精确位置回归 |

只显式暂存本轮文件。未推送、合并、发版或部署；没有访问真实相机/PLC、现场工程或凭据，也没有进行物理网络改址。旧工程迁移、旧描述符/驱动兼容不属于本轮。

本轮复用 `ba779fe` 已形成的四阶段实现，按原第 11 节核对阶段出口：

| 原计划阶段 | 本次补齐和实测出口 |
| --- | --- |
| 1. 契约与新工程可用 | F01/F02/F08/F11：2.7 创建、表单、绑定、保存/另存重开和无 I/O 候选校验 |
| 2. 首次发布与实际执行 | F03—F07：同工程首次写入、三类 TCP/第三方 spawned Job、相机→处理→PLC、Stop/资源退出、业务与框架协议 |
| 3. 更新、两端管理和包流转 | F09—F11、超时/UI：选中发布后运行、权威状态/日志、两端管理及三类包在新目录实际运行 |
| 4. 定向验证与交付 | 固定源码快照的 43/271 两批通过、初次失败原文、环境/导入路径/哈希及本地提交；Linux 等限制单列 |

## 2. F01—F11 逐项结论

下表的“已修复”指固定源码中的实现与 Windows 软件验证；平台/设备限制逐项保留。没有把未执行测试归为通过，也没有因清单存在而改动正确代码。

| 项目 / 最终状态 | 原因、修改位置和最终行为 | 本轮验证依据 / 剩余限制 |
| --- | --- | --- |
| F01 **已修复** | [`project_controller.py`](../../src/emo_master/apps/designer/controllers/project_controller.py)：2.7 受管工程另存为不再进入旧通信释放接口。更新编辑器文件、资源根和保存状态；Runtime 文档、原后端、scope/generation、连接及 Job 保持原归属 | `testA1SavedCurrentProjectValidatesWithoutListeningThenInitialPublication`：已发布后另存新目录、资源字节一致、Runtime 不变、重开副本相对资源仍可用。首次失败在 `stage1-first-controlled.log` |
| F02 **已修复** | [`configuration.py`](../../src/emo_master/core/project/configuration.py)、[`project_configuration.py`](../../src/emo_master/apps/designer/ui/project_configuration.py)：先补齐新增整设备等传递引用，再由 `bindingSelectors` 推导数组路径；最后复用 `validateDeviceBinding`。新设备带完整定义，已有设备只带所需数组变化，依赖仍由原窗口确认 | `testF02NewServerReferenceIsAddedBeforeSelectorValidation`、声明驱动的 selector 参数化、`testF02NewServerAndBoundNodeSelectedPublicationRuns`、A4 已有 Server 新通道更新后正式 Job。补充在原 d307 源码用原 API 复现，排除仅是新参数签名导致测试失败 |
| F03 **已修复** | [`tcp_dependencies.py`](../../src/emo_master/core/project/tcp_dependencies.py)、执行器：从递归流程中实际绑定的设备类型推导 TCP 桥，与执行器共用类型判断；普通 Job、子流程、循环及 Dispatch 消费者沿用这一个发现规则，每个需要 TCP 的 Job 一条桥 | 非内置 ID `closure.tcp.client` 的合法第三方算子，直接/子流程两种正式 spawned Job 回环收发；循环/Dispatch 受影响框架回归。没有为所有结构排列组合新增验收矩阵 |
| F04 **已修复** | [`_communication_operators.py`](../../src/emo_master/plugins/builtins/_communication_operators.py)：client_once / receive_once 发生传输异常后检查真实 Job 取消令牌；主动 Stop 传播取消并保留事务诊断；业务失败仍失败，未知发送不重放 | `testF04StopSingleTransactionAbortsAndReleases[client_once/receive_once]`：各运行两次，在等待响应/接入时 Stop，断言 ABORTED、owner 实际退出和 socket 释放；A4 停 Job 不关常驻监听 |
| F05 **已修复（Linux 实测未执行）** | [`_tcp_transport.py`](../../src/emo_master/plugins/builtins/_tcp_transport.py)、[`tcp.py`](../../src/emo_master/apps/runtime/devices/tcp.py)：旧 helper 和单次接收实际执行/端点测试共用 bind 前选项；Windows 使用 `SO_EXCLUSIVEADDRUSE`，其他适用平台使用 `SO_REUSEADDR` | Windows 首轮连续接收反例原本就通过，未把它算作 Windows 已证实缺陷。最终连续两次接收与活动 listener 独占均通过；Linux 分支只有静态依据，**NOT_RUN**，不声称 Linux 问题已实测闭环 |
| F06 **已修复** | [`ipc.py`](../../src/emo_master/apps/runtime/devices/ipc.py)、Worker、周期/Dispatch 调用方：业务 `device_action` 与框架 `framework_operation` 明确分类；业务 execute 不再被操作名截获，未知种类拒绝；两端同步更新，不保留旧 Worker 分流 | `testF06BusinessReleaseAndFrameworkFrameReleaseHaveDifferentRoutes`：第三方业务 `release` 到达驱动，同时 FrameStore 框架 release 仍释放帧；周期、Dispatch 和 IPC 退出回归 |
| F07 **已修复** | [`service_executor.py`](../../src/emo_master/apps/runtime/devices/service_executor.py)、[`manager.py`](../../src/emo_master/apps/runtime/devices/manager.py)、replacement：ServiceExecutor 共用 DeviceExecutor 条件锁、quarantine 和 lastError，准入/登记原子完成；未知异常或 unhealthy 错误隔离，普通业务拒绝仍可重用 | unhealthy DeviceError / RuntimeError 两种直接反例，隔离后拒绝新业务、owner 退出后 recover、再次可用；`DeviceError(unhealthy=False)` 保持可用。未新增第二套健康账本 |
| F08 **已修复** | [`device_wizard.py`](../../src/emo_master/apps/designer/ui/device_wizard.py)、[`device_workspace.py`](../../src/emo_master/apps/designer/ui/device_workspace.py)：表单显式接收设备类型和 section，只有 `device.tcp.server` 的 connection 使用 TCP 专用表单 | `testF08OrdinaryChannelsUseGenericCreateEditAndSave`：第三方普通 `channels=[{id,name}]` 创建、编辑、保存；A1/既有组件回归 TCP 通道原子提交和字段反馈。节点向导内新建复用同一入口 |
| F09 **已修复** | 设备 IPC 周期准入、[`cycle_plan.py`](../../src/emo_master/apps/runtime/project_config/cycle_plan.py)、执行器与 [`diagnostics.py`](../../src/emo_master/apps/runtime/devices/diagnostics.py)：冻结设备 scope/ID/generation，嵌套周期恢复原快照；执行日志使用当次身份，生命周期日志保留创建身份；投影保留原事件时间、级别、phase/data、owner/cycle、planRevision 和执行 ID | `testF09ProjectionPreservesFrozenIdentityAndOriginalEvent` 小/大数据、`testF09LifecycleLoggerKeepsCreationIdentityWhileExecutionUsesNewSnapshot`、嵌套周期恢复、A6 原事件与投影逐字段对照。继续 `write=False`，节点事件只持久化一次；截断保留执行身份、级别和 TCP 关键回执；缓存/分页容量不变 |
| F10 **已修复** | ServiceOperation 记录 action/requestId 并提供既有 64 个并发上限内的 snapshot；Manager 合并 FIFO 和服务活动，RPC/两端 UI 读取同一权威状态。BUSY、完整 `activeOwners`、唯一 owner 才有 `activeOwner`；控制活动与后台 `driverState` 分开 | `testF10ConcurrentServiceOwnersAppearInAuthoritativeState`：实际挂起一个/两个动作，检查 BUSY、真实 requestId/action、双 owner、退出后 IDLE/空活动。修正为合法周期的反例也在原 d307 独立快照失败 |
| F11 **已修复** | [`compiler.py`](../../src/emo_master/core/workflow/compiler.py)、[`runtime_directory.py`](../../src/emo_master/core/project/runtime_directory.py)、[`runtime_package.py`](../../src/emo_master/core/project/runtime_package.py)、设备声明/资源/参数校验及原配置 RPC：源头构造 `ValidationIssue`，调用方只补上下文；定义路径含 deviceId，节点路径含 workflow/node、slot、selector/override/dynamic 字段。包先检查 manifest 依赖，再取注册表 | selector、port、IP、override、动态变量、首次 RPC→UI 的精确位置断言；候选未创建后端/执行 I/O；运行包缺算子/设备类型与版本不符、设备包缺类型/版本提示均包含 ID、所需及安装版本。不统一降为无位置 `E_DEVICE_RUNTIME_UNAVAILABLE`，消除裸 KeyError |

定位和提示测试集中于 [`test_device_feedback_closure.py`](../../tests/runtime/test_device_feedback_closure.py)；前三组反例在 [`test_tcp_device_closure.py`](../../tests/runtime/test_tcp_device_closure.py)、[`test_device_contract_closure.py`](../../tests/runtime/test_device_contract_closure.py)。原计划已有入口中正确的部分继续复用；本轮不存在需要以“后续提交已解决”冒充本轮修复的独立条目。

## 3. 共享超时和界面补齐

| 补充项 | 最终行为与证据 |
| --- | --- |
| 固定 5 秒动作超时 **已修复** | Runtime 的 `rpcBudgetsMs` 按控制/管理动作提供有限预算；TCP 复用纯预算函数，其他动作复用现有队列/连接/operationTimeout/recover 预算。共享客户端加 2 秒传输余量，Designer 委托传递同一 deadline/cancellation；目录、状态和日志查询仍用 5 秒读取时限。合法 6 秒动作在 embedded、gRPC、Designer 三条路径成功；embedded/gRPC 主动关闭取消后 owner 和周期实际退出。超时不重发 |
| 首次 TCP 分类定位 **已修复** | 快捷入口在元数据未到达时保留当前 scope 的分类定位意图；加载完成后应用；关闭/scope 改变清除，不自动选中第一台 Server。`testTcpShortcutAppliesAfterMetadataAndDoesNotChooseFirstServer` |
| 在线本地草稿状态 **已修复** | 状态读取成功、Runtime 目录无该本地设备时显示“尚未写入 Runtime”；读取失败显示实际读取错误。`testDraftAndConnectedServerSummaryUseSameSnapshot` |
| Server 连接摘要 **已修复** | 权威 driverState 有 Peer 时显示“监听中，已连接 N 个客户端”，无 Peer 才显示“等待客户端”；单次事务依据同一 BUSY 快照。组件断言有/无 Peer 两种文案 |

整条工程链保持原有四个边界：设备窗口修改草稿，文件保存写本地工程，Runtime 菜单显式发布，运行入口创建 Job。另存为不会替换在线后端；选中发布补齐必要设备/通道后才校验与提交；COMMITTED 后启动失败仍是已提交事实，原 requestId 给出连接结果，UI 去重反馈且不重复写入。导入复用包的禁用副本需补现场字段并明确启用；完整运行包保留启用/连接策略。所有包最终都经原编译、配置、设备管理和正式 Job 路径运行。

## 4. 最终固定源码验证

环境：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python **3.10.21**，PySide2 **5.15.2.1**，Qt **5.15.2**，Windows `10.0.26200`，`QT_QPA_PLATFORM=windows`，`PYTHONIOENCODING=utf-8`。`PYTHONPATH` 指向独立快照的 `src` 和快照根；`EMO_TEST_SOURCE_SHA` 固定为 `24fcc827…`，避免无 `.git` 快照误取父目录移动 HEAD。

运行器及精确 argv/cwd/时间/退出码见 [`run_final_validation.py`](../evidence/2026-10-21-tcp-device-closure/run_final_validation.py)、[`final-validation.json`](../evidence/2026-10-21-tcp-device-closure/final-validation.json) 和第一次快照的 [`snapshot-deep-validation.json`](../evidence/2026-10-21-tcp-device-closure/snapshot-deep-validation.json)。后者保留同一源码的静态检查及 Linux 环境探测；最终重跑只重复受目录问题影响的两个测试批次，不反复扩大测试范围。

实际最终重跑命令（从规范工作区执行，源码与工程数据目录分离）：

```powershell
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe `
  docs/evidence/2026-10-21-tcp-device-closure/run_final_validation.py `
  build/tcp-device-closure/final-24fcc827 `
  docs/evidence/2026-10-21-tcp-device-closure `
  24fcc827de18085f432fababa679acb0d382e940 `
  4dd9d4dd3ebcb5dfcff1a4ddc110dca05c658319 --tests-only
```

| 批次 | 实际命令 / 范围 | 最终结果 |
| --- | --- | --- |
| A1—A6 连续链与新增反例 | `python -m pytest -q -rA`：`test_tcp_device_integration.py`、`test_tcp_device_closure.py`、`test_device_contract_closure.py`、`test_device_feedback_closure.py`、`test_tcp_closure_ui.py`；另带独立 basetemp 和 JUnit 参数，完整 argv 在 JSON | **43 passed, 64.10s，exit 0**；[`final-chain.log`](../evidence/2026-10-21-tcp-device-closure/final-chain.log) / JUnit |
| 受影响回归 | 相同 pytest 参数：工程配置/设备模型/资源、运行目录/运行包、设备 executor/manager/lifecycle/IPC、FrameStore/Dispatch、嵌套周期、算子日志、首次写入、Designer 配置/字段反馈/设备表单/设备及流程包，共 18 个文件 | **271 passed, 80.20s，exit 0**；[`final-regression.log`](../evidence/2026-10-21-tcp-device-closure/final-regression.log) / JUnit |
| Ruff | `python -m ruff check` + 本轮 39 个受改 Python 源码/测试路径 | exit 0，All checks passed |
| mypy | `python -m mypy` + 本轮 32 个受改源码路径，使用仓库配置 | exit 0，no issues found in 32 source files；无类型函数体仍遵循项目现有 mypy 规则，不声称全部函数体严格检查 |
| 协议一致性 | `python scripts/gen_proto.py --check`，在同一快照执行 | exit 0 |
| 差异检查 | `git diff --check d307b9f 24fcc827de18085f432fababa679acb0d382e940` | exit 0 |

上述批次与前期回归有交叠，不相加为“总通过数”，也不是全仓测试。原报告的 **565 passed / 25 passed / 106 passed** 仅是历史证据，本报告不把它们算成本轮通过。

最终两批 JUnit 均为 0 failures、0 errors、0 skipped。两个 Qt 截图已打开核对，属于控件自动化留存，不计为人工桌面走查。

| 验收组 | 本轮实际覆盖 | 证据 |
| --- | --- | --- |
| A1 | 同一 2.7 工程创建 TCP、非法通道反馈/原子草稿、节点向导绑定、保存重开、候选无监听、首次写入、无隐式 Job、已发布后另存并重开副本 | 连续链 A1，Qt 截图、原工程/资源和保存副本 |
| A2 | 同一工程模拟相机→blob 处理/判定→计数→脚本 SLMP PLC 写结果，实际输出和设备独立队列 | 连续链 A2；不等同真实相机/PLC |
| A3 | 四个内置 TCP 算子、三类设备、双 Job 不同通道、第三方非内置 ID spawned Job、两次单次接收、16 MiB 桥、session/receipt 和过期 session 拒绝 | 连续链 A3/16 MiB/绑定及 F03/F05；真实 socket 回环 |
| A4 | 单次两类等待 Stop、维护/resume/recover、单/多服务活动、新 Server+绑定选中发布、已有 Server 新通道依赖、发布后再运行、COMMITTED 后端口冲突及去重反馈 | 连续链 A4、F02/F04/F10 和配置 UI 回归 |
| A5 | 从同一保存工程导出设备包、流程包、运行包，新目录导入、补现场字段/启用后正式 Job 运行；缺算子/设备类型/版本精确提示 | 连续链 A5；`acceptance-results.json` 记录三类实际导入目录、Job 和回执；F11 包依赖测试 |
| A6 | 第三方持续服务/重名动作/管理动作，普通 channels 表单、unhealthy 隔离和可恢复业务拒绝、日志完整身份、窗口关闭/重开不重启服务；6 秒动作与主动取消 | 连续链 A6、契约/日志/活动/超时测试；第三方诊断 JSON |

A1—A5 原始工程位于 `C:/Users/jsdfhasuh/AppData/Local/Temp/emotcp-dc_w6gso/final-chain/tcp-device-chain0/engineering/acceptance.emoproj`，留存副本见 [`acceptance/engineering/acceptance.emoproj`](../evidence/2026-10-21-tcp-device-closure/acceptance/engineering/acceptance.emoproj)。完整原始目录、快照导入路径、包路径、实际运行后的状态和回执见 [`acceptance-results.json`](../evidence/2026-10-21-tcp-device-closure/acceptance/acceptance-results.json)。

| 实际导入 | 正式运行 Job ID | 仓库证据 |
| --- | --- | --- |
| `device-import/imported.emoproj` | `3fc62633-e974-43ac-9b3c-22860c2397fc` | [`device-import/`](../evidence/2026-10-21-tcp-device-closure/acceptance/device-import/) |
| `workflow-import/imported.emoproj` | `78739fa7-e6ec-4de9-9874-7b8091f487df` | [`workflow-import/`](../evidence/2026-10-21-tcp-device-closure/acceptance/workflow-import/) |
| `runtime-import/imported.emoproj` | `06caf945-fc3c-46a0-9a5b-0ace02f6015f` | [`runtime-import/`](../evidence/2026-10-21-tcp-device-closure/acceptance/runtime-import/) |

## 5. 首次失败、修正和复跑历史

原始输出全部留在独立 [`2026-10-21-tcp-device-closure/`](../evidence/2026-10-21-tcp-device-closure/) 目录；没有删除失败后只展示成功。

| 原始记录 | 实际结果 | 解释 / 后续证据 |
| --- | --- | --- |
| `stage1-first.log` | `FFFFF.` 后中断，未产生完成统计 | Save As 的原生 warning 等待人机交互；核对并只停止本次 pytest 进程。后续测试控制 warning 返回值；不算通过 |
| `stage1-first-controlled.log` | 6 failed, 1 passed, 16.91s | F01/F02/F03/F04 直接反例；F02 新接口签名错误后来另用原 API 在 d307 复现实际依赖问题；F05 Windows 首轮即通过 |
| `stage1-fixed.log` | 41 passed, 40.51s | 第一组修复、连续链及相关工程/配置 UI 回归 |
| `stage2-first.log` | 1 failed, 4 errors | 第三方测试声明漏 actionSchema，不能把该夹具错误当作产品缺陷 |
| `stage2-first-corrected-fixture.log` | 4 failed, 1 passed, 1.40s | 补齐合法声明后，F06/F07/F08 仍直接失败 |
| `stage2-fixed.log` | 3 failed, 91 passed, 55.31s | recover 测试需等待原 owner 真实退休；周期 fake proxy 需同步新 framework 协议；未弱化断言 |
| `stage2-corrected-fixtures.log` | 15 passed, 3.33s | 修正相关夹具后通过；最终快照再次覆盖 |
| `stage3-first.log` | 11 failed, 1 passed, 26.40s | 日志、状态、定位、版本及超时；其中混有不合法周期和 manifest 夹具问题，另见基线确认 |
| `stage3-fixed-first.log` | 3 failed, 9 passed, 28.81s | 两个夹具问题与实际 gRPC `FutureCancelledError` 未规范反馈；修复取消反馈后回归 |
| `ui-first.log` / `ui-first-corrected-fixture.log` | 各 2 failed，3.23s / 4.10s | 第一批含无有效通道的 Server 夹具；改为合法定义后仍复现分类意图和草稿状态缺口 |
| `stage3-regression.log` | 3 failed, 85 passed, 87.67s | A1 窗口关闭了模块共享 RuntimeClient，导致 A4/A5 使用已关闭客户端；改为各自拥有的客户端。另一个是旧 2.5 示例既有失败 |
| `baseline-confirmation.log` | 4 failed, 4.73s | d307 原源码独立快照：F02 原 API、F10 合法周期、F11 算子版本提示均仍失败；旧示例也失败，确认与本轮修复无关 |
| `stage3-regression-corrected.log` | 76 passed, 88.14s | 修正拥有关系及排除本轮明确不支持的旧示例后，通过当前工程相关回归 |
| `chain-complete.log` / `locations-regression.log` | 53 passed, 66.68s / 60 passed, 26.77s | 扩展连续链、创建身份日志及精确 override/dynamic/IP 字段位置；属于提交前候选批次，不冒充最终 SHA 验证 |
| `ruff-first.log` | 6 个检查错误 | pytest 夹具导入声明的 F401/F811 等问题，已明确标注并修正；最终 scoped Ruff 通过 |
| `mypy-first.log` | 18 errors in 1 file | 误把测试文件纳入源码 mypy 时触及未改 `runtime_test_utils.py` 的现有类型问题；保留失败。最终仅对计划要求的受改源码执行项目 mypy 检查 |
| `snapshot-deep-chain.log` / `snapshot-deep-regression.log` | 1 failed, 42 passed, 140.51s / 9 failed, 262 passed, 183.01s | 第一次独立快照把临时工程也放进深层快照目录，资源缓存 rename 返回 Windows `WinError 3`，引发 A5、设备资源和首次写入失败。保留完整输出/JUnit/argv；将测试数据移到系统临时目录，源码、环境、测试文件、断言不变后复跑。最终结果见第 4 节；深层路径仍作为已观察限制保留 |

旧 2.5 示例 `tests/core/project/test_tcp_delivery.py::testExampleHasRealSessionToCalculationDependency` 在 d307 原源码同样因旧节点未声明设备槽位失败。该测试不在当前 2.7 工程验收范围，未修旧工程、未改断言、未将其记为通过。其他受影响新工程包路径实际执行。

## 6. 未执行项与限制

| 项目 | 状态与具体原因 |
| --- | --- |
| Linux F05 连续回环/独占 | **NOT_RUN**。`wsl --list --quiet` 只有 docker-desktop，没有可用用户 Linux 发行版；`docker info --format '{{.OSType}}'` 因 `dockerDesktopLinuxEngine` pipe 不存在失败。两个原始输出单独保留。SO_REUSEADDR 分支静态阅读不能替代 Linux 产品验证 |
| 真实相机/PLC/TCP 硬件联调 | **NOT_RUN**。本轮只授权模拟设备/回环临时工程，无现场硬件访问；A2 是软件设备与脚本 SLMP 端 |
| 人工 Windows UI 完整走查 | **NOT_RUN**。执行的是 windows 平台原生 Qt 控件自动化，确认/警告部分由测试控制返回；不是人工交互验收 |
| 冻结 EXE/安装包 | **NOT_RUN**。本轮是源码和本地提交，未构建产品；现有已运行 EXE 不会自动加载本轮源码 |
| 跨机器 / 独立远程文件系统 | **NOT_RUN**。gRPC 是本机回环，不能证明跨机器资源安装或现场网络。远端空 Runtime 仍须先在执行端安装工程/运行包 |
| 深层 Windows 临时目录 | 第一次最终快照实际观察到资源复制路径错误，修正测试目录后验证当前正常链。未新增全平台长路径改造；不得据此声称任意深度工程路径均通过 |
| 全仓测试、压力、强杀、全平台故障矩阵 | 未执行；按 AGENTS.md 与本轮授权只验证受影响正常路径和直接反例，不作为本轮前置门槛 |
| 旧工程/旧驱动兼容 | 不纳入本轮；保留已有失败和原文件，不把兼容性排除项写成通过 |

## 7. 用户操作复验

1. 使用本轮源码和项目 Python/Qt 环境启动 Designer，打开/新建 2.7 临时工程。创建 TCP Server、通道及绑定节点，保存并重开；非法字段应显示具体页签/字段位置。
2. 从 Runtime 菜单校验候选，确认尚未监听；显式写入后按策略启动。对已发布工程执行另存为，再打开副本，确认资源完整、Runtime 原后端与运行归属不变。
3. 从“通信服务”快捷入口首次打开设备窗口，应定位 TCP 分类，且不自动选择第一台 Server。新建但尚未发布的设备显示“尚未写入 Runtime”。
4. 新增 Server 和绑定节点，只选择相关节点发布，在原依赖确认窗口接受必要设备；已有 Server 新通道也只补所需依赖。运行检查回环收发与 session/receipt；单次客户端等待响应、单次接收等待接入时分别 Stop，终态应为 ABORTED，随后可再次运行。
5. 查看在线状态、owner 活动与设备日志；连接 Peer 后主摘要显示实际客户端数量。维护/resume/recover 按能力可用，业务 owner 退出后清理；关闭观察窗口不停止 Runtime 服务。合法 6 秒管理动作应正常结束，主动取消不自动补执行。
6. 从同一保存工程导出设备包、流程包和完整运行包，在三个新目录导入。复用包补模拟现场字段并启用设备，运行包按保留策略运行；每个工程均从正式 Job 入口执行。缺依赖时应显示准确 ID、要求版本及当前安装版本。

原始证据以证据目录中的 [`SHA256SUMS.txt`](../evidence/2026-10-21-tcp-device-closure/SHA256SUMS.txt) 校验。最终文档 SHA 不能作为字面常量包含在生成它自身的提交中；本地交付回执在文档提交完成后写入 `.git/tcp-device-closure-delivery.json`，同时绑定被测源码 SHA/tree、文档提交 SHA 及证据清单 SHA-256，最终答复给出对应值。
