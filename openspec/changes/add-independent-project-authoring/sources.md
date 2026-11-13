# 来源与决策依据（含 V7.1 实现审查）

**编号约定：**V7.1 活动设计/任务中的 F1–F6 均指 `3f717510` 实现审查。历史 V5 计划再审查也曾使用 F1–F6 编号；它们只在历史来源中保留，不是本轮新增修复任务，不重新叠加。

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
| U7 | v6 前用户说明“刚推送部分代码，核对计划是否需要变化” | 上轮 v6 静态对齐 ROI、集合算子、共享状态与桌面改进 |
| U8 | 用户指定 4a602120、f663c007，以及紧接的代码审查结果 | 四个协议字段及生成文件已存在；Runtime 尚未填值；ROI 测试改为实际注册版本 |
| U9 | 当前请求“那就改动计划压缩包” | 仅将 U8 直接合并为 v7 压缩包与合订本；保留历史归档，不改源码、不推送 |

**v4 相对 v3 已完成的修订**：3.3 的全局 Designer 顺序改为 design D2 的条件性最小适配；不再要求每个新命令同步迁移桌面。6.1/7.1 的泛化 catalog 被补为完整能力发现规范。原七组验收保留，新增目录字段断言并入 G2/G7。独立命名空间是实现选择，不再是全量搬包前置条件。其它资源/候选/完整工程要求继续沿用；未添加新的生产执行权限。

**v5 相对 v4 的修订**：保留原五项能力、四阶段与七组验收；将 U5 六项审查结论直接落实到 design/specs/tasks/acceptance，新增 file-transfer.md 记录最小传输契约与 PowerShell 辅助步骤。完整对应见交付根目录 review/changes-v5.md；不把旧审查文件作为第二份实施约束。

## 源码定位

**v7 参考基线：`b61a96f6d8135497531eabf18245118de253ee03`（上轮已核对的 main / PR #13）**；v6 参考基线为 `556acb10760a9463fe832aa4d8e7fc5c0a95a67e`。上轮 compare 记录二者 ahead=4、behind=0，包含字段提交、测试提交、文档归档与合并；其中实际新增实现范围为四字段协议/生成文件及 ROI 测试修复，不能当作 MCP 已交付。

本轮工作依据上传 v6 的真实文件及 U8 已提供的 GitHub 审查内容做文档修订，**未重新获取远端 HEAD，未重跑源码或协议测试**。下列 P0–P4 是上轮已读取内容的固定引用；不是本轮新增联网核验。执行时仍核对实际 HEAD 与部署目标。

R0–R11 为旧基线历史定位，N0–N7 为 v6 时读取的证据，相关文件的当前状态以实际工作区及本版 P0–P4 优先；历史链接不用于覆盖新实现。尤其 R9 不再表示四字段缺少协议定义，剩余工作按 design D5.1。无需为了文档基线回退任何提交。

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


## v6 基线核对（历史来源，保留固定链接）

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

以上是 v6 时的读取与工具记录，不表示 v7 重新执行：v6 的容器直接 Git 下载因 DNS 失败，使用 GitHub 连接完成静态阅读，未运行仓库。固定版本表不是远端版本要求；实时目录以目标返回为准。v7 没有检查或修改用户当前 Designer/Runtime 进程。

## v7 协议与测试对齐依据（上轮已读取）

