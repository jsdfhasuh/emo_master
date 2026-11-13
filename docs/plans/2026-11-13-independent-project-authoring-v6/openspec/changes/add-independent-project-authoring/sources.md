# 来源与决策依据

这份文件区分上传材料、后续讨论和外部格式约定。它用于定位与避免猜测，不把历史计划当成已实现事实，也不要求执行时回退到旧代码。

## 用户材料与讨论

| ID | 来源 | 本包采用的内容 |
| --- | --- | --- |
| U1 | 用户上传 `emo_master_web_draft_mcp_plan_sol.md`，v3、435 行，参考提交 ba30c2ab | 新 Web、正式完整工程、四阶段、七组验收、单工作区、单 ZIP 候选、取消的过重范围 |
| U2 | 本对话后续关于“Web 是否控制 Designer”“是否顺路改造”的讨论 | Web 独立运行；共用底层规则，各持会话；允许必要局部提取，不要求整体桌面重构或统一调用链 |
| U3 | 本对话关于“MCP 未暴露算子支持信息”的讨论 | 摘要+完整详情、实际编辑支持、工程预检三层区分；信息发现前置，不增加百分比评分或手写支持矩阵 |
| U4 | 原 OpenSpec 组织请求与已交付 v4 | OpenSpec 变更目录、五项能力及架构边界 |
| U5 | 已上传 emo_master_openspec_v4_audit.md | F1 协议取消、F2 文件往返、F3 可定位诊断、F4 校验分工、F5 同步桌面适配、F6 阶段 D 代表验收 |
| U6 | 上轮 v5 交付请求 | 将 F1–F6 直接合并后交付 v5；不改实现、不推送 |
| U7 | 用户本轮说明“刚推送部分代码，核对计划是否需要变化” | 读取实际远端分支、比较旧新基线，静态核对新增算子及共享状态代码；按差异更新本包，不修改仓库实现 |

**v4 相对 v3 已完成的修订**：3.3 的全局 Designer 顺序改为 design D2 的条件性最小适配；不再要求每个新命令同步迁移桌面。6.1/7.1 的泛化 catalog 被补为完整能力发现规范。原七组验收保留，新增目录字段断言并入 G2/G7。独立命名空间是实现选择，不再是全量搬包前置条件。其它资源/候选/完整工程要求继续沿用；未添加新的生产执行权限。

**v5 相对 v4 的修订**：保留原五项能力、四阶段与七组验收；将 U5 六项审查结论直接落实到 design/specs/tasks/acceptance，新增 file-transfer.md 记录最小传输契约与 PowerShell 辅助步骤。完整对应见交付根目录 review/changes-v5.md；不把旧审查文件作为第二份实施约束。

## 源码定位

**v6 当前核对基线：`556acb10760a9463fe832aa4d8e7fc5c0a95a67e`（main）**；**旧 v5 基线：`ba30c2ab94a1fbc1d7396c12788dd695183bb88a`**。GitHub compare 返回 ahead，6 commits，behind=0。比较范围未改变 Web 后端、Runtime gRPC service/proto、SDK 或工程格式定义；新增核心影响是 ROI、集合算子、页面/工作流状态和桌面交互。

下表 R0–R11 是保留的历史定位，链接固定在旧基线，不能据此覆盖新实现。当前有关文件的变化以新增 N0–N7 为准；未改的文件可以沿用旧证据定位，但执行者仍读实际工作区。v6 本轮静态读取与对比，未运行实现测试。

