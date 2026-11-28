# Change: Operator Runtime R3 收尾修正

## Why

R3 已完成 Operator Runtime 主链、Job 工作台接入、Designer
共享日志组件和原生 Qt 软件 Runtime 走查。对提交
`1f35861a22f4e5bd553fe210db989e28c3c4a0d1` 的源码复核仍发现少量问题：

-   `RuntimeLogSources` 默认选中索引 2「本机 JSONL」，但 Operator
    的主要日志入口应优先展示运行任务。
-   `RuntimeLogView.resetFilter()` 同样切回本机
    JSONL，重置筛选会意外改变来源。
-   `setToolVisible(False)` 和切换到非 Job 来源时没有请求停止已有
    `RuntimeGlobalLogs` reader；reader 线程及 UI poll 可能继续工作。
-   Operator 主题对字体、复选框、滚动条和来源标签的处理不完整，R3
    原生截图仍呈现部分系统默认控件和较松散的来源标签布局。
-   `RuntimeGlobalLogs._notice()` 仍使用
    `designer.runtime_logs.connection`
    事件类型，迁移到共享客户端后命名不再中立。

## What Changes

-   默认日志来源设为「运行任务」；重置筛选回到「运行任务」；显式设备日志入口
    `setDevice()` 仍进入本机 JSONL。
-   reader 生命周期与来源/工具可见状态绑定：离开 Job
    页或隐藏日志工具时请求停止；保存 Runtime instance 与最后确认
    cursor；reader 完全结束后再按 cursor 恢复，避免重复
    reader、重复事件和游标缺口。
-   将 Operator
    自有页面的字体回退、QCheckBox/QRadioButton、QScrollBar、QTabBar
    和控件间距补齐；来源标签改为紧凑、非均分宽度；不重排共享
    `RuntimeJobLogs` 的 Designer 可见布局。
-   将共享 reader 的本地连接诊断事件改为中立 Runtime
    命名，并更新受影响断言。

## Existing Capabilities Reused

-   `RuntimeGlobalLogs` 现有 cursor、Runtime instance、取消和完成状态。
-   `RuntimeJobLogs` 现有 Job 目录、执行树、详情、筛选和导出。
-   `OperatorLogClient.queryRuntimeLogs()` 与现有 Runtime RPC。
-   `applyOperatorTheme()` 和 `LIGHT_THEME`。
-   现有 `test_runtime_job_log_sources.py`、Operator 原生 Qt probe 和
    Designer 共享日志测试。

## Non-goals

-   不开启 `allowAdvancedHistory`。R3 当前没有为 Operator 接入 Designer
    的「查询整个历史范围...」结果页；本补充明确不扩张为新的历史检索 UI。
-   不改变 Job
    查询、历史分页、事件分类、执行树、导出快照和设备诊断语义。
-   不改变 `RuntimeJobLogs` 的共享布局或 Designer 的可见表现。
-   不改变 Operator 的生产页、开始/停止回调、Job
    控制目标、QStackedWidget、toolHistory 和返回焦点。
-   不修改 Runtime RPC/proto、数据库、事件持久化、设备 owner 或生产页面
    QSS。
-   不做 Web UI parity、真实硬件或冻结包验收。
