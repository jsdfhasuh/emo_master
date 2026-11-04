# 日志体系整合实施与验证

日期：2026-11-03（Asia/Shanghai）。实施依据计划 v3.4；完成状态更新为
[v3.5 仓库副本](../../plans/2026-11-03-logging-consolidation-implementation-plan.md)，
外部原件为 `D:\jsdfhasuh\documents\md\2026-11-03-logging-consolidation-implementation-plan.md`。

## 当前交接状态

S1—S6 实施及 F01—F08 修复已完成并通过独立复审。用户已授权提交、推送当前分支
`codex/logging-consolidation-20261103`，并由 Dot 继续完整可见桌面点击走查及相关 CI/编译修复。
操作清单及入口见 [Dot 交接](dot-handoff.md)。实际提交身份及远端状态以 Git/PR 为准，
不能再仅检出下面的实施基线来取得本轮实现。

下面各轮报告、`final-workspace.json`、原始失败日志及产物 `sourceDirty=true` 均是提交前的验证记录，
原样保留；其中“未提交/本轮不提交”描述的是当时阶段，不是后续交接的限制。
本次提交准备仅更新状态和交接文档，没有更改复审后的业务代码或重记测试结果。
完整可见人工桌面走查、SDK 样例实际 Designer 打开及冻结 EXE 验证继续待补，不能用已有自动化代替。

提交准备新增核对：待提交实现路径与 `final-workspace.json` 一致，另加入计划、说明图和 Dot 交接。
将原先未跟踪的原始测试输出纳入索引后，全量 `git diff --cached --check` 报告这些 `.txt` 证据中的
行尾空格和结尾空行；保留原始输出，不将该全量检查记为通过。排除原始 `.txt` 后，源码、测试、
配置和说明文档的同项检查通过；没有为空白格式重写历史失败日志。

## 提交前实施与修复记录

**当前状态（独立审查修复后）：F01—F08 已全部修复并完成针对性回归及相关集成验证。**
详见 [独立审查修复总记录](review-fixes.md)，附 [桌面](review-desktop.md)、
[前端/浏览器](review-frontend.md)、[Web 级别解析](review-web-backend.md) 分项记录。
Runtime 最终 64 passed；Windows Qt 相关组 126 passed、最终 operational 17 passed、导出/日志视图
42 passed；Web 后端 32 passed / 69 subtests；前端 81 passed，组件/effect 6 passed；
真实 HTTP/Chromium 与最终 Web wheel 独立安装通过。测试组有交叠，不相加。
原 S1—S6 报告和以下首轮结果保留；独立审查发现的问题及本轮修正以新记录为准。
该轮记录时 HEAD 仍是固定基线，所有实现和修复当时为未提交修改。

## 源码基线与工作区

- 开始时 `main@2b0033a4a26b5213d0b3ae627d7ca19508b9dba7`，工作区干净。
- `git fetch origin` 后 `origin/main` 为 `940994b3420b47ff0add8766ccce5a4f2686a02a`。
- 执行 `git switch -c codex/logging-consolidation-20261103 940994b3420b47ff0add8766ccce5a4f2686a02a`。
- 实施基线和最终 HEAD 均为 `940994b3420b47ff0add8766ccce5a4f2686a02a`；功能来自其上的未提交修改。
- C: 仓库入口对应实际 `D:\jsdfhasuh\documents\my_project\emo_master`，同一工作区。
- 本轮不提交、推送、合并或发布，不用基线 HEAD 冒充功能实现提交。

## 分批记录

S1—S6 已全部实现；以下保留分批验证和失败修正证据。最终交付状态及未执行的验收层次见文末。

| 批次 | 主要文件与行为 | 详细记录 |
| --- | --- | --- |
| S1（完成） | devices/manager.py、events/jsonl_writer.py：真实调用上下文、异常已有 outcome、设备完成紧凑 payload | [设备与导出](devices-export.md) |
| S2（完成） | diagnostic_logging.py、事件模型/EventStore/SQLite/006、Worker/Supervisor、设备连接发布责任 | 下列主链测试及 [设备与导出](devices-export.md) |
| S3（完成） | 三启动入口、app_log.py、Designer local_log/MainWindow、ProductionRuntime：初始化前 writer、启动实例及关闭归属 | [桌面记录](desktop.md) |
| S4（完成） | proto/生成代码、SQLite queryRuntimeLogs、Service QueryRuntimeLogs、aio sqlite 类别、SDK 1.4.0 | 主链测试、[协议与 Web](protocol-web.md) |
| S5（完成） | Designer/Runtime 本机/设备 UI、历史分页/固定范围迭代导出/取消、调试 payload | [桌面记录](desktop.md)、[设备与导出](devices-export.md) |
| S6（完成） | Web 查询/JSONL、网关 JSONL/admin、权限/会话/结构化错误、64 位 ID | [协议与 Web](protocol-web.md) |

