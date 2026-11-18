# 交给 Sol 6.2 的执行说明（V1.2）

请在jsdfhasuh/emo_master实施OpenSpec change **enhance-generic-page-composition**。用户已明确没有现成工程，不需要兼容原工程。本版已移除旧格式/旧包/旧SDK兼容及迁移工作；以V1.2为准。仓库旧说明若仍要求此类兼容，按本次用户明确范围调整相关任务与检查，无需再询问是否保留旧工程。

先读取实际AGENTS、当前HEAD/工作区，再读review-report、proposal、design、contracts、六份spec、reference-layouts、tasks和acceptance。参考SHA为31afa5ecab8b7ddb53f0d6bdedc620f0a243e340；比较后续代码并复用已完成部分，不回退、不覆盖用户改动、不引入此前脚垫原型分支。

## 交付目标

在现有Designer、Qt和Web链路增强通用页面能力，交付同一个可编辑2.10/1.3双页工程与.emoweb：生产监控具有目标结构与紧凑层级；实际模拟生产经ImageSaver/SQLiteWriter保存后，在历史页按同键查看大图、局部图、字段和日志，并能持续翻条、筛选、回到最新、切页恢复。

不能仅交静态页面或能保存但不能渲染的字段。不得通过增加一套业务专用页面绕过正式控件。

## 已确定的实施边界

- 保留Python3.10/PySide2/Qt Widgets与现有Web栈，增强七组既有组件；不新建布局引擎或专用脚垫/PLC组件。
- 生产输出直接绑定；宿主live、resultScope和页面历史会话各沿用原归属，不造变量暴露层或第二状态中心。
- 外观必须与测量一起改：普通叶子min0、实际padding/font参与尺寸，Designer辅助提示不占业务布局；图片新比例触发几何更新。
- Qt兑现既有tabs/buttons。显式导航自动隐藏宿主，允许有意多处放置；不新增navigationPlacement或重复导航硬错误。
- shell只有轻量显示属性，诊断初始折叠布尔值；开始/停止保持原宿主。统一emptyText，不加三套stateFallbacks。
- 历史按真实keyValues扩既有QueryHistoryPage的anchor_key_json/anchor_step；原分页保留，新来源返回对应可选字段。按键邻条及返回同条更新imageRefs，不新增RPC或跨请求快照。
- count可选，引用才查且同筛选；只做已有单列关键词，不能保留无实现的全列筛选按钮。
- Annotate补中文类别/颜色/线条，字体随执行包而非每个工程；输出一次位图由生产显示和历史保存共用。
- 当前正式格式统一2.10/1.3，SDK目标1.6.0，Qt/Runtime/Web和生成物同时更新；当前目录沿用web_panel/contracts/panel-contract-v1/。不承担旧工程加载/迁移、格式升级撤销、旧.emoweb摘要或旧SDK字节兼容。受影响fixture可直接更新，历史交接档案无需清理。
- 所有页面走同一默认：容器card、叶子none、当前主题文字、imageView.aspect、indicatorView.text、按钮secondary、紧凑shell。继承/自定义/恢复默认与普通undo/redo保持，不维护cardStyle/fontSize旧回退。
- 历史响应按当前DTO统一；table/list首次非空均选首条。Annotate采用一条当前绘图路径，不做旧像素行为回归。
- 一个实际模拟工程即可：五条记录/pageSize3，再运行第六条。更换一次字段/单位/映射证明通用性；不新增palette树预设或第二项目。

业务名称、码值、寄存器和类别只存在于示例配置。用户此前讨论的0/1、1/2/3不在本轮被重新定义为平台协议。

## 验证方式

按acceptance的A设计保存发布、B生产同次结果、C归档历史三条用户旅程完成。复用同一工程与夹具；一次操作可以支撑多条规格。已验证且后续未影响的路径不重复全套跑。

仅补必要直接检查：精确类型/缺值、当前工程/包/SDK往返、图像晚到/缺图/引用到期、综合Annotate。持续新增已归入C；不要扩成并发删改、全权限、全版本、全DPI、压力、外部Windows发行或真实硬件矩阵。只为旧格式/迁移/旧快照服务的测试可按授权删除或改为当前用例；当前功能的有效断言继续执行，不得为掩盖功能失败删断言。

修改proto后通过原脚本生成并check，当前SDK从正式owning sources生成且实际被Web安装；不手改生成物；当前目录由生成器正常更新，历史文档不作为当前字节冻结门槛。环境可用时运行OpenSpec strict validate；不存在CLI时说明事实，不把自定义检查当成官方通过。

## 实施与收尾

按tasks推进并更新真实状态；局部命名、代码拆分和资源选择按现有代码最小处理。无须为已确定的设计选择反复请求确认。公开契约如因真实代码差异必须调整，先写清原因和功能影响，不静默删减Web、可编辑性或历史闭环。

交付一份简短记录即可：最终HEAD/工作区、实际检查与结果、同一个工程/资源/页面包/SDK、Qt/Web生产与历史截图、操作步骤及未验证项。三条旅程全部成立才称本轮功能完成；现场模型/PLC/机械动作未验证不得冒称通过。

提交、推送、合并和发布按执行会话已有授权。实际推送时遵守用户要求，在对应提交或PR附**ChatGPT Web**评论，写明真实实施者（若为Sol6.2据实填写）、计划来源、实际改动、验证与未验证项。

## 可复制短提示

```text
请按 openspec/changes/enhance-generic-page-composition 的 V1.2 计划实施。用户没有现成工程，不需要旧工程/旧包/旧SDK兼容、迁移或旧样式回退，直接统一当前格式及消费者。先读 AGENTS、review-report.md 和 handoff-sol-6.2.md，核对当前代码后按 tasks.md 推进。重点交付同一个可编辑双页工程，达到参考布局，并跑通生产显示→ImageSaver/SQLiteWriter归档→历史同条回看。实现按键邻条/切页恢复，补紧凑测量及正式编辑、Qt/Web、保存发布。不要恢复已删除的导航硬限制、状态回退配置、预设引擎或重复验收。按 acceptance.md 的三条用户旅程与必要直接检查收尾，任务按真实证据勾选。推送依本会话授权，获准后附 ChatGPT Web 评论并注明实际执行者。
```
