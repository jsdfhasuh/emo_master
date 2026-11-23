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

### 3.2 执行准入（当前不接产品源码和密钥）

公共标准 Runner 是临时 VM，不是私有源码与测试进程之间的保密边界。测试、npm 生命周期脚本、打包子进程一旦可读源码，单靠重定向 stdout、撤销 token、固定 Action SHA 或清理工作目录，不会限制网络、Runner 服务、GitHub 工作流命令文件与其他公开输出。公开 Artifact 也不能转运产品分片原始清单。按以下顺序推进：

1. 公共端先运行独立合成项目，真实产品权限、源码、配置和凭据一律不接入。
2. 后续产品资格试验只能以无敏感内容的私有替身和合成标记开展。Linux 和 Windows 都须独立验证隔离执行身份、文件与进程边界、可访问网络、Runner API/工作流命令文件及固定格式结果出口；不得在证据不足时用真实源码测试安全性。
3. 若标准 Runner 无法保留原覆盖并证明上述边界，方案 A 的产品执行停止。可单独提请用户选用私有仓库专用隔离自托管 Runner（Runner 计算额度与主机成本分开核算），或保留私有托管执行并优化触发/缓存。不默认改变已授权架构。
4. 公开工作流不得加入原始报告或产品资产上传步骤。若维持各平台两个独立分片 VM，完整节点 ID 清单的跨 job 证明必须留在私有边界；如改同机顺序运行，则先核对原独立桌面、稳定性与时间预算，且须在原断言不变的前提下实测。只比较计数不等价于集合覆盖。
5. 私有端依据来源、当前提交和检查策略创建 queued Check Run。公共请求采用固定 workflow/ref 和不含敏感字段的请求标识；固定真实目标 SHA 和 PR head/base 与 run/attempt 的映射留在私有可信端。公共回调是完成通知而非可信的 PASS；私有端以 GitHub API 核实 run 对应仓库、固定 workflow、调度版本、attempt、各必需 job 和当前目标 SHA 后才能回填。取消回调未运行时由私有调度/对账处理；未回调、缺 job、失败、取消、超时和 SHA 不一致均不产生通过结论。

### 3.3 GitHub App 与权限隔离

- Dispatcher App 仅安装在公共 CI 仓库，Actions: write；私钥仅在私有可信端。该权限并不原生限制为单个 workflow，需由私有调用端固定 workflow/ref 且对账核实。
- Source Reader App 仅安装在产品仓库，Contents: read；长期私钥仅在私有可信端。安装令牌限仓库和权限，不能原生限制到某个 SHA；固定 SHA 授权须由私有端实现。若缺乏有证据的私有取源及隔离路径，不下发任何真实读取凭据。
- Check Reporter App 仅安装在产品仓库，Checks: write；长期私钥仅在私有可信端。以同一 App 创建、更新和归属 required check，不依赖跨 App 更新或公共端写私有关卡。用于回读公共运行状态的身份与 Checks 写入分离，优先使用公共只读 API。
- fork PR 不自动运行真实产品测试；owner/可信同仓库分支也不能绕过执行准入。避免由 PR 可改写的代码接触私钥。公共默认分支在任何敏感集成前先保护；合成自测从不接触产品 Secrets。

## 4. 工作流逐项迁移策略

下表为通过准入后的目标，不是已验收状态。当前 12 项均保留现有产品执行路径和原有断言。

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

1. 核对最新 main、PR #21 和工作流；修订本 OpenSpec 并严格校验。
2. 创建公共 `emo_master_ci` 仓库、许可证、保护规则和分权 GitHub App 的设计；真实密钥不上公共端。
3. 先用合成项目实际验证标准 Runner，再使用私有替身验证调度和检查；真实产品执行须先通过保密准入。
4. 将主 CI 迁移至公共 Runner，先跑 Linux/Windows 四分片并确认检查绑定正确 SHA。
5. 迁移 Web、Display Session、Runtime 诊断和 Windows 包验证；逐项审查日志和 artifact。
6. 拆分 Release：公共端做验证，私有端持有发布权限和正式资产。
7. 逐一验证 12 个活动工作流的触发、结果、覆盖和权限；归档的 4 个 YAML 保持原样。
8. 盘点公开 Fork/Clone/Release/Packages/Pages/外部镜像/历史 artifacts；说明 Private 不能撤回既有副本。
9. 所有关键链路通过后，经用户明确确认切换 emo_master 为 Private。
10. 在 Private 状态复验 PR、push、手动触发、回调、Windows 构建和 Release 控制；读取 Billing usage 比较迁移前后实际消耗。

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
