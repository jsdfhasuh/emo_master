# 实施任务

所有复选框代表尚未完成的实现工作；编写了规范不能勾选实现任务。每项完成后记录实际改动和验证；中间 A/B 可用不等于完整计划完成。括号内为主要能力目录。

**跨阶段约束：**任何阶段实际提取已有 Designer 规则，都在同一变更适配对应桌面调用并验证直接受影响行为；不等到 D 再接回。未提取的操作不要求迁移。文件传输与诊断按 design.md、file-transfer.md 的最小契约落实，不新增管理系统。

**v7 对齐状态：**参考上轮已核对 main `b61a96f6d8135497531eabf18245118de253ee03`。`4a602120` 的四字段声明/生成代码及 `f663c007` 的 ROI 测试版本修复已存在，不重复实施；该基线 Runtime 仍未填值，任务 1.1 的目录查询与端到端映射未完成。以下 25 项仍为接口实施与验收任务，未勾选不否认其底层已完成部分。

## 0. 执行基线

- [ ] 0.1 读取实际 AGENTS.md，记录 HEAD、分支和工作区状态；以 `b61a96f6d8135497531eabf18245118de253ee03` 为参考并对照后续变化，不回退提交。合并活动 openspec 目录中的现有规范与进度，不覆盖用户改动；保留 `docs/plans/2026-11-13-independent-project-authoring-v6/` 原始归档及摘要，不把它当成本轮活动变更。
- [ ] 0.2 沿首条图像流程确认可直接复用的工程会话、规则、依赖和保存入口；只为真实阻挡无界面使用的依赖调整代码，记录本轮具体桌面改动边界。

## 1. A — 能力发现与最小可用服务

- [ ] 1.1 接入真实算子/设备目录与完整详情；复用 4a602120 的四字段定义和生成代码，不重复新增/改号。完成 Runtime 从真实 manifest 填充 next_cycle_parameter_paths_json、min_core_version、max_core_version，并提供真实 runtime_instance_id；核对实际客户端/SDK 解析，贯通新 Web 与 HTTP/MCP。递归保留端口、嵌套参数和条件扩展，区分未提供与明确空列表；不手写目录。（operator-capability-discovery）
- [ ] 1.2 从实际命令实现生成编辑能力和输入结构；分开表达目录声明、编辑支持、工程检查，明确未知与目录查询失败；fileTransfer 只声明已实现方式和真实用途枚举。（operator-capability-discovery）
- [ ] 1.3 复用正式模型/会话，实现独立创建、读取、流程/普通节点/参数/连线、undo/redo/save；接入无需在线目标的本地 validate，明确 scope=local 与未完成项；一次操作一次事务、失败回滚、保存重读；通用配置支持 ROI points/regions 等真实嵌套参数，不移植 Qt 绘图界面。（project-draft-authoring）
- [ ] 1.4 实现草稿工作目录、修订和串行修改、正常重启读取保存内容；不恢复未保存历史、不增加幂等账本。（project-draft-authoring）
- [ ] 1.5 接入同一服务的 HTTP 与可选 MCP，统一 Bearer 凭据、命令 schema、可定位结果及生命周期；实测并固定 Python/SDK/协商协议/实际客户端组合，明确文件由宿主还是 HTTP 辅助操作，按该协议处理请求取消。（mcp-authoring-access）
- [ ] 1.6 用实际客户端在 Designer 未运行时完成“发现 Threshold → 查看模式和限制 → 创建/编辑/本地校验/保存”；G2 核对四字段真实值与空值语义、Threshold 和一个复合 ROI 声明/配置、一个定位反例及离线校验。复用 f663c007，源码测试核对 manifest/注册版本，远端按目标返回版本请求，不硬编码 ROI 版本或删功能断言；不新增版本矩阵。

## 2. B — 资源与可继续编辑的工程交付

