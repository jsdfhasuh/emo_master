# 任务清单

## A. 计划与现状

- [x] A1. 核对 12 个活动工作流、4 个归档工作流、CI 脚本和外部打包调用链。
- [x] A2. 修复 OpenSpec delta / Requirement / Scenario 标题并通过 OpenSpec 2.1.1 strict validation（执行记录：dot，PR 评论）。
- [ ] A3. 记录迁移前 Actions usage、Runner OS/minutes、Artifact/Cache storage；无账单权限时明确标注待用户提供。
- [ ] A4. 盘点公开 Releases、Packages、Pages、Fork、Actions artifacts、外部镜像；明确 Private 不会撤回历史 clone/download。
- [ ] A5. 将本轮设计收敛为“公共仓库合成自测；产品源码不得进入公共 Runner，除非另获安全批准”。

## B. 公共合成 CI（当前唯一获准实施阶段）

- [ ] B1. 创建 `jsdfhasuh/emo_master_ci`，设置 Public、明确许可证、默认分支保护和可信维护者权限。
- [ ] B2. 不创建、不安装任何可访问 emo_master 的 GitHub App；不配置产品 Source Reader、Checks、Release Secrets。
- [ ] B3. 建立独立合成 Python fixture，确保 Git 历史、测试数据、依赖和日志均不来自 emo_master。
- [ ] B4. 在公共仓库实际运行 Linux/Windows 标准 Runner，验证调度、Python 环境、测试执行、退出码、失败处理和白名单摘要。
- [ ] B5. 验证公共 self-test 的 fork PR 无产品 Secrets；不触发 emo_master Check Run，不传产品 repo、SHA、PR 号或内部标识。
- [ ] B6. 记录实际公共 Runner minutes、Artifact/Cache 使用量和费用口径；明确这只是基础设施验证，不代表产品 CI 已迁移。

## C. 产品源码安全与成本替代路线评估（只做设计，不接真实源码）

- [ ] C1. 审查 GitHub-hosted Runner 默认公网访问和 Private Networking 的实际范围；不得将 Private Networking 误写为默认拒绝公网出口。
- [ ] C2. 明确合成项目不能证明真实产品源码不会被测试代码、依赖脚本或构建子进程外传。
- [ ] C3. 评估私有仓库一次性自托管 Runner：每 job disposable VM、Runner group 仅授权 emo_master、外部防火墙默认拒绝、依赖镜像、无长期凭据、任务后销毁。
- [ ] C4. 单独评估 Windows 原生 Qt / GUI / 冻结包验证能否在隔离 Windows VM 中保持现有桌面和测试语义。
- [ ] C5. 对比三条路线：私有托管 Runner、私有自托管临时 Runner、公共 Runner（仅在有独立可信出口隔离证明后）；分别估算 GitHub 额度和主机维护成本。
- [ ] C6. 输出安全威胁模型、网络/文件/进程/Runner 命令文件/日志/Artifact/缓存边界和可绕过点；不得用真实源码做安全探针。
- [ ] C7. 如果无法证明公共 Runner 产品源码隔离，明确判定公共产品执行不适用；不创建 Source Reader App，不迁移产品代码。

## D. 现有产品 CI 保持与后续候选

- [ ] D1. 当前 12 个活动工作流继续保留原触发、执行边界和门禁，不删除、不弱化断言。
- [ ] D2. 4 个历史归档工作流保持原样。
- [ ] D3. 不将合成 CI 结果挂到 emo_master 的产品 required check。
- [ ] D4. 对 `python_build_scripts` 的 `secrets: inherit`、私有源码读取和 Release token 另行做最小权限审查；本阶段不改变正式发布。
- [ ] D5. 如未来考虑产品执行迁移，先提交独立 OpenSpec / 威胁模型，等待用户明确批准后再实施；不得将本计划的合成自测通过视为产品准入。

## E. 闭源准备（独立门槛，不随合成自测自动通过）

- [ ] E1. 盘点公开历史和第三方副本风险。
- [ ] E2. 核对 `python_build_scripts` 在 emo_master Private 后的只读源码、Release 权限、构建和 dry-run 路径。
- [ ] E3. 确认 Private 状态下的 Actions policy、required checks、PR fork 处理和发布依赖。
- [ ] E4. 提交闭源风险报告，等待用户明确确认后才切换 emo_master 为 Private。
- [ ] E5. Private 切换后复验原有私有 CI、Windows 构建和 Release dry-run。
- [ ] E6. 对比迁移前后 GitHub Billing / Actions usage；若产品 CI 未迁移，必须如实说明产品计算额度并未因公共合成 CI 而节省。