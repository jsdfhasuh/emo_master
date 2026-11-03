# 独立审查后的桌面定点修复：F01、F02、F05

日期：2026-11-03（Asia/Shanghai）。依据本轮独立审查请求、实施计划 v3.4 与仓库 AGENTS.md，仅修复已核对调用链的三个桌面问题。原实施记录、原失败输出和本轮新增失败输出均保留；没有提交、推送、合并或发布。

源码入口为 `C:\Users\jsdfhasuh\my_scripts\emo_master`，实际目录为 `D:\jsdfhasuh\documents\my_project\emo_master`。实现分支 `codex/logging-consolidation-20261103`；实施固定基线与本记录时 HEAD 均为 `940994b3420b47ff0add8766ccce5a4f2686a02a`。实现与此次修复均在未提交工作区，不能把该 HEAD 当作实现提交。

## 根因与修改

| 编号 | 实际触发与后果 | 最小修改与主要文件 |
| --- | --- | --- |
| F01 | 已成功查询 A，编辑为 B 后查询失败；原代码先覆盖 `_historyFilters`，同时保留 A 的记录、实例、上界和游标，导致后续翻页/完整导出把 B 条件与 A 游标混用。 | `designer/ui/log_dialog.py` 将候选条件随当前异步任务保留，只有成功解析回复后才同时提交成功条件与回复；翻页/导出始终读取同一次成功查询的条件、实例和上界。读取失败明确说明仍保留上次成功范围，并显示该范围；成功重试 B 后再切换到 B。 |
| F02 | 全局 reader 正常读取后因错误停止；重建 reader 从旧 reader 取游标，却从尚未更新的窗口字段取实例，生成不匹配的 `(instance, cursor)`，同实例普通重试也无法正确接续。 | `designer/ui/main_window.py` 重建时从同一 reader 同时取 `cursor` 与 `runtimeInstanceId`；无旧 reader 时才使用窗口已保存的二者。新实例仍按现有 `RESET_REQUIRED` 清理旧 Runtime 显示并重新建立起点。 |
| F05 | 原 S5 抑制任务事件向同表追加，但旧 Runtime 返回 `UNIMPLEMENTED` 后没有恢复原单 Job 日志，导致当前任务日志不可见；短 Job 还可能在支持能力回复前已发完事件。 | `designer/services/global_logs.py` 使用当前 reader 的三态支持结果（未知/支持/不支持），保留 `.unsupported` 查询接口。`main_window.py` 仅在当前连接确认不支持查询时转发原任务日志；确认前暂存在现有显示数量上限约束的本地 deque，确认不支持后释放一次，确认支持后丢弃以保留 Query 单一来源。切目标清空暂存、旧 Runtime 显示和支持结果；任务状态、控制与结果回调继续沿用原控制器。 |

没有新增后台任务体系、跨连接能力缓存或第二套持久化日志账本。F05 的短暂队列只保存当前连接等待首个支持结果时的任务显示记录，受 `DEFAULT_MAX_LOG_ENTRIES` 约束。

## 反例与修复后验证

以下命令均在仓库根执行；`python` 指 `D:\Conda\envs\emo_master\python.exe`。所有本记录 Qt 测试明确设置 `$env:QT_QPA_PLATFORM='windows'`，为 Windows 原生 Qt 平台自动化，未混同 offscreen、人工桌面或真实设备验收。测试数量有交叠，不相加为独立总数。

1. 修复前针对性反例：

   `python -m pytest tests/designer/test_operational_log_integration.py -k 'FailedFilter or ErrorRetry or LegacyJobSource' -q --tb=short`

   [review-desktop-counterexamples.txt](review-desktop-counterexamples.txt)：4 failed / 11 deselected。F01 请求实际携带 B 而断言需要 A；F02 两参数实际为 `('', 1)` 而断言需要 `('test-runtime', 1)`。此轮 F05 的失败来自新增测试夹具在 gRPC server 注册后替换方法，注册的 handler 没有随之改变，因此该条不是有效 F05 产品反例，不能用它证明生产缺陷。

2. 修正 F05 夹具为注册 handler 读取实例 `legacy` 标志后，临时仅将任务日志回调恢复为修复前 no-op，执行：

   `python -m pytest tests/designer/test_operational_log_integration.py -k LegacyJobSource -q --tb=short`

   [review-desktop-legacy-counterexample.txt](review-desktop-legacy-counterexample.txt)：1 failed / 16 deselected。真实 loopback gRPC 已返回 `UNIMPLEMENTED`，随后控制器收到任务事件，日志条数实际 0、预期 1。运行后立即恢复修复后的回调，没有留下生产代码回退。

3. 修复后新增反例：

   `python -m pytest tests/designer/test_operational_log_integration.py -k 'FailedFilter or ErrorRetry or LegacyJobSource or ShortJob' -q --tb=short`

   [review-desktop-focused.txt](review-desktop-focused.txt)：6 passed / 11 deselected / 7.86s。实际覆盖：A 成功/B 失败后的提示、A 翻页和完整固定范围导出；B 成功重试后的全新起点、B 翻页和完整导出；原实例重试接续与新实例 RESET；旧/新/旧目标切换时单一来源；真实 gRPC 首次回复被正常延迟时，短 Job 事件在旧 Runtime 释放一次，在支持 Query 的 Runtime 抛弃任务副本并仅展示 Query 记录。

4. 受影响原生 Qt 回归：

   `python -m pytest tests/designer/test_operational_log_integration.py tests/designer/test_designer_startup_settings.py tests/designer/test_runtime_qt_ownership.py tests/designer/test_runtime_log_usability.py tests/designer/test_runtime_log_names.py tests/designer/test_runtime_log_refresh.py -q --tb=short`

   [review-desktop-regression.txt](review-desktop-regression.txt)：126 passed / 28.12s；没有跳过或未处理失败。覆盖原日志窗口、启动设置、Qt 所有权、日志名称/详情/刷新、读者关闭与已有操作行为。

5. 对同一失败范围提示补齐本地输入校验错误入口后，最终运行：

   `python -m pytest tests/designer/test_operational_log_integration.py -q --tb=short`

   [review-desktop-final.txt](review-desktop-final.txt)：17 passed / 11.23s；同一输出保存重点文件 `python -m ruff check` 的 `All checks passed!`。重点文件为 `global_logs.py`、`main_window.py`、`log_dialog.py` 与 `test_operational_log_integration.py`。此前 [review-desktop-lint.txt](review-desktop-lint.txt) 亦保留。`git diff --check --` 上述四文件通过；Git 的 LF/CRLF 提示属于工作区换行提示，未报告 whitespace error。

## 验收层次与剩余事项

本分工修复 F01/F02/F05 均已实现，新增正常路径/直接反例与相关已有回归通过。所有新增用例继续扩展已有 `tests/designer/test_operational_log_integration.py`，使用临时 SQLite、临时日志目录与 loopback gRPC，不访问真实设备、现场工程、凭据或生产日志。

本轮未另行执行人工桌面点击、冻结 EXE、真实相机/PLC、真实 LAN 或部署验收；不把本记录的原生 Qt 自动化等同这些层次。原实施界面工具输入受限的证据继续保留在 `desktop.md`，此次没有删除或覆盖。Runtime、Web、协议/SDK 与整体交付的其他审查项由主记录统一汇总，本记录不替代其结果。
