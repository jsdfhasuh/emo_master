# 设计说明：公共仓库执行验证 CI

## 1. 审查基线

- 仓库：`jsdfhasuh/emo_master`
- 审查 main：`b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`
- 初始可见性：Public
- 活动工作流：12 个；历史归档工作流：4 个
- 当前 main 分支保护：GitHub API 返回 `protected=false`
- 当前 CI 关键脚本：`scripts/ci_scope.py`、`scripts/ci_check.py`、`scripts/ci_pytest_reports.py`、`scripts/ci_pytest_shard.py`、`scripts/ci_shard_summary.py`
- 既有外部打包仓库：`jsdfhasuh/python_build_scripts`，当前产品打包工作流固定引用 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`
- 本计划分支：`plan/extract-public-ci-workflows-20261123`；PR #21。

## 2. 费用模型与目标边界

GitHub 官方计费说明：公共仓库使用标准 GitHub-hosted Runner 的计算使用免费；私有仓库标准 Runner 使用量会消耗账户 included compute；Reusable Workflow 的费用仍归调用方。故本方案必须把 job 的实际 `runs-on` 执行位置迁移到公共 `emo_master_ci`，不能只把 `uses:` 指向公共仓库。

首期只选标准 `ubuntu-latest`、`windows-latest` Runner；不使用 Larger Runner。公共仓库 Artifact、缓存、Release、外部存储和第三方服务单独核对，减少保留时间和数据量。最终节省额以 GitHub Billing / Actions usage 实测为准，不能仅凭 YAML 估算。

## 3. 总体架构

### 3.1 仓库职责

**私有 `emo_master`**
- 产品源码、测试、诊断脚本、构建配置、项目文档及私有证据。
- 轻量 dispatcher：响应 push / pull_request / workflow_dispatch，创建待执行 Check Run 并触发公共 CI。
- 私有回调：接收公共 CI 的 Check Run 结果并保留 PR 门禁。
- Release 控制与发布凭据；最终私有 Release 资产不放入公共 Artifact。

**公开 `emo_master_ci`**
- 所有可公开执行的产品验证 workflow。
- 只在保密准入后引入取源适配；不存储产品 App 私钥。
- 测试输出脱敏、公开日志白名单、Check Run 回调适配器。
- 合成项目自测与使用文档。
- 不保存 emo_master 源码，不在 Git 历史中出现私有源码副本。

### 3.2 公共执行准入（当前禁止接入产品源码）

GitHub 官方说明：GitHub-hosted Runner 默认可访问公共互联网。临时 VM 只能降低跨任务持久化风险，不能阻止当前任务中的测试、npm 生命周期脚本、依赖安装脚本或打包子进程读取源码后通过网络外传。stdout 重定向、撤销 Token、固定 Action SHA、清理工作目录和只上传白名单摘要都不能证明源码保密。合成项目也不能证明真实产品代码在同一环境下不会外传。

因此设为明确硬门槛：

1. 公共仓库首阶段只运行独立合成 Python 项目，使用虚构数据和无产品权限身份；不接真实 emo_master 源码、测试、配置、App、Token、工程文件或原始证据。
2. 合成测试只证明公共仓库的调度、Runner、测试执行和结果处理链路可用；不得将其作为产品源码保密已通过的证据。
3. GitHub-hosted Runner 默认有公网访问。GitHub 的 Private Networking 文档描述的是让 Runner 访问私有网络资源，不等于为标准公共 Runner 提供默认拒绝公网出口的安全保证。除非另有由 Runner 外部强制、不可由 job 内代码绕过的网络出口控制及进程/文件隔离，并有独立审计证据，否则禁止向公共 Runner 提供真实产品源码。
4. 若不能证明完整隔离，公共 Runner 的产品源码执行路线正式判定为不适用；12 个产品工作流继续在私有仓库原路径执行，不得用“受信任分支”替代安全证明。
5. 为最大限度节省额度，优先评估私有仓库专用临时自托管 Runner：每个 job 使用一次性 VM/镜像，Runner group 仅授权 emo_master，工作结束销毁 VM；外部网络防火墙默认拒绝并只放行明确依赖；不存长期凭据、不与其他项目共享。该路线仍须审查测试代码外传、Runner 持久化和主机隔离，主机/维护成本单独计算。未经用户确认不得部署或改用该路线。
6. Windows 原生 Qt、GUI、冻结包验证必须单独评估是否可在隔离自托管 Windows VM 中保留真实桌面能力；不能用 Linux 容器或合成测试替代。
7. 若后续另行申请公共 Runner 产品试验，必须先提交隔离架构、可绕过性分析、网络规则、执行身份、日志/命令文件/缓存/Artifact 输出清单和独立审查证据，等待用户明确批准；不得用真实源码做安全探针。

### 3.3 调度与检查结果

公共合成自测不创建或更新 emo_master 产品 required check。未来如有经用户批准的产品 CI 调度，私有端应创建 queued Check Run 并保留目标 SHA、PR head/base、请求 ID、公共 workflow/run/attempt 的可信映射。公共回调仅为完成通知；私有端通过 GitHub API 核验固定 workflow、run/attempt、必需 job 和准确目标 SHA 后，才可更新同一门禁 App 创建的 Check Run。取消回调未运行时由私有端对账；缺失回调、缺 job、失败、取消、超时和 SHA 不匹配均不得成功。

### 3.4 GitHub App 与权限隔离（仅设计，不在合成阶段创建产品密钥）

- 公共合成自测不需要安装任何可访问 emo_master 的 GitHub App，也不配置 Source Reader / Check Reporter 私钥。
- 只有用户批准了明确的产品执行隔离架构后，才设计 Dispatcher、Source Reader 和 Check Reporter 的安装范围。App 权限应按真实调用需求最小化；Actions: write 不能天然限制为单个 workflow，Contents: read 不能天然限制为某个 SHA。
- 不把 Source Reader App 私钥放在公共仓库。若方案要求公共 Runner 直接取得可读取产品源码的 Token，则该 Token 和源码都处于可被测试进程读取/外传的风险环境，不能视为安全隔离。
- 任何私有 Check Run 都由私有可信端创建和更新；公共仓库只提供不含敏感信息的合成运行状态，不能持有产品 Checks 写权限。
- fork PR、同仓库受信任分支均不能绕过产品源码保密准入。

## 4. 工作流逐项迁移策略

下表仅是未来候选，不代表已获准迁移。当前 12 项均保留现有产品执行路径和原有断言；公共仓库只运行合成等价自测，不读取或执行这些产品文件。产品 CI 若要节省额度，优先另案评估私有临时自托管 Runner。

| 当前工作流 | 迁移后的执行位置 | 说明 |
|---|---|---|
| `ci.yml` | 公共 CI | 完整 Linux/Windows、Python 3.10、runtime/other 分片、报告覆盖检查。私有端只保留 scope 选择与 dispatcher；scope 决策也可通过固定 SHA/变更列表交给公共端计算，但不得传任意命令 |
| `web-panel-ci.yml` | 公共 CI | Linux/Windows Web 测试、浏览器、HTTP/WS、wheel、软件 Runtime 集成；仅保留白名单摘要，不公开原始浏览器截图/日志 |
| `display-session-contract.yml` | 公共 CI | Windows 原生 Qt 与 Display Session 合约测试；不公开 display-evidence、JUnit 原文和截图 |
| `runtime-a18-diagnostics.yml` | 公共 CI | 执行既有 A18 诊断；公开只输出诊断是否完整和失败类别，不输出线程 ID、内部 JSON 或原始 SQLite 上下文 |
| `runtime-capture-diagnostics.yml` | 公共 CI | 执行既有采集诊断；只公开摘要，不公开采集内部数据 |
| `runtime-credit-diagnostics.yml` | 公共 CI | 执行现有导出额度对照；仅公开实验通过/失败和固定统计，不上传原始报告 |
| `runtime-sqlite-append-controls.yml` | 公共 CI | 执行七项固定控制；不公开原始 SQLite 内容 |
| `runtime-sqlite-failure-diagnostics.yml` | 公共 CI | 执行故障观察和独立控制；原始故障上下文不出 Runner |
| `runtime-stop-diagnostics.yml` | 公共 CI | 执行停止生命周期诊断；只输出脱敏摘要 |
| `runtime-windows.yml` | 公共 CI 执行构建/验证；私有发布控制 | Runtime 包在公共 Windows Runner 内生成、验证后清理；不上传公共 artifact。需要留存或交付的正式包由私有发布链路重新生成/私有化保存 |
| `validate-windows-package.yml` | 公共 CI | 构建、冻结产物验证均在同一个公共 Windows job 内完成；不上传公开包或原始证据 |
| `release-windows.yml` | 公共 CI 做 tag 源码验证和非发布验证；私有仓库执行最终发布 | 公共端不持有 Release token，不发布私有二进制；私有发布 job 只处理已验证 SHA，并按需在私有 Runner 重新构建/签名/发布 |

`docs/ci/archived-workflows/` 中 4 个历史归档 YAML 保留在私有仓库，不恢复、不迁移、不公开。

如果某项诊断输出无法在不泄漏内部信息的情况下脱敏，则该项先暂停公共调度并单独评估；不得为满足“全迁移”而把原始证据公开。所有 12 个活动工作流都必须有明确的迁移结果，不能静默遗留。

## 5. 日志、Artifact 与数据保护

- 公共 run 的名称、输入、日志和摘要视为可公开读取；默认不放产品分支、PR 号、SHA 或内部路径，只使用随机请求 ID 和合成数据。
- 不允许在公共 runner 输出源代码片段、测试 fixture 内容、绝对路径、设备/工程配置、原始 JSON 诊断、线程 ID、数据库内容、截图、JUnit、core dump 或完整异常上下文。
- 测试步骤 stdout/stderr 重定向到临时文件；不能把失败日志作为公共 artifact 上传。
- 公共日志使用白名单输出：工作流阶段、平台、分片、测试总数/通过数/失败数、错误类别、请求 ID、最终结论。具体错误可在私有端使用 Check Run annotations 或其他私有渠道提供；若无安全传输方式，只输出通用失败提示。
- 不在公共仓库缓存产品源码、虚拟环境中包含的私有文件、工程样例或构建产物；依赖缓存仅允许公开依赖包，且缓存 key 不含源码内容。
- Public repo Artifact/缓存的实际可见性、保留期限和计费需实测；默认不上传任何产品 artifact，尽可能关闭缓存或仅缓存依赖下载。
- 所有测试使用既有合成数据；真实相机/PLC/现场工程不接入公共 Runner。

## 6. 信任边界与 PR 策略

公共 Runner 会实际读取并执行私有源码，因此测试代码具有网络外传能力。GitHub-hosted Runner 是临时环境，但不能阻止被执行的代码主动把源码发送到外部网络。必须把“受信任代码”作为硬条件：

- 只为仓库所有者或明确受信任作者的同仓库分支触发公共 CI。
- 不为 fork PR 自动发放私有源码读取权限；不使用 `pull_request_target` 执行 PR 源码。
- 对受信任 PR，可在私有 dispatcher 检查 `head.repo.full_name == github.repository`、作者 allowlist / approval 状态和固定 SHA 后再 dispatch。
- 对外部 fork PR，默认不执行公共 CI；需人工审查后将代码带入受信任分支，再触发公共验证，或采用私有验证 Runner。
- 公共 `emo_master_ci` 默认分支开启保护；仅所有者可直接写入，外部 fork PR 的 self-test 不得访问 Source Reader App Secret。
- 公共 workflow 仅由 `workflow_dispatch` / `repository_dispatch` 触发；不得添加可由任意 PR 改写 workflow 后又读取私有凭据的触发路径。
- 不运行不受信任的第三方 Action；使用完整 SHA 固定依赖 Action；升级必须经过代码审查和合成测试。

## 7. 私有发布链路

- 公共 CI 只做源代码测试、诊断、构建验证；不持有 Release 写 token、签名证书或私有发布凭据。
- 公共 Runtime/Designer 构建若只用于验证，产物留在 Runner 本地，验证后清理。
- 正式 Release、私有二进制资产、签名和发布说明仍由私有 emo_master / python_build_scripts 链路完成。
- 审查并修正现有 `secrets: inherit`：改为显式最小 Secrets 映射；Source Reader 只读 token 与 Release 写 token 严格分离。
- 切换前在已有隔离私有替身中验证 `python_build_scripts` 的私有取源、手动触发、私有 Artifact 往返及不发布 dry-run；切换获批后再在正式仓库复验。现有固定版本的通用构建工作流会上传包和日志，不能原样从公共仓库调用。
- 不因迁移而公开产品安装包、Runtime ZIP、内部诊断附件或构建日志。

## 8. required checks 与结果关联

- 私有控制端在 dispatch 前用专用 Check Reporter App 为准确的被测 commit SHA 创建 queued Check Run，名称固定，external_id 记录 request ID。原 CI 在 PR 上评估合并候选 SHA；不得仅测试 head 再把结果冒挂到合并候选。
- 公共 CI callback job 使用 Checks: write 更新同一个 check run；必须校验 source SHA、check_run_id、external_id/request_id、预期 App 身份和 workflow run 对应关系。
- callback job 只依赖 GitHub job result，不信任测试脚本写入的 PASS 文件或 artifact。
- 公共 workflow 失败、取消、超时、未回调、重复请求或 SHA 不一致时，私有 check 不得标记 success、neutral 或 skipped。
- 分支保护规则要求此 Check Run 来自指定 GitHub App；正式仓库闭源前须核实 GitHub 套餐在 Private 上仍支持该门禁，配置并实测拒绝错误 SHA、错误 App 和未通过结果。
- 同一 PR 的新提交要创建新 check run；旧 SHA 的成功结果不能覆盖新 SHA。

## 9. 安全验证与失败处理

必须测试：
- 无效仓库名、短 SHA、错误 SHA、错误 PR/ref、伪造 check_run_id 被拒绝。
- Source Reader token 不出现在 workflow inputs、日志、artifact、GITHUB_OUTPUT/GITHUB_ENV 和被测进程环境。
- token 在 checkout 后撤销/销毁；Git 配置无残留认证。
- 测试进程无法读取 App 私钥、Release token 或私有回调 token。
- 原始测试输出、失败堆栈、诊断文件不会进入公共日志和 artifact。
- fork PR 不触发带私有读取能力的 workflow。
- callback job 在干净 Runner 上执行，无法被测试代码篡改；失败/取消/超时正确反映为 Check Run 非成功。
- 测试结束后 Runner 临时工作目录与缓存中不保留产品源码。

安全检查不应变成无穷故障矩阵；覆盖上述直接信任边界与实际触发路径即可。

## 10. 实施顺序

1. 完成 OpenSpec strict validation 和 PR 审查。
2. 创建公共 `emo_master_ci`，配置许可证、默认分支保护和维护者权限；不配置任何产品 Secrets/App。
3. 仅建立合成 Python fixture，验证公共 Linux/Windows Runner、调度、测试退出码、固定格式结果和运行摘要；不触发产品 required check。
4. 对照 GitHub 官方 Runner 安全文档，记录公共 Runner 默认网络能力及其不提供的出口隔离保证；明确合成测试不证明产品源码安全。
5. 独立评估私有临时自托管 Runner 的 Linux/Windows 可行性、费用、网络策略和 Qt 桌面支持；先提交方案，不擅自部署。
6. 只有找到由 Runner 外部强制且不可由任务绕过的产品源码隔离架构，并经用户明确批准，才可新增真实产品源码试点；否则保留所有产品 CI 在私有原路径。
7. 在任何闭源切换前，先解决 `python_build_scripts` 私有取源、发布权限、分支门禁和公开历史盘点；切换 Private 需要用户另行明确确认。
8. 切换后在 Private 状态复验现有 PR、push、手动 CI、Windows 构建和 Release 控制；读取 Billing usage 比较真实节省额。

## 11. 本轮静态复核

| 基线 | 复核结论 |
|---|---|
| PR #21 原始 head `19af981f1a142715b544ba06412daee7add5bd5d` | 原 OpenSpec 使用本地化标题，未被识别为 delta；以规范标题修复，不标记产品实施完成 |
| main `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd` | 12 个活动 YAML、4 个归档 YAML；原四分片先分散生成 JUnit、诊断及节点 ID 清单，再在每个平台检查采集相等、选集互斥、选集并集完整和每个选中项均开始运行。pytest 跳过不是通过 |
| Web / Display / 诊断 | 现有工作流有原始 Artifact 与诊断输出路径；不得直接复制到公共端。六项诊断仍以诊断自身条件判断，不冒称人工验收 |
| 外部构建 | 产品固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`；外部 master 观察值 `702fca523be0aae306763cde6f19742a96c4a0cb`。固定版本支持 `SOURCE_REPO_TOKEN` / `RELEASE_REPO_TOKEN`，存在 `secrets: inherit`、原始报告/正式包上传及 Release 路径；静态读取不等于私有 dry-run |
| 历史公开面 | GitHub 仓库接口当时报告 0 forks；不能证明没有 clone、脱离 fork、外部镜像或已下载产物。Private 不撤回历史副本 |

来源：[计费](https://docs.github.com/en/actions/concepts/billing-and-usage)、[Runner 安全](https://docs.github.com/en/actions/concepts/security/compromised-runners)、[受保护分支](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)、[App 安装令牌](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)、[可见性变更](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)。

## 12. 尚未核实

- `emo_master_ci` 仓库是否创建及其名称可用性。
- GitHub App 创建权限、安装范围、可授予权限和账户策略。
- 当前 Actions artifacts 在公开仓库中的具体访问控制与存储计费表现。
- 当前所有测试/诊断脚本输出是否能安全白名单化；需按真实失败样本审计。
- 外部 fork PR 是否需要支持公共 CI；默认不支持自动公共执行。
- Release 发布是否必须保留现有构建产物，及私有重建的时间成本。
- 迁移前后真实 Runner minutes 与存储费用；不能从当前 YAML 推算准确节省额。
