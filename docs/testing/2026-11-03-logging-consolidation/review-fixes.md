# 日志整合独立审查修复

日期：2026-11-03（Asia/Shanghai）。依据用户独立审查 F01—F08、实施计划 v3.4 与仓库 AGENTS.md。
本记录追加在原 S1—S6 实现之后，原报告和原始测试日志均保留。本轮开始时已重新阅读审查附件、
AGENTS.md、计划及交付 README，核对分支、HEAD、工作区和相关源码，没有从基线重建或清理工作区。

- 分支：`codex/logging-consolidation-20261103`。
- 实现基线及开始 HEAD：`940994b3420b47ff0add8766ccce5a4f2686a02a`。
- 实现位于其上的未提交工作区；C: 入口解析到 `D:\jsdfhasuh\documents\my_project\emo_master`。
- 本轮不提交、推送、合并或发布；没有访问真实相机/PLC、现场工程或凭据，没有改网络地址。
- F01—F08 已全部修复并完成必要针对性及集成验证；没有未处理的相关功能失败或实现阻塞。

## 完成矩阵

| 审查项 | 当前状态与证明 |
| --- | --- |
| F01 | 完成：A 成功/B 失败后翻页及完整导出仍属 A；B 成功重试从新起点开始。review-desktop-focused.txt / review-desktop-final.txt |
| F02 | 完成：同一 reader 的实例/游标成对传给新 reader；同实例接续、新实例 RESET 清理旧显示。review-desktop-focused.txt |
| F03 | 完成：真实组件 effect 及 HTTP/Chromium 均覆盖两种异步返回顺序和真正实例变化。review-frontend-focused.txt / review-frontend-browser.txt |
| F04 | 完成：JSONL 和实际 QueryRuntimeLogs RPC 均保留六字段，原数据库 payload 不变，大 outputs 仍剔除。review-runtime-regression-final.txt |
| F05 | 完成：真实 UNIMPLEMENTED 后恢复单 Job；新服务单一来源；短 Job 首段和旧/新/旧切换均有回归。review-desktop-focused.txt |
| F06 | 完成：当前映射/006 一致，临时旧库升级及重开与当前写入通过，正式 dispatch 文件路径也已覆盖。review-runtime-regression-final.txt |
| F07 | 完成：紧凑/空格/空项/repeated 参数在查询及 JSONL 导出均等价。review-web-backend-integration.txt |
| F08 | 完成：实时显示/缓存 1000 条、游标继续推进；历史及完整导出不裁剪。review-frontend-focused.txt / review-frontend-browser-observations.json |

## F04：完成事件保留已有调度关联

根因：Worker 的 `_publisher()` 已将 runGroupId、dispatchId、deliveryId、invocationId、
targetSlotId、documentRevision 注入 payload，但 `_projectEvent()` 的完成事件白名单遗漏六项。
QueryRuntimeLogs 复用该只读投影，因此 SQLite 原 payload 正确，JSONL 和新查询/导出丢字段。

最小修复：`src/emo_master/apps/runtime/events/jsonl_writer.py` 在既有保留元组追加这六个标量字段。
没有推测或新增身份；没有修改执行结果或恢复大 outputs。

回归：扩展 `testCompletedEventProjectionRemovesOutputsButKeepsOperationalSummary`，通过实际 writer
写文件核对六项；扩展 `testQueryRuntimeLogsRpcByteBudgetAndProjectionKeepCursor`，通过真实 loopback
RPC 核对同样字段，并将数据库 payload 断言加强为与完整原对象相等。两个测试均参数化 node.completed
和正式 dispatch.invocation.completed，保留原字节预算、截断游标、单条超限等断言。

## F06：调度事件分类与正式文件路径

根因：实际生产者使用 `dispatch.invocation.failed/completed`，纯映射与 006 SQL 均没有 dispatch.*，
新写入和历史升级都得到 application。修改 `core/contracts/diagnostic_logging.py` 与
`context/migrations/006_logging_metadata.sql`，统一归为 execution。006 是本轮尚未发布的迁移，
直接修正其映射；没有新增历史兼容框架或改写现场数据库。

扩展原临时 v5 迁移/重复初始化/重开用例，加入两个真实事件类型，继续检查原 sequence、全局 ID、
payload、originApp 与空历史实例；另实际使用 EventStore 写当前记录，核对持久化分类及当前实例。

核对生产调用链还发现同一 `dispatch.*` 被 JSONL writer 的既有 operational 白名单拒收。
这会使仅修分类后依旧没有正式调度文件记录，属于同一链路直接缺口；局部将 dispatch.* 纳入该白名单。
参数化原 writer 顺序/Unicode 用例覆盖失败事件，完成事件覆盖六项关联与 execution 类别。
没有纳入心跳或扩大为所有事件落盘。

