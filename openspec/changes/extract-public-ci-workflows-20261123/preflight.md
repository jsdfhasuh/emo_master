# 睡前准备：一个新增仓库、一个 Token

用户已明确选择最少准备：只新增 Public emo_master_ci，只提供一枚有足够权限的 Token。本版取代之前的多 Token／额外验证仓库清单；dot 不得继续要求用户建立这些附加项。

## 1. 用户先准备两件事

1. 创建 jsdfhasuh/emo_master_ci，选择 Public，并用 README 初始化 main；也可让已登录的 dot 创建。产品 emo_master 与工具 python_build_scripts 沿用已有仓库。
2. 创建或提供一枚足够权限的 PAT。统一名称 EMO_CI_TOKEN。优先 fine-grained PAT，限定本任务仓库；用户已提供更宽权限 Token 时核实实际范围并如实记录，不再要求拆成四枚。

入口：[创建仓库](https://github.com/new) · [创建 fine-grained PAT](https://github.com/settings/personal-access-tokens/new)。

先有 CI 仓库，才能在 Only select repositories 中选它。**本次已核实 `jsdfhasuh/emo_master_ci` 已存在且为 Public、默认分支 main**。如果你创建 fine-grained PAT 时该仓库尚不存在，必须回到 Token 的 Repository access 检查并将 `emo_master_ci` 加入授权范围；仅把 Token 保存为该仓库 Secret，不会自动授予它访问该仓库的权限。若现有 Token 无法编辑授权范围，则重新生成一枚并替换两处 Secret。若这枚 Token 同时给 dot 实施和 CI 运行使用，仓库范围选择 emo_master、emo_master_ci；只有确实要由该 Token 推送 `python_build_scripts` 时才加入第三个仓库。建议有效期 90 天，受账户策略限制时遵循策略。

## 2. 一枚 Token 的权限清单

| 用途 | Repository permissions |
|---|---|
| 取源与 Release；触发和读 Actions；回写状态 | Contents、Actions、Commit statuses：Read and write |
| 读取产品 PR；dot 创建／更新本任务 PR | Pull requests：Read and write |
| dot 修改 workflow、写 Secrets、配置 Environment／Variables 与仓库／门禁／可见性 | Workflows、Secrets、Environments、Variables、Administration：Read and write |

Metadata 保持必需只读。`Variables: write` 用于设置 `CI_EVIDENCE_RECIPIENT` 等非敏感仓库变量；`Environments: write` 用于创建环境及配置环境变量／Secret。dot 已有足够管理登录时可沿用，不必为同一工作额外生成管理 Token。Token 不能超越账号自身权限，也不能解锁付费账户功能。Private 强制分支保护仍核对 GitHub 账户条件；不能自动付费升级。

## 3. 用户只录入一次，配置由 dot 完成

优先在 dot 的受控本机不回显输入一次；dot 用 API 或适当 CLI 凭据机制将同一值写入以下两处 Actions repository Secret：

- emo_master → EMO_CI_TOKEN
- emo_master_ci → EMO_CI_TOKEN

也可以用户在 GitHub 页面把同一 Token 粘贴到这两处。它们是同一枚 Token 的两份配置，不是两枚凭据。Secret 无法从 API 读回原值，因此只在公共仓库存一处后，不能要求 dot 读取复制。

Token 不贴到聊天、不写 Git、命令参数、运行日志或报告。dot 自行建立 ci-control / release 环境、生成并持久备份 age 密钥、配置变量和解密 Secret。source-read 环境不是必需组件；普通任务和本轮 dry-run 不新增逐次人工批准，已有规则按实际授权处理。

## 4. dot 自动继续的顺序

- Public 阶段直接在产品与公共 CI 两个仓库接入，完成最小连通后继续全部真实测试、诊断、构建和包验证。必要合成文件在工作目录生成，不另建仓库。
- 保留原 CI 和回滚资料；当前产品 Public 时的取源成功只证明当前访问，不能写成 Private 访问已验证。
- 按已有授权将 emo_master 转 Private；随后实际复验取源、请求、状态，以及 emo_master 自身的 draft Release 产品包上传／读取。
- 验证 tag 使用 ci-migration-validation-*，避免新建 v* 标签误触发正式版本发布。Private 验证通过后完成门禁及原重任务切换；失败按预备回滚方案处理，保持仓库 Private。
- 交付 12 项实际证据、费用和回滚说明。首次连通不等于迁移完成，不保证一夜必然完成全部工程量。

## 5. 单 Token 模式的明确取舍

同一 PAT 在取源、回写、发布中具有相同权限。多个 job、多个 Secret 名称和 workflow permissions 都不会让该 PAT 变成只读。若还包含管理权限，恶意代码可能改工作流或设置并接触其他 Secrets；不能宣称发布权限或解密私钥在这种情况下有不可绕过的隔离。

正常实现仍只在必要步骤引用 Token，输出加密，汇总／发布在干净任务运行。该方案以可信维护者和受审代码／依赖为前提，减少人工配置；不另建外部权限平台或强制补回多 Token。

## 6. 给 dot 的简短补充

以下内容以用户实际发送的范围作为授权，不因文档存在自动扩大操作权限。

> 按 PR #21 的最新简化方案执行：只新增 Public emo_master_ci，我只提供一枚足够权限的 Token，统一 EMO_CI_TOKEN。不要再要求额外验证仓库或四枚 PAT。由你在受控本机完成配置，继续全部 12 项真实产品 CI；产品转 Private 后直接在 emo_master 验证私有取源和 draft Release。共享 Token 的权限如实说明，其他既定覆盖、日志加密、状态准确性和回滚要求保留。已有授权不重复询问，推送后以 dot 身份评论实际改动和验证。

## 官方依据

- [PAT 类型、仓库范围与权限](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [Runner、Secrets 与跨仓库访问的安全边界](https://docs.github.com/en/actions/concepts/security/compromised-runners)
- [Secret 设置](https://cli.github.com/manual/gh_secret_set)
- [Private 分支保护条件](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
