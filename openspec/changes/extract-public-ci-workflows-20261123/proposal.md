# 提取公共 CI 工作流并闭源 emo_master

## 背景

emo_master 当前 GitHub 仓库仍为 Public。2026-11-23 的 main 基线为 `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`。仓库包含 12 个启用中的 Actions 工作流、4 个归档工作流，以及产品专属 CI 脚本和诊断代码。

目标是将 emo_master 转为 Private，同时把适合跨项目复用的 GitHub Actions 能力沉淀到独立 Public 仓库（暂定 `jsdfhasuh/emo_master_ci`），并保持私有仓库可持续执行完整测试、构建和发布。

## 目标

- 建立独立公开的 CI 工具仓库，提供经过固定 SHA 引用的 reusable workflow / composite action。
- 私有 emo_master 继续持有所有产品源码、产品测试、测试数据、诊断脚本和发布策略。
- 私有仓库的 PR、push、手动运行继续触发 Linux/Windows Python 3.10 全量检查，保留现有分片覆盖与汇总门禁。
- 通过公开仓库自带的合成示例项目验证公共工作流，不将 emo_master 源码复制到公共仓库。
- 在完成跨仓库调用、PR 检查、构建和发布验证后，将 emo_master 仓库可见性改为 Private。

## 非目标

- 不迁移 Designer、Runtime、Web、算子、设备、协议或任何产品实现。
- 不公开产品测试代码、诊断观察器、生产配置、工程样例或内部证据。
- 不重写现有测试框架，不减少测试覆盖，不为了抽象而将产品专属步骤参数化成任意 shell 命令。
- 不在本变更中重构 `python_build_scripts` 的产品打包逻辑；继续固定 SHA 调用现有打包工作流。
- 不承诺通过改为 Private 撤回已经被公开 Fork、Clone、缓存或下载的代码。

## 实施策略

优先从主 `CI` 工作流中提取稳定、通用且能独立测试的 Runner 编排能力。产品专属的变更范围选择、`scripts/ci_check.py`、pytest 分片实现和分片报告仍留在 emo_master；公共工作流只负责在调用方仓库上下文中检出源码、配置 Python、安装调用方依赖、执行固定约定的检查入口、上传/汇总产物。

其余 11 个活动工作流逐个按 design.md 分类，不以“全部 YAML 搬走”为目标。公开仓库只提供通用能力，不主动拉取私有源码；使用 `workflow_call` 时，checkout 默认检出调用方仓库。所有外部工作流和 Action 固定到审核过的完整 Commit SHA。

## 完成标准

- 公共 CI 仓库已创建、公开，具有合成样例项目和通过的自测工作流。
- emo_master 私有分支成功调用固定 SHA 的公共 reusable workflow；PR 检查名、Linux/Windows 两平台、Python 3.10、分片覆盖、artifact 与汇总行为保持。
- Web、诊断、Windows 包验证、发布等产品专属工作流仍可在私有仓库独立运行。
- 私有仓库的 PR、push、手动 CI、Windows release 和 Runtime package 路径完成验证。
- 对外部打包仓库的私有源码读取凭据、release token、权限和仓库可见性影响完成核对。
- 迁移清单记录所有 12 个活动工作流及 4 个归档工作流的处置，不把未执行或 skipped 当作通过。
- 完成公开历史与产物风险告知后，由仓库管理员执行 Private 可见性切换，并验证切换后 Actions 仍可运行。
