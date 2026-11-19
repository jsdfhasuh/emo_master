# 交给 Codex 的执行说明 · OpenSpec V1.3 收尾

请继续实施jsdfhasuh/emo_master的同一change：enhance-generic-page-composition。本版是ce8f84ca已交付V1.2的定点收尾。先读取实际AGENTS、HEAD、未提交状态和本change的README/review-report/contracts/tasks/acceptance；后续已修好的部分对照证据复用，不回退、不覆盖用户工作、不重做原22项。

## 实施目标

只完成tasks.md的R1–R6：

1. Qt动态text更新正确测量、显示时间/多行日志，并保留横排对齐。
2. Web正式创作入口接受整数indicator绑定，保持1/true/"1"精确区分。
3. Web历史来源与UI实例身份分清，复制页能保存、发布、正确查询图片/字段，并再次编辑发布。
4. Web宿主页头紧凑，次级配置进入运行设置；观察与控制对象可辨别及选择，原启停命令/权限/确认不变。
5. Qt/Web的indicator row按状态使用整行浅底，空值清旧色；badge/text按原类型语义。
6. 关闭当前mypy及两条浏览器失败的真实原因，完成当前生成物、有效CI和最终记录。

## R3必须整体贯通

严格按contracts.md第5.6节使用正式Props.historySource，直接引用原historySources目录。不建立alias registry或另一套来源目录。

外层componentId是UI实例；historySource.componentId是Runtime声明来源；选中行图片的sourceComponentId是同页表格UI实例。复制保留Runtime来源、重映射页内ID。发布复用已有Published history字段，Runtime RPC不增加参数。

需要同步正式模型/形状校验、Editor创建与复制、字段目录、build_package、包语义校验、网关查询/固定图、HistoryView的imageRefs查找、active包再次构建目录。引用列和图片父表在现有发布上下文核对，不把重复目录元数据写进草稿，不使用空sqlite.columns否决有效引用。原生Designer仍拥有并复制实际SQLite/ImagePath声明。

新Web草稿与受影响当前fixture统一显式historySource，不加旧草稿回退或迁移。新字段进入owning model后使用下一未占用当前SDK正式版本，通过原生成器同步Schema/TS/wheel/manifest及真正消费者；沿用web_panel/contracts/panel-contract-v1/。工程2.10/页面1.3不因计划版本另建分支；没有RPC变更不改proto生成物。

## 验证与范围

用原同一个双页工程。原历史29项、Annotate5项和真实5+1归档证据按acceptance影响表复用，不把旧证据写成新SHA上重跑。补Qt长短/多行文本、一次正式整数绑定保存发布、一次历史页复制后再编辑发布，以及实际Qt/Web页头与row表现。

CI需先分清测试同步和产品行为：401或locator超时不直接等于登录/停止坏了。修等待条件时保留原身份、目标、终态和图像归属断言；不绕过鉴权、跳过有效测试或全局延长超时。最终稳定后检查原CI的后续skipped步骤是否真正执行。

继续Python3.10/PySide2/Qt Widgets和现有Web栈。用户没有存量工程，不增加旧格式/草稿/包/SDK兼容、迁移、故障矩阵、第二布局/历史服务、变量暴露层、脚垫/PLC专用控件、第二示例或每Scenario一测试。现场设备、部署和检测精度不属于本轮软件收尾。

## 交付方式

局部修复、普通实现选择与必要验证按本任务直接推进，不因每个控件或文案选择反复请求确认。实际HEAD发生变化时先比较已完成内容，按语义合并本补丁。

提交/推送沿用当前Codex会话已经确定的授权和方式；本计划不另行要求改分支、合并或部署。实际推送时在对应提交或PR附“Codex｜ChatGPT Web OpenSpec V1.3收尾”评论，写清计划来源、实际执行者、改动、验证和未验证项；模型名称据实际使用填写，不冒充ChatGPT Web实施。

最终一份简短记录和更新后的同一示例即可。任务按真实完成勾选，直到R1–R6达到判据；不要只改文档/截图就宣称源码已修好。