## Runtime 修复验证

解释器：`D:\Conda\envs\emo_master\python.exe`，所有数据均为临时数据库/文件，RPC 为 loopback。

```powershell
python -m pytest tests/runtime/test_jsonl_logging.py tests/runtime/test_runtime_persistence_contract.py -k 'CompletedEventProjection or QueryRuntimeLogsRpcByteBudget or LoggingMetadataMigration' -q --tb=short
```

- 修复前 `review-runtime-before.txt`：3 failed、26 deselected，分别复现六字段丢失和 dispatch 分类错误。
- 初次修复后 `review-runtime-focused.txt`：3 passed、26 deselected。

```powershell
python -m pytest tests/runtime/test_jsonl_logging.py -k 'PersistsUnicodeContext or CompletedEventProjection' -q --tb=short
```

- 增补正式 dispatch 文件路径后、修白名单前 `review-dispatch-writer-before.txt`：
  2 failed、2 passed、11 deselected，确认 enqueue 拒收失败事件和完成文件缺失。失败证据保留。

```powershell
python -m pytest tests/runtime/test_runtime_persistence_contract.py tests/runtime/test_device_operational_logs.py tests/runtime/test_jsonl_logging.py tests/runtime/test_worker_event_batch_persistence.py -q --tb=short
python -m ruff check src/emo_master/apps/runtime/events/jsonl_writer.py src/emo_master/core/contracts/diagnostic_logging.py tests/runtime/test_jsonl_logging.py tests/runtime/test_runtime_persistence_contract.py
```

- `review-runtime-regression.txt`：第一轮 61 passed，发生在增补 dispatch writer 路径之前，保留该阶段证据。
- `review-runtime-regression-final.txt`：最终 **64 passed，9.32s，无 skip**，含新增反例、设备、JSONL、
  查询/迁移及批量持久化相关回归。
- `review-runtime-ruff.txt`：All checks passed。

F04/F06 已完成，没有本分工未处理失败。

## F07：Web 级别输入空白

根因：`web_panel/backend/emo_web_panel/app.py::log_query()` 直接按逗号拆分，没有逐项去空格，
`ERROR, INFO` 的第二项为 ` INFO`，查询和复用该解析的 JSONL 导出会漏掉 INFO 记录。
最小修改只做每项 strip 后去空项，保留现有大小写约定、错误、分页、权限和会话语义。

现有 `web_panel/tests/test_app.py` 增加回归，对查询与导出均比较紧凑输入、空格、空项与重复 levels
参数；基准必须存在实际日志，避免两个空结果造成假通过。

使用已有独立 Web venv，未修改主环境依赖：

```powershell
$env:PYTHONPATH='web_panel/backend'
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_app.py -k runtime_log_level_spacing -q
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_app.py web_panel/tests/test_runtime_contract.py web_panel/tests/test_contract_manifest.py -q
```

- `review-web-backend-before.txt`：修复前 4 failed，保留反例证据。
- `review-web-backend-after.txt`：1 passed、6 subtests passed。
- `review-web-backend-integration.txt`：**32 passed、69 subtests passed**；含既有权限、401/CSRF、
  查询/错误、JSONL、RPC 和 manifest 回归。
- 语法、未定义名字检查和 diff 空白检查通过；全规则 Ruff 另发现原文件已有 164 个单行风格/unused
  诊断，未做无关整文件格式重写，也未将此检查写成通过。一次误拼测试文件路径没有执行用例，
  记录保留，随后已按正确路径重跑。

完整命令和边界见 [Web 后端修复记录](review-web-backend.md)。F07 完成，无未处理相关功能失败。
## F01：失败筛选不能改变已成功查询的范围

根因：`designer/ui/log_dialog.py::queryHistory()` 在 B 请求成功前覆盖 A 的 `_historyFilters`，
失败后却保留 A 的回复和显示，后续翻页/导出将 B 与 A 的游标/上界拼接。
修复为候选条件随异步任务保留，成功解析后一起提交条件、回复和显示；失败不提交，提示明确列出
仍在使用的上次成功范围。下一页与完整导出只取该成功范围。

现有 operational 测试新增 A 成功 → B 失败 → A 翻页/完整 JSONL 导出，再 B 重试成功 →
从 0 建立 B → B 翻页/导出；明确断言实际请求筛选、after、upper 及导出内容。
F01 已完成，无未处理缺口。

## F02：重建 reader 保留同一来源的游标与实例

根因：`designer/ui/main_window.py::_pollOperationalLogs()` 从 reader 读 cursor，却从窗口尚未更新的
字段读实例。修改为有 reader 时二者都读该 reader，无 reader 才使用此前保存的配对状态。
不修改既有 RESET_REQUIRED 合同或目标隔离。

