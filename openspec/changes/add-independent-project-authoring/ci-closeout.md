# V7.1 — CI 集成收尾补充计划

计划整理：ChatGPT Web，2026-11-14。执行对象：Sol。
活动变更仍为 `add-independent-project-authoring`；这是原任务 **6.7 的细化**，不是新的功能版本、独立 change 或架构重写。

**本计划待实施。** 固定依据为 `9dce7543232042c6840d9cc20c72a4a903556154`（下称 9dce7543）的源码、活动计划和上一轮已下载的 CI 产物。它不是对执行时最新 main 的状态承诺。任务完成状态只在 [tasks.md](tasks.md) 维护，本文不另建一套复选框。

## 1. 这次改什么，不改什么

保留原 0–5 节 25 项完成记录、证据映射，以及 6.0–6.6 的完成状态。F1–F6 已修复的关键实现和相应通过记录不重开；原记录中的“本轮”仍指它原来所属的执行轮次，不冒充本次新测试。

本轮只处理已观察到的三类 CI 集成问题：

| 项目 | 已有证据 | 本轮目标 |
| --- | --- | --- |
| R1：Runtime 目录测试实例身份 | 两平台各 5 项 Runtime 测试缺 `runtimeInstanceId`；两项图标集成复用同一 fixture 并等待目录失败 | 修正测试构造前提，确认目录/端口/图标及重连回归；先修共同原因再判断余下失败 |
| R2：ROI 测试接口过时 | 两平台各 4 项 ROI 用例访问不存在的 `RoiCanvas._image` | 按当前画布实际接口保留等价图像与交互断言 |
| R3：YOLO 小窗口裁切 | Linux 在 460、620 宽度失败，Windows 在 460 宽度失败；可见区域高度小于控件高度 | 在原窗口尺度定位并修正局部布局或确有证据的测试同步问题，保持可操作要求 |

不新增旧 Web 迁移、MCP 工具、Runtime 执行权限、协议字段、SDK 版本、Qt 框架迁移、全局防御机制、压力矩阵或专用 CI。没有新证据，不把 SQLite 绑定、私有 ignored 测试和其他历史失败加入本轮必修清单。R3 的具体根因仍待复现，不预判为 DPI、字体或某个布局属性。

## 2. 固定证据与执行基线

### 2.1 9dce7543 的已观察结果

| 工作流 / 分片 | 结果 | authoring 结果 |
| --- | --- | --- |
| 主 CI / Linux runtime | 5 failed | 不在该分片 |
| 主 CI / Windows runtime | 5 failed | 不在该分片 |
| 主 CI / Linux other | 8 failed | 59 passed，0 failed，0 skipped |
| 主 CI / Windows other | 7 failed | 59 passed，0 failed，0 skipped |
| Web panel isolated checks | success | 按其原任务范围 |

