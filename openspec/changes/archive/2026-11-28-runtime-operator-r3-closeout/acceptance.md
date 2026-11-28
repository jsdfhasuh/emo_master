# Operator Runtime R3 收尾验收

源码、命令和验证范围见 [交付记录](delivery.md)。原生走查使用 Windows Qt 和软件 Runtime。QTest 通过应用实际入口操作已显示的原生窗口。截图来自这些窗口的 Win32 句柄。

## A 日志入口

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 默认来源和真实入口 | 通过 | 从起始页点击「查看全部日志」，直接看到「运行任务」和任务目录。生产页「查看日志」也进入该来源。见[默认首页](verification/native-qt/03-log-default.png)及回归。 |
| 从更早历史重新查看全部日志 | 通过 | 先按工程、失败状态和日期查询历史，再返回起始页点击「查看全部日志」。旧条件已清空，「运行中」列出两个实际运行的任务，下方显示「最近已结束」分组。[原生窗口](verification/startup-catalog/03-log-default.png) |
| 清除筛选 | 通过 | Job 筛选清除后仍在运行任务。起始页入口同时清掉 JSONL 的旧设备与级别过滤。 |
| 三个来源与设备入口 | 通过 | 系统事件和本机 JSONL 分别显示实际记录。设备窗口入口选中本机 JSONL 并保留设备 ID。 |

## B 实时读取

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 切来源、隐藏工具、返回生产页 | 通过 | 等待旧读取线程退出和轮询停止后，跨过正常查询间隔核对 Job 请求数保持不变。 |
| 快速切回、排空与恢复 | 通过 | 确定性回归在事件已排队但进度尚未交付时触发切换。待旧线程退出才以已确认的 Runtime 实例和游标重开，按事件 ID 核对未交付尾部与去重。 |
| 阅读位置 | 通过 | 恢复时保留原 Job、选中事件、关键字和滚动位置。点击「返回最新」后，暂停期间写入的标记事件只出现一次。 |
| 导出与关闭 | 通过 | 隐藏工具时完整任务导出继续完成。当前筛选导出保留点击时范围。关闭回归核对查询取消和资源释放。 |

## C 原生窗口

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 起始与设置 | 通过 | 1060×800 下两个保存操作完整可见。900×700 下可纵向滚动到保存操作。Windows 实际使用 Microsoft YaHei UI，回归覆盖后备字体和系统默认字体。见[起始页](verification/native-qt/01-startup.png)与[设置](verification/native-qt/02-runtime-settings.png)。 |
| 标签、筛选与详情 | 通过 | 三个来源标签保持紧凑左对齐。检查控件焦点、启用／禁用、事件选中态和右侧详情。见[详情](verification/native-qt/04-log-detail.png)与[展开筛选](verification/native-qt/05-log-filters.png)。 |
| 设备控制及诊断 | 通过 | 软件相机连接状态、操作可用性和近期诊断均有实际数据；核对从任务日志下钻相同设备并返回原事件。见[设备窗口](verification/native-qt/06-device-management.png)。 |
| 样式隔离和生产目标 | 通过 | 核对项目原有自定义样式、页面及图像归属。保持查看 B，停止生产控制选中的 A，B 继续运行。Designer 共享日志走查由定向回归覆盖。见[生产页](verification/native-qt/07-production.png)。 |

## D 共享诊断

Designer 实际读取 `runtime.logs.connection` 连接诊断，错误码和日志等级断言通过。同实例及 Runtime 实例变化后的重连回归通过。Operator 独立入口回归核对未载入 `apps.designer`。

[原生自动化记录](verification/native-qt/native-runtime-r3-closeout.json)提供本次实例、游标、Job、事件 ID、窗口尺寸和采集方式。人工桌面验收、真实硬件、现场工程、冻结包和 Web 验收均未执行。
