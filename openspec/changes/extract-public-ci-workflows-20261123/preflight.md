# 用户在线时的准备与 dot 连续执行交接

本文件补齐完整迁移计划的账号／权限准备，不增加 CI 平台或自托管 Runner。审查基线为计划 `082ef878`、产品 main `82a1aa6`；实际开工仍读取最新远端。当前公共 CI 仓库通过本会话连接查询为 404，表示尚未确认存在／可访问；不能据此断言用户其他连接下也不存在。

## 1. 首先让 dot 做什么

先让 dot 在它实际执行任务的电脑检查 GitHub 登录并准备仓库。用户只处理需要本人登录／2FA 和生成 PAT 的步骤，不需要先写 YAML、安装 Python 依赖或运行产品全套测试。

1. 读取最新 PR #21、AGENTS.md 和本 change；确认本机有可持续使用的 GitHub 连接或 CLI 登录。CLI 可用 `gh auth status`，不使用 `--show-token` 或输出凭据文件。
2. 核实对 `jsdfhasuh/emo_master`、`jsdfhasuh/python_build_scripts` 的代码、workflow、分支／PR 和必要设置权限。该本机登录与四个 CI Token 不同，不能上传到 Runner。需要用户浏览器授权时，在用户在线期间完成；已可用的登录不重新创建。
3. 核实／按本次授权创建以下两个仓库；同名已存在时先核对归属与内容，不覆盖：
   - `jsdfhasuh/emo_master_ci`：Public，默认分支 main，可信工作流与控制工具。
   - `jsdfhasuh/emo_master_ci_probe`：Private，默认分支 main，仅合成源码、请求和 draft 验证产物；不镜像产品 Git 历史。
4. 初始化两者的真实默认分支与最小 README。公共 workflow_dispatch 入口需要进入默认分支才能被发现；具体可信联调 workflow 由 dot 编写，不要求用户手工操作。
5. 公共 CI 创建 `source-read`、`ci-control`、`release` 三个 Environment，先只允许受信 main。普通取源／回写和本轮 dry-run 不新增 Required reviewers 或 wait timer；已有审批规则先按实际授权处理。新仓库如果未在 dot 的连接授权范围内，由用户一并加入。
6. 核实 GitHub 账户是否支持产品转 Private 后的强制分支保护。Free 不支持该最终门禁时如实列出；不得自动付费升级，也不阻断其余代码与公共实跑。ChatGPT 订阅不是 GitHub 账户方案。

准备完成后 dot 给出：当前登录身份、四仓库可访问情况、两个新仓库的可见性与默认分支、三个 Environment、待填 Secret 的确切位置。不要只报告“已授权”而不核实 workflow 写入与配置权限。

## 2. 用户一次生成四个运行时 Token