## 主链自动验证

解释器 `D:\Conda\envs\emo_master\python.exe`（Python 3.10.21、grpcio 1.78.0、PySide2）。
命令从仓库根执行，下文 `python` 指该解释器。批次有交叠，不将条数相加作为独立总数。

| 测试组与命令后缀（`python -m pytest`） | 结果 | 日志 |
| --- | --- | --- |
| `tests/runtime/test_worker_event_batch_persistence.py tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_sqlite_store.py -q --disable-warnings --maxfail=5` | 33 passed | s2-initial.txt |
| `tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_worker_event_batch_persistence.py tests/runtime/test_jsonl_logging.py tests/runtime/test_device_operational_logs.py -q --disable-warnings --maxfail=5` | 初轮 3 failed / 57 passed；新迁移夹具重复建表、旧完整字典缺 metadata、新 TCP 夹具 port=0 违反约束，均已修正 | s2-s4-initial.txt |
| `tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_worker_event_batch_persistence.py tests/runtime/test_job_supervisor_contracts.py tests/runtime/test_operator_logging.py tests/runtime/test_presentation_log_stream.py tests/runtime/test_sqlite_store.py -q --disable-warnings --maxfail=5` | 70 passed | s2-s4.txt |
| `tests/runtime/test_runtime_startup_progress.py tests/runtime/test_startup_cancellation.py tests/runtime/test_operator_runtime_logs.py tests/runtime/test_jsonl_logging.py tests/runtime/test_device_operational_logs.py -q --disable-warnings --maxfail=5` | 48 passed | runtime-regression.txt |
| `tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_runtime_grpc_service.py tests/runtime/test_runtime_grpc_network.py tests/runtime/test_worker_event_batch_barriers.py tests/runtime/test_worker_heartbeat_finalization.py tests/runtime/test_heartbeat_liveness.py tests/runtime/test_worker_capture_diagnostics.py -q --disable-warnings --maxfail=5` | 初轮 1 failed / 94 passed：心跳完整字段集合需加入源 timestampMs，保留原身份断言并新增时间断言 | rpc-worker-regression.txt |
| `tests/runtime/test_heartbeat_liveness.py tests/runtime/test_runtime_persistence_contract.py -q --disable-warnings` | 34 passed，包含上述修正和最终字节预算实现 | rpc-worker-followup.txt |

`runtime-regression-initial.txt` 保留一次误引不存在测试文件的命令，未运行任何用例，不计通过。
查询通过真实 loopback unary RPC 核对元数据、全局包装、固定/空上界、跨旧工程、Job/设备/槽位关联、
时间/模块/级别/节点筛选、RESET_REQUIRED、字节截断游标及 EVENT_TOO_LARGE；另验证生产 AioRuntimeServer
新 RPC 与 capability。迁移仅用临时 v5 数据库，重复升级并重开，核对原身份和 payload。

## 调整与验收边界

- 沿用 EventStore/SQLite/异步 writer/设备聚合，仅新增小型映射、薄启动日志、只读查询和纯导出函数。
- 完整回复预算检查包含本页最终游标和 hasMore，避免上界含排除记录时最终游标字节增加导致超预算。
- 完整对象断言同步补充新字段，未删除或放松原有正确断言，初轮失败证据保留。
- SDK、环境、桌面/Web 走查与最终完成状态在子记录及本文件后续交付记录分别说明；已完成实现没有改变第 5 节范围。
- 不连接真实相机/PLC，不改现场工程、网络地址或凭据；模拟/loopback 不代表真实设备验收。

## 可见原生窗口检查

执行 `python docs/testing/2026-11-03-logging-consolidation/desktop_mock_probe.py --seconds 600`，
使用 `QT_QPA_PLATFORM=windows`，两台临时 RuntimeService 和模拟记录，不连接设备。
实际窗口标题“日志整合走查 · 临时模拟数据 · 无硬件”，通过 computer-use 选定该 Python 窗口、
激活并读取截图及可访问性树。已实际看到实时 node.log、`runtime / operator`、live-job、inspection、
locate、模拟 Runtime alpha 消息、范围选择器、历史/本机入口和 JSON 详情控件。
发现应用/模块默认列宽 88px 显示成 `runtime / ...`，局部调宽为 160px。

