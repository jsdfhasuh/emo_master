# 实施完成后的维护交接

## 当前交付状态

`add-generic-control-advanced-appearance` 的 **13 种可见组件＋开始／停止两个宿主按钮**已完成实现及约定验证，并于 2026-11-23 归档；新增 capability `page-advanced-appearance` 已同步至正式规格。

2026-11-23 收尾核查基线为 `main` 的 `4f3ea2da9ddb2157d85cff195e7fb0b2f5fa3bb6`。该提交的完整 Source CI 通过；适用产品版本 `9a9cc511d2c2eec588b5602405cd364e40ae313a` 的 Web CI 通过，之后至收尾基线仅有日志工作台的规范和交付资料归档。两轮 CI 修复及完整结果见 [acceptance.md](acceptance.md)。

本目录保留 v0.7 计划范围、设计依据、已完成任务及验收过程，供后续维护使用。[tasks.md](tasks.md) 是本 change 的实施完成记录；无需按最初计划重新实施已完成的功能。最初计划核查基线 `6cbff0dcbea671663cf9ab773c6ce5c81690da35`、首次功能验收源码及其工作区差异均保留在验收记录中，不与最终收尾核查基线混用。

## 后续维护方式

1. 阅读实际仓库的 `AGENTS.md`、`openspec/config.yaml`，记录当前提交和未提交修改。保留用户工作区；遇到同文件未提交改动先理解并整合，不重置、覆盖或批量清理。
2. 阅读本 change 的 `proposal.md`、`design.md`、`specs/page-advanced-appearance/spec.md` 和 `source-map.md`，以及归档后正式的 `openspec/specs/page-advanced-appearance/spec.md`。先比较后续提交，避免重复实现已完成的修复。
3. 本 change 的 `tasks.md` 保存已经完成的历史实施记录。新需求按实际范围维护新的任务与规格，不因后续需求把已核实的历史结果改成未执行。新验证仍须记录实际命令、环境、结果及未覆盖项。
4. 已交付范围是 **13 类可见组件 + 开始／停止两个宿主控件**，逐项证据见 `acceptance.md` 和 `component-coverage.md`。`spacer` 继续只负责布局。后续局部修改不重新要求所有类型重跑完整生命周期。
5. Designer 使用 QSS，Web 使用 CSS；双方保存共用配置及另一端样式，停用保留代码，清空只清当前端代码。没有 CSS／QSS 自动互转或 Web 自动回写 `.emoproj` 的流程。继续复用现有外观入口、真实部位和状态配置。
6. 改共享字段时沿用仓库既有契约生成、保存和发布路径；从实际问题对应的现有入口或渲染部位局部修改。仅验证受影响的正常路径及直接反例，不新增全属性、全平台、硬件或极端故障组合矩阵。

13 类可见组件为 `image`、`text`、`number`、`indicator`、`runtime_status`、`table`、`navigation_button`、`container`、`page_title`、`page_navigation`、`clock`、`sqlite_table`、`sqlite_value`；宿主控件为 `startControl`、`stopControl`。

## 验证与证据

功能完成依据由三部分组成：首次原生 Qt／Edge 作者端及软件 Runtime 验收、后续两轮实际 CI 修复、2026-11-23 对固定提交的源码与远端 CI 核查。详见 [acceptance.md](acceptance.md)；既有运行记录保留各自源码和环境。

- [完整 Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45017102613)：Linux 9682 通过、40 原有跳过；Windows 9716 通过、6 原有跳过；零失败，分片覆盖完整。Ruff 通过，mypy 648 个源码文件通过。
- [适用 Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44990324003)：Windows、Linux、软件 Runtime 集成三个 job 成功。平台分工的条件跳过及后端 opt-in 跳过仍如实保留。
- 同一示例工程及 Web 包位于 `examples/advanced_appearance/`，样式示例和目标配置见 [examples/README.md](examples/README.md) 与 [examples/target-effects.md](examples/target-effects.md)。实装截图和原始运行证据位于 `docs/testing/2026-11-20-advanced-appearance/`。

交付中的 draw.io、叠加标注及改进效果图用于说明**计划改动位置和目标效果**。即使底图来自既有软件截图，修改后的示意图仍不是当前软件实装截图，不能作为点击、保存、发布或跨端一致性验收的证据。

2026-11-23 没有重新执行完整人工界面、独立高级外观浏览器脚本或软件 Runtime 回放；冻结 EXE、真实相机／PLC／机器人和生产工程仍未验证，不因本次文档收尾扩展验收范围。图中样例数据与受控软件 Runtime 不是生产设备验收。

归档前验证本 change，归档后验证正式 `page-advanced-appearance` 规格及归档引用；使用实际可用的 OpenSpec CLI 并记录真实结果。归档后的变更目录不再作为活动 change 运行 `status --change`。格式通过不能替代产品运行验证，未执行的命令不能预登记成功。

本次收尾已核实可用的 CLI 为 OpenSpec 2.0.0。归档后可在仓库根目录使用：

```bash
OPENSPEC_TELEMETRY=0 OPENSPEC_NO_UPDATE_CHECK=1 openspec validate page-advanced-appearance --type spec --strict
OPENSPEC_TELEMETRY=0 OPENSPEC_NO_UPDATE_CHECK=1 openspec validate --archived --strict
```

以上是维护命令；本次实际执行结果以收尾验证记录为准。

## 提交与执行角色

本次收尾执行者为 **ChatGPT Web**。推送时应在对应提交或 PR 下附上独立评论，说明实际改动、已执行验证及未验证范围；提交说明中的角色文字不替代这条评论。是否已推送或已发布评论，以本次实际回执为准，不在本交接文件中预登记成功。

后续维护由实际执行者如实署名；原始计划来源、首次本地实施与本轮收尾核查分别记录，不将他人的运行验收写成本轮重新执行。