| ID | 固定源码或目录 | 证据用途与本轮状态 |
| --- | --- | --- |
| R0 | [仓库根树](https://api.github.com/repos/jsdfhasuh/emo_master/git/trees/ba30c2ab94a1fbc1d7396c12788dd695183bb88a) | v4 读取，参考根目录没有 openspec；因此包内提供新增能力 delta，执行时复查是否已有后续规范 |
| R1 | [Runtime ListOperators](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/runtime/grpc_server/service.py#L1182-L1241) | v4 读取；已有 summary、完整端口 JSON、paramSchema、deviceRequirements 等；该返回装配未携带 nextCycleParameterPaths |
| R2 | [Threshold manifest](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/plugins/builtins/threshold/manifest.json) | v4 读取；真实五种 mode、nullable、xOdd/xEnabledWhen 及 nextCycleParameterPaths，是目录保真样例 |
| R3 | [ProjectEditSession](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/project_edit_session.py)、[WorkflowStore](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/workflow_store.py)、[project_store](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/project_store.py) | 原计划定位；会话、事务、撤销和正式保存优先复用 |
| R4 | [ProjectDocument](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/core/project/models.py)、[工程规范](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/docs/project-json-spec.md) | 原计划定位；正式字段与版本 |
| R5 | [FlowGraphModel](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/flow_graph_model.py)、[WorkflowController](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/controllers/workflow_controller.py)、[MainWindow](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/ui/main_window.py) | 原计划及后续讨论定位；只提取当前需要的图编辑/参数规则 |
| R6 | [PageCoordinator](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/page_designer/coordinator.py)、[draftCommand](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/page_designer/commands.py) | 原计划及后续讨论定位；画布同步、事务归属和资源复制，不能把桌面同步当成 Web 前置流程 |
| R7 | [DeviceCatalogEditor](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/device_catalog.py)、[PresentationStore](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/presentation_store.py)、[变量编辑](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/designer/state/global_variables.py) | 原计划定位；设备、页面、变量能力 |
| R8 | [资源模型](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/core/project/resources.py)、[资源准备](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/core/project/runtime_directory.py)、[WorkflowCompiler](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/core/workflow/compiler.py) | 原计划定位；资源/候选检查复用入口，副作用边界仍须实际核对 |
| R9 | [Runtime proto](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/proto/runtime.proto)、[AioRuntimeServer](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/apps/runtime/grpc_server/aio_entry.py)、[生成脚本](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/scripts/gen_proto.py) | 原计划定位；目录字段和候选接口的局部扩展，不假设已实现新 RPC |
| R10 | [AGENTS.md](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/AGENTS.md) | 原计划定位；执行时读取实际工作区版本及授权范围 |
| R11 | [ValidationIssue / locatedValidationError](https://github.com/jsdfhasuh/emo_master/blob/ba30c2ab94a1fbc1d7396c12788dd695183bb88a/src/emo_master/core/workflow/errors.py) | U5 再审查已读取；沿用 code/message/projectId/workflowId/nodeId/fieldPath 的诊断结构，不另造定位体系 |


## v6 本轮新基线核对

| ID | 来源 | 本轮读取结论与限制 |
| --- | --- | --- |
| N0 | [旧新提交比较](https://github.com/jsdfhasuh/emo_master/compare/ba30c2ab94a1fbc1d7396c12788dd695183bb88a...556acb10760a9463fe832aa4d8e7fc5c0a95a67e)；[新 HEAD](https://github.com/jsdfhasuh/emo_master/commit/556acb10760a9463fe832aa4d8e7fc5c0a95a67e) | GitHub 连接核对 main 与差异；6 个新提交。没有新增本计划的新 Web/MCP/候选 RPC，不能据此勾选实现任务 |
| N1 | [ROI manifest](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/src/emo_master/plugins/builtins/roi/manifest.json)；[ROI operator](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/src/emo_master/plugins/builtins/roi/operator.py) | 本轮读取；1.2.0、regions 组合、嵌套几何参数、keep/exclude、enabled/name；Qt 编辑器元数据不等于 MCP 绘图功能 |
| N2 | [Collection Filter manifest](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/src/emo_master/plugins/builtins/collection_filter/manifest.json) | 本轮读取相关段；1.3.0，轮廓点数筛选及条件字段 |
| N3 | [Minimum Enclosing Circle manifest](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/src/emo_master/plugins/builtins/minimum_enclosing_circle/manifest.json) | 本轮读取；1.1.0、scope=perContour/allContours，不能解释成平均圆心 |
| N4 | [PresentationStore](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/src/emo_master/apps/designer/state/presentation_store.py)；[WorkflowStore](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/src/emo_master/apps/designer/state/workflow_store.py) | 本轮读取相关代码；页面仅校验页面部分，返回隔离副本；工作流捕获深拷贝节点，不能回退新状态所有权语义 |
| N5 | [页面初始化优化提交](https://github.com/jsdfhasuh/emo_master/commit/f475f34e086c2518540908282690a9b03b85f503)；[ROI 改动提交](https://github.com/jsdfhasuh/emo_master/commit/babc86cd9dd134bd1593e4e57be49c6df8d67853) | 提交/变更文件列表核对；桌面交互和性能改进保留，不转成 Web 全量迁移要求 |
| N6 | [编辑会话测试](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/tests/core/presentation/test_edit_session.py)；[工作流状态测试](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/tests/designer/test_workflow_store.py) | 前者本轮读取，后者变更列表定位；复用页面快照隔离、历史更新和新状态测试；本轮未运行 |
| N7 | [Workspace Delivery](https://github.com/jsdfhasuh/emo_master/blob/556acb10760a9463fe832aa4d8e7fc5c0a95a67e/docs/testing/2026-11-12-workspace-delivery.md) | 从 HEAD 提交内容读取：报告的被测源码是 `fb54ae097cfdd3112d34cbba4be33fede5bb02b3`，后续 HEAD 为文档收尾；1755 passing 是报告记载的既有本地回归，不是本轮复跑或 MCP 验收 |

算子版本表只描述本次源码；部署目标可能仍运行旧版本。实时目录以目标实际返回为准。没有检查或修改用户当前 Designer/Runtime 进程。直接 Git 网络下载在本容器因 DNS 不可用而失败；源码阅读使用已连接 GitHub 工具完成，未声称克隆并执行本地仓库。

## 外部规范：仅用于组织和协议边界

| ID | 官方来源 | 本包使用方式 |
| --- | --- | --- |
| O1 | [OpenSpec spec-driven](https://openspec.dev/docs/schemas/spec-driven)、[CLI](https://openspec.dev/docs/cli)、[变更元数据](https://openspec.dev/docs/configuration/change-metadata) | proposal/design/tasks、按能力目录放 spec.md、ADDED Requirements、Requirement/Scenario 标题和 WHEN/THEN；schema: spec-driven；不把未实施内容提前放入主 specs |
| O2 | [MCP Tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | tools/list 与业务目录分离、明确 inputSchema、structuredContent 和工具结果；这是本次参照版本，不声明它是执行时最新协议 |
| O3 | [MCP Transports 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | v5 重新读取；该参考版本不将普通断开直接解释为取消。保留作为可选择且须实测的版本，不强制所有版本沿用它 |
| O4 | [MCP Cancellation 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation) | v5 重新读取；该版本 Streamable HTTP 的请求 SSE 响应流关闭是取消信号；用于确认生命周期条款应绑定实际协议，不要求升级或双版本兼容 |

O1–O4 保留自 v5 的格式和协议来源记录；v6 未重新核验这些外部协议页面，也未改变按实际协商版本处理取消的条款。本轮变更依据新代码差异，不引入新的 SDK/协议版本承诺。

外部资料没有增加多租户、任务系统、客户端矩阵或生产操作要求。阶段 A 固定一个实际可用组合；业务草稿生命周期与请求传输生命周期分开。实际文档校验情况见交付根目录 review/validation.md。
