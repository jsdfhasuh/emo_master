# Tasks：Operator Runtime R3 收尾

## P0：实施基线

-   [x] P0.1 从
    `codex/runtime-overall-r3@1f35861a22f4e5bd553fe210db989e28c3c4a0d1`
    创建独立收尾分支；实施前核对远端 HEAD、工作区状态和后续提交。
-   [x] P0.2 阅读本 Change、R3 归档记录和 `AGENTS.md`；保留 R3
    原提交，不 amend、不 force-push、不覆盖归档证据。
-   [x] P0.3 核对本 Change
    引用的源码位置和测试当前断言；路径变化时先更新源码地图。

## P1：修正日志默认来源

-   [x] P1.1 `RuntimeLogSources` 默认选择索引 0「运行任务」。
-   [x] P1.2 `RuntimeLogView.resetFilter()` 重置后仍留在「运行任务」；从
    StartupPage 点击「查看全部日志」时清除 Job
    工作台筛选和本机日志的设备/级别过滤，避免带着旧过滤进入空列表。
-   [x] P1.3 保留 `RuntimeLogView.setDevice()` 显式进入本机 JSONL
    的行为。
-   [x] P1.4 扩展现有
    `test_runtime_job_log_sources.py`：验证首次打开、从 StartupPage
    打开、resetFilter 和 setDevice 四个入口；不重复创建相同 fixture。

## P2：修正 Job reader 生命周期

-   [x] P2.1 Job reader 只在日志工具可见且来源索引 0 时活动。
-   [x] P2.2 离开 Job 来源或隐藏日志工具时，请求当前 reader
    停止；不再发起新的 Runtime 查询。
-   [x] P2.3 drain 停止前已返回的事件/progress，保存 Runtime instance
    和最后确认 cursor。
-   [x] P2.4 reader 未完全结束时不得启动第二个 reader；结束后再次进入
    Job 页，从保存 cursor 恢复。
-   [x] P2.5 保持导出 worker 生命周期独立；隐藏 Job
    工作台不误取消用户已启动的导出。
-   [x] P2.6 Operator 关闭仍使用现有
    beginClose/setRuntime(None)/finishClose 路径；不新增常驻线程或
    reader 管理器。
-   [x] P2.7 用有限确定性 Runtime
    事件验证：切到系统事件/JSONL、隐藏工具、重新打开、无重复 eventId、无
    cursor 缺口；复用现有 Runtime fixture。

## P3：Operator 视觉小范围收尾与共享命名

-   [x] P3.1 `theme.py`
    采用已安装无衬线字体回退顺序；不下载/打包新字体。
-   [x] P3.2 补齐 Operator 根作用域下
    QCheckBox/QRadioButton/QScrollBar/QTabBar/QToolButton、焦点/禁用态及合理控件高度。
-   [x] P3.3 来源 QTabBar
    设置紧凑非均分宽度，保持三个来源标签文案和顺序不变。
-   [x] P3.4 检查
    StartupPage、日志页、设备窗口的标题/辅助文字/卡片间距和长路径；只修截图中实际可见的不协调处，不重排共享
    RuntimeJobLogs 布局。
-   [x] P3.5 确认 Operator 样式不污染 `RuntimePages`、工程自定义
    QSS、Designer 默认样式和共享 `communication_panel`。
-   [x] P3.6 将 `designer.runtime_logs.connection` 改为中立 Runtime
    事件类型；更新受影响断言和说明，不改重连状态机。
-   [x] P3.7 对字体回退、tab 宽度、控件焦点/禁用态补最少必要 Qt
    断言；布局/文案以原生 Qt 走查为主。

## P4：验收和收尾

-   [x] P4.1 完成 `acceptance.md` 本补充范围内的代表场景。
-   [x] P4.2 保存 Operator 原生 Qt 截图：默认来源
    Job、起始页、日志页、设备窗口；窄窗只在实际布局变化影响时追加。
-   [x] P4.3 运行受影响 Operator pytest、必要的共享 reader/Designer
    回归、Ruff/mypy 和 OpenSpec strict；不重跑未受影响的 Runtime
    全量矩阵。
-   [x] P4.4 以最终提交 SHA
    记录测试版本、环境、命令、通过/失败/跳过和未验证项；不得只记录未提交工作树结果。
-   [ ] P4.5 创建/更新 PR，附真实执行者身份的 GitHub
    comment，说明改动、验证结果和未验证项；不合并、不发布，除非另有授权。

## 明确不做

-   不开启 `allowAdvancedHistory`，不连接未实现的 `historyRequested`
    宿主结果页。
-   不改变 Designer 可见布局，不重跑 Designer 全部日志测试，除非共享
    reader 行为有实质变化。
-   不增加新的日志服务、查询
    RPC、数据库、事件写入者、订阅中心或常驻线程管理器。
-   不做全 Runtime 重验、真实硬件、冻结包、现场和 Web parity 验收。
