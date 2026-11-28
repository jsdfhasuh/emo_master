# 源码索引

核对版本为 2026-11-28 远端 `main` 的 `ea5aefa9cf62f0d7ee3ac0fb6fa4a14661dc663b`。下列路径相对仓库根目录，表内 `S/` 表示 `src/emo_master/`。

## M1 正确性和共享页

| 场景 | 源码入口 | 实施时关注 |
| --- | --- | --- |
| 心跳判活、业务序号 | `S/apps/runtime/jobs/supervisor.py` 的 `consumeWorkerEvent`、`consumeWorkerEvents`，`S/apps/runtime/events/models.py` 的 `batchableWorkerEvent` | 在更新接收时间和已见标记后退出。普通批次已排除心跳。 |
| 新订阅和旧历史 | `S/apps/runtime/events/event_store.py` 的 `readMerged`、`follow`，`S/apps/runtime/context/sqlite_store.py` 的 `listJobEventsAfter`、`queryRuntimeLogs`、`markOrphanedJobsFailed`，`docs/runtime-event-flow.md` | 逐 Job 重放读取已存记录。工作台历史查询过滤心跳。启动时已有单独的旧任务终态写入路径。 |
| SQLite 和子工作流日志 | `S/apps/runtime/execution/operator_executor.py` 的 `publishSqliteWrite`，`S/apps/runtime/events/jsonl_writer.py` 的 `_isOperationalEvent`、`_projectEvent`，`S/core/contracts/diagnostic_logging.py` | 当前筛选在 `sqlite.write.finished` 分支提前返回。确认普通完成和后台最终回执各自的记录内容。 |
| 任务归属、时间和定位 | `S/ui/runtime_logs/job_log.py` 的 `ExecutionTree`、`ExecutionBranch`、`JobEventWindow`，`S/ui/runtime_logs/runtime_job_logs.py` 的 `_eventSelected`、`_showEntry` | 区分 Job 计时与暂停窗口内的分支估算。失败提示展示代表记录的已知字段，跳转、抽屉标题和原始详情使用同一条事件。 |

## M2 展示与导出

| 场景 | 源码入口 | 实施时关注 |
| --- | --- | --- |
| 任务和历史名称 | `S/apps/runtime/jobs/manager.py`、`supervisor.py`、`models.py`、`repository.py`、`read_model.py` 与 `S/apps/runtime/context/sqlite_store.py` | 普通任务走 `JobManager.createJob`，派发消费者由 Supervisor 直接创建 `JobRecord`。历史目录从 jobs 读取，优先使用本次运行时保存的名称。已有 `job.accepted` 事件写入可取得的工程名和工作流名，离线 JSONL 也能读取。 |
| 事件中的名称与协议 | `S/core/workflow/models.py`、`compiler.py`，`S/apps/runtime/jobs/worker_main.py`，`S/apps/runtime/workflow/`，`S/core/runtime_log_records.py`，`proto/runtime.proto` | 复用编译时工作流名称，按已存在的真实名称补关键事件和 completed 投影。协议和生成物沿用 `scripts/gen_proto.py`、`scripts/build_panel_contract.py`。 |
| Markdown | `S/apps/designer/ui/log_dialog.py` 的 `exportHistory(jobScope=...)`，`S/apps/operator_runtime/log_sources.py` 的 `_exportWhole`、`_exportFiltered`，`S/clients/runtime/log_records.py` 的 `exportWholeJob`，`S/core/log_export.py` | Designer 在历史页的 Job 分支分页，Operator 有完整 Job 与已加载范围两个入口。给完整 Job 的 Markdown 标出实际范围，只渲染调用方已提供的可读字段。 |
| 共享桌面 | `S/core/runtime_log_messages.py`、`S/ui/runtime_logs/log_record_detail.py`、`runtime_job_logs.py`、`log_filters.py`，`S/apps/designer/ui/log_dialog.py`，`S/ui/communication_panel.py` | 详情已有后台 SQLite 回执、算子身份、参数和结果摘要。共享工作台的关键字匹配在 `runtime_job_logs.py` 的 `_scopePredicate`。设备事件抽屉使用当前 Job 记录，设备诊断页使用其已有缓存。抽屉内可归并技术字段，展示和复制保留真实业务值。 |
| Operator 本机文件 | `S/apps/operator_runtime/log_view.py` 的 `LogTableModel`、`RuntimeLogView`，`S/apps/operator_runtime/log_reader.py` 的 `LogFilters.matches`、`recordDetails` | R4 已有 `QTableView`、等级色、组合筛选、阅读锚点、脱敏复制和快照导出。让新增显示文案在现有匹配路径中可搜索。 |
| Web | `web_panel/frontend/src/components/RuntimeLogs.tsx` | 本地时间转换成现有毫秒参数。列表与详情取同一条事件的框架文案，原始查看对应其实际记录。保留现有实例检查和字符串游标。 |

## 验证入口

- 心跳接收时间与业务序号分别看 `tests/runtime/test_runtime_persistence_contract.py` 和 `tests/runtime/test_heartbeat_liveness.py`。JSONL 现有用例在 `tests/runtime/test_jsonl_logging.py`。
- 桌面共享操作和完整 Job 导出已有 `tests/designer/test_runtime_job_workbench.py`、`tests/designer/test_operational_log_integration.py`。Operator 的独立入口和本机日志分别见 `tests/ui/operator_view/test_runtime_job_log_sources.py`、`test_runtime_log_view.py`。
- Web 既有游标和反馈用例在 `web_panel/frontend/tests/logging.test.mjs`、`runtime-log-feedback.test.mjs`。哪些行为需要新增断言见[实施与验收清单](tasks.md)。
