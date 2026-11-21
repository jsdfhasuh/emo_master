# OpenSpec change：redesign-runtime-log-workbench

## 实施状态

本 change 的产品实现、验证结果和原生 Qt 截图见 [实施与验收记录](implementation-report.md)，勾选状态见 [tasks.md](tasks.md)。下文保留附件的原始计划状态。

## 附件基线

- 修订版本：**V2.3 算子覆盖补齐与精简审查**，2026-11-21。
- 固定源码基线：`9d59fd16a1d0d3ec0c60725c839ff38183d6916b`；本次复核远端 HEAD 一致。
- 本包仅包含设计、实施计划、验收标准和既有交互原型，**不含正式实现**。任务初始全部未完成；没有 Qt / Runtime / 真机验收通过的声明。
- 正式栈：Python 3.10、PySide2 / Qt Widgets、现有 gRPC；不引入 WebView 或另一套日志服务。
- 本版补齐所有节点的真实执行摘要与单算子调试入口；此前已确认的来源、任务、循环、筛选、时间与设备交互保持。原型样例与真实源码结构对齐，数值和连接仍为模拟。

## 阅读与使用

1. 先读 `sol-handoff.md`，再核对目标仓库当前 `AGENTS.md`、HEAD、工作区。
2. 先打开 `reference/prototype.html` 体验当前场景，再看 `reference/visual-gallery.html`：9 张 PNG + 9 张 SVG 静态布局参考图可进入对应场景；本版更新图 08 通用算子详情，新增图 09 独立单算子调试。另读 `reference/visual-guide.md` 的逐图约束和未演示能力。图 03 保留区域关系，详情内容以新版图 08 / 09 为准。这些图片不是浏览器或 Qt 截图，不作为像素基准。
3. `design.md` 定义数据、身份、范围和交互合同；`specs/runtime-log-workbench/spec.md` 定义必须达到的行为。
4. `tasks.md` 是唯一实施进度清单；按阶段实现，实际完成后才勾选。
5. `acceptance.md` 给出聚焦自动验证与两条 Qt + 软件 Runtime 走查；`source-map.md` 给出现有代码和测试入口。
6. `operator-coverage.md` 与 `reference/operator-coverage.json` 记录 82 个内置算子的静态盘点；盘点不是逐算子测试清单。`review-notes.md` 记录本轮查漏与精简判断；`readiness-check.md` 记录计划文件与原型的实际验证边界。

## 本次收口

保留既有查询、唯一实时源、循环、时间、设备与完整 Job 导出合同。V2.3 将 node.started / completed / failed / skipped 中已返回的 ioSummary / metrics / diagnostics 设为通用易读展示的必做项；node.log.payload.data 仍表示作者主动记录的字段。单算子调试复用同一展示组件，适配已有 ReadOperatorDebugEvents 与 GetOperatorDebugExecution，不造正式 Job 或 node.completed，不新增 RPC。原始检查继续使用 LogJsonTree。

不逐个给 82 个算子补重复 logger.info，不为每个算子建立专属渲染器，也不新建成功预览历史或图片库。新增的调试入口接线单列任务，验证合并到既有检查；仍保留两条原生 Qt + 软件 Runtime 走查。

不新增任务表、设备日志表、全库扫描服务、通用补偿框架或硬件测试矩阵。不能仅凭 OpenSpec 校验或原型承诺最终实现成功；最终交付以实际变更及验收证据为准。

## 给实施者的视觉要求

先体验原型，再对照图片和视觉指南实施。保留已确认来源关系、任务优先、折叠信息区、高级筛选、执行树、运行时间、右侧详情和设备往返。没有主动业务日志的算子，也应能读到已有执行摘要。调试会话事件和执行结果明确各自来源；原始页保留现有树能力。不能只看 PNG 仿制页面，也不能因原型简化而删掉 design/spec 中要求的历史查询、完整导出和诊断能力。
