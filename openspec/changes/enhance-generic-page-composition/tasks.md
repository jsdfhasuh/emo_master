# V1.3 唯一实施任务清单

**状态：R1–R5 已通过本轮复验，R6 待最终 GitHub Actions 验收。** [实际验证记录](../../../docs/testing/2026-11-18-generic-page-composition-v1.3/README.md)

基线：ce8f84caf4d0fd779e579b23ff82fa4e80de978d。先核对实际 HEAD、AGENTS 和未提交改动；后续已经修好的内容验证后复用。本文件是唯一待办清单，不在交接、报告或各 spec 再建立平行勾选表。

## 已交付基础与证据

原 V1.2 的 [22 项实施记录](https://github.com/jsdfhasuh/emo_master/blob/ce8f84caf4d0fd779e579b23ff82fa4e80de978d/openspec/changes/enhance-generic-page-composition/tasks.md) 保留在固定提交中。该提交交付了正式 2.10/1.3 模型、通用控件、双页工程、SDK 1.6.0、实际归档及按键历史回看。

review-report.md / review-evidence.json 记录本次审查结果：历史 29 项、Annotate 5 项和实际 5+1 归档已有通过证据；它们不是未来修复 SHA 的重新执行。当前未收口的项目仅列在下方。

## 当前六项任务

- [x] R1. 修复 Qt 动态文本测量和对齐。使短占位更新为时间、长字段、多行日志及切回短字段时，当前内容测量进入父布局；保留横排末端对齐和纯文本行为。复用现有 renderer/layout 用例保留一个直接反例，最终工程核对时间及日志完整可读。完成依据：acceptance A1。

- [x] R2. 接通 Web 整数指示器的正式创作入口。目录点击、拖拽和属性绑定使用一致的 integer/boolean/string 规则；在同一工程绑定一个整数输出、配置映射、保存重开并发布预览。类型精确性复用现有参数化检查。完成依据：acceptance A2。

- [x] R3. 修复 Web 历史控件来源身份与复制发布链。按 contracts 5.6 增加正式 historySource 引用，贯通模型结构校验、现有目录选择、复制引用重映射、发布/包校验、网关 RPC 地址、imageRefs 查找及已发布来源目录重建。使用原目录与 RPC，保留声明身份和图片与表格的真实关系。先完成当前模型/消费者，再通过正式生成器更新 SDK/Schema/TS/wheel/manifest。完成依据：acceptance A3；原生 Designer 拥有来源的复制语义不改成引用旧声明。

- [x] R4. 收拢 Web 宿主页头。以现有 shell 实现紧凑主栏和可展开运行设置，常用开始/停止、当前观察对象与控制对象可访问并可辨别；Runtime/包管理和工作流输入归入次级入口，诊断保留。切页或冻结不改变目标，原权限/确认/命令路径继续使用。完成依据：acceptance B 与 reference-layouts。

- [x] R5. 补齐 Qt/Web 整行状态浅底。indicatorView=row 的 soft 背景覆盖标题/圆点/值所在整行；badge 只包围紧凑状态值，text 使用文本语义；无映射/缺值清除上一状态色并中性显示。使用同一主题与现有映射，无新颜色配置层。完成依据：acceptance B 的一次视图检查。

- [ ] R6. 完成当前检查、视觉与交付收尾。修正 appearance.py 的主题类型关系；定位并修复原 asset_retirement_browser 和 product_runtime_e2e 失败，不削弱原行为断言。执行受影响的现有检查、Web 构建及官方 OpenSpec strict；本轮若无 proto 变更不修改生成的 RPC 文件。当前 SDK 变化后确认消费者和生成物一致，最终稳定版本运行既有有效 CI 并检查原先 skipped 步骤。更新一份最终证据记录和同一示例的受影响截图，逐项据实勾选 R1–R6；执行会话已授权推送时附实际执行者评论。完成依据：acceptance 第 4–5 节。

## 停止条件

R1–R6 的实际行为成立、受影响的正式保存发布链路可用、当前有效检查完成后交付。复用未失效证据，不恢复旧版兼容，不新增故障/权限/DPI/浏览器排列组合或一 Scenario 一测试，不要求先清空无关历史风险。
