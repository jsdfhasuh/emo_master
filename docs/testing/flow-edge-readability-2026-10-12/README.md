# 连线可读性正式交付与开发验证

日期：2026-10-12。实现提交：`1f76a94c8c6689aefd7600b9eb2cd3ac41869c58`；正式基线：`4bcce9a9e4c158dcd618b04239bb9d8397ba9f73`。目标远端分支：`agent/runtime-workflow-architecture-v1`。用户自行转交 dot 提示词，开发侧未向 dot 发消息。

状态：A1/A2 已实现，A3 正式检出软件回归完成，`READY_FOR_DOT_WITH_KNOWN_RISK`；性能预检部分 `FAIL`，dot 独立验收 `NOT_RUN`。本目录只记录正式限定范围交付，不把旧 dirty 快照的完整 CI 结果套用到当前版本。

## 1 交付范围

普通灰线 1.6，淡化 1.2，聚焦蓝线 2.2，选中琥珀线 3.2；受阻保留橙色虚线。节点/多选/端口/单边直接聚焦，端点与方向提示，同源同诊断公共段去重，候选支路菜单、精确选择/删除/撤销、生命周期清理。

正式提交同时包含此前未提交的相关画布前置：端口感知显式布局、路由通道/分片优化、窄命中与视图导航边界。查看和聚焦不移动节点、不触发布线。与基线相比并非仅色值变更；具体边界见 [计划](../../plans/2026-10-12-flow-edge-readability-v1.md)。

排除原工作区的页面设计、Runtime、YOLO、工作流导航等其他任务及私有工程。恢复正式基线的一参数工作流打开回调，未将其他 MainWindow 导航改动混入。原工作区未 reset/覆盖/清理。

## 2 正式源码验证

环境：Windows build 26200、1920x1080、逻辑 DPI 96 / DPR 1，Python 3.10.21，PySide2 5.15.2.1 / Qt 5.15.2；解释器为现有 `emo_master` conda 环境。测试从独立 Git worktree 的 `src` 导入，不依赖原 dirty 工作区。原生环境记录为 `delivery/environment.json`。

| 范围 | 结果 | 证据 |
| --- | --- | --- |
| 专项回归：聚焦/公共段/选择/Scene/路由/布局/拖动/纯算子示例 | 89 PASS | `delivery/focused-final.log`、`delivery/focused-final.xml` |
| 正式版本 Proto / Ruff / mypy | PASS；mypy 404 个源文件 | `delivery/ci-check.log` |
| 正式版本全量 pytest | 5016 PASS / 11 SKIP，另有 28 subtests PASS；1527.65s，退出码 0 | `delivery/ci-check.log` |
| 原生 Windows Qt 自动交互 | 36 PASS，`QT_QPA_PLATFORM=windows` | `delivery/native-final.log`、`delivery/native-final.xml`；不等同 dot |
| 独立源码启动检查 | PASS；没有打开窗口或启动 Job | `delivery/startup-check.log` |
| 冻结输入核对 | 1021 文件，changed/added/removed 均为空；tracked source diff 退出码 0 | `delivery/source-check.json` |
| 可见场景性能预检 | PARTIAL_FAILURE；聚焦目标通过，两个固定几何的局部拖动增幅超门槛 | `delivery/performance-summary.json`、前后 `timings.json` |
| dot / DPI 矩阵 / Debian / 安装包 / 设备 | NOT_RUN | 各自验收，不相互替代 |

上述专项与全量范围重叠，PASS 数不能相加。QtTest/场景渲染仍是开发自动化，不是 dot 独立桌面验收。

完整回归启动前冻结 `src/tests/scripts/examples/proto/config` 的 1021 个非缓存文件哈希；结束后逐项核对无漂移。前后清单 SHA256 相同，为 `e00bfefbbd66e78c357bd02499dd55ca1c6b7fc7505acf9cff466a9f125b4e83`。原始清单仅在本机保留，正式结论与差异写入 `delivery/source-check.json`。

交付整理时发现复制 Scene 带入另一任务的双参数导航接口，mypy 曾报一处不匹配；已恢复正式基线接口，专项复测 89 PASS，完整 CI 使用修正后的实现提交。不削弱测试或混入另一项导航变更来消除错误。

原 dirty 冻结快照曾有不同数量的代码/测试及失败，不与本次正式结果合并。旧补丁、未跟踪源码 ZIP、私有截图和原始私有工程不发布、不用作正式验收输入。

## 3 可复现夹具与对照