参数化回归先实际读临时 SQLite 日志，再使 reader 因普通查询错误结束，显式重试并核对首个重连请求
仍是原 `(instance, cursor)`。同实例读到后续记录一次；实际实例改变则收到 RESET、清旧显示并重建起点。
F02 已完成，无未处理缺口。

## F05：旧 Runtime 的原单 Job 日志入口

根因：RuntimeController 的 appendEvent 被无条件置为空，而 UNIMPLEMENTED 停止 Query 后没有旧来源。
`designer/services/global_logs.py` 在当前 reader 保存未知/支持/不支持三态；`main_window.py` 只在
当前连接确认不支持时转发原任务事件。支持则只使用 Query。支持结果确认前的短 Job 显示事件暂存在
受既有 `DEFAULT_MAX_LOG_ENTRIES` 限制的当前连接 deque，确认旧服务后释放一次，新服务则丢弃副本。
切目标清空暂存和旧支持状态；保留历史不可用提示、状态/控制/结果链路。

扩展真实 loopback gRPC 夹具的 legacy 开关，覆盖旧 → 新 → 旧切换及新服务无重复记录；另正常延迟
首次查询回复，验证任务已结束而支持结果未到时，旧服务不丢首段，新服务不追加任务副本。
F05 已完成，没有增加跨版本兼容框架或第二套持久化状态。

## 桌面修复验证

解释器同 Runtime 组，全部显式使用 `$env:QT_QPA_PLATFORM='windows'`，不是 offscreen 或人工点击。

```powershell
python -m pytest tests/designer/test_operational_log_integration.py -k 'FailedFilter or ErrorRetry or LegacyJobSource or ShortJob' -q --tb=short
python -m pytest tests/designer/test_operational_log_integration.py tests/designer/test_designer_startup_settings.py tests/designer/test_runtime_qt_ownership.py tests/designer/test_runtime_log_usability.py tests/designer/test_runtime_log_names.py tests/designer/test_runtime_log_refresh.py -q --tb=short
python -m pytest tests/designer/test_operational_log_integration.py -q --tb=short
python -m pytest tests/ui/operator_view/test_runtime_log_view.py tests/designer/test_log_export.py -q --tb=short
```

- `review-desktop-counterexamples.txt`：初轮 4 failed；其中 F01/F02 有效，F05 夹具在 handler 注册后替换
  方法不生效，已明确标为无效产品反例。纠正后另存 `review-desktop-legacy-counterexample.txt`：1 failed，
  确认真实 UNIMPLEMENTED 后原单 Job 显示为空。所有原输出保留。
- `review-desktop-focused.txt`：**6 passed**、11 deselected，覆盖全部新增桌面反例。
- `review-desktop-regression.txt`：**126 passed**，28.12s，无 skip。
- 补齐本地输入失败的范围提示后 `review-desktop-final.txt`：**17 passed**，11.23s，重点文件 Ruff 通过。
- 主执行者实际运行 `review-export-ui-regression.txt`：**42 passed**，20.20s；检查原 Runtime 日志视图、
  完整导出、固定文件截止、取消及三种格式。
- 桌面 owned files `git diff --check` 通过。详见 [桌面修复记录](review-desktop.md)。

## F03：异步确认同一实例不重建实时起点

根因：`frontend/src/components/RuntimeLogs.tsx` 的缓存 key 包含 `/status` 独立返回的 prop 实例，
未知 prop 变成日志查询已确认的同一实例时，key 改变并重新 baseline，丢失已有行并跳过间隔日志。
修复以目标和筛选决定范围，独立比较 prop 与 cursor.instance；相同只确认，不取消当轮请求，不改
after/upper/事件。实际实例改变才取消读取/下载、清理旧显示并重建；保持请求归属检查、会话及取消。

扩展现有 `logging.test.mjs`，直接执行生产组件闭包/effect（显式 hook doubles，不是仅 merge 函数）：
在上界 12 的下一页等待时确认同实例，核对未 abort、未新增 baseline、保留 11 并继续读 12；
另覆盖 status 先到、实际换实例时 abort 与旧晚返回隔离。正式 React/DOM 行为另由现有 backend_e2e
中的真实 FastAPI HTTP + Chromium 三种时序确认。

## F08：实时显示和重开缓存有界

根因：实时 merge 后完整数组同时用于渲染和 liveCursors，持续无界增长。
在同一组件仅对实时数组保留最新 **1000 条**，界面标明该范围；消费游标、当轮上界和实例继续完整推进。
不裁剪历史分页或完整导出，没有增加通用缓存机制或性能专项。

