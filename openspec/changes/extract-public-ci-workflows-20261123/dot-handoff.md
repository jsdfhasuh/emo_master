# dot：完整迁移 emo_master 产品 CI（公共 Secrets Token 直连）

继续 `jsdfhasuh/emo_master` PR #21，分支 `plan/extract-public-ci-workflows-20261123`，OpenSpec change `extract-public-ci-workflows-20261123`。读取最新远端与 AGENTS.md，不回退后续提交／用户工作区。

## 目标必须执行完整

用户要求产品源码最终 Private，全部 12 项真实产品测试、诊断、构建、包验证和发布计算迁到公共 `emo_master_ci` 标准 GitHub 托管 Linux / Windows Runner。公共仓库 Secrets 存只读 Token，直接拉取产品提交。

不考虑私有／自托管 Runner，不增加 GitHub App、Workers、数据库、外部对象存储。合成测试只是前置步骤，通过后继续真实产品 12 项、状态回传、私有发布 dry-run 和切换，不在 synthetic-only 阶段结束交付。

产品仓库允许留一个短 dispatcher；只取事件、写 pending、调公共 workflow，不运行产品测试或构建，不等待重任务。它仍消耗少量私有额度，须如实报告，不能宣称私有 Actions 总用量为零。

## 按最新计划持续执行

用户在线时先执行 [preflight.md](preflight.md)：核实你实际执行环境的管理登录，建立公共 CI 与固定 Private probe、初始化默认分支及 Environment，给出准确 Token 填写位置；用户填好后尽早完成小型权限连通。不要把本会话 ChatGPT Web 能访问 GitHub 当作 dot 已获同样访问权限。用户离线后按其本次已授权范围持续实施，不重复请求已获授权动作。

1. 阅读 proposal / design / workflow-migration / specs / tasks / acceptance。产品静态审查基线为 `82a1aa6d8499ee203ea8e452c2f016db37ea7b3f`，开工核对最新差异，记录实际 HEAD 和未提交修改范围。
2. 使用 OpenSpec 2.1.1；设置 `OPENSPEC_TELEMETRY=0`、`OPENSPEC_NO_UPDATE_CHECK=1` 后执行 `openspec validate extract-public-ci-workflows-20261123 --strict --no-interactive` 和 `git diff --check`。历史通过不代替本次内容。
3. 核实／创建公共 CI 仓库及 Private emo_master_ci_probe；先有仓库再配置 PAT，read / status / release 限定产品与 probe，dispatch 仅限定公共 CI。建立可信分支／Environment，日常测试和本轮 dry-run 不新增逐次人工批准；一次写齐所有 Secret 位置，不尝试读回 Secret。生成并备份 age 公私钥。不得把秘密放入代码、聊天、URL 或 Artifact。
4. 实现私有轻量 dispatcher、可信请求 Artifact、公共 preflight、固定 SHA 直接 checkout，以及显式源事件接口。不能将公共 github.sha／workflow_dispatch 当产品 SHA／原事件。
5. 实现原始输出捕获与 age 密文 Artifact。产品 job 只持公钥；状态回写和解密由独立干净控制 job 执行，不执行下载报告中的代码。管理员可本地解密完整报告。
6. 按迁移矩阵逐项落实所有 12 项真实产品路径，保留原触发／输入／平台／断言／诊断结论。主 CI 保留四分片与完整集合证明；Windows 原生 Qt 桌面要求是既有契约，不能以合成运行代替。
7. 修改外部 python_build_scripts 的受控新入口：显式产品上下文、同目标 build+verify 后加密、独立 publisher，移除明文 Artifact 和构建发布 Token／secrets:inherit，固定实际新 SHA，不影响其他产品原入口。
8. 更新 product scope／release workflow contract 等相关测试，保留语义，不删除测试或弱化断言。构建+运行包验证放同 job，避免将共享解密私钥交给执行产品代码的 job。
9. 用独立 Commit Status reporter 回写产品正确 check SHA，使用新 context 前缀；核实 GitHub API job 真实结果与证据。全局取消无回调保留 pending 阻断，使用原生重跑，不建设额外对账平台。
10. 按 acceptance 完成同版本 12 项实跑对照、PR 新 head / base／失败／取消重跑、私有替身 draft Release。新门禁确认可用后停止原重任务双跑，保留回滚快照。
11. 完成公开历史盘点、Private 权限与账户门禁核实；在已有授权边界内执行可见性与生产动作，切换后独立复验。没有具体授权的付费／可见性／生产发布动作，先准备全部可审查内容再列出该动作；不要每个阶段重复请求许可。
12. 交付完整证据矩阵、密文报告读取方法、实际费用组成、未验证项与回滚方式。产品仓库最终只留短控制任务，不把 0 项迁移写成阶段最终成果。

## 必须如实说明

- 公共仓库 Secrets 不随仓库公开自动披露；但可信工作流维护者与实际执行代码构成安全边界。
- Token、persist-credentials:false、日志捕获和加密 Artifact 不能保证任意恶意代码无法外传已经读取的源码。记录该残余风险，不再设无限的绝对证明门槛。
- 公共 Artifact 存的是可公开下载的密文，不能称为私有存储；私钥只在可信控制 job 和用户备份中。
- 12 项诊断／测试结果分别报告，inconclusive 和 skipped 不冒称通过。
- 取源成功／合成成功／OpenSpec 通过都不等于完整迁移成功。
- 推送后按实际执行者 `dot` 在对应 PR 评论，说明修改、真实验证与未验证项；不要冒用 ChatGPT Web。

权限或账户条件受阻时，完成所有仍可完成的代码、配置模板和验证，再准确列出缺口。最终目标始终是完整真实产品 CI 迁移。
