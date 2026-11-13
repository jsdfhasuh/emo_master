# EMO Master · OpenSpec 交付与 Sol 接手说明

**交付版本：v6 / 新源码基线对齐版**  
**日期：2026-11-12**  
**变更 ID：`add-independent-project-authoring`**  
**核对基线：main `556acb10760a9463fe832aa4d8e7fc5c0a95a67e`**  
**状态：平台底层已有新改进；本变更的新 Web/MCP 实现任务仍待执行。**

本包在 v5 上对齐用户新推送的 6 个提交，替代 v2–v5 的实施约束。保留已合并的 F1–F6、独立 Web、完整工程、算子能力发现、Designer 必要局部适配及七组验收；只补嵌套 ROI 参数与声明、目标实际版本以及页面快照优化的复用要求，不恢复旧 Web 迁移或重型防御机制。已有平台改进不等于 MCP 接口已交付。

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

把本包的 `openspec/changes/add-independent-project-authoring/` 目录复制到仓库对应位置。若目录已存在，先比较合并，不覆盖现有任务或用户修改。阅读根目录与实际文件夹中的开发规范。

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
以本 v6 包为本轮完整计划，旧版只作来源，不叠加已替换要求。
核对基线为 main 556acb10760a9463fe832aa4d8e7fc5c0a95a67e；不要回退旧基线，也不要把新增平台代码当作 MCP 接口已完成。

按 tasks.md 的 A–D 顺序逐段交付：先算子能力发现和最小工程草稿，
再资源/正式交付，再候选预检，最后补齐复杂流程、设备、变量和页面配置。
Runtime 侧预检可在核心契约明确后并行准备，不等待全桌面改造。

Web/MCP 必须在 Designer 关闭时独立工作。优先直接复用已有无界面能力。
确需从界面提取规则时，在同一变更调整对应桌面调用并做局部回归，
不要等到 D 才接回；保持一项操作一次事务，
不重写整个 Designer，不复制另一套规则，不强制全量搬包。

算子查询必须有摘要和完整详情，保留真实参数/端口/模式/设备声明；
已存在但未传出的关键字段按需贯通到协议和 SDK。当前实现能力从真实命令生成。
将算子声明、编辑支持和工程预检分开，未知明确说明，不能编造支持百分比。
递归保留 ROI regions/points 等嵌套参数和 xVisibleWhen/xValidateWhen；
结构化配置不依赖 Qt 绘图，已部署目标未升级时不能冒报新源码能力。
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

## v6 相对 v5 的增量

| 调整 | 已合并的位置 |
| --- | --- |
| 新基线与状态 | proposal/design/sources、任务 0.1；6 个新提交不作为 MCP 接口完成证据 |
| ROI 嵌套配置与条件声明 | design D4/D5、目录和草稿 spec、任务 1.1/1.3/1.6、G2 |
| 集合筛选/求圆与运行版本 | 目录从实际目标读取，不硬编码源码版本或扩成算法重测 |
| 页面快照及状态隔离 | design D4、草稿 spec、任务 2.2、G2；复用现有优化和测试 |
| 桌面范围与证据归属 | design D2、任务 4.4、sources；不移植 Qt ROI 编辑器、不把历史测试记作本轮通过 |

详细差异见 [review/baseline-alignment-v6.md](review/baseline-alignment-v6.md)。本包仍是五项能力、四阶段、七组验收、25 项未勾选任务。

## 沿用 v5 的补齐

| 审查项 | 已合并的位置 |
| --- | --- |
| F1 协议与取消 | design D8.2、MCP spec、任务 1.5/3.4、G6/G7 |
| F2 文件往返 | file-transfer.md、design D6/D8、资源/MCP spec、任务 2.1–2.4、G4/G7 |
| F3 可定位诊断 | design D8.1、MCP/预检 spec、任务 3.3/3.5、G5/G7 |
| F4 校验分工 | design D7、本地草稿/预检 spec、任务 1.3/3.2、G1/G5 |
| F5 同步桌面适配 | design D2、tasks 跨阶段约束及 4.4 |
| F6 高级公开入口 | 任务 4.5、acceptance G2；不是第二套完整端到端验收 |

详细修订记录见 [review/changes-v5.md](review/changes-v5.md)。这些要求已合并在本 v6 中，旧修订记录只作来源；任务仍全部未勾选。

## 文档验证范围

交付检查结果见 [review/validation.md](review/validation.md)。文档检查不是实现测试；所有新增能力均仍待实现。官方资料及本次核对的固定源码在变更内 sources.md 中，不需要执行者重新猜来源。