组件回归实际读取 6×200 条，检查最新 1000 条与 after=1200；卸载/重挂仍为 1000 条且继续该游标，
切历史 6 页保留完整 1200 条。真实浏览器多页后 DOM 最新 1000 条、隐藏/重开仍 1000；
历史最终 **1202 条**，完整 JSONL **1202 行**。

## 前端与受影响 Web 产物验证

前端命令在实际 D: 仓库 `web_panel/frontend` 执行；Python 使用现有独立 Web venv，无依赖环境变更。

```powershell
node --test tests/logging.test.mjs
npm run build
npm test
```

- `review-frontend-focused.txt`：**6 passed**，无 skip；包含生产组件/effect 和原下载合同测试。
- `review-frontend-tests.txt`：**81 passed**，无 skip；`review-frontend-build.txt`：tsc/Vite 通过。
- 初次组件测试因跨 VM 数组原型比较导致 3 passed/3 failed，是测试 harness 问题；使用宿主 Array.from
  修正收集后保留全部值断言，日志 `review-frontend-initial-harness-failure.txt` 保留，未写成产品反例。
- 没有将审查者的临时复现当作本轮执行；本轮正式新增组件/effect 与浏览器用例均实际运行通过。

```powershell
& build/logging-web-venv/Scripts/python.exe web_panel/scripts/package_frontend.py
& build/logging-web-venv/Scripts/python.exe web_panel/frontend/tests/backend_e2e.py
& build/logging-web-venv/Scripts/python.exe -m pip wheel ./web_panel --no-deps --no-build-isolation --wheel-dir web_panel/dist
# 下一条从 web_panel 目录执行
python scripts/verify_wheel.py
```

- `review-frontend-browser.txt`：真实 HTTP/Chromium 三种返回时序、实时/重开/历史/导出全部通过；
  原登录、HTTP/WS、权限、控制、冻结恢复、目标隔离、手机布局 e2e 亦通过。
- `review-frontend-browser-observations.json` 保留实际请求：logs-first 只有 1 次 baseline，
  after=10→11→12；当轮上界保持；实际换实例恰 2 次 baseline，新实例只显示 21。
- F07 落入后已同步静态构建并重建 Web wheel。`review-frontend-wheel-verify.txt` 独立安装 PASS，
  包含精确静态文件集合、SDK 1.4.0 元数据/optional 上界/原 Job 与 Stop、CLI、无 Qt/主应用/模型导入。
- `review-frontend-artifacts.json` 保存最终 wheel SHA256、字节大小、基线、分支及 `sourceDirty=true`。
  本轮没有 proto/SDK 合同变动，未重复重建无关 SDK 或覆盖原 manifest；现有 manifest 仍诚实记录 dirty。
- 详见 [前端修复记录](review-frontend.md)。

## 最终检查与交付边界

各分工停止修改业务代码后，主执行者核对了新增反例与实际输出，F01—F08 均有对应实现和必要证明。

- `python scripts/gen_proto.py --check`：exit 0，`review-proto-check.txt`。
- 最后逐文件 `compile(Path(path).read_bytes(), path, 'exec', dont_inherit=True)`：66 个新增/修改 Python
  文件语法通过，`review-python-syntax-final.txt`；`git diff --check` exit 0，`review-diff-check-final.txt`。
- `review-artifact-integrity.txt`：主执行者重新核对最终 wheel SHA256，与当前后端 F07 源文件、全部
  打包静态文件逐字节相同；current proto 与 vendored proto 相同，SDK 1.4.0/sourceDirty 符合记录。
- 更新后的完整工作区清单：`final-workspace.json`；本轮开始前的首轮交付清单另保留为
  `implementation-workspace-before-review.json`，没有覆盖其原证据。
- 最终分支仍是 `codex/logging-consolidation-20261103`，HEAD 与基线仍为
  `940994b3420b47ff0add8766ccce5a4f2686a02a`。功能和修复来自未提交修改，不是这个 HEAD 的提交内容。
- 本轮新增定向/集成测试通过，没有把任何 skipped、deselected 或未执行项目记作通过；
  全规则 Web Ruff 的已有风格诊断保留在 F07 记录中，没有改动无关代码以追求清零。
- 本轮实际完成源码测试、Windows 原生 Qt 自动化、真实 HTTP/Chromium 浏览器自动化和 Web wheel
  独立安装。没有新增人工桌面/浏览器、offscreen Qt、冻结 EXE、真实设备、跨机器 LAN、现场或部署验收。
  原先人工桌面输入受限和样例 DesignerOpen=NOT_RUN 的边界继续保留，不用本次自动化替代。
- 没有剩余 F01—F08 实现项、相关功能失败或外部阻塞；本轮不提交、推送、合并或发布。
