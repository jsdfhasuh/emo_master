# 设计说明：公共合成 CI 与 GitHub 托管执行边界

## 1. 审查基线

- 仓库：`jsdfhasuh/emo_master`；本轮仓库接口仍返回 Public。
- 产品 main 基线：`b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`。
- 计划审查起点：`5f480ded9e4fd162f7e347764581d85d5bee732d`，分支 `plan/extract-public-ci-workflows-20261123`，PR #21。
- 活动工作流 12 个，历史归档工作流 4 个。此前接口观察 main 的 `protected=false` 属于历史证据，不能代替后续设置核实。
- CI 关键脚本：`scripts/ci_scope.py`、`scripts/ci_check.py`、`scripts/ci_pytest_reports.py`、`scripts/ci_pytest_shard.py`、`scripts/ci_shard_summary.py`。
- 外部打包仓库：`jsdfhasuh/python_build_scripts`；产品调用固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`。
- 用户最新约束：只考虑 GitHub 托管 Runner；不评估、不部署自托管／私有 Runner。

## 2. 费用与术语

| 事项 | 本计划中的含义 |
|---|---|
| 仓库 Public / Private | 仓库可见性；本轮不改变产品仓库可见性 |
| GitHub 托管 Runner | GitHub 提供和维护的执行环境；本轮唯一允许的 Runner 类型 |
| 私有／自托管 Runner | 用户自行维护或租用的执行机器；本轮完全排除，含评估 |
| 公共仓库标准 Runner | 计算使用免费；只适用标准机型，不扩展为一切服务免费 |
| 私有仓库标准托管 Runner | 消耗账户包含的计算用量，超出部分按账户规则计费 |
| 公共 Reusable Workflow | 仍在调用方上下文执行并计费；YAML 所在仓库不改变这一点 |

首期采用标准 `ubuntu-latest`、`windows-latest` 和 Python 3.10，不选 Larger Runner。实际 image / OS 版本随 run 记录。

当前 `emo_master` 为 Public，不能把它的标准托管运行量当成已经产生的私有付费分钟。公共合成项目不替代产品任务，因此本轮产品迁移数为 0，不能归因出产品额度节省。将来 Private 的预测与当前实测分列。

记录 job 实际时长与 OS；将耗时、账户计费计算用量、Artifact 和 cache 存储区分。没有账单权限时报告缺口，不把墙钟时间直接写成计费分钟或节省金额。费用依据见 §10。

## 3. 仓库职责和数据边界

### 3.1 产品仓库 emo_master

产品源码、测试、诊断、构建配置和现有工作流继续留在原仓库，使用既有 GitHub 托管执行方式。本轮不改变触发、测试、门禁或发布链路，也不增加公共 dispatcher、Source Reader、Check Reporter 或产品凭据。

将来仓库转为 Private 与更换 Runner 类型无关；继续使用 GitHub 托管 Runner 不需要用户部署机器。本轮不实施可见性切换。

### 3.2 公共合成仓库 emo_master_ci

- 先核实仓库是否存在及当前权限；已有仓库先审查内容，不覆盖历史或无关工作。
- 从零编写小型独立 Python fixture，使用虚构输入；允许公开依赖。
- 不复制 emo_master 的源码、产品测试、工程样例、配置、诊断、构建文件、文档或 Git 历史；当前产品仍公开不构成例外。
- 仅运行公共仓库自身的自测；输入只包含合成测试参数，不接收产品提交、路径、工程或凭据。
- 不创建或更新 emo_master 产品 Check Run，不触发产品构建或发布。
- 对外说明这是合成基础设施验证；公开代码的许可遵循已确定的仓库政策，不把产品材料一并公开。

### 3.3 安全结论的范围

GitHub 托管 Runner 默认可访问公网。能读取源码的测试进程、依赖安装脚本或构建子进程，可能通过网络或其他输出通道带出数据。撤销下载 Token 不能收回已经落盘的源码；重定向 stdout 仅改变部分输出位置。[3][4]

这一风险并非公共仓库独有。公共执行还引入公开日志／产物和跨仓库授权面；Private 本身也不保证恶意代码无法外传。本轮采用用户已确认的边界：公共端没有产品源码、数据和访问权限，安全审查据此检查输入与权限，不要求合成测试证明真实产品保密。

不将 Private Networking 等同于免费标准 Runner 已具备外部强制的默认拒绝出口策略；本轮不建设网络隔离平台。未来若重新考虑公共执行产品任务，需另立变更评估具体威胁模型、费用和剩余风险，并取得相应授权；不把未来方案写成本轮待实现任务或完成条件。

## 4. 最小公共自测设计

- 触发：公共仓库自己的 `push`、`pull_request` 和 `workflow_dispatch`；执行上下文必须属于公共仓库。
- 平台：标准 Linux / Windows 托管 job，Python 3.10；同一独立合成项目在两平台执行。
- 成功路径：正常合成输入，断言通过，测试命令退出 0，job 成功。
- 失败路径：仅手动合成验证用的明确失败开关，造成一个可控断言失败；测试命令非零退出、相应 job / workflow 真实失败。失败 run 是预期的负向证据，正常默认运行不应持续红灯。
- 摘要：平台、合成测试标识、真实结果与时长；汇总不能覆盖失败结论，不将取消、超时、未执行记为成功。
- 无需为本轮新增 Qt 桌面、冻结包、真实设备或全部历史诊断验收；这些属于产品既有覆盖。
- 未使用的 artifact、cache 或回调不为满足清单而专门引入；无需对未实现能力增加故障演练。

可控失败在 Linux / Windows 各验证一次即可；如果 workflow 使用矩阵，应避免一个失败提前取消另一平台而丢失验收证据。验收完成后保留默认正常运行与手动失败验证能力，不需要长期重复制造失败。

## 5. 权限、日志和仓库设置

- 公共 workflow 默认 `permissions: contents: read`；未使用的写权限与 `id-token` 不授予。正常使用公共仓库作用域的 `GITHUB_TOKEN`，不能把“无产品凭据”写成“完全没有 Token”。
- checkout 仅当前公共仓库；不保留 Git 写入凭据，不拉产品仓库、私有 submodule 或私有依赖。
- 第三方 Actions 固定经核实的完整提交 SHA；不能编造版本 SHA。
- 不使用 `pull_request_target` 执行外部 PR 的代码。fork 合成自测不能获得产品权限或触发产品流程。
- 核实可信维护者、默认分支保护与 Actions 权限；管理权限不足时列出具体未完成设置，不假称已生效。
- 合成 fixture、虚构数据、断言错误和普通测试堆栈本来就应可公开，允许必要的调试输出。不要求重定向全部日志，也不建立不存在的私有错误回传渠道。
- 公共日志、摘要、Artifact 和缓存不得含产品材料或真实凭据。不使用产品源码包、产品构建包或历史诊断作测试样本。
- 默认不额外上传 Artifact 或开启 cache；确有必要时只含独立合成材料／公开依赖，并记录保留时间和实际使用量。

## 6. 产品工作流保留清单

以下 12 项在本阶段均保持产品仓库原路径、原触发和 GitHub 托管执行方式，迁移状态为“未迁移”。表中的覆盖描述为审查基线，不代表本轮重新执行过这些测试。

| 当前工作流（均位于 .github/workflows/） | 保留的产品覆盖 |
|---|---|
| `ci.yml` | Linux / Windows、Python 3.10、runtime / other 四分片、JUnit 与测试集合完整性汇总 |
| `web-panel-ci.yml` | Web 后端、前端、浏览器、HTTP / WS、wheel、软件 Runtime 集成 |
| `display-session-contract.yml` | Windows 原生 Qt、Display Session 和实时数据契约 |
| `runtime-a18-diagnostics.yml` | A18 线程诊断及 SQLite 失败上下文 |
| `runtime-capture-diagnostics.yml` | Runtime 采集诊断 |
| `runtime-credit-diagnostics.yml` | Windows 原生 Qt 导出额度对照 |
| `runtime-sqlite-append-controls.yml` | SQLite append 生命周期控制 |
| `runtime-sqlite-failure-diagnostics.yml` | SQLite 故障点观察和独立控制 |
| `runtime-stop-diagnostics.yml` | Runtime stop 生命周期诊断 |
| `runtime-windows.yml` | Operator Runtime 构建、归档和验证 |
| `validate-windows-package.yml` | Windows 冻结包构建与验证 |
| `release-windows.yml` | Tag 校验、Designer / Runtime 正式发布 |

`docs/ci/archived-workflows/` 中 4 个历史 YAML 保持归档，不恢复、不迁移。

原主 CI 的集合完整性证明必须保留：同平台两个分片的 collected 集合相等，selected 集合互斥、并集等于 collected，每个 selected 项确实开始执行。它不是代码覆盖率，不能被测试数量相同替代；pytest 跳过不等于通过。

## 7. 仅限 GitHub 托管 Runner 的后续节省候选

本轮只形成有证据的建议，不修改产品工作流：

1. 以实际运行数据识别重复触发、过期提交运行和耗时热点。
2. 核实现有 scope / 路径过滤、concurrency、公开依赖缓存和 Artifact 保留策略，提出局部改进；不能把已经存在的优化再算一遍节省。
3. 评估需保留的 required checks 和手动诊断语义，不通过漏测、取消必需验证或让检查永久 Pending 节省用量。
4. 只有真实减少了产品运行量并保持覆盖，才可报告产品额度改善；Public 当前实测和未来 Private 估算分开。

不列出或比较任何自托管／私有 Runner 替代路线，不将其改名为专用机器、临时 VM 或租用执行服务继续评估。

## 8. 闭源与发布的后续事项

这些是独立的后续准备，不阻塞本轮独立合成实现，也不随合成通过自动完成：

- 盘点已公开的源码历史、Release、Packages、Pages、Artifact 和外部副本；Private 不会撤回已下载副本。[5]
- 复核外部打包固定版本的私有取源、`secrets: inherit`、Artifact 往返和发布权限。本轮不修改该调用，不运行正式发布。
- 同仓库触发的 reusable workflow 与外部仓库独立取源的权限情形不同，不能未经验证一律要求新增 PAT。
- 真正申请 Private 切换前，在不使用产品秘密的隔离替身中确认必要的权限与不发布 dry-run；提交切换报告，按已确认的独立审批边界执行。
- 正式切换后另行复验 PR / push / 手动 CI 与 Windows 构建控制链路；不能将切换后的结果写成切换前已验证。

## 9. 证据与验收记录

保留历史证据，避免混用版本：

| 版本／事件 | 证据含义 |
|---|---|
| 原始 `19af981f1a142715b544ba06412daee7add5bd5d` | 历史 OpenSpec delta 标题问题；不代表产品实施完成 |
| dot 的 `5e15f431205aeb7df20fbb89bade4c8891bec4c5` | dot 在 PR 评论报告 OpenSpec 2.1.1 strict、diff check、CI scope 9/9 通过；仅对应其版本 |
| 本轮起点 `5f480ded9e4fd162f7e347764581d85d5bee732d` | ChatGPT Web 在干净 checkout 实际运行 OpenSpec 2.1.1 strict，退出 1：一个 Scenario 层级错误、两个 Requirement 缺 MUST / SHALL |
| 外部构建静态审查 | 固定引用 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`；此前观察 master 为 `702fca523be0aae306763cde6f19742a96c4a0cb`，不作为新的实时值 |
| 历史公开面 | 此前 0 forks 的接口结果不能证明没有 clone、脱离 fork、外部镜像或下载产物 |

本轮修订后须重新严格校验；结果以本次提交的 PR 评论及 dot 的后续实跑报告为准。运行 OpenSpec 时使用其官方遥测关闭选项 `OPENSPEC_TELEMETRY=0`，并关闭更新检查，执行本地验证。

当前未完成项：公共仓库创建／配置及 Linux / Windows 实跑、账户用量读取、产品后续优化、Private 权限 dry-run 和可见性切换。没有执行的项目不得写成已通过。

## 10. 官方资料

以下为本轮费用与安全判断依据，后续执行需以当时规则和账户实际配置核实：

1. [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)：公共标准托管计算、私有包含用量、存储与 Larger Runner 的区别。
2. [Billing and usage](https://docs.github.com/en/actions/concepts/billing-and-usage)：Reusable Workflow 归调用方计费。
3. [Private networking with GitHub-hosted runners](https://docs.github.com/en/actions/concepts/runners/private-networking)：默认公网访问；私有网络连接与出口隔离须区分。
4. [Compromised runners](https://docs.github.com/en/actions/concepts/security/compromised-runners)：执行恶意代码、Token 与跨仓库权限的风险。
5. [Setting repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)：可见性变更影响。
