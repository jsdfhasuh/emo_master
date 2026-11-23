# 将 emo_master 验证 CI 迁移到公共 Runner 并闭源

## 背景

emo_master 当前 GitHub 仓库为 Public，2026-11-23 审查基线为 `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`。当前有 12 个活动 Actions 工作流、4 个历史归档工作流。用户选择优先最大限度节省 GitHub Actions 计算额度，同时将产品源码仓库转为 Private。

GitHub 对公共仓库使用标准 GitHub-hosted Runner 的计算分钟不收费；Reusable Workflow 即使定义在公共仓库，仍按调用方仓库计费。因此，本变更目标不是“把 YAML 放到公共仓库”，而是让主要验证任务真正由公共仓库触发并在公共仓库的标准 Runner 上执行。

## 目标

- 将 emo_master 的产品源码仓库转为 Private。
- 新建公开 CI 仓库 `jsdfhasuh/emo_master_ci`，实际承载 Linux/Windows 标准 Runner 上的产品测试、Web 测试、Runtime 诊断及可安全公开日志的验证任务。
- 私有 emo_master 只保留轻量触发/回调工作流、必要的发布控制和私有凭据；主要计算负载迁移至公共 CI 仓库。
- 公共 Runner 只在受信任的提交上读取私有源码；读取凭据不进入被测代码环境，且不出现在 dispatch payload、日志或产物中。
- 通过 GitHub App 的最小权限和 Check Runs 回调，将公共执行结果准确关联到私有仓库的原始 commit / PR，保留 required-check 门禁。
- 最大限度保留现有 Python 3.10、Linux/Windows、pytest 分片、Web、Display Session、Runtime 诊断和冻结包验证覆盖。
- 只有在公开 Runner 联调、私有回调、凭据隔离和闭源后复验通过后，才将 emo_master 切换为 Private。

## 非目标

- 不公开 emo_master 源码、测试代码、工程样例、诊断原始证据、构建配置或内部文档。
- 不将私有源码提交、镜像或长期存放在公共 CI 仓库的 Git 历史、分支或 Release 中。
- 不把私有源码上传为公共 Actions Artifact；公共 Runner 运行日志只允许输出经过审核的摘要。
- 不在公共 Runner 上执行来自未信任 fork / 外部贡献者的 PR 代码；这类 PR 不自动进入公共执行链路。
- 不把 Release 写入凭据、私有包或最终发布资产交给公共 CI 仓库。
- 不为了抽象而将产品专属测试重写为通用框架，不减少断言或覆盖。
- 不将 GitHub Actions 公共仓库免费误解为所有配套服务、Artifact 存储、网络、第三方服务或大型 Runner 均免费。

## 核心架构

私有 emo_master 监听 push / pull_request / workflow_dispatch，使用专用 GitHub App 对公共 CI 仓库发起 dispatch。公共 CI 仓库在受信任的默认分支工作流中，根据固定仓库白名单和 commit SHA 获取私有源码，在公共标准 Runner 执行验证。测试结束后由独立、干净的回调 job 使用最小权限 GitHub App 更新私有仓库 Check Run；私有 PR 的合并门禁依赖该 Check Run。

公共 Runner 的主要测试 job 不持有私有仓库 token、App 私钥、Release token 或其他凭据。私有源码仅存在于该次临时 Runner 工作目录中；不推送到公共 Git 仓库、不上传公共 Artifact，任务结束后由 GitHub 回收 Runner。日志与报告必须先做公开安全审查，只输出白名单摘要。

## 完成标准

- 公共 CI 仓库已创建并公开，默认分支受到保护，外部 fork PR 不会触发任何持有私有仓库凭据的工作流。
- 私有 emo_master 可通过 GitHub App 安全触发公共 CI；dispatch 只传 repo 固定标识、commit SHA、PR 元数据和关联 Check Run ID，不传 Token。
- 公共 Runner 能读取指定私有 commit，读取凭据不进入测试进程，并在测试开始前撤销或销毁。
- 公共测试日志和产物不暴露私有源码、路径、工程数据、诊断原始记录、环境变量或凭据。
- 所有验证任务在公共标准 Linux/Windows Runner 实际运行，公共 Runner 运行分钟不计入私有仓库额度；记录实际迁移的 runner minutes。
- 私有仓库的 required check 能正确显示 queued / in_progress / success / failure / cancelled，并绑定准确 SHA。
- 现有测试覆盖、分片覆盖证明、Web/Runtime/Windows 验证和诊断语义保持；未运行的测试不能标记通过。
- Release 发布凭据和私有发布资产仍留在私有边界，实际发布前验证来源和产物身份。
- emo_master 转为 Private 后，重新验证 PR、push、手动触发、检查回调、Windows 构建/发布控制链路。
- 迁移报告包含费用节省计算、公共执行的安全边界、已验证项目和未验证项。