- [ ] 2.1 按 file-transfer.md 实现 raw-byte 上传/替换、name/purpose/resourceId、期望修订及长度/摘要约定；返回新修订并用于后续绑定；替换写新路径、失败不登记，保留仍在用的旧字节。（project-resource-delivery）
- [ ] 2.2 接入基础页面和控件配置，output_choices 从当前草稿生成，完成 mask 绑定；复用新的页面快照与隔离语义，一次目录构建复用所需文档，不恢复逐控件全工程序列化，复用现有对应测试。（project-draft-authoring）
- [ ] 2.3 实现正式 .emoproj/ZIP 导入、单文件/完整 ZIP 导出；返回固定下载路径、类型、大小/摘要、草稿修订和认证说明；复用正式保存重读与资源复制，GET 下载必须使用同一凭据。（project-resource-delivery）
- [ ] 2.4 贯通 PNG → Image Loader → Threshold；用选定客户端及已说明的辅助方式实际上传、使用新修订绑定、导出并携带凭据下载，核对文件后 Designer 打开/修改/保存重读；明确“客户端 + HTTP 辅助”或实际自动方式。（mcp-authoring-access）

## 3. C — 真实候选预检

- [ ] 3.1 增加独立候选 ZIP 流协议和 SDK/服务适配；使用请求整体大小和单块上限，不引入多资源子流；运行既有 proto 生成一致性检查。（candidate-project-precheck）
- [ ] 3.2 按 scope=target 实现目标预检，复用正式模型、真实目录、引用、设备/页面声明、资源准备和编译；与本地 validate 分工明确，限制在候选副本，不调用生产写入、run/connect/execute。（candidate-project-precheck）
- [ ] 3.3 返回 scope、被检修订/目标实例、三态结论、compileStatus/executionStatus；保留 ValidationIssue 的 code/message/对象位置/fieldPath 和已知 resourceId；资源不足不伪造输入，服务错误与正常 FAILED/INCOMPLETE 分开。（candidate-project-precheck）
- [ ] 3.4 按阶段 A 实测协议处理请求取消/断开与超时；保留整个草稿及已提交操作，不自动重放，工作退出后清理，验证重连读取。（candidate-project-precheck、mcp-authoring-access）
- [ ] 3.5 在同一图像工程验证“缺项/错误诊断 → 按位置修复 → 新修订再预检 → 真实结果”，并确认在线 A 不被 B 替换与一次所选协议下的正常取消；不建立故障组合矩阵。

## 4. D — 补齐完整工程编辑能力

- [ ] 4.1 接入流程复制/删除、入口与接口、Subflow/Loop/Dispatch/Join；沿用引用、派生端口和合同语义。（project-draft-authoring）
- [ ] 4.2 接入设备、变量、资源/现场参数声明及正式运行/生产配置；复合编辑一次事务，保持类型和引用规则。（project-draft-authoring）
- [ ] 4.3 接入完整页面/控件布局、终端、导航与绑定配置；保留正式字段和当前草稿来源，不增加输出别名层。（project-draft-authoring）
- [ ] 4.4 收尾核对各阶段提取的规则已在同一变更适配对应 Designer 调用并完成直接回归；发现遗漏在该项范围内补齐，而非到此才开始接入。保留已交付 ROI 交互、图像控件、调用链导航和页面初始化优化；不整体迁移、不重复提取已独立函数，不把桌面导航或绘图扩成新 MCP 功能。（project-draft-authoring）
- [ ] 4.5 在 G2 内复用 fixture，经新增公开命令验证复杂流程代表、设备配置与变量绑定、页面/生产配置的代表修改及保存重读；据实际实现更新能力，未完成继续标记未支持，PNG 示例不独自作为 D 完成证据。（operator-capability-discovery、project-draft-authoring）

## 5. 收尾与交付

- [ ] 5.1 按 acceptance.md 七组目标整合证据；选定客户端完成查询、编辑、上传/绑定、本地校验、目标预检、诊断修复/再检查、导出与受保护下载，Designer 继续编辑；隔离软件 Runtime 明确运行一次。
- [ ] 5.2 补启动/连接/上传下载步骤与一份简短完成记录，区分自动测试、Qt 自动化、人工操作、软件运行和未做的硬件验收；修正与实现不符的设计说明但不得静默缩减需求。
- [ ] 5.3 在本地核对规范和任务，按实际权限提交交付；推送/合并/发布需用户授权。获授权推送时标注实际执行者，并附“ChatGPT Web 计划来源”评论，列改动、已验证、未验证；未完成前不归档为已交付能力。
