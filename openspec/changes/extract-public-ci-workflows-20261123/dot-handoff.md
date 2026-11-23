# dot：一个公共 CI 仓库、一个 Token，完成全部迁移

继续 jsdfhasuh/emo_master PR #21，分支 plan/extract-public-ci-workflows-20261123，OpenSpec change extract-public-ci-workflows-20261123。读取最新远端、AGENTS.md 和 preflight.md，保留后续提交及用户工作区。

## 用户最新选择

- 只新增 Public emo_master_ci；产品 emo_master 和外部 python_build_scripts 沿用现有仓库。
- 用户只提供一枚足够权限 PAT，统一为 EMO_CI_TOKEN。由你配置到产品与公共 CI 两处 Actions Secret；不要求四枚 Token 或额外验证仓库。
- 全部 12 项真实产品 CI 迁到标准 GitHub 托管 Linux / Windows Runner；产品最终 Private，仅保留短 dispatcher，披露其少量私有用量。
- 不引入自托管 Runner、GitHub App、外部服务或新权限框架。

## 持续执行到完成

1. 核实最新 main / PR HEAD、已有工作区和实际 GitHub 登录权限。静态审查产品基线 82a1aa6，不能把旧结果当新版本证据。
2. 使用 OpenSpec 2.1.1，设置 OPENSPEC_TELEMETRY=0、OPENSPEC_NO_UPDATE_CHECK=1，运行本 change 的 strict 校验和 git diff --check。
3. 只创建／核实公共 emo_master_ci 并初始化 main。用户可在受控本机不回显录入一枚 Token，由你写入两处 EMO_CI_TOKEN。需要管理设置时沿用已有登录或该 Token 的已授权管理权限。
4. 自行生成 age 密钥、保存用户可取用的持久私有备份，配置公共变量与 ci-control / release 解密 Secret。普通任务和本轮 dry-run 不新增逐次人工批准。
5. 实现短 dispatcher、可信请求 Artifact、公共 preflight、固定产品 SHA 直接取源及明确原事件接口。公共 github.sha 不能当产品 SHA。
6. 实现原始输出捕获和 age Artifact，可信汇总／发布 job 不执行下载的产品代码。共享 PAT 在各 job 中仍有相同权限，不能宣称只读取源或发布权限隔离。
7. 按 workflow-migration.md 逐项完成全部 12 项真实路径；保留四分片集合、Windows 原生 Qt、手动输入、断言和诊断结论。
8. 改造 python_build_scripts 的受控入口：明确产品身份，同目标 build+verify 后加密，独立 publisher；显式传入 EMO_CI_TOKEN，移除无边界 secrets:inherit 和原始公开产物，固定真实新 SHA，保留其他产品兼容。
9. 更新受影响的 scope／release workflow contract，说明单 Token 权限模型改变；其他正确断言和覆盖不降低。实现正确 check SHA、独立 status reporter 和旧结果拒绝，取消未回报保持阻断并可原生重跑。
10. 产品仍 Public 时完成真实测试、诊断、构建和包验证对照、PR 状态、联合 build-only dry-run及回滚准备，不把这些结果称为私有权限验证。
11. 按已有授权将 emo_master 转 Private，立即实际验证私有取源／请求／状态，并在产品自身的 ci-migration-validation-* draft Release 上传／读回验证包。通过后切换门禁、停用旧重任务；失败按回滚方案处理，保持 Private。
12. 交付 12/12 矩阵、准确 SHA / run / attempt、密文证据读取方式、实际费用、未验证项和回滚步骤。以实际执行者 dot 在相关推送 PR 评论，不冒用 ChatGPT Web。

## 明确边界

用户只需准备一个新增仓库和一枚 Token；其他正常配置由你实施。仅缺少实际账号操作／账户功能时集中报告缺口，同时完成其他可做工作，不重复索取已获授权。

PAT 换名称、job 分离、persist-credentials:false 或 workflow permissions 均不缩减 PAT 权限。管理 PAT 可能被恶意代码用于修改工作流和接触其他 Secrets，因此加密私钥只按正常路径注入控制任务的做法不构成绝对隔离。保持可信维护者和受审代码的前提，不要求无限证明或恢复多 Token 架构。

OpenSpec 通过、首次取源和合成自测不是完整迁移完成；inconclusive、skipped 和未执行如实保留。正式生产发布、付费变更、合并和可见性操作以用户已授范围为准，不从本文自行推导新增授权。