主 CI 为 [run 43402182752](https://github.com/jsdfhasuh/emo_master/actions/runs/43402182752)，Web CI 为 [run 43402182710](https://github.com/jsdfhasuh/emo_master/actions/runs/43402182710)。以上均为固定 SHA 的历史执行结果，不是本包生成时重新启动的 CI。两平台各 59 项是相同测试组的跨平台执行，不相加称为 118 个不同用例。[S1][S2]

### 2.2 接手顺序

先读当前 `AGENTS.md`，记录实际 HEAD、分支、工作区及远端参考。用户最近交付在 main；确认仍在该分支后继续本轮，不新建分支、不回退到 9dce7543、不覆盖未提交修改或 `.tmp/` 私有材料。

比较 9dce7543 之后与 R1–R3、活动计划相关的变化。已经由后续提交修好的项，用当前证据关闭，不重复改代码。无需为取得基线执行强制同步、重置或清理。历史“已推送”不等于本轮自动获得提交/推送权限；执行会话已有明确授权则沿用，不重复请求。

计划只合并到原活动目录；保留 proposal/design/specs/file-transfer 的现有契约及历史 V6/V7 归档。包内原审查报告用于修前定位，不列作本轮执行结果。

## 3. R1：目录测试 fixture 的实例身份

### 3.1 固定源码入口

- `tests/runtime/test_operator_icon_assets.py`：`iconService()` 及使用它的目录、图标、重连测试。
- `tests/runtime/test_operator_port_specs.py`：`testListOperatorsKeepsLegacyTypesAndExposesFullSpecs`。
- `tests/designer/test_operator_icon_integration.py`：`testActualRuntimeContractFeedsCustomQtIcons`。
- 只读核对正式 `src/emo_master/apps/runtime/grpc_server/service.py` 的构造及 `ListOperators()` 返回路径。[S3]

原 fixture 经 `RuntimeService.__new__()` 或 `object.__new__()` 跳过构造，只有插件目录；正式方法现在返回 `self.runtimeInstanceId`。这是测试对象前提不完整的直接证据，不能据此断言正常生产 Runtime 必然缺身份。图标 UI 的两项失败与同一 fixture 有调用链关联，但修后是否全部解除仍须复跑。[S1][S3]

### 3.2 最小实现

给现有测试对象明确、稳定的测试实例身份，或复用已正确初始化且不会扩大测试成本的现有 fixture。身份属于该测试实例：同实例查询不变；测试确实创建新的 Runtime 实例并验证身份变化时，使用不同的身份。不要只为省代码新增通用 Runtime fixture 框架。

在已有目录测试里增加一条回复身份等于该实例身份的断言即可。保留端口完整声明、历史类型字段、图标字节/摘要、真实 gRPC 往返、重连及 channel 所有权等已有断言。

不得在产品方法里用 `getattr(..., "")`、主机名或每次查询生成随机 ID 来包容半初始化测试对象；不得删掉身份字段、缩减目录内容或先延长 UI 等待时间。若复测发现另一个实际问题，再按证据局部处理。

### 3.3 通过条件

上述两个 Runtime 测试文件与图标集成文件完成直接回归，原各平台 5 项 Runtime 失败和 2 项关联图标失败有明确结果；不因修 fixture 改变生产协议或 SDK。没有跑到的平台仍标未验证，最终交由原 CI 平台组合确认。

## 4. R2：ROI 用例改用当前画布接口

入口为 `tests/designer/test_draft_pure_preview_ui.py` 的 `testSpecializedLocalImageErrorRecoveryPreviewAndClose`，固定失败参数为 `roi-2.1`、`roi-2.2`、`roi-2.3`、`roi-2.4`。[S1][S2]

先定位当前 `RoiCanvas` 类、图像加载和显示路径，确定已有可观察接口。将过时的 `_image` 访问换为对同一业务事实的有效断言；不预设应改为哪个新私有字段，更不能只用 `hasattr`、默认值或“不抛异常”代替图像真的载入。

保留原用例的正常本地图像、错误后恢复、预览及关闭行为；涉及草稿不写回等既有断言也保留。优先只改测试观察方式；只有直接复现出产品缺陷时才局部改产品实现。不得为迁就旧测试给画布添加无实际用途的影子 `_image`，不升级算子版本、不删除旧参数化分支、不重做 ROI 架构。

通过条件是该参数化测试原支持分支完成验证，观察方式与当前实现一致，功能覆盖未缩水。固定异常只证明测试接口不一致，本轮不把未做的人工 ROI 绘图验收写成已通过。

## 5. R3：YOLO 小窗口的真实可操作性

入口为 `tests/designer/test_yolo_parameter_presentation.py` 的 `testCompactYoloWindowKeepsValuesSourcesAndActionsUsable`。复用其原参数化尺度；首先复现固定失败的 460/620 宽度，不扩大窗口消除失败。[S1][S2]

在 Qt 正常完成布局与事件处理后，查看失败控件、父容器、滚动区域及按钮区的实际几何/可见区域；保留一份能说明裁切位置的尺寸记录，具备原生窗口环境时配一张代表截图即可。无需建立字体、DPI、桌面环境的排列组合测试。

依据复现结果局部修正实际原因，例如最小尺寸约束、行布局或滚动区域分配；这些只是排查方向，不是已确认结论。若确为测试未等布局完成，用现有事件/等待辅助解决，不加任意长 sleep。若原断言确与正式滚动交互契约矛盾，必须说明依据并用等价的可操作性检查替换，不能改成仅检查控件存在。

数值、来源选择和确认/取消等原断言覆盖的动作仍须正常使用。不得靠隐藏/禁用控件、裁掉断言、加 skip/xfail、扩大用例窗口或关闭 CI 分片变绿。不重构整个 Designer，也不新增全局 UI 布局框架。

通过条件是原参数化用例及直接受影响测试通过，原业务值和动作未丢失；Windows/Linux 的差异由既有 CI 确认。只有实际取证才能给出具体根因。

## 6. 验证安排：先定点，再集中收尾

### 6.1 局部迭代

采用仓库声明的 Python 3.10/PySide2 环境和当前 CI 的依赖/Qt 初始化方式。每修一类，先跑对应文件或已有用例；不要每次改一行就重复整仓。下列是从仓库根目录执行的目标选择示例，不替代依赖安装和平台环境设置：

```text
python -m pytest tests/runtime/test_operator_icon_assets.py tests/runtime/test_operator_port_specs.py tests/designer/test_operator_icon_integration.py -q --tb=short
python -m pytest tests/designer/test_draft_pure_preview_ui.py -q --tb=short
python -m pytest tests/designer/test_yolo_parameter_presentation.py -q --tb=short
```

使用工作区测试时明确是否含未提交修改；不得称为该 HEAD 干净 checkout 的结果。私有 ignored 用例不强制推送，不能把它们的本地失败计数混成 GitHub 的失败数。

### 6.2 合并修复后的一次集中回归

复用上述受影响文件和 `tests/authoring`，确认原 authoring 路径没有被改坏；固定基线是 59 项通过，但后续合理新增用例时不以“数量必须等于 59”为验收合同。保留现有检查入口：

```text
python -m pytest tests/authoring -q --tb=short
python -m ruff check src tests
python -m mypy --config-file mypy.ini src
python scripts/gen_proto.py --check
openspec validate add-independent-project-authoring --strict
```

上述是应执行的命令，不是本包已跑结果。生成检查复用既有入口；本轮不应主动改 proto 或生成代码。不添加全局 ignore，不降低原 CI 断言，不新增平行的测试产品线。

### 6.3 原 6.7 的贯通验收不取消，也不重复放大

G1–G7 仍是观察目标，而不是七套重新开发任务。F1–F6 的已完成证据保留；本轮基于实际最终源码复用已有 Inspector + HTTP 辅助流程做 **一次** 图像/文件交付，Designer 正式打开同一下载物、继续编辑与保存；软件 Runtime 的明确运行仍使用原隔离样例，不新增 MCP 运行能力。

复用 `scripts/authoring_acceptance.py`、`tests/authoring/test_designer_handoff.py` 及当前指南中已验证的入口；外部 Inspector 路径和实际下载目录按本机环境填写，不使用占位路径冒充执行。沿用既有用例完成复制/声明采用/导出语义的观察，不为 R1/R2/R3 分别再跑一套全链路。原生 Qt 自动化、人工 UI、软件 Runtime 运行分开命名；不增加相机/PLC 实物验收门槛。

### 6.4 现有 CI 和关项

得到本轮推送授权后，核对**最终交付 SHA** 的原主 CI（Windows/Linux、runtime/other）及 Web panel isolated checks；记录 run ID、attempt、head_sha、完成状态和结论。不要把 9dce7543 的 Web 成功借给后续提交，也不以“已触发/仍运行”代替通过。

新失败先按日志定位并判断与本轮改动的关系；实际阻塞继续处理，确属范围外的问题单独说明，不擅自扩成无限清仓。无推送授权或无远端验证条件时交付本地结果，保持 6.7 未完成，不自动创建监控任务或声称以后会完成。

只有 R1–R3 有真实处置与验证、约定贯通验收完成、最终 SHA 的现有两条工作流结论满足要求，才勾选 6.7。例外必须有用户明确处置决定并如实记录，不伪称全绿。归档、合并、发布是独立授权事项，本计划默认不执行。

## 7. 记录与交付

只维护一份本轮简短报告，建议放在 `docs/testing/<实际日期>-independent-project-authoring-ci-closeout/README.md`：实际源码/工作区、R1–R3 触发与根因、最小改动、测试命令及结果、最终 SHA/run、未验证项。原始历史 XML、截图、Inspector JSON、V6/V7 归档和私有材料不覆盖。

本包 `review/` 中的旧审查报告和聚合 JSON 保留原字节，是修前依据，不算本次运行证据。原完整 CI 诊断和六份私有日志不在本包中。公开记录只包含可公开的说明、必要汇总与摘要，提交前检查本次实际暂存清单，不将审查材料自动全量入库。

获授权推送后，在对应提交或 PR 评论中标明实际执行者，例如 **Sol**，同时写明 **ChatGPT Web 计划来源**，列出本次改动、已验证和未验证项。不得把 Sol 的执行冒写为 ChatGPT Web 已运行；未推送则不声称已附评论。

## 8. 来源与证据范围

本包整理阶段只读取固定源码/计划、复核现有 CI ZIP 的摘要与 JUnit 计数，并验证计划补丁；未修改或推送仓库源码，未在本环境执行应用/Inspector/Qt 验收。执行者应核对后续变化。

- **[S1]** 包内 `review/emo_master_9dce7543_repair_review.md`：上一轮固定提交复核，原件保留。
- **[S2]** 包内 `review/emo_master_9dce7543_ci_summary.json`：原四分片失败名称、计数与 artifact 摘要；`validation.json` 记录本包的再核对口径。原 `junitEntries` 沿用报告总数口径；other 的报告 tests=6266 包含额外 subtest 计数，实际 `<testcase>` 元素为 6200，不与唯一用例数混算。
- **[S3]** [固定提交源码](https://github.com/jsdfhasuh/emo_master/tree/9dce7543232042c6840d9cc20c72a4a903556154)：R1 路径见第 3 节，ROI/YOLO 路径见第 4–5 节。
- **[S4]** [原活动任务](https://github.com/jsdfhasuh/emo_master/blob/9dce7543232042c6840d9cc20c72a4a903556154/openspec/changes/add-independent-project-authoring/tasks.md)、[原验收](https://github.com/jsdfhasuh/emo_master/blob/9dce7543232042c6840d9cc20c72a4a903556154/openspec/changes/add-independent-project-authoring/acceptance.md)、[AGENTS.md](https://github.com/jsdfhasuh/emo_master/blob/9dce7543232042c6840d9cc20c72a4a903556154/AGENTS.md)：保持完成记录、原七组观察目标、正常路径优先和推送角色约定。
