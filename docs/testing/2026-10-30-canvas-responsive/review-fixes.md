# P1—P4 复查缺口修正与 Dot 交接

日期：2026-10-30。审查基准：`a28bea27e099b1dadf572d2efea81aca38670b2e`。修正实现：`b4eb353d93aab450d1cd7505c999a6acfabdb2b5`。

用户在审查后要求修正问题、更新计划并提交推送。本记录说明源码修正和开发验证；P5 的 Dot 桌面、人工拖动手感仍未执行。相关计划：[实施计划](../../plans/2026-10-30-designer-canvas-alignment-responsive-plan.md)、[Dot 23 项计划](../../plans/2026-10-30-dot-desktop-canvas-acceptance-plan.md)。

## 1. 已修正的问题

| 复查发现 | 修正行为 | 针对性验证 |
| --- | --- | --- |
| P4：A+B 多选、主选 B 输入未应用内容后 Ctrl 取消 A，表单被覆盖 | 主选不变只更新选择集合、装饰与菜单；保留合法／非法输入；切换主选继续提交／校验 | 两种输入反例修前均失败，修后均通过；修正非法值后正常保存，选择变化不增加历史 |
| P3：480 宽窗口图片卡片 213px，内部 ImageView 仍为 240px，右边缘被裁掉 | 响应式图片内部允许按可用宽度缩小，维持原比例绘制；结果图片与文件图片同一路径 | 编辑／运行 × 结果／文件图片，实测色带像素与卡片几何；不只比较外层 card |
| P2／P4：只显示插入线／半卡片，没有候选重排 | 候选与提交共用摆放函数；借用真实叶控件原生测量，以临时容器测量标题／边框并调用同一布局器；显示移动对象与受影响兄弟控件的结果轮廓及滚动范围变化 | 宽窄自动编组、长标题跨容器、多选、新组件、长页面、同候选缓存、取消；候选与提交几何差不超过 2px |
| P3：当前无设备 2.8 页面仍被旧测试包入口拒绝 | 文件菜单在当前格式显示“导出 Runtime 项目包…”；复用受管包构建器导出权威草稿，保留 2.8／1.1、设备／资源／页面；资源转换只改包内文档副本 | 导出未应用文字并实际安装重读；原磁盘文件、输入文档不被隐式改写；旧 2.2 包及导入约束继续通过 |
| 测试遗漏：两项页面快捷键测试直连旧 renderer | 焦点使用 PageCanvas，卡片点击通过 graphics proxy 映射到 viewport，保留原断言 | 修前两项失败，完整快捷键文件修后 29 通过；没有把该测试入口问题当作产品 Ctrl+D 缺陷 |

实际拖动开始前先处理待应用属性，确保拖影与候选测量使用已接受内容；非法输入保留且本次拖动不启动。新控件使用同一原生 renderer 的单控件原型，在独立 graphics scene 中测量并在一次拖动中复用；不会加入真实画布，也不会重建真实数据控件或发起真实查询。

拖动位置在同一插入／编组候选内移动时只更新指示线，候选或可用尺寸改变才重新求解。取消清理轮廓和临时原型，工程与真实图片／表格状态保持。这里只陈述实现及定向验证，没有帧率或实际手感结论。

新格式导出是本地 `.vxpkg` 文件操作，实际消费入口为独立 Runtime 的“更新工程”。本轮仅用临时目录执行归档构建与安装重读；没有更新用户 Runtime 或现场工程，也没有通过降级工程／删除设备字段绕过旧包限制。

## 2. 本轮执行的验证

解释器 `C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe`，Python 3.10／PySide2 5.15.2.1。Qt 用例使用临时工程与模拟客户端，源码归属检查解析到 `D:\jsdfhasuh\documents\my_project\emo_master\src`。组合有交集，不相加成唯一用例总数。