后续点击范围选择器时输入工具报告 `GetCursorPos failed: 拒绝访问。 (0x80070005)`；
刷新窗口后重试仍相同，停止发送原生输入。因此本次可见窗口的历史/详情/切源/导出点击走查未完成，
不能记成人工 UI 通过。上述交互另由实际创建 QWidget/QTest 事件的原生 Qt 自动化验证，结果见 desktop.md
及 devices-export.md。第一次以隐藏启动标志运行的探针没有可见窗口，未算作可见 UI 验证；随后改为直接
执行探针得到上述可见窗口。探针仅用于临时模拟验证，不属于产品启动入口。

## 协议和源码合同补验

- `python scripts/gen_proto.py --check`：通过，记录 proto-check.txt。
- `python -m pytest tests/core/presentation/test_panel_contract.py -q --disable-warnings`：55 passed，
  panel-contract-source-final.txt。首轮 panel-contract-source.txt 保留 1 failed / 54 passed，原版本断言
  固定 1.3.0，已按本轮明确 SDK 升级同步为 1.4.0。
- `python -m pytest tests/runtime/test_runtime_persistence_contract.py -q --disable-warnings`：16 passed，
  query-final.txt，补核当前连接实例与历史行产生实例的区别。
- 主链 14 个 Python 源/测试文件 `python -m ruff check ...`：All checks passed，root-ruff-initial.txt。
- `git diff --check`：exit 0，diff-check.txt；其中 Git 行尾转换 warning 不是空白错误。

## 首轮集成结果（独立审查前，保留原证据）

S1—S6 的实现及本轮必要自动验证已完成，没有遗留代码失败或需要用户决定的范围变化。
实现仍是上述固定基线上的未提交工作区修改，最终 HEAD 没有发生变化。
独立审查应读取当前工作区 diff、新增文件和本目录证据；仅检出 HEAD 不含本轮功能。

以下列出最终代表性结果，完整命令、分批输出和修正原因见三个子记录。各组有交叠，不相加为独立总数。

| 验证层次与范围 | 最终结果 | 记录 |
| --- | --- | --- |
| 元数据/批量/Worker 源时间/SQLite/算子/页面回归 | 70 passed；后续心跳与查询补验 34 passed | s2-s4.txt、rpc-worker-followup.txt |
| 查询 RPC、旧库升级及重开、关联筛选、上界/字节预算/超限/实例 | 16 passed，包含实际 loopback unary 和生产 aio 适配 | query-final.txt |
| JSONL、设备连接/恢复、TCP mock Job、三种导出与日志视图 | 90 passed | devices-export-test-03.txt |
| 完整文件范围、2300 条历史、运行中截止、取消和设备缓存 Qt 交互 | 42 passed | devices-export-ui-01.txt |
| 最终设备上下文/反馈与 Runtime Qt 日志视图 | 57 passed；生命周期/FIFO/取消另 30 passed | devices-export-final.txt、devices-lifecycle-regression.txt |
| Windows 原生 Qt：启动失败/重试目录/writer owner、历史/本机/详情/导出/取消/切换 | 57 passed；最终查询及注入 writer 提示 11 passed | desktop-native-11.txt、desktop-query-final-14.txt |
| 日志窗口/导出/工作流调试受影响回归 | 126 passed、1 skipped；mock 调试 payload 专项 2 passed、3 deselected | desktop-debug-10.txt、desktop-debug-payload-12.txt |
| 源码 panel 合同 | 55 passed | panel-contract-source-final.txt |
| Web 全量后端 | 150 passed、2 skipped、207 subtests passed | web-pytest.txt |
| Web 最终 App/RPC/manifest 定向组 | 31 passed、63 subtests passed；最后固定文件范围修改后的 App 15 passed | web-final-focused.txt、protocol-web.md |
| 前端测试及 TypeScript/Vite build | 77 passed；build 通过 | web-frontend-tests.txt、web-build.txt |
| 真实 FastAPI HTTP/WS + Chromium + mock Runtime | 通过：旧工程筛选、结构化详情、JSONL 下载、网关错误；已有登录/权限/控制/目标隔离/手机布局 | web-browser.txt、web-history.png、web-runtime-detail.png |
| 正式 SDK wheel + 独立 Web 执行器 + 实际软件 Runtime 子进程 | 4 tests，OK，无 skip；没有 PySide2/emo_master 导入 | web-owning-runtime-wheel-unittest.txt、devices-export.md |
| 最终 SDK/Web wheel 独立安装 | PASS：SDK 1.4.0、query/metadata/optional 上界 0、普通 Job/ABORTED/Stop、静态文件、CLI、无 Qt/主应用/模型导入 | web-wheel.txt |

