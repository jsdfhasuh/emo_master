# EMO Master · OpenSpec 交付与 Sol 接手说明

**交付版本：v7 / 协议字段与测试基线对齐版**  
**日期：2026-11-13**  
**变更 ID：`add-independent-project-authoring`**  
**参考基线：上轮已核对 main `b61a96f6d8135497531eabf18245118de253ee03`（PR #13）**  
**状态：四字段声明与生成代码、ROI 测试版本修复已存在；Runtime 填值、目录适配与新 Web/MCP 公开接口仍需实施。**

本包在上传 v6 上合并对 4a602120 与 f663c007 的审查结论，替代 v2–v6 的活动实施约束，不另叠加旧补丁要求。独立 Web、完整工程、F1–F6、嵌套 ROI、页面快照、Designer 局部适配与七组验收保持。四个已有协议字段不重建，Runtime/客户端/MCP 真实映射仍待完成；ROI 版本使用实际注册/目标值而不减少功能断言。本轮只修改文档，没有重新获取远端最新 HEAD 或执行代码测试。

## 文件怎么使用

```text
openspec/changes/add-independent-project-authoring/
├── .openspec.yaml
├── proposal.md                 为什么做、范围和五项能力
├── design.md                   独立架构、局部提取、接口及三幅时序/关系图
├── tasks.md                    四阶段任务，全部未勾选
├── acceptance.md               七组业务验收、诊断修复与公开命令验证
├── file-transfer.md            上传/下载契约与 PowerShell 辅助步骤
├── sources.md                  用户材料、讨论结论和固定源码依据
└── specs/
    ├── project-draft-authoring/spec.md
    ├── operator-capability-discovery/spec.md
    ├── project-resource-delivery/spec.md
    ├── candidate-project-precheck/spec.md
    └── mcp-authoring-access/spec.md
```

先读 proposal.md 与 design.md，再读当前任务对应 spec，按 tasks.md 执行。涉及文件上传和下载时同时阅读 file-transfer.md。specs 定义对外可观察行为；design 解释实现选择；tasks 记录工作完成情况；acceptance 将场景合并为少量业务验证，不为每条场景新建测试工程。

**未实施的要求只在 changes 下。** 本包不提供已生效的 `openspec/specs/`，也不覆盖仓库根 AGENTS.md、README.md 或已有 OpenSpec 配置。

## 放入仓库

把本包的 `openspec/changes/add-independent-project-authoring/` 目录放到仓库根的对应位置；若已存在，先比较合并，保留真实实施进度与用户修改。**不要覆盖 `docs/plans/2026-11-13-independent-project-authoring-v6/` 及其 SHA256SUMS：它是 v6 原始归档，不是本轮活动目录。** 新归档需要单独的 v7 目录。阅读根目录和实际文件夹的开发规范。

使用已安装的 OpenSpec CLI 在仓库根目录检查（本包本轮是否执行成功以 review/validation.md 为准）：

```bash
openspec list --specs
openspec status --change add-independent-project-authoring --json
openspec validate add-independent-project-authoring --type change --strict --no-interactive
openspec instructions apply --change add-independent-project-authoring --json
```

若仓库未初始化，可按实际工具环境执行 `openspec init --tools none` 创建基础配置后再核对已有能力；不指定或伪造 Sol 的集成名称。存在同义正式能力时，先读取其完整 spec，合并为相应 delta，不另建重复能力。没有 CLI 仍可按 Markdown 执行，但不能宣称官方校验已通过。

本包遵循官方 spec-driven 常规结构。可用的斜杠指令取决于客户端已安装的集成，**Sol 只要能读取仓库文件就可以按下面的普通提示词接手**，不依赖某个专用 `/opsx:*` 命令。

## 关键方向

