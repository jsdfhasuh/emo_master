# 独立完整工程草稿与 MCP 编排：v7 计划归档

整理日期：2026-11-13。状态：计划资料已整理，尚未执行本计划的功能开发或验收。

本轮用户要求为“你把计划整理到计划文件夹，不额外开启分支开发”。因此，本目录保存 v7 原始计划包、附带的实施提示词和本阅读入口。原文中的“开始实施”、创建仓库根活动变更、初始化 OpenSpec、执行开发与验收等指令属于归档资料，不是本轮执行授权。

v7 是后续实施的计划参考版本；v6 保留为历史来源，不叠加已取消的要求。[v6 原始归档](../2026-11-13-independent-project-authoring-v6/README-OpenSpec.md)及其摘要保持不变。本目录中的 `openspec/changes/` 保留压缩包结构，属于计划材料存档，不表示已在仓库根启用活动变更或交付这些能力。

## 阅读入口

| 顺序 | 文件 | 用途 |
| --- | --- | --- |
| 1 | [原包说明](README-OpenSpec.md) | v7 定位、关键决策、版本增量和原始接手说明 |
| 2 | [proposal.md](openspec/changes/add-independent-project-authoring/proposal.md) | 目标、范围、五项新增能力及不纳入事项 |
| 3 | [design.md](openspec/changes/add-independent-project-authoring/design.md) | 独立架构、共用工程规则、目录链路、事务及接口边界 |
| 4 | [tasks.md](openspec/changes/add-independent-project-authoring/tasks.md) | 25 项待办，保留原始未勾选状态 |
| 5 | 下表中的五项能力规范 | 各能力的可观察行为与场景 |
| 6 | [acceptance.md](openspec/changes/add-independent-project-authoring/acceptance.md) | G1–G7 验收和主贯通流程 |
| 7 | [file-transfer.md](openspec/changes/add-independent-project-authoring/file-transfer.md) | 上传、替换、修订传递与鉴权下载契约 |
| 8 | [v7 基线对齐记录](review/baseline-alignment-v7.md)、[sources.md](openspec/changes/add-independent-project-authoring/sources.md) | 已有协议/测试基础、剩余链路和固定来源 |
| 9 | [原始实施提示词](implementation-prompt-source.txt) | 用户附带文本的逐字节副本，仅作后续实施参考 |

五项能力规范：

| 能力 | 规范 |
| --- | --- |
| 独立完整工程草稿与编辑 | [project-draft-authoring](openspec/changes/add-independent-project-authoring/specs/project-draft-authoring/spec.md) |
| 真实算子/设备目录与编辑能力发现 | [operator-capability-discovery](openspec/changes/add-independent-project-authoring/specs/operator-capability-discovery/spec.md) |
| 工程资源及正式交付 | [project-resource-delivery](openspec/changes/add-independent-project-authoring/specs/project-resource-delivery/spec.md) |
| 目标候选工程预检 | [candidate-project-precheck](openspec/changes/add-independent-project-authoring/specs/candidate-project-precheck/spec.md) |
| HTTP/MCP 统一服务入口 | [mcp-authoring-access](openspec/changes/add-independent-project-authoring/specs/mcp-authoring-access/spec.md) |

## 阶段与验收索引

下表是原始任务的导航摘要，具体范围以本目录的设计、规范和任务原文为准，不新增或勾选任务。

| 阶段 | 任务 | 主要交付目标 |
| --- | --- | --- |
| 执行基线 | 0.1–0.2，2 项 | 核对实际工作区、已有实现、可复用规则和依赖 |
| A：能力发现与最小可用服务 | 1.1–1.6，6 项 | 真实目录、编辑命令、独立草稿、基础流程编辑、撤销重做、保存、本地校验、HTTP/MCP 和实际客户端 |
| B：资源与正式工程交付 | 2.1–2.4，4 项 | 真实上传/替换、页面与输出绑定、正式工程导入导出、鉴权下载和 Designer 继续编辑 |
| C：目标候选预检 | 3.1–3.5，5 项 | 候选 ZIP 流、真实目标编译、结构化诊断、修复后再检查、正常取消与清理 |
| D：完整工程编辑能力 | 4.1–4.5，5 项 | 复杂流程、设备/变量、完整页面与生产配置、必要 Designer 适配核对和公开入口验收 |
| 收尾与交付 | 5.1–5.3，3 项 | 汇总七组验收、启动连接说明、实际完成记录和权限内交付 |

