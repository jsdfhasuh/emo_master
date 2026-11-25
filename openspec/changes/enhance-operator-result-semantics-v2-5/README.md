# V2.5 OpenSpec 计划与复审补充

## 当前状态

**实施清单 24/24 项已完成。** 八项审计缺口已修复，P3.5 已由用户授权 Codex 在原生 Windows Qt 桌面验收。PR #20 修复提交 `d70ef456` 的 10 项远端检查全部通过。[交付记录](delivery.md#修复提交的远端验收)

- 原 main 基线：`ed98a0212820efd0e6436029b69e82dc6e313fc7`，已包含 PR19、PR25。
- 下表的 R1–R3 复审基于 PR20 头 `3d6448d3da18790c5f97bfdecb5fc845ebd67a1c`。
- 计划、P0 与独立复审由 **ChatGPT Web** 完成。主体实现、本轮修复和开发验证由 **Codex（6.2 Sol）** 完成。
- 82 算子静态覆盖与注册核对继续有效，后续按实际提交复核受影响增量。

| 修复项 | 实际范围 | 对应原任务 |
|---|---|---|
| R1 真实调试身份未接入详情 | 从实际记录的 sourceIdentity 匹配定义并显示身份 | P2.4、P3.4 |
| R2 调试 outputs 认证 URL 未安全化 | 外发记录生成安全副本，内部 output_ref 保留执行原值 | P1.5、P3.3 |
| R3 动态参数说明未消费 | 共享树读取已有 additionalProperties 对象说明 | P2.2、P3.4 |

原复审头的 CI 和历史基线故障按固定提交留档。本轮源码回归与修复提交的 CI 见 [交付记录](delivery.md) 和 [PR20 Checks](https://github.com/jsdfhasuh/emo_master/pull/20/checks)。

## 阅读入口

- [tasks.md](tasks.md) 是本轮实施与验收的状态清单。
- [reference/pr20-review-20261125.md](reference/pr20-review-20261125.md)：三项已确认问题、源码依据、实际复现及最小回归要求。
- [design.md](design.md)：保留原设计，并明确本轮三项局部修正边界。
- [proposal.md](proposal.md)：原目标、当前阶段与本次补充范围。
- [desktop-walkthrough.md](desktop-walkthrough.md)：修复后独立桌面走查；离屏测试不能代替 P3.5。
- [delivery.md](delivery.md)：保留原 P0、实施与 CI 记录，追加本次复审状态。
- [operator-field-audit.md](reference/operator-field-audit.md) 与 [完整字段记录](reference/operator-field-audit.json)：沿用已经完成的 82 算子 P0，不重复审查。
- [runtime-field-audit.md](reference/runtime-field-audit.md)：原基线共用链路事实及实现复核补充。

## 规格

- [operator-result-semantics](specs/operator-result-semantics/spec.md)：补足认证输出安全副本与内部输出引用的场景。
- [operator-readable-run-details](specs/operator-readable-run-details/spec.md)：补足真实调试记录身份与动态对象展开场景。
- [runtime-log-workbench](specs/runtime-log-workbench/spec.md)：沿用既有正式 Job、查询、导出与详情要求，本补充不修改该规格。

本轮不重开全量 P0，不要求全部 82 个算子实际执行，不新增历史迁移、全模型、生产硬件或第二套日志框架。现有界面布局与正式 Job 合同继续沿用。