| 事项 | 本包决策 |
| --- | --- |
| Web 是否控制电脑 Designer | 不控制、不启动、不连接；两边各持工程会话 |
| Designer 是否完全不能改 | 可局部提取 Web 真正需要的规则并适配对应调用；不整体改造，不强制统一事务/命令框架 |
| 算子支持信息 | 首期提供摘要、完整详情、实际编辑操作；目录存在、可编辑和预检通过分别表达 |
| 简化要求 | 无旧 Web 迁移、多租户、支持百分比、目录句柄体系、复杂资源修复或多资源子流 |
| 交付范围 | 正式完整工程、资源、设备、变量、复杂流程、页面与生产配置；完整 Web 流程画布不在本次范围 |
| 运行权限 | 检查不发布、不运行；软件运行只是隔离环境中的一次明确验收 |

## 给 Sol 的接手指令

```text
请实施 openspec/changes/add-independent-project-authoring/ 中的变更。

先读取实际 AGENTS.md、proposal.md、design.md 和当前任务对应的 specs，
记录 HEAD/分支/工作区状态并核对现有实现，不覆盖用户改动，不回退到参考提交。
以本 v7 包为本轮完整计划，旧版只作来源，不叠加已替换要求。
参考基线为上轮已核对 main b61a96f6d8135497531eabf18245118de253ee03；核对实际后续改动，不回退提交。
保留 docs/plans/2026-11-13-independent-project-authoring-v6/ 原始归档与摘要，
在仓库根的活动 openspec/changes/ 中合并修订。
4a602120 的四字段定义/生成与 f663c007 的 ROI 版本测试修复已存在，不重复做；
该基线 Runtime 尚未填值，任务 1.1 仍需贯通真实目录链路，不能整体勾选。

按 tasks.md 的 A–D 顺序逐段交付：先算子能力发现和最小工程草稿，
再资源/正式交付，再候选预检，最后补齐复杂流程、设备、变量和页面配置。
Runtime 侧预检可在核心契约明确后并行准备，不等待全桌面改造。

Web/MCP 必须在 Designer 关闭时独立工作。优先直接复用已有无界面能力。
确需从界面提取规则时，在同一变更调整对应桌面调用并做局部回归，
不要等到 D 才接回；保持一项操作一次事务，
不重写整个 Designer，不复制另一套规则，不强制全量搬包。

算子查询必须有摘要和完整详情，保留真实参数/端口/模式/设备声明；
复用 next_cycle_parameter_paths_json(18)、min_core_version(19)、max_core_version(20)
及 ListOperatorsReply.runtime_instance_id(3)，不重复新增或改号。
从 manifest/真实实例身份填值，核对实际加载的客户端/协议代码并映射到 HTTP/MCP；
空字符串为未提供，JSON [] 为明确空列表，不能用 or "[]" 抹去未知。
源码生成文件更新不代表部署 wheel 已更新。当前实现能力从真实命令生成。
将算子声明、编辑支持和工程预检分开，未知明确说明，不能编造支持百分比。
递归保留 ROI regions/points 等嵌套参数和 xVisibleWhen/xValidateWhen；
结构化配置不依赖 Qt 绘图，已部署目标未升级时不能冒报新源码能力。
同源码测试保留 manifest/注册版本一致断言；远端按目标目录版本请求，
不把旧 ROI 1.1.0 改为另一固定常量，也不删嵌套配置、摘要和注册快照断言。
仅页面读取复用新 PresentationStore.snapshot，不恢复逐控件全工程序列化；
完整事务、保存、预检和导出的校验/快照隔离仍保留。

阶段 A 固定一个真实 Python/SDK/协商协议/客户端组合，取消语义随实际协议；
草稿不随连接删除，取消不擅自撤销已提交编辑，不自动重放写请求。
按 file-transfer.md 完成真实字节上传、新修订绑定和携带凭据下载；
需要 HTTP 辅助就如实记录，不把 URL 或本地路径当作已传输文件。
保留已有 code/message/workflowId/nodeId/fieldPath 等诊断，
本地 validate 无需在线 Runtime；目标 precheck 才用真实目标编译。

验证按 acceptance.md 七组观察目标复用一个 PNG 图像流程，
包含“诊断 → 修复 → 新修订再检查 → 实际下载”；
D 阶段高级能力用既有 fixture 从新公开命令入口代表性验证并保存重读，
只增加直接相关反例，不建立全功能/全异常/全客户端测试矩阵。
保持范围完整，不用 stub、固定成功、跳过失败断言或删节点代替实现。
每完成任务再勾选，记录实际验证、未验证及真实客户端版本。

只有会改变用户目标、引入生产副作用或扩大桌面重构的发现才单独确认；
其它局部实现选择自行处理并记录。推送、合并、发布以用户实际授权为准。
获授权推送时标注实际执行者，并附“ChatGPT Web 计划来源”评论，
说明改动、已验证和未验证，不伪装为其他执行者。
```

