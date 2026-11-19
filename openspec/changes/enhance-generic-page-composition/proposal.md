# 通用页面组合与历史回看能力增强

**计划 V1.3 · 2026-11-18（北京时间）。已交付 V1.2 的定点收尾。**

## Why

用户要用通用控件实现生产监控与历史回看两个可编辑页面，并达到原参考图的结构和信息密度。固定实现提交 ce8f84ca 已具有正式模型、双页工程、标注以及实际 ImageSaver/SQLiteWriter 归档和稳定键回看能力；这些实现与有效证据继续复用。

代码审核和直接复现确认三个正常操作断点：Qt 动态文本尺寸及对齐更新失效、Web 指示器整数绑定被拒绝、Web 复制已声明 Runtime 历史页后发布失败。实际 Web 页头仍有多行常驻管理工具，状态浅底只作用于值区域，尚未完成原视觉目标。当前源 CI 与 Web CI 也未全部通过。详见 review-report.md。

## What Changes

- 修正既有 WrappedText/ValueView 的动态测量失效与对齐保持。
- 同步 Web 正式绑定入口对 integer/boolean/string 指示器的类型支持。
- 在唯一正式创作模型增加直接引用既有 Runtime 历史声明的 historySource；沿用已有 Published history 与 RPC，贯通复制、保存、发布、查询、图片及再次编辑。
- 用当前 shell 和宿主控件收拢 Web 页头；次级配置进入运行设置，观察/控制目标仍可辨别和选择。
- 明确 indicatorView=row 的 soft 背景覆盖整行，badge 保持紧凑值区域，text 保持文本表现。
- 修复已确认类型检查失败；根据实际根因修复两条失败浏览器路径，完成既有有效 CI 后续步骤。
- 同一示例和原 A/B/C 用户旅程只补受影响步骤，正式模型变化时同步当前 SDK、生成物及消费者。

## Capabilities

### New Capabilities

沿用本 change 尚未归档的六项能力：page-composition、page-value-presentation、history-record-review、operational-page-shell、vision-annotation-presentation、page-authoring-delivery。V1.3 只补齐这些能力的正常行为，不增加第七项平台能力。

### Modified Capabilities

继续使用现有 ADDED Requirements。执行时如后续提交已经归档到主规范，应按实际归属合并同样的修正，不创建重复规范。

## Impact

本轮主要影响 Qt presentation 的文字/状态表现、Web 编辑器、既有发布投影与历史网关、正式 SDK model/生成物、Web 宿主页头和相关现有测试。代码落点见 source-map.md，完整身份语义见 contracts.md 第 5.6 节。

Runtime 数据库查询、anchor 算法、ImageSaver/SQLiteWriter、Annotate 与生产同次结果机制默认保持现有实现；只有修复确实触及其行为才补跑对应检查。

用户没有存量工程，不增加兼容、迁移、别名注册表、第二布局/历史服务、新 RPC、脚垫/PLC 专用控件或新验收矩阵。完成以 tasks.md 的六项收尾和 acceptance.md 的受影响用户路径为准。