| ID | 固定来源 | 已支持的结论与限制 |
| --- | --- | --- |
| P0 | [PR #13 合并基线](https://github.com/jsdfhasuh/emo_master/commit/b61a96f6d8135497531eabf18245118de253ee03)；[v6 到该基线比较](https://github.com/jsdfhasuh/emo_master/compare/556acb10760a9463fe832aa4d8e7fc5c0a95a67e...b61a96f6d8135497531eabf18245118de253ee03) | 上轮核对 main 与四个新增提交；本轮沿用固定基线，不声明它仍是最新 HEAD |
| P1 | [四字段提交](https://github.com/jsdfhasuh/emo_master/commit/4a602120c335babb6a1b4b49ddc3c3804adc4fca)；[当前 proto](https://github.com/jsdfhasuh/emo_master/blob/b61a96f6d8135497531eabf18245118de253ee03/proto/runtime.proto)；[生成文件](https://github.com/jsdfhasuh/emo_master/blob/b61a96f6d8135497531eabf18245118de253ee03/src/emo_panel_protocol/generated/runtime_pb2.py) | OperatorInfo 18/19/20 与 ListOperatorsReply 3 已声明；生成文件已同步；注释区分空字符串与 JSON 空列表 |
| P2 | [ROI 测试修复](https://github.com/jsdfhasuh/emo_master/commit/f663c007cb6df594e04f3488c9b9e81e491b54e0)；[测试文件](https://github.com/jsdfhasuh/emo_master/blob/b61a96f6d8135497531eabf18245118de253ee03/tests/runtime/test_operator_editor_preview.py) | 用 manifest 核对目录版本，再按 roiInfo.version/descriptor.manifest.version 请求；不是删除版本或功能断言 |
| P3 | [Runtime ListOperators 返回装配](https://github.com/jsdfhasuh/emo_master/blob/b61a96f6d8135497531eabf18245118de253ee03/src/emo_master/apps/runtime/grpc_server/service.py#L1182-L1241) | 上轮实际读取：该基线没有填四字段，字段定义不等于真实目录值已可用 |
| P4 | [工作区集成记录](https://github.com/jsdfhasuh/emo_master/blob/b61a96f6d8135497531eabf18245118de253ee03/docs/testing/2026-11-13-workspace-integration/README.md)；[v6 历史材料](https://github.com/jsdfhasuh/emo_master/tree/b61a96f6d8135497531eabf18245118de253ee03/docs/plans/2026-11-13-independent-project-authoring-v6) | 记录说明仅协议声明/生成、未发布新 SDK wheel；既有 91 项测试/生成/兼容检查是报告记载，不是本轮复跑；v6 原始材料及 SHA256 需保留 |

**v7 实施约定**：复用字段且不改号；接通真实填值/实际协议包/HTTP/MCP 映射；未提供与空列表分开；源码注册测试和远端测试分别采用对应真实版本；不撤掉功能断言。上述约定已直接落实在 proposal/design/tasks/目录 spec/acceptance，不要求另叠加口头审查。变更记录见交付根目录 `review/baseline-alignment-v7.md`。

**V7 历史文件基线（不是 V7.1 本轮执行记录）**：上传 v6 ZIP SHA-256 为 `c6fb71a2325a02c55c0f5ff8721fd5b9a539ebf1d76a0436527a49a005a3ae81`；本轮核验其中 18 项原始文件摘要均匹配，再在独立目录生成 v7，不修改输入 ZIP 或仓库 v6 归档。

## 外部规范：仅用于组织和协议边界

| ID | 官方来源 | 本包使用方式 |
| --- | --- | --- |
| O1 | [OpenSpec spec-driven](https://openspec.dev/docs/schemas/spec-driven)、[CLI](https://openspec.dev/docs/cli)、[变更元数据](https://openspec.dev/docs/configuration/change-metadata) | proposal/design/tasks、按能力目录放 spec.md、ADDED Requirements、Requirement/Scenario 标题和 WHEN/THEN；schema: spec-driven；不把未实施内容提前放入主 specs |
| O2 | [MCP Tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | tools/list 与业务目录分离、明确 inputSchema、structuredContent 和工具结果；这是本次参照版本，不声明它是执行时最新协议 |
| O3 | [MCP Transports 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | v5 重新读取；该参考版本不将普通断开直接解释为取消。保留作为可选择且须实测的版本，不强制所有版本沿用它 |
| O4 | [MCP Cancellation 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation) | v5 重新读取；该版本 Streamable HTTP 的请求 SSE 响应流关闭是取消信号；用于确认生命周期条款应绑定实际协议，不要求升级或双版本兼容 |

O1–O4 保留为 v5/v6 的格式与协议来源记录，v7 未重新核验这些外部页面，不将它们声明为最新规范。本版没有改变按实际协商版本处理取消的条款；只按 U8 已核对的代码更新实施状态，不引入新的 SDK/协议版本承诺。

外部资料没有增加多租户、任务系统、客户端矩阵或生产操作要求。阶段 A 固定一个实际可用组合；业务草稿生命周期与请求传输生命周期分开。实际文档校验情况见交付根目录 review/validation.md。

## V7.1 直接输入与事实边界

| ID | 来源 | 本版采用方式 |
| --- | --- | --- |
| I1 | [附件实现审查原文](../../../docs/testing/2026-11-13-independent-project-authoring-v7_1/review/implementation-review-3f717510.md) | 固定 SHA `3f717510751f4bd0d8277c3c2bf676236f51f49e`；保留 F1–F6 及 CI/静态/仓库记录/未验证等级；本轮未重跑 |
| I2 | [固定提交活动任务](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/openspec/changes/add-independent-project-authoring/tasks.md) | 本轮通过 GitHub 连接只读复核 25 项已勾选及首轮交付状态；本包保留原任务作为合并参考，不覆盖后续进度 |
| I3 | [固定提交设计](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/openspec/changes/add-independent-project-authoring/design.md) | 本轮只读前 50 行核对设计文档仍沿用早期基线措辞；V7.1 Context 直接替换这些过期状态 |
| I4 | 用户上传 V7 ZIP | 作为完整五能力/四阶段/七组验收文字基础；本轮核对其中 18 条逐文件 SHA-256 与 ZIP CRC |
| I5 | 本轮用户请求“Sol 提示词和压缩包，是否在 V7 上改” | 交付同一活动变更的修复版资料，不改仓库实现、不推送、不重跑现场验收 |

**本轮设计选择，不是源码事实**：F4 默认采用明确目标的已有草稿声明重新采用入口；F6 默认采用明确“未附带报告”的返回。两者来自审查给出的最小可选方案，本版为了让 Sol 直接执行给出首选；后续已有正确的等价实现可复用，不同时强制多套机制。

本轮没有查询远端最新 main 或 CI，没有为文档任务重查外部 MCP/OpenSpec 版本；上面的历史链接和 O1–O4 只保留来源，不声明最新。CLI 与本地文档校验范围见 [本轮文档检查](../../../docs/testing/2026-11-13-independent-project-authoring-v7_1/review/validation.md)。
