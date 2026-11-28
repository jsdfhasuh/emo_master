# Design：R3 收尾修正

## 1. 审查基线

-   仓库：`jsdfhasuh/emo_master`
-   分支：`codex/runtime-overall-r3`
-   提交：`1f35861a22f4e5bd553fe210db989e28c3c4a0d1`
-   R3 父提交：`48b30191884ad5ef5d96e8d26591104d16d54a58`
-   本设计根据该提交的实际源码、R3 `delivery.md`、原生 Qt
    记录和测试代码制定。
-   本补充包生成阶段没有修改产品源码，也没有运行产品测试。

## 2. 已确认的问题

### 2.1 日志默认来源不正确

`src/emo_master/apps/operator_runtime/log_sources.py`： - 来源顺序为
`运行任务`、`Runtime 系统事件`、`本机 JSONL`。 - 构造函数当前调用
`self.sources.setCurrentIndex(2)`，默认落在本机 JSONL。 -
`_selectSource()` 根据当前来源切换页面，并调用 `_ensureReader()`。

`src/emo_master/apps/operator_runtime/log_view.py`： - `resetFilter()`
当前将来源设为索引 2。 - `setDevice()` 当前也将来源设为索引
2；这一行为是显式设备日志入口，应保留。

最小修正： - 构造默认来源改为索引 0「运行任务」。 - `resetFilter()`
改为索引 0，并清理对应过滤状态。 - `setDevice()` 保持索引
2，不把设备文件日志误改成 Job 工作台。

### 2.2 reader 生命周期没有跟随页面可见性

`RuntimeLogSources.setToolVisible(False)` 当前只更新
`_toolVisible`，随后 `_ensureReader()` 因不可见直接返回；已有 reader
不会被停止。

切换到系统事件或本机 JSONL 时，`_ensureReader()` 因来源索引不是 0
直接返回；已有 Job reader 仍会继续执行 `_poll()` 与
`RuntimeGlobalLogs._read()`。

最小目标： - Job reader 只在 `toolVisible == True` 且来源索引为 0
时运行。 - 离开 Job 来源、隐藏日志工具或进入关闭流程时，请求 reader
停止。 - 在停止完成前继续 drain 已排队的 event/progress，保留最后确认的
`(runtimeInstanceId, cursor)`。 - reader 完全结束后才允许创建下一个
reader；不能并发创建两个 reader。 - 再次进入 Job 页时，从保存的 Runtime
instance/cursor 继续，利用现有 eventId 去重和服务端游标语义。 -
系统事件/本机 JSONL 页面不需要 Job
reader；导出任务仍可按现有方式继续，不能因暂停 reader
而误取消用户主动导出。

实现边界： - 优先复用 `RuntimeGlobalLogs` 已有
`stop`、`context.cancel()`、`stream.cancel()`、`finished` 和 `close()`
生命周期。 - 如同步 `close()` 会阻塞 Qt
主线程，可把"请求停止"和"等待线程结束"分开；使用当前 reader 实例和现有
poll/timer 收尾，不新增常驻线程管理器、第二个 executor 或新订阅中心。 -
关闭 Operator 时继续使用现有 `beginClose()` / `setRuntime(None)`
的最终资源回收路径。

### 2.3 Operator 视觉细节仍不完整

R3 原生 Qt 截图： -
Startup：`verification/native-qt/01-startup-settings.png` - Job
工作台：`verification/native-qt/04-job-log-workbench.png` -
设备窗口：`verification/native-qt/05-device-controls.png`

截图显示 R3
已有浅色背景、卡片和边框，但部分控件仍使用平台默认外观；中文字体呈现不够统一；来源标签占用过宽；复选框、滚动条和输入焦点状态没有完整纳入
Operator 主题。

最小修正： - 在 Operator 专属主题中选择已安装的无衬线字体：优先
`Microsoft YaHei UI`，其次
`Microsoft YaHei`、`Noto Sans CJK SC`，最后保留系统默认，不强制加载外部字体文件。 -
只补齐
QCheckBox、QRadioButton、QScrollBar、QTabBar、QToolButton、禁用/焦点状态及合理控件高度。 -
`RuntimeLogSources.sources` 使用紧凑 tab 宽度，不平均拉伸三项来源。 -
保持 R3 的卡片、浅色、蓝色主操作和 QSS 作用域；不新建设计系统，不改共享
Designer 布局。 - 对 `StartupPage`、日志页和设备窗口使用同一套 Operator
根作用域主题；生产页面和工程自定义 QSS 不受影响。

### 2.4 共享 reader 事件类型残留 Designer 命名

`src/emo_master/clients/runtime/global_logs.py` 的 `_notice()` 当前写入
`eventType="designer.runtime_logs.connection"`。

共享 reader 由 Designer 与 Operator
共用，连接/断连事件应使用中立名称，例如
`runtime.logs.connection`。只改事件类型字符串和明确依赖它的测试/文档，不改等级、错误码、payload
或重连行为。

## 3. 高级历史搜索的范围说明

R3 的 `RuntimeJobLogs` 构造时使用 `allowAdvancedHistory=False`，因此
Operator 不显示 Designer 的「查询整个历史范围...」按钮。

当前 Operator 已支持运行中/最近已结束/更早历史任务目录、当前 Job
最近事件窗口、已加载事件筛选和两种导出；尚未接入 Designer
的全历史事件范围检索入口及独立命中结果视图。

本补充不启用该入口，因为其宿主结果页/查询任务接线尚未在 R3 Operator
中实现；不得只把 `allowAdvancedHistory` 改成 True 后留下无处理的
`historyRequested`
信号。此项明确列为本补充非目标，后续若用户要求完整高级历史检索，再单独复用现有
`QueryRuntimeLogs` 合同规划。

## 4. 主要用户操作

1.  从起始页点击「查看日志」：直接进入「运行任务」。
2.  在运行任务中切换 Job：任务、事件、执行树、详情和 JSON 仍绑定同一
    Job/事件。
3.  点击「重置筛选」：清除当前 Job 过滤，但仍留在「运行任务」。
4.  从设备日志入口进入：显式进入「本机 JSONL」，按设备 ID
    过滤；不改变默认来源规则。
5.  切换到系统事件或本机 JSONL：Job reader 停止并保存 cursor。
6.  返回运行任务：等待前一 reader 退出后从 cursor
    恢复，不重复创建订阅，不漏读事件。
7.  离开整个日志工具：停止 Job reader；重新打开时恢复。
8.  从日志工具返回生产页：原 Job 控制目标、页面和焦点不变。

## 5. 验收边界

-   一组 Operator Qt 测试覆盖默认来源、reset、显式设备入口、tab
    切换、隐藏/恢复、cursor 去重。
-   一组原生 Qt 走查确认来源默认页和实际字体/控件样式。
-   一组 Designer 受影响回归只在共享 `RuntimeGlobalLogs`
    事件类型变化时运行；不重跑完整 203
    项日志矩阵，除非共享代码其他行为也改变。
-   OpenSpec strict、受影响 pytest、Ruff/mypy 与仓库 CI 按实际改动执行。
-   不重跑 Runtime
    工程加载、并发、设备动作、生产页面和硬件测试，因为本补充不修改这些领域。
