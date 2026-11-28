# Runtime 总体计划修订记录

## R3 · R2 可行性审查落实

2026-11-27，Codex。输入为 R2 计划包，SHA-256 为 `d86c4d8dba8dd551d50e20cfce1dee2a9cb956fb7040b68937271270c4ad973b`。源码定位按 `48b30191884ad5ef5d96e8d26591104d16d54a58`。上一轮的两个反例运行于干净工作区 `78a547270324553b8e88dea5a05258e562fcfb08`，涉及的 Operator、页面渲染及准入方法与计划基线一致。以下是修订依据，修复完成情况由实施后的产品验收记录。

| R2 审查发现及证据 | R3 决定 |
| --- | --- |
| **受控内存反例。** Python 3.10.21，设置一个并发名额且由 B 占用。Runtime 对 A 返回 `E_MAX_CONCURRENT_JOBS`，Operator 返回 `READY/canStart=True`。释放后两者均允许开始。 | 手动开始和已有自动启动接入同一 Runtime 准入结果，在既有宿主状态允许时启动。验收 A 核对占满和释放，验收 B 复用包更新后的 autoStart 两值用例。 |
| **Qt 离屏 + 软件 Runtime。** A 与 B 是同一工作流的两个真实 Job。A 在第二页，B 切到第一页，返回后实际 Job 为 A，显示页却仍为第一页。 | 保留 A 的原生产页和结果来源。验收 E 故意让两个 Job 停在不同页，分别核对页号与 Job 身份。 |
| **固定提交源码。** 系统事件页、客户端绑定和打开查询分别位于 Designer 外层窗口。 | 将系统事件入口及查询写入 P6.7，并在 F 验证无 Job 系统事件与 Job 事件各自可见的位置。 |
| **固定提交源码及既有用例断言。** Designer 的目录能按选中项停止任务，Operator 生产页已有独立控制目标。 | 采用只读 Operator 日志目录，保留 Designer 原动作。验收 C 在观察 B 期间从生产控制停止 A，并核对 B 继续运行。 |
| **固定提交源码与正式规范。** Job 窗口与普通日志表使用两组缓存，只有 Job 页本身知道当前匹配事件。正式规范限定整 Job 导出为固定上界内仍可查的记录。 | P6.14 明确两组事件的来源。验收 F 比对实际导出文件，包括本 Job 窗口外事件与另一 Job 同级别事件。 |

修订沿用 R2 的 Change ID、任务编号、视觉参考和验证范围。R3 的校验命令及结果见[计划包检查记录](validation.md)。

## R2 历史记录

### 1. R2 修订依据

用户要求：审查并改正过度设计、过度防御与不必要测试，重新交付 OpenSpec 计划压缩包。R2 保留 Runtime 总体目标、Operator 视觉改进及日志增强，收紧实现和验证范围。

固定源码基线为 `48b30191884ad5ef5d96e8d26591104d16d54a58`；2026-11-27 复核 main 未变化。R1 输入 ZIP 的 SHA-256 为：

```text
161fcc1bb6a7d158b0a68085d18f33d3057e5a597e68fe889307786ebba995b5
```

### 2. R2 已落实到计划的修改

| 审查问题 | R2 修改 | 主要位置 |
| --- | --- | --- |
| 已有核心领域被写成无条件完整重验 | 采用本期必做、按实际影响触发、保留约束；正常主链保留，内部专项按 diff/依赖/真实缺口触发 | proposal、design §3、tasks P1—P5、acceptance A—E/I、gap-matrix |
| Web 验收与“只验触及契约”冲突 | 两个受影响宿主必验；Web 专项仅实际影响时追加，既有必需 CI 保留 | design §7、P7、验收 G、总体 spec |
| 共享抽取范围易扩大 | 只抽必要读取、事件转换、模型和 UI；Designer 保留完整 RuntimeClient，明确抽取停止条件 | design §6、P6.1—P6.6、日志 spec |
| RuntimeClient 来源写错/遗漏 eventPayload | 补充 Designer services/runtime_client.py 与 page_designer/runtime_logs.py；calls.py 定位为取消/期限基础 | source-map §4 |
| 每项修复固定补一正一反 | 删除配额；复用现有用例，仅补真实覆盖空缺；低影响布局/文案走查 | P2.6、P9、验收验证规则 |
| 历史阅读要求长时间 Job | 改成有限确定性事件，验证固定上界、去重、选中和阅读位置 | P6.15、验收 F |
| 四视图×两尺寸固定截图 | 默认代表视图 + StartupPage 窄窗口，其他按布局影响追加 | visual spec、P9.6、验收 H、visual-baseline |
| 重复任务可能导致重复执行 | 同一相关源码版本证据可覆盖多项；P9 汇总，受影响后才重跑 | tasks P9、acceptance I |
| 轻量准入被描述成完整预检已通过 | 明确完整校验仍由 StartJob 执行，不增加健康服务/全设备门槛 | design §5.1、总体 spec |
| 单一读取来源没有宿主范围 | 每宿主复用读取驱动，允许不同客户端各自观察；保留 EventStore→JSONL | design §6.3、P6.3、日志 spec |
| 原日志来源 Requirement 对宿主范围含糊 | 通过 MODIFIED 明确 Designer 作用域，保留完整 Requirement 和原三个 Scenario；Operator 规则用 ADDED | 日志 spec |
| R1 原型完成度描述过强 | 原字节保留；明确提示式复制/导出/连接、固定诊断和简化树/分页 | reference/prototype-notes.md、README、P8.10 |
| 增量规格标题前有非操作段 | 三份 spec 直接从 ADDED/MODIFIED Requirements 开始 | specs/*/spec.md |

“已落实”在本表仅指计划文档修改，不指产品代码实施或产品验证通过。

### 3. R1 结论如何保留

- V3 的视觉、局部 QSS、返回和原生 Qt 要求继续保留。
- operational/application 两类本机文件继续保留。
- Designer 允许必要 import/adapter 调整，可见界面与原行为保持。
- 不新增第二个自动运行开关，沿用 production.autoStart。
- 源码存在、历史报告和 HTML 原型不等于产品验收通过。

**纠正旧 R5 记录：** 不能再写“设备下钻、导出等均已按同一状态模型实现”。R1 原型只具备部分模拟选择/筛选/详情响应，复制、导出、连接和诊断仍有明确局限。本轮不以修复所有 HTML 演示控件为产品实施前置，正式行为以规格和验收为准。

### 4. R2 保留的保障

真实 Job 身份、查看/停止目标独立、owner、设备代次、显式发布、正常取消、使用中资源不误回收、有界窗口、固定历史上界、原始值语义、导出范围及 QSS 隔离都保留。

本期没有证据要求新增全局锁、第二份 readiness、通用事务/补偿框架、跨进程订阅中心或任意故障组合容错。原本禁止这些机制的要求继续有效。

### 5. R2 实际工作边界

- 已修改计划文档与增量规格，未修改产品源码。
- 原型及两张用户原截图原字节保留。
- R2 strict 的三次执行保存在 [R2 校验原始记录](verification/openspec-validation.json)。
- pytest、产品 CI、原生 Qt、浏览器交互、冻结包、真实设备及现场节拍，本轮均未执行。
- 本轮未提交、未推送或合并 GitHub。后续若获授权推送，评论应标注实际执行者并说明实际改动、验证和未验证项。