七组验收分别为：G1 独立工程与保存、G2 能力发现与共用规则、G3 草稿状态、G4 资源与导出、G5 真实预检、G6 正常取消、G7 实际客户端交付。具体观察点和证据要求见 [acceptance.md](openspec/changes/add-independent-project-authoring/acceptance.md)，本轮未执行这些验收。

原计划主线为：发现目标/命令/文件约定 → 查询 Image Loader 与 Threshold 详情 → 创建草稿 → 上传 PNG 并使用新修订绑定 → 配置连线与页面 mask 绑定 → 编辑及撤销重做 → 本地校验与保存 → 目标预检 → 按定位诊断修复并用新修订复检 → 导出及鉴权下载 → Designer 打开、修改、保存 → 隔离软件 Runtime 明确运行一次。G2 另含复合 ROI 嵌套配置和 D 阶段代表操作，不能只用 PNG 流程代替完整能力验收。

## v7 的复用重点

- 按原计划记录，`4a602120` 已声明并生成四个协议字段，`f663c007` 已修复 ROI 测试版本来源；后续实施先核对实际代码，不重复新增字段或重做修复。
- 任务 1.1 仍要求贯通真实 manifest/Runtime 实例身份 → Runtime RPC → 实际客户端/SDK → Web DTO → HTTP/MCP。字段定义存在不代表接口已经完成；未提供元数据与明确空列表须区分。
- 工程服务独立于 Designer 进程，优先复用正式工程模型、会话和保存入口；确需提取桌面规则时，同步调整直接相关的 Designer 调用。
- A/B 可以作为中间交付，完整完成仍包括 C/D 及相应验收。本轮归档不核验以上代码能力，也不把来源文档中的状态当作本轮功能验证结果。

## 本轮实际基线与整理范围

- 分支：`main`。
- HEAD：`b61a96f6d8135497531eabf18245118de253ee03`，与包内参考基线相同。
- 整理前 `git status --short` 无输出，工作区干净；整理后新增文件均位于本 v7 目录，保持未提交。
- 未创建或切换分支，未创建仓库根活动 `openspec/`，未初始化 OpenSpec，未修改实现代码或 v6 原始归档，未提交、推送、合并或发布。

## 来源与完整性

| 来源 | SHA-256 |
| --- | --- |
| `D:/jsdfhasuh/Downloads/emo_master_web_mcp_openspec_v7.zip` | `dd88f86798e4307e8a3b19534b683ad8af86d8240e4846219dd63c2b6b65189d` |
| 附件 `pasted-text-1.txt`，本目录保存为 `implementation-prompt-source.txt` | `4f76792c152d4c4e570662ec7fde64cf5a597878c549feca19bb56a0d0157731` |

原压缩包 19 个文件全部按原目录展开并保留原始字节，包括 [SHA256SUMS.txt](SHA256SUMS.txt)、`.openspec.yaml` 和 `review/` 中的原始记录。该清单覆盖其余 18 个原包文件，不包含清单自身；本轮核对 18/18 摘要匹配。另核对展开文件与 ZIP 条目逐字节一致，以及提示词副本与附件一致。

本目录另加本 `README.md` 和提示词副本，共 21 个文件。原包摘要清单保持原样，不用于声明新增两份文件已被原作者签入清单。

## 验证边界

本轮执行的是文件整理与完整性检查：原包及展开文件比对、18 项 SHA-256、提示词副本、任务计数/未勾选状态、阅读入口本地链接和改动范围检查。

[原包文档检查](review/validation.md)及其 [JSON 记录](review/validation.json)是来源方历史记录，其中的 PASS 仅指其文档结构、修订一致性和完整性检查。本轮未重跑其中的语义、示例或渲染检查，也未进行功能开发、源码测试、Qt 自动化、人工 Designer、MCP 客户端、Runtime 集成或真实设备验收。

当前 PATH 未找到 `openspec` 命令，本轮未安装 CLI，也未执行官方 strict validate；不宣称官方 OpenSpec 校验通过。今后获授权启动实施时，先按当轮要求读取实际 AGENTS.md，重新核对 HEAD、工作区及已有进度，再决定活动目录落位和验证。
