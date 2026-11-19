# V1.3 计划补丁检查记录

日期：2026-11-18（北京时间）。固定实现基线：ce8f84caf4d0fd779e579b23ff82fa4e80de978d。本文件只记录计划与补丁检查，不是 R1–R6 的源码实现验收。

## 1. 官方 OpenSpec strict

使用官方 `@fission-ai/openspec` CLI **2.1.0**，校验结果为：

```text
Change 'enhance-generic-page-composition' is valid
```

执行命令等价于：

```bash
OPENSPEC_TELEMETRY=0 OPENSPEC_NO_UPDATE_CHECK=1 openspec validate enhance-generic-page-composition --strict
```

本次执行已下载的官方 CLI 入口，不通过 npx 再次解析包。最初默认调用因额外请求 edge.openspec.dev 被自动审批拦截；随后只读核对官方 README、telemetry 和 version-check 源码，确认上述官方开关关闭遥测和自动版本查询，再进行本地 strict 校验。没有修改 CLI、替换验证器或忽略警告。

首次离线 strict 提示两段 Requirement 过长；已将其精简为契约要点，详细设计保留在 contracts/design 和现有 Scenario，未新增并行能力或测试矩阵。最终 exit code 为 0。

## 2. 计划结构与引用

| 项目 | 实际结果 |
| --- | --- |
| 变更身份 | 沿用 enhance-generic-page-composition；文档 V1.3，不另建 change |
| 当前任务 | 唯一 tasks.md，恰好 R1–R6 六项，全部未勾选 |
| 正式规格 | 六份 spec，共 41 条 Requirement、48 个 Scenario |
| 相对提交基线增量 | ce8f84ca 实际为 40/46；本次仅增加 1 条 Requirement、2 个 Scenario，其余为定点修订 |
| 增量用途 | Web 来源引用复制及再编辑发布；Qt 动态文字测量 |
| 相对文件链接 | 22 处，按覆盖后的仓库上下文解析，目标存在 |
| 当前固定 SHA 文件链接 | 2 个不同路径在基线检出中存在 |
| 源码落点 | source-map 所列 33 个关键文件/目录存在 |
| JSON | 3 个局部示例与 review-evidence.json 解析通过；局部示例不冒充完整可运行工程 |
| Markdown | 代码围栏配对；当前任务、契约、验收与交接已交叉核对 |
| 原参考图 | 3 张与 ce8f84ca 中字节一致，补丁不重复打包 |

48 个 Scenario 是整个 change 的累计行为说明，不代表新增 48 个测试。原历史回看与 Annotate 规格继续复用。原 V1.2 22 项任务的固定提交记录保留，未将其全部重开。

## 3. 补丁应用与打包检查

- 差异仅覆盖同一 change 内有变化的 16 个 Markdown/JSON 文档；没有源码、测试实现、SDK 二进制或 CI 配置改动。
- 对干净的 ce8f84ca 检出执行 `git apply --check` 通过，原源码检出保持干净。
- 在临时副本实际应用统一差异后，全部 16 个修改文件与 overlay 字节一致；未改动的 spec、配置与参考图片保持原内容。
- ZIP 仅包含上述变动文件及使用说明、Codex 提示词、统一差异、清单，共 20 个文件成员；解压校验、路径检查和清单 SHA-256 核对通过。
- 覆盖文件与统一差异是同一修改的两种应用方式，不能重复应用。后续 HEAD 或本地文档存在修改时按语义合并，不以强制覆盖解决冲突。

## 4. 证据与范围

本轮没有重新执行平台功能测试，也没有把旧通过结果记成未来修复 SHA 上的复测。已审核的 ce8f84ca 历史 29 项、Annotate 5 项、软件 Runtime 六次实际归档、Web 构建与 protobuf 检查继续保留在 review-report/review-evidence 中，实施时按 acceptance 的影响表选取复验范围。

本补丁没有修改平台实现、没有推送或发远端评论、没有操作现场 PLC/相机。当前六项收尾仍由 Codex 据实际实现和验证后勾选。官方 strict 通过只能证明本计划的规格格式有效，不能代替真实编辑、发布、运行和视觉检查。