唯一桌面跳过项为既有 `testDesignerConnectionRenewsWhilePausedBeyondLease`，要求显式启用
`EMO_DEBUG_STRESS=1` 的 65 秒租约专项，本轮未启用，不算通过。Web 全量的两个 opt-in
actual_runtime 跳过随后已单独启用，使用正式 wheel 和直接 unittest 执行成功；保留原 skip 输出。
初轮测试/环境/校验脚本失败及修正记录均保留，没有未处理的相关失败。

### 协议、SDK 和交付物

- SDK 与 Web 依赖、健康/导入提示、CI 版本断言同步为 1.4.0。
- 通过现有 `scripts/build_panel_contract.py` 生成
  `build/logging-consolidation-panel-contract-sdk-1.4.0/` 及对应 ZIP，39 文件；
  current vendored 合同与新 wheel、[1.4.0 handoff](../../handoffs/runtime-web-panel-v1/sdk-1.4.0/README.md)
  同步。旧版历史 handoff 未覆盖。产物诚实记录 `sourceSha` 为基线、`sourceDirty=true`。
- 样例模型、保存后重读、工作流编译与 normalCapture 通过；生成样例的实际 Designer 打开
  未执行，manifest 保留 `DesignerOpen=NOT_RUN`。
- 已同步已有 Runtime 事件、算子开发、插件注册及 panel 合同文档；未建立第二套日志规范。

### 必要局部调整

- 设备连接发布复用 lifecycle 与通信线程现有入口，区分同步最终结果与后台真实结果，避免重复计数。
- 新字段引起的完整对象/版本/启动目录断言同步更新，原设备归属、取消、只读请求、控制显示断言保留。
- 发现正常无 Job 时服务 writer 尚无文件，Runtime LogFeed 改为采用同次应用文件；没有新增兜底目录。
- 历史查询/导出接入既有 DisplayCallContext 取消；Web 复用原 sessionGeneration/401/Abort。
- Windows 当前 Starlette 的测试客户端要求 httpx2，Web test extras 补实际依赖，在独立 venv 验证。
  Vite 使用实际 D: 目录避免 junction 路径混合，没有修改前端构建架构。
- 网关固定范围读取追加局部文件身份/长度核对，常规轮转使选中范围缺失时明确失败，不报完整成功。
- 可见窗口发现应用/模块列过窄已调宽；工作流调试只格式化 node.log，控制日志保持既有显示。

### 最后轻量检查

所有子任务停止修改业务代码后再次执行：

- `python scripts/gen_proto.py --check`：exit 0，proto-check-final.txt。
- 使用 `git diff --name-only --diff-filter=ACMR` 与 `git ls-files --others --exclude-standard`
  获取当前新增/修改 Python，逐文件 `compile(Path(path).read_bytes(), path, 'exec', dont_inherit=True)`：
  **66 文件语法通过**，python-syntax-final.txt；没有导入或执行产品代码。
- 各分工受影响 Python 生产/测试文件 Ruff 通过，命令范围在对应子记录；没有把语法检查等同测试。
- `git diff --check`：exit 0，diff-check-final.txt；仅有 Git 行尾转换提示。
- 最终分支/HEAD/远端跟踪 SHA 和工作区清单保存于 final-workspace.json。

### 未完成的验收层次与已知限制

- 可见原生窗口已实际启动和检查；computer-use 鼠标输入两次被当前桌面拒绝（0x80070005），
  完整可见人工点击走查未完成。恢复桌面输入权限后可运行本目录 probe，人工复核历史/本机/详情、
  Runtime 切换及导出取消。相关交互已有 Windows QWidget/QTest 自动化，不能将其写成人工通过。
- Web 浏览器自动化完成；真实用户人工浏览器使用未执行。软件 Runtime 与 SDK 使用实际 gRPC，
  设备场景使用 mock/loopback；未做真实相机/PLC、跨机器 LAN 或现场验收。
- 正式协议/SDK/Web wheel 打包校验完成；未冻结 Designer/Runtime EXE、未部署或发布。
  生成样例实际 Designer 打开和上述既有可选租约专项保持未执行。
- npm ci 报告现有依赖一项 high advisory，本轮未升级无关依赖；记录在 protocol-web.md，
  不代表本轮日志改动引入的新缺陷。没有通过扩大测试、删除历史证据或弱化断言追求全绿。
- 没有访问真实相机/PLC、执行网络改址或修改现场工程/凭据；没有提交、推送、合并或发布。
