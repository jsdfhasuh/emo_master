# 通用页面设计能力增强 · V1.3 收尾补丁

日期：2026-11-18（北京时间）。Change：enhance-generic-page-composition。

**当前状态：V1.2 已有实际实现和运行证据；V1.3 收尾尚未实施。** 本补丁修订计划，不代表缺陷已修复，也不重新开发已完成能力。

本轮固定审核基线为 [ce8f84ca](https://github.com/jsdfhasuh/emo_master/commit/ce8f84caf4d0fd779e579b23ff82fa4e80de978d)。最初计划的 31afa5ec 只保留为历史设计依据。执行者先比较实际 HEAD 的后续修复，不回退代码或覆盖用户改动。V1.3 是计划文档版本，不自动代表工程、页面或 SDK 的版本号。

## 本轮只完成六项收尾

1. Qt 动态 text 更新后重测尺寸、保留对齐，使时间和多行日志完整显示。
2. Web 编辑器正式绑定入口支持整数 indicator。
3. Web 复制 Runtime 历史页后能保存、发布、独立选择记录，并再次编辑发布。
4. Web 宿主页头达到参考图的紧凑结构，保留实际观察对象、控制对象与原操作。
5. Qt/Web 的 indicator row 按状态显示整行浅底。
6. 处理当前有效 CI 失败，完成受影响链路复验、生成物同步和真实交付记录。

唯一未完成清单在 [tasks.md](tasks.md)。原 V1.2 的实现与证据继续有效，不把原 22 项任务全部重开，也不把全部 Scenario 变成新测试。

## 阅读顺序

1. [review-report.md](review-report.md)：确认问题、视觉差距与证据边界。
2. [proposal.md](proposal.md)、[design.md](design.md)：范围和最小实现设计。
3. [contracts.md](contracts.md)：正式属性、身份与发布契约，尤其第 5.6 节。
4. [source-map.md](source-map.md)：本轮代码落点。
5. [tasks.md](tasks.md)、[acceptance.md](acceptance.md)：六项任务与按影响复验。
6. [reference-layouts.md](reference-layouts.md)：原目标图与本轮视觉完成标准。
7. [handoff-sol-6.2.md](handoff-sol-6.2.md)：Codex 接手说明。
8. [validation-report.md](validation-report.md)：本补丁的文档和应用检查，非实现验收。

六份现有规格继续使用同一 change 的 ADDED Requirements；没有另建能力或重写其他活动 change。保存于仓库的原 [V1.2 验收记录](../../../docs/testing/2026-11-18-generic-page-composition/README.md) 是旧提交/工作区的证据，不改写成未来修复 SHA 上的复测。

## 延续的边界

用户没有存量工程，本轮不增加旧工程/旧草稿/旧页面包/旧 SDK 兼容、迁移或回退。生产继续直接绑定算子输出，七组控件保持通用；Python 3.10、PySide2/Qt Widgets 和现有 Web 栈不变。已有资源、身份、权限和当前包完整性继续使用。

当前工程/页面基线为 2.10/1.3。新增正式 historySource 创作引用会改变 SDK owning model，应通过原生成器同步当前 SDK 及消费者；文档版本本身不是升级理由。无需新 RPC、第二个历史目录或变量暴露层。
