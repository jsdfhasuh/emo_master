# 完整方案的统一视觉参照

本目录只有一个 [参考导航](index.html) 和一个 [可点击原型](prototype.html)。设计与数据合同以本变更的 [design.md](../design.md) 和 [spec.md](../specs/runtime-log-workbench/spec.md) 为准。

## 当前目标结构

| 图片 | 用途 | 证据性质 |
| --- | --- | --- |
| [target-01-task-status.png](images/target-01-task-status.png) / [SVG](images/target-01-task-status.svg) | 当前首页分组、终态冻结、独立关联 Job 展开。 | 静态目标示意，不是 Qt 截图。 |
| [target-02-compact-reading.png](images/target-02-compact-reading.png) / [SVG](images/target-02-compact-reading.svg) | 当前日期范围、收起技术条件、执行结构与右侧详情的空间关系。 | 静态目标示意，不是像素级验收标准。 |

## 保留的结构参考

这些图来自此前方案的静态绘制，内容仍可说明特定结构。**外围导航、工具条、默认筛选展开方式以及时间和技术字段的当前表达，以最新原型和两张目标图为准。** 图中的模拟值、列表长度、已加载条数均不是正式数据保证。

| 图片 | 保留的含义 | 当前交互入口 |
| --- | --- | --- |
| [02_while_tree.png](images/02_while_tree.png) / [SVG](images/02_while_tree.svg) | 同一 Job 内 While 多轮、普通子调用、嵌套 Repeat、节点事件。 | [While 结构](prototype.html#02_while_tree) |
| [04_continuous_calls.png](images/04_continuous_calls.png) / [SVG](images/04_continuous_calls.svg) | 同一 Job 内连续运行的不同根调用；不能按可见行数虚构调用序号。 | [连续调用](prototype.html#04_continuous_calls) |
| [05_execution_time.png](images/05_execution_time.png) / [SVG](images/05_execution_time.svg) | 调用/轮次/节点时间与右侧时间详情。 | [运行时间](prototype.html#05_execution_time) |
| [06_device_diagnostics.png](images/06_device_diagnostics.png) / [SVG](images/06_device_diagnostics.svg) | 任务事件下钻近期设备缓存，再返回原位置。 | [设备诊断](prototype.html#06_device_diagnostics) |
| [07_designer_logs.png](images/07_designer_logs.png) / [SVG](images/07_designer_logs.svg) | Designer 是独立产生端；本机客户端错误不是 Runtime 任务失败。 | [Designer 日志](prototype.html#07_designer_logs) |

旧首页图和旧筛选图没有纳入，避免与当前目标状态冲突。旧算子和调试静态图含有已经取消的演示场景栏，也没有纳入；相应能力完整保留在同一最新原型的 [算子详情](prototype.html#08_operator_reading) 和 [独立调试](prototype.html#09_operator_debug) 入口。

## Qt 基线存档

| 图片 | 基线与限制 |
| --- | --- |
| [q1-while-warning.png](images/q1-while-warning.png) | `fa4b72d751189c2689882a0a0d127fd280a35fde`，既有 While 告警、筛选与详情窗口。旧布局用于说明已实现能力及需要修正之处，不是新方案验收。 |
| [q1-execution-summary.png](images/q1-execution-summary.png) | 同一提交的既有算子执行摘要。保留原样，不以修改后的示意图替换实际存档。 |

## 原型简化不能改变合同

- 原型主要操作已加载模拟数据，未完整实现服务端历史查询、全量分页导出、更早任务目录或能力协商。这些能力仍按完整规格实施。
- “已加载上下文”不代表全部历史；图中树节点和时间是明确的模拟执行边界，不能据此推断真实缺页、重启时间或当前运行节点。
- 原型的近期设备诊断展示有所简化。正式实现继续复用已有通道、HEX / UTF-8、详情和缓存导出；样例中的关联值不构成真实设备缓存 cursor 到 Runtime eventId 的映射。
- YOLO、整数加法和 SQLite 样例演示框架摘要、指标/诊断及主动日志的不同来源。不得把通用 `demo.*` 数据写成所有算子都已记录的真实业务字段。
- 独立调试使用已有会话事件与独立执行结果；它不生成生产 Job，也不伪造正式任务的 `node.completed`。
- 已停止/失败/完成主任务与独立关联任务分别显示状态。普通子工作流仍在同一 Job 的树内；不能把其数量写成独立 Job 数量。

静态图由绘图工具生成。可点击、图片可解码、摘要一致或脚本冒烟通过都不能替代原生 Qt 与软件 Runtime 的实际操作验收。
