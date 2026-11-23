# dot 执行提示词：仅 GitHub 托管 Runner 的公共合成 CI

继续处理 `jsdfhasuh/emo_master` 的 PR #21，计划分支 `plan/extract-public-ci-workflows-20261123`，OpenSpec change 为 `extract-public-ci-workflows-20261123`。按最新远端内容执行，先核对 HEAD 与工作区差异；不要回退后续提交或重复已完成的工作。

## 用户已确定的范围

用户明确“不考虑私有 runner”。本轮不评估、不部署自托管、自建、本地、租用服务器或一次性 VM Runner；只用标准 GitHub 托管 Runner。产品仓库将来转为 Private 仍可用 GitHub 托管 Runner，这不是自托管路线。

公共 `emo_master_ci` 只运行从零建立的独立合成项目。不得读取、复制、下载或运行 emo_master 产品源码、产品测试、工程数据、配置、历史诊断或构建包；产品仓库目前公开也不构成例外。不得创建产品 GitHub App、Source Reader、跨仓库产品 Token、产品 Secrets、dispatcher 或产品 Check Run 回调。

## 执行内容

1. 阅读最新 AGENTS.md 与本 change 的 proposal / design / specs / tasks。本轮起点曾有严格校验错误，不能沿用 dot 旧提交的通过记录。使用固定 OpenSpec 2.1.1、关闭遥测和更新检查，运行：
   `openspec validate extract-public-ci-workflows-20261123 --strict --no-interactive`
   并运行 `git diff --check`。设置 `OPENSPEC_TELEMETRY=0`、`OPENSPEC_NO_UPDATE_CHECK=1`；按当前 shell 的方式设置，不修改全局用户配置。报告真实提交及是否包含工作区修改。

2. 核实 `jsdfhasuh/emo_master_ci` 是否已存在及权限。在已有授权与权限内创建或使用独立公共仓库；已有内容先读，不覆盖历史。配置维护权限、默认分支保护和 Actions 最小权限，管理权限受阻则列明具体设置和已完成部分，不能虚报成功。

3. 从零写一个小型 Python 3.10 合成项目，可使用公开依赖。建立公共仓库自身的 push / pull_request / workflow_dispatch 自测，使用标准 ubuntu-latest、windows-latest。第三方 Action 固定核实过的完整 SHA；默认 permissions 为 contents: read，checkout 不保留写凭据。不使用 pull_request_target 执行外部 PR 代码。

4. 两个平台各实际验证正常成功与手动可控失败。失败开关仅操纵虚构 fixture，测试必须非零退出，相关 job / workflow 必须真实失败，不能用 continue-on-error、忽略退出码或汇总覆盖掩盖。若用矩阵，避免一平台失败提前取消另一平台的验收。默认正常运行保持可通过。

5. 允许公开的合成日志与普通断言堆栈，确保没有产品内容或真实凭据。没有需要就不引入 Artifact、cache、回调或额外凭据；不用为未实现能力增加故障演练。

6. 保留 emo_master 12 个活动工作流、4 个归档工作流、原平台、测试集合、断言、报告与检查门禁。本轮不修改产品 CI、不重跑无关诊断，不新增 Qt 桌面或冻结包验收。不合并 PR、不发布产品、不修改仓库可见性。

## 交付

- 公共仓库和 workflow 链接、完整提交 SHA、Linux / Windows 成功与预期失败的 run URL 和真实 job 结论。
- 最新 OpenSpec strict / diff check 的命令、版本、退出状态及工作区范围。
- 实际 job 时长、使用的 Artifact / cache、仓库保护与权限状态；无账单访问时标明，不能虚构费用。
- 明确结论：本轮只验证公共合成基础设施；产品迁移数为 0，未产生已验证的产品额度节省，也未证明产品源码公共执行安全。
- 后续若有建议，仅限 GitHub 托管执行方式，先审查已有 scope / concurrency / cache，避免重复已有优化；本轮不实施产品变化，不再提出私有 Runner。
- 保持 PR #21 为 Draft。推送后在对应 PR / 提交下以实际执行者 `dot` 发表评论，说明改动、验证及未验证项，不冒用 ChatGPT Web。

完成上述相关验收后交付，不扩展为所有通道绝对保密证明、全部产品测试迁移或自托管平台建设。
