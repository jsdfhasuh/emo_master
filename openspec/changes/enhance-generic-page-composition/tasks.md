# 实施任务

**V1.2。实施与软件模拟验收已完成。** 验收见 [实测与交付记录](../../../docs/testing/2026-11-18-generic-page-composition/README.md)。 本文件是唯一任务清单；先核对实际 HEAD，已由后续提交完成的内容复用并标依据。规范 Scenario 是行为说明，不是一条对应一个新测试。验证统一归入 acceptance.md 的三条用户旅程与少量直接检查，未受后续修改影响的证据复用。

## 1. 正式模型与编辑链路

- [x] 1.1 阅读实际 AGENTS 与本 change，核对基线后的相关改动和工作区；确定沿用的现有控件/保存/历史/发布入口，不引入旧脚垫原型分支。
- [x] 1.2 在唯一正式模型加入 contracts 所列可选属性、记录引用、稳定键和有限动作；当前创建/编辑/保存/发布统一 2.10/1.3，保留设备/变量/资源/生产等当前功能；不做旧格式加载/迁移、条件升级或旧包摘要兼容。（page-authoring-delivery）
- [x] 1.3 桌面/Web 属性面板、page settings 和既有 authoring 命令同步新能力，支持继承/自定义/恢复默认；继承当前主题与自定义覆盖的编辑意图保持，复用普通 undo/redo 与校验；不建立版本切换事务。（page-authoring-delivery）
- [x] 1.4 更新当前发布投影、rendererRequirements、SDK owning sources 与真实消费者，目标 1.6.0；沿用 web_panel/contracts/panel-contract-v1/ 作为唯一当前产物目录，消费者一起更新。移除仅为旧工程/旧包/旧SDK保留的检查，当前 schema/TS/wheel/manifest 从正式入口生成并核对。（page-authoring-delivery）

## 2. 组合、外观与宿主

- [x] 2.1 实现 surface 和 typography 的共用解析，修正新组合的 min/sizeHint/heightForWidth 与 Designer candidateGeometry；移除不适用固定加成，使 min0 叶子真实紧凑；编辑绑定提示/选中辅助不进入业务布局。（page-composition）
- [x] 2.2 Qt/Web 同步 text/number 横纵排、分角色字体、纯文本换行及 indicator 精确整数/布尔/字符串映射、text/badge/row、浅底圆点和 neutral emptyText。（page-value-presentation）
- [x] 2.3 实现 imageView 的 fixed/aspect、contain、背景对齐及图像比例变化后的几何刷新；保留原图像来源，消除冲突的 Web 固定高度。（page-composition）
- [x] 2.4 Qt 按已有 tabs/buttons 渲染；显式页内导航自动隐藏宿主重复导航。接通轻量 shell 与无绑定 runtime_status，诊断默认收起，开始/停止复用原宿主控制。（operational-page-shell）
- [x] 2.5 实现按钮 primary/secondary/link 与 container.collapse；结果冻结/恢复/详情继续原 scope 语义，历史按钮使用明确 componentId。编辑器中可配置这些外观和动作。（page-composition、page-value-presentation）

## 3. 历史查询与同条联动

- [x] 3.1 扩展原 SqliteSource/readPage 的 recordKey 与 typed keyValues，普通表沿用 PK/rowid，VIEW 可声明真实键；recordValue 从同页来源声明字段或可选 position/count 投影，图片保持 imagePath/imageRefs。（history-record-review）
- [x] 3.2 给既有 QueryHistoryPage 增加可选 anchor_key_json、anchor_step，并贯通本地读取、Runtime、Qt 客户端及 Web。按键读取同条/邻条、返回目标实际页和 selectedRowId/新 refs；一次短只读事务，原查询预算不变；更新 proto 后通过原脚本生成。（history-record-review）
- [x] 3.3 table/list 共享查询和选择，列表可配置 title/subtitle/trailing；筛选只用有效声明列和关键词。需要 count 时复用同过滤 aggregate，并补 Qt readValue 的过滤参数，不引入搜索/排名服务。（history-record-review）
- [x] 3.4 实现 previous/next 的按键导航、first 的可重复刷新、初选和边界状态；切页保存查询/键/折叠并按同条恢复新 refs，过期图像引用允许一次同条重取，生产推送不抢历史选择。（history-record-review）
- [x] 3.5 关联大图、局部图、字段和日志；切 B 清 A 图并显示 B 加载态，沿用 generation 过滤晚到回调，缺图保留所选记录字段；table/list统一首次选首条，有稳定键的响应统一携带keyValues，无版本兼容分支。位置/总量仅表示最近查询，不后台实时重排。（history-record-review）

## 4. 标注与一个可编辑工程

- [x] 4.1 扩展既有 Annotate 的类别显示/颜色、中文底色、置信度、绘图副本 BGR、线色/虚线/端点；随执行包附确定字体与许可证，更新算子 manifest/注册版本和实际包资源。（vision-annotation-presentation）
- [x] 4.2 用既有模拟输入、Annotate、ImageSaver、SQLiteWriter 配置同一流程：生产图像/结果 → 实际保存 → 可读取的稳定键历史记录；生成五条小样本，再追加第六条，不以独立静态历史库代替。（history-record-review、page-authoring-delivery）
- [x] 4.3 按 reference-layouts 配置两个正式 Page 和受管资源，主图/侧栏/列表/局部图/折叠日志可继续编辑；数据与回执模拟标识简短可见。用同一组合换一次字段/单位/映射证明通用性，无需新增 preset 引擎或第二项目。（page-authoring-delivery）
- [x] 4.4 对最终同一工程完成 acceptance 的 A/B/C 用户旅程：正式编辑保存、Qt/Web 显示与发布、生产同次结果、归档和历史连续回看；实际截图与缺口一次收集，前面已做且未被后续修改影响的步骤不重复执行。（全部能力）

## 5. 必要验证与交付

- [x] 5.1 复用受影响的当前功能测试和最少直接反例，相关fixture/生成示例统一为目标格式；检查当前协议生成一致性与 Web 构建，处理相关失败。检查与用户旅程共享夹具/记录，不追加全版本、权限、故障、DPI、性能或现场硬件矩阵。（acceptance）
- [x] 5.2 更新实际受影响的工程格式、页面编辑、历史回看和 SDK 使用文档；明确按键浏览、模拟数据与现有 PLC 协议的边界，不重写无关项目文档。
- [x] 5.3 执行本 change 的 OpenSpec strict validate，核对规范/任务/实际交付一致。用一份简短记录写明最终 HEAD、实际检查、截图和未验证项，按真实完成勾选；不为每阶段另建证据包。
- [x] 5.4 交付代码/必要生成物、当前 SDK、同一个可编辑 .emoproj、所需资源、对应 .emoweb 和操作说明。推送按执行会话授权；实际推送时按用户要求附 ChatGPT Web 评论，注明真实执行者、改动与验证。

完成以功能连通和最终工件可用为准。不得把计划文档、静态 HTML 效果图或单独控件截图标成工程已经完成。没有新增用户要求或具体剩余风险时，不再扩展实施范围。
