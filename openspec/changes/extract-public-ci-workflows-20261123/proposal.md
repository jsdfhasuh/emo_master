# 全部产品 CI 迁移到公共 GitHub 托管 Runner（Token 直连）

## 为什么修改

用户要求的是完整迁移方案：产品源码仓库最终转为 Private，全部真实产品测试、诊断、构建与发布计算转到公共仓库，最大限度减少私有 Actions 用量，并明确排除私有／自托管 Runner。

上一版 `39ac1bfb` 将合成测试误设为交付终点，没有完成用户目标。本版纠正范围：合成项目只是一项前置验证，最终必须完成 12/12 活动产品工作流的真实迁移。用户进一步明确：只新增一个公共 emo_master_ci，只提供一个足够权限的 Token。故不创建额外验证仓库，不要求四个 PAT，也不引入 GitHub App、Workers、数据库或外部对象存储。

## 确定采用的方案

1. `emo_master_ci` 为 Public，保存可信 CI 工作流和通用控制工具。使用标准 GitHub 托管 Linux / Windows Runner，通过用户提供的一枚 PAT 拉取 `emo_master` 的指定提交，执行真实产品任务。
2. `emo_master` 最终为 Private，保留源码、测试和产品配置；迁移完成后只保留一个轻量事件触发工作流，不在其中安装产品依赖、运行测试或打包。
3. 全链路统一使用一个 EMO_CI_TOKEN；产品 dispatcher 与公共 CI 各保存同一值，用户可以只录入一次由 dot 配置。任务仍按取源、测试、回写、发布分工，但不宣称同一 PAT 具有权限隔离；解密私钥仅按正常配置注入可信控制任务。
4. 公共日志输出必要状态；原始日志、JUnit、诊断、截图和包使用 age 公钥加密，仅上传密文 Artifact。独立可信汇总／发布 job 解密，汇总真实结果或上传到产品 Private Release。
5. 使用 GitHub Commit Status API 回写产品提交，保持可阻断的 PR 门禁；本版不需要自建 GitHub App，也不冒充 Check Runs 的 App 身份绑定。

公共仓库 Secrets 不会随 Git 仓库内容一起公开。安全建立在可信维护者、受保护工作流、受审代码与依赖、限定仓库范围和输出处理之上；不承诺能够阻止同一 Runner 内任意恶意程序外传其已经读取的源码。

## “全部迁移”的准确范围

全部 12 项的实际产品计算都迁入公共仓库，包括主源码 CI、Web、Display Session、六项 Runtime 诊断、Runtime 包、Designer 冻结包验证及 Designer / Runtime 正式发布链路。4 个历史归档工作流保持归档。

产品仓库保留的短任务只收集事件、写 pending 状态和调用公共 workflow；它仍消耗少量私有托管计算及请求描述 Artifact 存储。因此本方案目标是“全部产品 CI 计算公共执行，私有侧只留控制开销”，不声称私有 Actions 总用量为零。私有仓库调用公共 reusable workflow 仍归私有调用方计费，不能用该形式承载产品计算。

## 实施成果

- 产品与公共 CI 的完整事件、SHA、输入和结果关联协议。
- 12 项迁移矩阵及对应真实运行证据，保留原平台、触发条件、断言、诊断语义和测试集合完整性。
- 外部 `python_build_scripts` 的受控输出与 build / verify / publish 职责拆分版本，并固定新 commit SHA。
- 原始证据加密上传、可信解密汇总；产品实际转 Private 后，在该产品仓库完成 draft Release 验证。
- 新旧检查切换、停止双跑、Private 切换前后验证、费用与回滚记录。
- 可持续执行到最终迁移完成的 dot 提示词，不以合成通过结束交付。

## 不扩大范围

不部署自托管 Runner，不建立外部控制平台，不重写产品测试框架，不降低断言，不把所有手动诊断改成每次 PR 必跑。没有现成签名需求就不增加签名服务，没有长期在线证据归档需求就不另建存储平台。

## 最终验收

1. 最新 OpenSpec 严格校验通过；计划通过不代替实施验收。
2. 12/12 产品工作流均有公共运行 URL、真实执行 SHA、平台、结果和对应证据；诊断的 inconclusive 如实保留。
3. 主 CI 四分片、原 Web 路径、Windows 原生 Qt、冻结包验证和联合发布 dry-run 全部保留原语义。
4. 产品状态准确回写；失败、取消、缺证据和旧提交结果不能放行新提交。
5. 产品原始材料未以明文进入公共 Git、日志、cache 或 Artifact；密文可被他人下载的事实与私钥保管要求已明确。
6. 全部产品计算从产品仓库停用；仅轻量 dispatcher 留在产品仓库。Private 切换及正式发布按照已有授权边界执行并分别记录。
7. 报告实际公共计算、私有控制开销、Artifact 存储与账户方案费用；不虚构节省比例。

详细执行方案见 [design.md](design.md)、[workflow-migration.md](workflow-migration.md)、[acceptance.md](acceptance.md) 和 [dot-handoff.md](dot-handoff.md)。