| 检查 | 结果／证据 |
| --- | --- |
| 原响应式编辑 29 + 候选预览 13 + 选择输入 2 | **44 passed in 17.56s**；[XML](evidence/review-page-responsive-after.xml) |
| 最终候选预览 13 + 拖动前输入 4 | **17 passed in 7.17s**；[XML](evidence/review-preview-final.xml)。包括最终轮廓与滚动偏移处理 |
| 原工作区／pending inputs／drops／visual editor／workspace modes／palette／alignment actions | **45 passed in 22.05s**；[XML](evidence/review-adjacent-after.xml) |
| 图片、路径图片、选中行图片、renderer | **35 passed in 3.31s**；[XML](evidence/review-image-after.xml) |
| 完整快捷键文件 | **29 passed in 13.54s**；[XML](evidence/p1-review-shortcuts-after.xml) |
| 新草稿导出、旧测试包和导出日志 | **26 passed in 7.67s**；[XML](evidence/p3-review-export-after.xml) |
| 原受管包默认磁盘读取、资源和更新正常路径 | **3 passed in 6.36s**；[XML](evidence/p3-review-runtime-package-regression.xml) |
| 纯响应式编辑命令与选择输入 | **26 passed in 1.83s**；控制台定向结果（未生成独立 XML） |
| Ruff | 本轮全部 **17 个 Python 文件**通过 |
| mypy | 本轮 **10 个源码文件** `--follow-imports=silent` 通过；不表示全仓库普通 mypy 已通过 |
| 源码启动导入检查 | `scripts/dev.py run-designer --local --check` 通过；没有打开窗口或启动 Job |
| 暂存空白／证据结构 | 排除原始 XML 后通过；本轮 18 份 XML 均可解析。原始失败 XML 中 22 处堆栈行尾空格保留，未格式化原证据 |

## 3. 修前失败和开发中间失败

- 审查基准上合法／非法属性丢失、半卡片预览与提交不一致的辅助反例：[JSON](evidence/review-baseline-probes.json)。这是 offscreen 原生 Qt 辅助证据，不是桌面 QDrag 验收。
- 选择输入修前 **2 failed**：[XML](evidence/review-selection-before.xml)；修后 **2 passed**：[XML](evidence/review-selection-after.xml)。首次命令曾有 PowerShell 参数格式错误，未执行测试，未算作产品失败。
- 图片最终有效修前组合 **2 failed、2 passed**：[XML](evidence/review-image-before.xml)，两项运行显示都实际丢失右侧色带。更早的 [夹具试作](evidence/review-image-fixture-before.xml) 也保留；其中空文件路径的 show 调度清空了测试图，修正夹具时没有把该结果当作有效裁切证明。
- 新候选模块第一次集成 **8 passed、3 failed**：[XML](evidence/review-drop-preview-first.xml)；仅数值／图片／表格新控件的原型高度分别偏差 4／4／20px。普通 QWidget 原型与 graphics proxy 的字体继承不同，再次定位失败 [XML](evidence/review-palette-preview-first.xml) 仍保留。改为同类 graphics proxy 承载后 13 项通过，未放宽 2px 断言。
- 快捷键修前 **2 failed**：[XML](evidence/p1-review-shortcuts-before.xml)；导出修前 **2 failed、1 passed**：[XML](evidence/p3-review-export-before.xml)。分别保留完整修后组合，不用新结果覆盖原文件。

初版记录中扩展 Presentation／Runtime 组合等待后中断、普通跨导入 mypy 图片模块诊断等历史边界仍保留，本轮没有把它们改成通过。未执行全仓库 pytest、Dot／人工桌面、冻结 EXE／安装包、真实设备和发布验证。

## 4. 交付与下一步

本轮正常提交并推送到 `codex/logging-and-designer-improvements`；功能修正提交为上方固定 SHA，随后文档提交更新计划与证据说明。初版 `7ef12599` 保留为历史基准；Dot 使用本轮修正提交，读取更新后的计划。

Dot 原 19 项基础上新增 R13 多选输入、R14 窄窗图片完整性、R15 当前草稿导出、R16 候选重排结果，共 23 项。R15 明确允许临时本地工程归档导出，同时仍不更新实际 Runtime。这里只交付计划，没有启动 Dot 或发送外部消息。
