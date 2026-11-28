# 本补充的审查依据与边界

## 已核实的四项收尾问题

1.  **默认来源偏差：** `log_sources.py` 构造时
    `setCurrentIndex(2)`；`log_view.py.resetFilter()` 也选择
    2；`setDevice()` 选择 2 是有意的显式本机设备日志入口，应保留。
2.  **reader 隐藏后继续工作：** `setToolVisible(False)` 没有停止现有
    reader；切换到系统/JSONL 时 `_ensureReader()` 直接返回但不停止旧
    reader；`_poll()` 继续 drain。
3.  **视觉细节不足：** R3
    原生截图仍能看到字体回退不统一、来源标签宽度过大、部分控件使用系统默认样式。只做
    Operator 局部主题收尾。
4.  **命名残留：** 共享 `RuntimeGlobalLogs._notice()` 使用 Designer
    专属事件类型字符串。

## 有意保留的边界

-   不把 R3 主链再验一遍；本补充只测试上述修改影响的行为。
-   不改共享 `RuntimeJobLogs` 的布局，以免 Designer 视觉回归；Operator
    只通过根作用域样式和宿主来源栏改善观感。
-   不把 `allowAdvancedHistory=False` 简单改成
    True。高级历史查询还需要独立结果页、分页/取消和宿主接线；本补充明确不包含它。
-   不改 `setDevice()` 的 JSONL 来源选择。
-   不为视觉收尾引入新字体包、图标库、全局样式系统或跨平台主题框架。
-   不增加新 Runtime
    RPC、日志数据库、事件写入者、订阅中心或常驻线程管理器。

## 验收复用原则

-   先更新现有 `test_runtime_job_log_sources.py`，不要重复创建相同
    fixture。
-   reader 生命周期用少量事件、显式 cursor
    和调用计数验证；不做长时间压力测试。
-   视觉用原生 Qt 截图和点击/焦点走查；不为每个控件分别增加截图矩阵。
-   Designer 只在共享事件命名或共享 reader 逻辑实际变化时跑受影响用例。
-   最终测试应针对推送提交 SHA，而不是只针对未提交工作树。
