# Operator Runtime R3 收尾补充计划

**OpenSpec Change ID：** `runtime-operator-r3-closeout`\
**目标基线：**
`codex/runtime-overall-r3@1f35861a22f4e5bd553fe210db989e28c3c4a0d1`\
**性质：** R3 已归档 Change 的小范围后续补充；不重开、不覆盖原 R3。\
**状态：** 实施中。任务进度见 [tasks.md](tasks.md)，验收结果见 [acceptance.md](acceptance.md)。

## 本补充只处理四件事

1.  Operator
    打开日志时默认进入「运行任务」，重置筛选也留在该页；显式设备日志入口仍可进入本机
    JSONL。
2.  Job
    实时读取只在日志工具可见且「运行任务」来源选中时工作；离开时请求停止，保存
    Runtime instance/cursor，回来后从游标恢复。
3.  对 Operator
    自有工具做小范围视觉收尾：字体回退、复选框/滚动条/来源标签样式、工具栏密度和卡片间距。保留
    Qt Widgets、`LIGHT_THEME`、现有页面栈、工程 QSS 和 Designer
    可见布局。
4.  将共享日志读取器的连接事件类型从 Designer 专属命名改为中立 Runtime
    命名。

## 明确不包含

-   不重做 Runtime 核心、Job 调度、设备管理、页面结果和历史服务。
-   不重构完整 RuntimeClient，不复制 Designer 的 LogDialog。
-   不新增数据库、事件写入者、订阅中心、后台任务服务或新的日志查询 RPC。
-   不改变 Designer 可见界面，不把 Designer 的整份 QSS 加载到 Operator。
-   **不在本补充中开放 Designer
    的「查询整个历史范围...」高级历史搜索。** R3 Operator
    已支持运行/最近结束/更早历史任务目录、当前 Job
    最近事件窗口和两种导出；但
    `allowAdvancedHistory=False`，全历史事件范围检索仍是明确差异。若用户要求该能力，应另立小
    Change，复用现有
    `historyRequested`/`QueryRuntimeLogs`，不可把它假称为本次已包含。
-   不做真实相机、PLC、现场工程、冻结包验收。

## 放置方式

将 ZIP 内的 `runtime-operator-r3-closeout/` 放入仓库
`openspec/changes/`。实施分支应从 R3 分支 `codex/runtime-overall-r3`
的提交 `1f35861a` 创建；实施前先核对远端分支
HEAD、工作区状态和后续提交，不要覆盖或改写 R3 提交。

## 文件

`proposal.md`、`design.md`、`tasks.md`、`acceptance.md`、`source-map.md`、`review-notes.md`、`CODEX_PROMPT.md`、两份增量规格、`reference/prototype.html`
和 `reference/visual-baseline.md`。

## 验收原则

只重跑被本补充实际改动影响的测试。一次原生 Qt
走查可覆盖多个验收项。不得把原 R3 报告中的 PASS 直接冒充本补充的
PASS；也不得为了清空所有历史 NOT_RUN 而重新验收未受影响的 Runtime
子系统。

随包提示词和原型作为实施参考。`SHA256SUMS.txt` 对应收到的原始 ZIP，任务与验收进度以本目录更新记录为准。