[无设备示例](../../../examples/flow_edge_readability/README.md) 提供 fanout/sparse/dense/crossing/blocked 五个工作流。fanout 为 8 节点/12 边，其中 seed 同一输出恰有 4 个比较支路；只含数值和比较算子。示例的四个可运行工作流已由专项测试实际执行；blocked 故意重叠，仅查看受阻，不作为运行错误测试。

开发辅助脚本为 `scripts/validate_flow_edge_readability.py`。固定 100 节点/300 边只用于显示几何压力，不可称为可执行业务工程。`--visible` 仍是自动化 QGraphicsView，不代替真实 Designer 鼠标操作、空白搭建、保存重开。

### 开发对照与性能风险

只使用本仓库公开示例和固定几何，不含私有项目。前后场景截图保存在 `delivery/visual-before/`、`delivery/visual-after/`，包括 `project-overview.png`、`project-node-focus.png`、`project-edge-focus.png`；后者另有局部支路菜单 `project-branch-menu.png`。已查看这些实际 PNG；它们是开发场景渲染，不是完整 Designer 或 dot 桌面截图。

本轮自动化对照从正式基线提取原始 `flow_scene.py`，Git blob 为 `a630a023f041e8ac99fbb15b45816670e62665cd`，双方均使用当前版本的路由/布局支持。它可用于同工程/固定几何的场景比较，不能冒充两个完整历史应用的运行结果。QGraphicsView 自动场景也不代替真实 Designer 的窗口/zoom/DPI 矩阵；dot 应另采集完整应用对照。

每组 5 次预热、30 次记录。以下为可见 QGraphicsView 含重绘的 P95，单位 ms；原始中位数和样本数见前后 `timings.json`：

| 夹具 | 聚焦前 / 后 | 局部拖动前 / 后 | 拖动 P95 增幅 | 20% 增幅门槛 |
| --- | --- | --- | --- | --- |
| 固定 9 节点 / 18 边 | 4.54 / 7.21 | 11.18 / 19.11 | +70.9% | FAIL |
| 固定 100 节点 / 300 边 | 11.26 / 56.96 | 125.02 / 179.23 | +43.4% | FAIL |
| fanout 8 节点 / 12 边 | 8.70 / 10.13 | 8.07 / 9.65 | +19.6% | PASS_SCENARIO |

聚焦 P95 均低于 100ms，不代表所有交互都通过。局部拖动增幅有两组超出原定门槛，明确记录 `PERFORMANCE_PRECHECK_PARTIAL_FAILURE`，不得放宽阈值或拿旧 dirty 快照的较好采样替换本次结果。压力图基线本身已超过 100ms，本次又增加约 54ms；这是 dot E16 应优先复核的已知风险，必要时由开发侧继续优化。

计时只覆盖该脚本的局部拖动与绘制，不包含释放后完整路由收敛，也不含 VNC 传输；不能凭有限样本断言所有真实业务工程的延迟。真实私有业务工程本轮未在正式交付中复测。

fanout 的实际边仍为 12 条，派生绘制为 40 个片段，其中 8 个共享片段、避免 15 份重复片段、6 个真实分叉标记。去重的是绘制，不是删除业务连接。

正式 dot 对照应使用两份完整隔离检出：基线 `4bcce9a9e4c158dcd618b04239bb9d8397ba9f73` 和最终交付 SHA。两版读取同哈希工程副本，保持坐标/窗口/DPI/画布 zoom，不主动布局。旧应用进程与 EXE 不会因源码推送而更新。

## 4 交接

按 [E01-E16 清单](../2026-10-12-dot-flow-edge-readability.md) 真桌面验收。必须额外从空白工程搭建、保存到新目录并重开，分类报告视觉、命中、误删/撤销、数据完整性、连接契约、生命周期、性能、入口发现和平台限制。

当前交付不是整轮桌面 PASS：独立 dot/DPI 尚未执行，且 E16 开发性能预检已有部分 FAIL。dot 应分别报告功能正确性、可读性和性能，不能用软件检查全绿替代未关闭的性能风险。

启动：在正式独立检出根目录运行 `python scripts/dev.py run-designer --local`，使用 Python 3.10 与仓库依赖。`--local` 为该目录使用独立嵌入 Runtime 数据，并移除子进程的外部 Runtime/Qt 覆盖，不改父终端或连接生产服务。

源码、自动化、dot、Windows/Debian、安装包、现场设备分别下结论。没有原生桌面的用例记 `NOT_RUN`，平台不支持记 `UNSUPPORTED`，不能用 offscreen 补成 PASS。dot 只测试回传，不自行修代码/提交/推送/部署或扩大到生产设备。
