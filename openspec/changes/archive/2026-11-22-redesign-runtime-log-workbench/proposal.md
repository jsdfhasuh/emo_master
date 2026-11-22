# Change: Runtime 日志工作台完整计划 V2.4

## Why

工程师需要从“一次真实任务”进入日志，按工作流调用、循环轮次和节点执行阅读，并在长时间运行时保留筛选、阅读位置、明确的时间与来源。原 `redesign-runtime-log-workbench` 已在 fa4b72d7 实现主要能力，但现有首页把 active 当成仍执行，导致终态任务混在运行中；“下游资源仍在收尾”、毫秒输入和集中展开的技术字段也增加理解成本。

本版把已确认的完整设计与当前修订合为同一个 change。Codex 只需要本包的规范、任务、原型和验收，不再自行拼接多份计划或套用 Patch 01。

## What Changes

- 完整保留 Runtime / Designer 归属、运行中 + 最近已结束首页、跨重启目录、真实执行树、循环与连续运行、有界日志窗口、唯一实时 reader、高级筛选、时间、右侧详情、通用算子 / 调试易读层、设备下钻和完整导出合同。
- 首页按 Job 自身 status 分组；最近历史与当前仍 active 的终态 Job 合并去重，实时状态优先且不被 recent 页额度挤掉。
- 对真实独立下游显示“还有 N 个关联任务未结束”，允许查看并返回原上下文；普通同 Job 子工作流和 While 轮次留在执行树。原因无法确认时使用“结束处理未完成”，不推测设备正在关闭。
- 复用本机日期时间控件，保留毫秒、可空边界及半开区间，覆盖任务本地事件、服务端历史事件和历史任务目录三个入口。
- 高级筛选分常用 / 技术条件，折叠不失效；主行压缩完整 UUID 和长说明，共享详情提供已知框架文字和状态的易读表达，原始值和导出保持。

## Impact

- 当前基线：`codex/runtime-log-workbench` / `fa4b72d751189c2689882a0a0d127fd280a35fde`（2026-11-22 核对）。实际实施前核对后续差异。
- 主要改动在 `runtime_job_logs.py`、`log_dialog.py`、`log_record_detail.py` 及必要的轻量 UI 状态适配；复用现有 Python 3.10 / PySide2 5.15.2.1 / Qt Widgets。
- `tasks.md` 保留原 38 条已实施记录，并以 7.1–7.11 列出当前未完成工作。本版的文档完成不代表这些 UI 修订已经实现。
- 本 change 的唯一 capability 为 `runtime-log-workbench`。正式 specs 目前尚未归档，因此统一提供 21 个 ADDED Requirement，无另一个 change 的归档或实施依赖。
- 当前修订无需新增 RPC、表、状态枚举、设备采集器、常驻读取器、逐算子渲染器或全量历史扫描。原已实现后台合同继续保留。
- 验证复用 G1–G6 与 Q1/Q2，只补受影响行为和相关 Qt 证据，保留仓库必须的门禁。

## Evidence and Delivery

完整设计见 [design.md](design.md)，当前工作见 [tasks.md](tasks.md)，验收见 [acceptance.md](acceptance.md)，源码依据见 [source-map.md](source-map.md)。先操作 [可点击原型](reference/prototype.html)，再按 [图片与场景入口](reference/index.html) 对照；页面明确使用模拟数据 / 模拟连接。

原 [implementation-report.md](implementation-report.md) 与 `evidence/` 是 fa4b72d7 的基线档案，本次没有新增产品实现或原生 Qt 验收。实施后的结果单独写入 `implementation-report-v2_4.md`，按真实结果勾选第 7 节。
