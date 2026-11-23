# 将 emo_master 验证 CI 迁移到公共 Runner 并闭源

## 背景

emo_master 当前 GitHub 仓库为 Public，2026-11-23 审查基线为 `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`。当前有 12 个活动 Actions 工作流、4 个历史归档工作流。用户选择优先最大限度节省 GitHub Actions 计算额度，同时将产品源码仓库转为 Private。

GitHub 对公共仓库使用标准 GitHub-hosted Runner 的计算分钟不收费；Reusable Workflow 即使定义在公共仓库，仍按调用方仓库计费。因此，本变更目标不是“把 YAML 放到公共仓库”，而是让主要验证任务真正由公共仓库触发并在公共仓库的标准 Runner 上执行。

## 目标

- 将 emo_master 的产品源码仓库转为 Private。
- 新建公开 CI 仓库 `jsdfhasuh/emo_master_ci`，首阶段只承载独立合成项目的 Linux/Windows 基础设施自测；不得将私有产品源码、测试代码或诊断任务送入公共 Runner。
- 评估在不暴露产品源码的前提下节省额度。公共仓库只运行合成项目；产品 CI 在确认安全可行前继续留在私有边界，同时评估私有仓库自托管临时 Runner（GitHub 计算分钟免费，但主机、维护和隔离成本由用户承担）。
- 不得把“可信提交”“短期 Token”“撤销凭据”当作源码保密保证。公共标准 Runner 默认可访问公网；只有存在可验证、由 Runner 外部强制的默认拒绝网络出口和进程/文件隔离时，才可另行申请产品源码准入。
- 通过 GitHub App 的最小权限和 Check Runs 回调，将公共执行结果准确关联到私有仓库的原始 commit / PR，保留 required-check 门禁。
- 最大限度保留现有 Python 3.10、Linux/Windows、pytest 分片、Web、Display Session、Runtime 诊断和冻结包验证覆盖。
- 公开 Runner 联调、私有替身权限与发布 dry-run、凭据及数据隔离均通过后先交验收报告，等待用户确认。正式仓库切换后另行复验；不得让切换前门槛依赖切换后的结果。

## 非目标

- 不公开 emo_master 源码、测试代码、工程样例、诊断原始证据、构建配置或内部文档。
- 不将私有源码提交、镜像或长期存放在公共 CI 仓库的 Git 历史、分支或 Release 中。
- 不把私有源码上传为公共 Actions Artifact；公共 Runner 运行日志只允许输出经过审核的摘要。
- 不在公共 Runner 上执行来自未信任 fork / 外部贡献者的 PR 代码；这类 PR 不自动进入公共执行链路。
- 不把 Release 写入凭据、私有包或最终发布资产交给公共 CI 仓库。
- 不为了抽象而将产品专属测试重写为通用框架，不减少断言或覆盖。
- 不将 GitHub Actions 公共仓库免费误解为所有配套服务、Artifact 存储、网络、第三方服务或大型 Runner 均免费。

## 核心架构

公共仓库首阶段只运行独立合成项目，验证 Runner、工作流和测试结果处理，不接触 emo_master 源码或产品凭据。合成测试不能证明真实产品源码不会外传。

产品 CI 暂时保留在私有 emo_master。为节省额度，另行评估私有仓库专用临时自托管 Runner；GitHub 自托管 Runner 不消耗 Actions 计算分钟，但需要用户承担主机、虚拟化、网络隔离、维护和 Windows 桌面环境成本。公共 Runner 产品执行只有在独立证明 Runner 外部网络出口和进程/文件边界不可绕过，并经用户明确批准后才可试点。

## 完成标准

- 公共 CI 仓库已创建并公开，默认分支受到保护，外部 fork PR 不会触发任何持有私有仓库凭据的工作流。
- 私有 emo_master 可通过 GitHub App 安全触发公共 CI；dispatch 只传不含内部标识的请求 ID，不传 Token；产品 repo、完整 SHA、PR 和 Check Run ID 在私有侧绑定。
- 公共合成 CI 通过不代表产品源码已安全迁移。未经独立隔离审查及用户批准，公共 Runner 不得读取产品源码。
- 公共测试日志和产物不暴露私有源码、路径、工程数据、诊断原始记录、环境变量或凭据。
- 记录每项验证的实际执行边界、未迁移原因和 runner minutes。若标准公共 Runner 无法同时满足保密和覆盖，不能按方案 A 宣布完成；保留原 CI，另行确认替代路线。
- 公共合成自测不创建产品 required check；现有私有 CI 门禁在本阶段保持不变。未来若批准产品调度，必须准确绑定 SHA 和真实 workflow/job 结果。
- 现有测试覆盖、分片覆盖证明、Web/Runtime/Windows 验证和诊断语义保持；未运行的测试不能标记通过。
- Release 发布凭据和私有发布资产仍留在私有边界，实际发布前验证来源和产物身份。
- emo_master 转为 Private 后，重新验证 PR、push、手动触发、检查回调、Windows 构建/发布控制链路。
- 迁移报告包含费用节省计算、公共执行的安全边界、已验证项目和未验证项。