入口：[Fine-grained personal access tokens](https://github.com/settings/personal-access-tokens/new)。Resource owner 选 `jsdfhasuh`，Repository access 选 **Only select repositories**，逐项按下表选实际存在的仓库。不能选 Public repositories (read-only) 代替，否则不能满足写操作和产品转 Private 后的访问。

建议本轮设置 90 天有效期并记录具体到期日；账户策略较短时遵循该限制。Metadata 的必需只读权限保持默认，其余不额外勾选。

| Secret 名称 | 选择的仓库 | Repository permissions | 写入位置 |
|---|---|---|---|
| PUBLIC_CI_DISPATCH_TOKEN | emo_master_ci | Actions: Read and write | emo_master 与 emo_master_ci_probe 各自的 Actions repository Secret |
| PRODUCT_READ_TOKEN | emo_master、emo_master_ci_probe | Contents、Actions、Pull requests：均 Read-only | emo_master_ci 的 source-read、ci-control、release：三个 Environment 各保存同值同名 Secret |
| PRODUCT_STATUS_TOKEN | emo_master、emo_master_ci_probe | Commit statuses: Read and write | emo_master_ci → ci-control |
| PRODUCT_RELEASE_TOKEN | emo_master、emo_master_ci_probe | Contents: Read and write | emo_master_ci → release |

两仓库授权用于本轮私有取源／状态／draft 验证，不授权其他产品或所有仓库。生产路径仍绑定 emo_master 的固定 repository ID；probe 是单独固定的验证目标。

Token 值只在 GitHub 设置页或用户本机不回显的输入提示中填写，不贴进聊天。用户可在创建后直接填齐所有位置；也可由 dot 先准备本机不回显、通过标准输入调用 `gh secret set` 的录入脚本，一次输入后写齐对应位置。录入脚本不把值放入命令参数、历史或持久文件，结束清理进程内临时变量。

**GitHub Secret 不能读回原值。** READ 需要三份、dispatch 需要两份，必须在仍持有该值时写齐，不能要求 dot 事后从一处 Secret 读出再复制。仅列出 Secret 名称也不能证明权限正确，后续做实际联调。

## 3. dot 自己完成的配置

- 生成 age 公私钥，公钥放公共 CI 的 `CI_EVIDENCE_RECIPIENT`，私钥放 ci-control / release 的 `CI_EVIDENCE_IDENTITY`。在用户可取用的持久私有目录保留备份，记录路径与公钥指纹；不放 Git、聊天或 Runner 的临时目录作为唯一副本。
- 保留三个环境的最小权限，固定真实 Action／公共 workflow／packager SHA；根据本机已有 GitHub 授权配置，无需用户逐项点设置。
- 固定产品与 probe 的 repository ID，分别实现生产和合成联调入口。probe 模式不接受任意源仓库或执行任意上传代码。
- 新建 private draft 使用 probe 自身真实提交作为 target_commitish；产品 execution SHA 独立记录。保留最小发布权限，遇到具体 API 权限条件先核实目标，不自动追加权限。

## 4. 用户离线前优先完成的小型连通

按同一明确版本跑一条最小链路，避免几小时实现后才发现需要用户重新授权：

1. Private probe 的短 dispatcher 用 dispatch Token 启动公共 workflow。
2. 公共 Linux 与 Windows job 用 READ Token 拉取该 Private probe 的固定提交，运行简单的合成成功／失败用例。
3. 正常报告经 age 加密上传，独立可信任务恢复内容，并在 probe 准确提交上写入成功／失败状态。
4. 干净 publisher 向 probe Private draft Release 上传并读回一份小型测试附件，校验 hash。此处只证明连接与权限，不能替代后续 Designer / Runtime 真实发布链路验收。

dot 交付这一轮 run URL、提交、实际结果和权限缺口。若用户准备休息时尚未完成，不要求用户陪跑：dot 继续其有权限完成的工作，仅把确需本人授权的缺口集中记录。用户在线时已核实凭据能减少中途停顿，但不保证一夜必定完成全部工程量。

## 5. 用户给 dot 的连续执行授权范本

以下文字用于用户实际发送给 dot，文档中的范本本身不是额外授权记录；以用户本次发送的范围为准。

> 按 emo_master PR #21 的最新完整计划执行，先读 preflight.md。你先核实本机 GitHub 登录，创建／核实 Public emo_master_ci 与 Private emo_master_ci_probe，建立默认分支和三个 Environment，把必须我亲自完成的登录与四个 PAT 配置一次列清；其余准备由你完成。Token 配好后，优先做小型私有取源、公共 Linux／Windows、状态回写、加密报告和 Private draft 附件连通，随后持续完成 12 项真实产品 CI，不停在合成验证。
>
> 授权你修改、验证并推送 emo_master、emo_master_ci、python_build_scripts 的本任务相关代码；在计划验收通过、无未解决必修问题且不混入无关改动后，合并本任务相关 PR、切换门禁、停用旧重任务，并把 emo_master 转为 Private，切换后继续复验。保持全部计算使用标准 GitHub 托管 Runner；产品仅保留短 dispatcher。允许在固定 Private probe 做 draft Release 验证。
>
> 不自动升级付费方案，不额外发布新的正式产品版本或改动无关项目；不得降低现有测试断言、删除失败证据或将未执行记为通过。已有授权不重复询问；真实缺少我的账号操作时，完成其他可做工作并集中报告缺口。推送按实际执行者 dot 评论，保留版本、12 项证据矩阵、密文读取方式、实际费用组成和回滚步骤。

## 6. 本次审核状态

- 已审核：Token 直连架构、12 项范围、私有 probe 权限、Secrets 写入位置、无人值守环境、开发登录与运行时权限区分。
- 本次只提交计划补充：未替用户创建仓库、生成 PAT、配置 Secrets、部署工作流、合并或切换 Private。
- 计划 strict 校验结果写入本次推送的 ChatGPT Web 评论；真实小型连通及 12 项迁移仍由 dot 记录。

## 官方依据

- [Fine-grained PAT 与仓库范围](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [GitHub CLI 登录](https://cli.github.com/manual/gh_auth_login)、[Secret 安全录入](https://cli.github.com/manual/gh_secret_set)
- [Secret API 仅返回元信息](https://docs.github.com/en/rest/actions/secrets#get-a-repository-secret)
- [Environment 规则](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [Private 分支保护方案条件](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Release API 与目标提交](https://docs.github.com/en/rest/releases/releases#create-a-release)
