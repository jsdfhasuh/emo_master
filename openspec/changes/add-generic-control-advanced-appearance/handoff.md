# 本地执行交接说明

## 当前交付状态

本包是 `add-generic-control-advanced-appearance` 的 **OpenSpec 实施计划**，schema 为 `spec-driven`，新增 capability 为 `page-advanced-appearance`。源码核对基线为 `6cbff0dcbea671663cf9ab773c6ce5c81690da35`。

本轮交付仅生成和检查计划文件；不代表已修改产品、通过运行验收、获得本地实施或推送授权。下列提示供用户后续授权实施时使用。是否通过 OpenSpec CLI 校验以包内实际验证记录为准；未执行的命令不能预先登记为成功。

当前为 v0.7 审查版。先阅读 [review-notes.md](review-notes.md)；全类型做基础效果覆盖，共享流程只用代表对象验证，实际目标配置见 `examples/target-effects.md`。保留 v0.6 参考图，不把重画图册或全属性隔离作为实施门槛。

## 开始执行时

1. 阅读实际仓库的 `AGENTS.md`、`openspec/config.yaml`，记录当前提交和未提交修改。保留用户工作区；遇到同文件未提交改动先理解并整合，不重置、覆盖或批量清理。
2. 阅读本 change 的 `proposal.md`、`design.md`、`specs/page-advanced-appearance/spec.md` 和 `source-map.md`，将当前代码与固定基线对照。仅复制本 change 的新增计划文件，不替换仓库 `AGENTS.md`、`openspec/config.yaml` 或旧 change。
3. **只按本 change 的 `tasks.md` 维护实施进度。** 不另设相互冲突的执行清单。任务未实施、未完成要求的验证，或只有静态示意图时，都保留未勾选；验证记录要写命令、环境、实际结果及未覆盖项。
4. 全量验收是 **13 类可见组件 + 开始／停止两个宿主控件**。首批 3 类只用于打通共用模型、Designer、Qt、Web、保存和发布链路，不能当作整项完成。具体批次与覆盖矩阵以 `tasks.md` 和设计为准。`spacer` 继续只负责布局。
5. 按现有外观入口扩展。Designer 使用 QSS，Web 使用 CSS；双方保存共用配置及另一端样式，不承诺任意 QSS／CSS 自动互转。结构化外观、独立部件与状态覆盖优先于随意追加全局样式。
6. 先修改共用源模型并运行仓库既有合约生成流程，再贯通两端编辑、预览、运行、草稿和发布。按功能触及的正常路径与直接反例验证，不额外建立通用兼容层或不相关改造。

13 类可见组件为 `image`、`text`、`number`、`indicator`、`runtime_status`、`table`、`navigation_button`、`container`、`page_title`、`page_navigation`、`clock`、`sqlite_table`、`sqlite_value`；宿主控件为 `startControl`、`stopControl`。

## 验证与证据

交付中的 draw.io、叠加标注及改进效果图用于说明**计划改动位置和目标效果**。即使底图来自既有软件截图，修改后的示意图仍不是当前软件实装截图，不能作为点击、保存、发布或跨端一致性验收的证据。

实施后分别记录实际做过的模型／保存测试、Qt 自动化、Web 测试、源码运行或软件仿真、人工视觉核对。没有执行的手工、真实硬件和生产工程验证应明确保留未验证；无需为了此外观改动擅自连接相机、PLC 或改动生产工程。涉及示例工程时，按仓库当前正式模型生成并通过当前校验，不以旧版示例或迁移常量猜测最新格式。

可在实际仓库根目录执行以下 OpenSpec 检查；CLI 版本由交付验证记录和仓库可用工具核实，不能将“命令已列出”写成“检查已通过”：

```bash
openspec --version
OPENSPEC_TELEMETRY=0 OPENSPEC_NO_UPDATE_CHECK=1 openspec status --change add-generic-control-advanced-appearance
OPENSPEC_TELEMETRY=0 OPENSPEC_NO_UPDATE_CHECK=1 openspec validate add-generic-control-advanced-appearance --strict
```

PowerShell 可先设置 `$env:OPENSPEC_TELEMETRY = "0"` 和 `$env:OPENSPEC_NO_UPDATE_CHECK = "1"`，再执行相同 `openspec` 命令。产品验证和合约生成按当前仓库脚本及 `tasks.md` 执行；OpenSpec 格式通过不等于产品功能通过。

## 提交与推送

本次计划包不发起提交、推送或远端评论。未来仅在用户已授权对应操作后执行推送；遵守用户“推送代码时附上 ChatGPT Web 评论”的要求，并按实际执行角色如实署名。若实际由本地 Codex 实施，不冒称 ChatGPT Web 已完成开发或验收；评论应说明 ChatGPT Web 的计划来源、本地执行者、具体修改、真实验证结果与未验证项。未运行的测试、未截取的实装图、未完成的全范围覆盖不得写成已完成。