## v7 相对 v6 的增量

| 调整 | 直接落点 |
| --- | --- |
| 四字段已声明，不重复新增/改号 | proposal、design D5.1、tasks 1.1；沿用原编号及保留声明 |
| 剩余真实链路 | Runtime manifest/实例身份填值 → 实际协议包解析 → 目录 DTO → HTTP/MCP；1.1 仍未完成 |
| 未提供与空列表分开 | design D5.1、目录 spec 现有场景、G2；不从空字符串断言目标一定是旧版 |
| ROI 使用实际版本但保留功能断言 | design D5.2、tasks 1.6、acceptance；沿用 f663c007，不重复修复 |
| 新基线和历史归档 | tasks 0.1、sources P0–P4；不覆盖仓库 v6 原包及摘要 |

详细记录见 [review/baseline-alignment-v7.md](review/baseline-alignment-v7.md)。保持五项能力、四阶段、七组验收和 25 项未勾选的接口实施/验收任务；已完成的协议基础用文字记录，不因此把目录功能任务整体勾选。

## 沿用 v6 的增量（历史来源）

| 调整 | 已合并的位置 |
| --- | --- |
| 新基线与状态 | proposal/design/sources、任务 0.1；6 个新提交不作为 MCP 接口完成证据 |
| ROI 嵌套配置与条件声明 | design D4/D5、目录和草稿 spec、任务 1.1/1.3/1.6、G2 |
| 集合筛选/求圆与运行版本 | 目录从实际目标读取，不硬编码源码版本或扩成算法重测 |
| 页面快照及状态隔离 | design D4、草稿 spec、任务 2.2、G2；复用现有优化和测试 |
| 桌面范围与证据归属 | design D2、任务 4.4、sources；不移植 Qt ROI 编辑器、不把历史测试记作本轮通过 |

历史差异见 [review/baseline-alignment-v6.md](review/baseline-alignment-v6.md)。其中基线与工具环境记录属于 v6，不表示本版再次核验；活动要求以本 v7 为准。

## 沿用 v5 的补齐

| 审查项 | 已合并的位置 |
| --- | --- |
| F1 协议与取消 | design D8.2、MCP spec、任务 1.5/3.4、G6/G7 |
| F2 文件往返 | file-transfer.md、design D6/D8、资源/MCP spec、任务 2.1–2.4、G4/G7 |
| F3 可定位诊断 | design D8.1、MCP/预检 spec、任务 3.3/3.5、G5/G7 |
| F4 校验分工 | design D7、本地草稿/预检 spec、任务 1.3/3.2、G1/G5 |
| F5 同步桌面适配 | design D2、tasks 跨阶段约束及 4.4 |
| F6 高级公开入口 | 任务 4.5、acceptance G2；不是第二套完整端到端验收 |

详细修订记录见 [review/changes-v5.md](review/changes-v5.md)。这些要求继续保留在本 v7 中，旧修订记录只作来源；任务仍全部未勾选。

## 文档验证范围

交付检查结果见 [review/validation.md](review/validation.md)。文档检查不是实现测试；所有新增能力均仍待实现。官方资料及本次核对的固定源码在变更内 sources.md 中，不需要执行者重新猜来源。
