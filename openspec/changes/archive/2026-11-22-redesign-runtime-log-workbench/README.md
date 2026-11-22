# emo_master Runtime 日志工作台 · 完整计划 V2.4

## 归档状态 · 2026-11-22

本变更已完成实施、审查修复及主分支合并，51 项任务全部完成。产品由 [PR #16](https://github.com/jsdfhasuh/emo_master/pull/16) 合入，交付记录由 [PR #17](https://github.com/jsdfhasuh/emo_master/pull/17) 补齐；本次归档以 `d702c4ef60722c51eb703a721942ec9bc9d007eb` 为基线，由 **ChatGPT Web** 执行。

- 当前有效规格：[runtime-log-workbench 正式规范](../../../specs/runtime-log-workbench/spec.md)，包含原文转入的 21 条需求和 57 个场景。
- 实施、验证与未验证范围：[V2.4 实施与验收记录](implementation-report-v2_4.md)。任务完成情况见 [tasks.md](tasks.md)。
- 下文保留当时的完整计划、原型和交接说明；其中“待实施”“继续实现”等文字属于历史上下文，不再表示活动任务。原始证据和模拟原型的性质保持不变。

---

**这是一份已整合的完整计划。只交给 Codex 这一包即可，不需要再提供 V2.3、补充包或 Patch 01。**

唯一 change：`redesign-runtime-log-workbench`。全部目标、当前待办和原型均归入此目录，不含第二个待执行 change。

## 先看可操作界面

打开 [reference/index.html](reference/index.html)，进入 [可点击原型](reference/prototype.html)。首页在“最近已结束”中找到“本批采集”，展开“还有 2 个关联任务未结束”，打开检测或保存任务，再返回来源任务。原型数据和连接均为模拟。

普通子工作流与主流程使用同一个 Job，由调用方等待返回；它们及循环轮次只在执行结构内展开。这里“关联任务”专指实际创建了独立 Job 的下游任务。当前字段无法给出具体未结束项时，仅显示“结束处理未完成”。

## 阅读与执行

| 文件 | 用途 |
| --- | --- |
| [proposal.md](proposal.md) | 完整变更目标与实际剩余范围 |
| [design.md](design.md) | 唯一完整设计，直接包含所有最终合同 |
| [specs/runtime-log-workbench/spec.md](specs/runtime-log-workbench/spec.md) | 一份完整 ADDED 规范 |
| [tasks.md](tasks.md) | 原 38 条历史完成状态与本轮第 7 节的 11 项实施记录 |
| [acceptance.md](acceptance.md) | 统一 G1–G6 与 Q1/Q2，当前只补受影响验证 |
| [codex-handoff.md](codex-handoff.md) | 当前执行步骤及可直接复制的提示词 |
| [source-map.md](source-map.md) | 当前源码落点、复用能力与状态语义证据 |
| [operator-coverage.md](operator-coverage.md) | 82 个内置算子的历史静态盘点和数据边界 |
| [review-notes.md](review-notes.md) / [readiness-check.md](readiness-check.md) | 一致性、实现范围与测试范围审查 |
| [reference/index.html](reference/index.html) | 唯一可视参考入口：原型、目标图、旧结构图及基线图 |
| [implementation-report.md](implementation-report.md) / [evidence/README.md](evidence/README.md) | fa4b72d7 的原实施报告和八张证据存档 |
| [implementation-report-v2_4.md](implementation-report-v2_4.md) | 本轮改动、命令、原生 Qt 走查及验证边界 |

## 应用到代码仓库

以当前仓库代码为准，将本目录中的计划文件更新到仓库同名 change。保留仓库已有的产品代码、用户修改、实施报告和证据；不要用整个目录清空或覆盖方式抹掉包外文件。按当前 `tasks.md` 第 7 节继续实现。

如旧 `refine-runtime-log-workbench-usability` 已被复制进工作区，先核对其是否含未纳入的实施记录或用户修改，把有效内容并入本 change；同一工作只保留本计划作为有效执行清单。不要再单独执行或排队归档旧 refine 计划。原 `Patch 01` 的“资源收尾中”口径已经被本设计的具体关联提示 / 准确未知原因提示替代，不再套用旧补丁。

`implementation-report.md` 是固定提交原文，不代表本轮新结果。实际完成第 7 节后，由执行者新建 `implementation-report-v2_4.md` 和相关真实 Qt 证据，记录验证环境与未验证项。

## 本轮实施证据

第 7 节完成项对应 [V2.4 实施与验收记录](implementation-report-v2_4.md)。原型仍用于说明交互，新的验收图位于 [evidence/v2_4](evidence/v2_4)。
