# EmoMaster 共享日志工作台 V3 实施计划

对应仓库 `jsdfhasuh/emo_master`，变更目录为 `openspec/changes/upgrade-log-diagnostics-v3/`。本版依据 2026-11-28 核对的远端 `main` 提交 `ea5aefa9cf62f0d7ee3ac0fb6fa4a14661dc663b`，已计入 Operator Runtime R4 和随后合入的 Designer 视口尺寸修复。

| 批次 | 用户可见的结果 | 交付方式 |
| --- | --- | --- |
| M1 正确性 | 待机心跳不会挤掉错误记录。SQLite 和子工作流日志可靠可查。用户能从任务树打开对应节点的详情、定位失败，并分辨暂停阅读和记录未加载。 | 首批独立交付 |
| M2 易读性 | 历史任务显示运行时的名称，完整任务可以导出 Markdown，本机、Web 和桌面日志更易查阅。 | 名称、导出和各入口展示可分别实施和验收 |
| 后续可选 | 比较同一循环的两轮结果。 | 由实际排障案例决定是否另行立项，本期交付以 M1、M2 为准 |

阅读顺序为[提案](proposal.md)、[设计](design.md)、[实施与验收清单](tasks.md)。[源码索引](source-map.md)列出当前主线的入口。两份规格分别描述[共享工作台的增量行为](specs/runtime-log-workbench/spec.md)和[Runtime、导出及各端契约](specs/shared-log-diagnostics/spec.md)。

[原版 43 个场景对照表](scenario-map.md)列出主线承接的要求、本版补回的验收条件，以及留待后续的范围。两份增量规格共 10 个场景，实施清单保留 13 项待办。

本包交付计划文档。后续获得实施授权时，可使用[实施提示词](CODEX_PROMPT.md)。任务清单的勾选用于记录实际完成的产品改动和验证。
