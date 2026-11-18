# 通用页面设计能力增强

**V1.2 · 2026-11-18**

Change：enhance-generic-page-composition。固定参考源码：jsdfhasuh/emo_master@31afa5ecab8b7ddb53f0d6bdedc620f0a243e340。

## 阅读顺序

1. [review-report.md](review-report.md)：过度设计、遗漏功能和修订结论。
2. [proposal.md](proposal.md)：目标与范围。
3. [design.md](design.md)：架构取舍及实施顺序。
4. [contracts.md](contracts.md)：已锁定字段、数据与动作语义。
5. [reference-layouts.md](reference-layouts.md)：三张参考图、可编辑布局参数和真实归档链路。
6. [tasks.md](tasks.md) 与 [acceptance.md](acceptance.md)：唯一实施清单及三条验收旅程。
7. [handoff-sol-6.2.md](handoff-sol-6.2.md)：执行交接。

## 正式能力规格

| 能力 | 规范 |
| --- | --- |
| 页面组合与真实紧凑测量 | [page-composition](specs/page-composition/spec.md) |
| 类型、状态与显示动作 | [page-value-presentation](specs/page-value-presentation/spec.md) |
| 历史同条联动与按键浏览 | [history-record-review](specs/history-record-review/spec.md) |
| 运行页头、导航与实时宿主 | [operational-page-shell](specs/operational-page-shell/spec.md) |
| 通用结果标注 | [vision-annotation-presentation](specs/vision-annotation-presentation/spec.md) |
| 正式编辑、版本与交付 | [page-authoring-delivery](specs/page-authoring-delivery/spec.md) |

基线没有对应已归档主规范，使用ADDED Requirements，不改写其他活动change的历史进度。字段与默认以contracts为准，行为以六份规格为准；实现者发现冲突先修正文档，不自行择一缩减。

[source-map.md](source-map.md)保存固定源码依据，[review-evidence.json](review-evidence.json)记录基线函数反例，[validation-report.md](validation-report.md)保留原计划包检查。实施结果见 [tasks.md](tasks.md) 和 [双页工程验收记录](../../../docs/testing/2026-11-18-generic-page-composition/README.md)。

用户已明确没有存量工程；本版不承担旧格式加载、迁移、旧包摘要、旧SDK快照或旧外观回退。正式路径统一为当前格式，普通编辑撤销和主题继承继续可用。场景不是新增测试数量；最终判断是同一工程能设计、运行、归档并回看。
