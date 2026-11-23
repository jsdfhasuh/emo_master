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
- GitHub App 私有源码读取适配器（仅在 fetch 步骤使用）。
- 测试输出脱敏、公开日志白名单、Check Run 回调适配器。
- 合成项目自测与使用文档。
- 不保存 emo_master 源码，不在 Git 历史中出现私有源码副本。

### 3.2 触发与回调流程

1. 私有 dispatcher 在受信任的 push / PR / 手动事件触发。
2. 私有端使用专用 Dispatcher GitHub App，生成仅作用于 `emo_master_ci` 的安装令牌，权限仅需触发指定 workflow（Actions: write）；创建私有仓库上的 pending Check Run（Checks: write）。
3. 私有端向公共仓库的固定默认分支触发 `workflow_dispatch`。Inputs 仅包含固定 source repo ID/name、source SHA、ref、event kind、PR number、check_run_id、request ID。严禁传递任何 token、密钥、工程数据或源码内容。
4. 公共 workflow 校验 source repo 必须是 `jsdfhasuh/emo_master`，SHA 必须为完整 40 位十六进制，PR / ref / check_run_id 必须与请求一致；拒绝任意仓库名、任意 URL、任意 shell 参数。
5. 公共 Runner 在安装依赖之前获取源码，使用独立 Source Reader GitHub App 的短期安装令牌读取指定私有 commit。优先采用一个封装的 fetch action，在同一进程内生成 JWT、获取 installation token、下载/checkout 源码、撤销 token；不将 token 写入 GITHUB_OUTPUT、GITHUB_ENV、磁盘配置、命令行参数或日志。
6. 获取源码后确认 checkout commit SHA 完全匹配请求 SHA；清除 Git 凭据与临时认证材料，再安装项目依赖和执行测试。测试进程不得继承任何私有仓库 token、App 私钥或 Release 凭据。
7. 测试命令 stdout/stderr 默认写入 Runner 本地临时日志，不直接输出到公开 Actions 日志。可信的公开汇总器只输出白名单状态、分片计数和不含内部数据的简短错误类别；不上传原始日志、JUnit、诊断 JSON、截图、工程文件、ZIP 或源码。
8. 公共 workflow 的独立 callback job 在干净 Runner 上运行，不 checkout 产品源码；它使用 Source Reader App 的 Checks: write 权限，根据真实 job result 更新私有 Check Run。回调前核对 check_run_id 对应的 head_sha、name、external_id/request_id；不得信任测试 job 自行生成的 PASS 字段。
9. 私有 PR 只将公共 Check Run 作为 required check；超时、未回调、SHA 不匹配、重复 request、workflow 被取消等均不能变成成功。支持重新触发并更新同一个逻辑检查或创建新的 attempt。

### 3.3 GitHub App 与权限隔离

建议使用两个独立 GitHub Apps，避免一个公开仓库 secret 可同时触发工作流、读取私有代码和写发布资产：

**Dispatcher App（私有 emo_master 持有私钥）**
- 安装范围：`emo_master`、`emo_master_ci`。
- `emo_master_ci`：Actions: write，仅用于触发固定 workflow。
- `emo_master`：Checks: write，仅用于创建 pending Check Run（如需由私有端创建）。
- 私钥只存放在私有仓库 Actions Secrets；绝不存入公共仓库。

**Source Reader / Check Reporter App（公共 emo_master_ci 持有私钥）**
- 安装范围：仅 `emo_master`。
- 私有仓库权限：Contents: read、Checks: write。
- 不授予 Contents: write、Actions: write、Pull requests: write、Releases/Packages 写权限。
- 公共 workflow 的 source-fetch job 仅请求 Contents: read；独立 callback job 仅请求 Checks: write。
- 私钥只通过 GitHub Actions Secret 注入到指定步骤；不能出现在 workflow_dispatch inputs、artifact、环境摘要或日志中。
- 若 GitHub App 的安装令牌不能按 job 需要覆盖权限，则创建两个更窄的 App，或采用等效隔离；不得为了省事扩大权限。

私有 fork PR 默认不具备 Secrets；不得使用 `pull_request_target` 去 checkout 并执行 fork 代码。公共 Runner 仅执行仓库所有者或明确列入 trusted policy 的提交。外部贡献者 PR 不自动 dispatch；需人工审查后由受信任分支/提交触发，或留在私有验证环境。

## 4. 工作流逐项迁移策略

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

- 公共 workflow run 的名称、分支、commit SHA、PR 编号和日志可被公众看到；把这些信息视为公开信息。
- 不允许在公共 runner 输出源代码片段、测试 fixture 内容、绝对路径、设备/工程配置、原始 JSON 诊断、线程 ID、数据库内容、截图、JUnit、core dump 或完整异常上下文。
- 测试步骤 stdout/stderr 重定向到临时文件；不能把失败日志作为公共 artifact 上传。
- 公共日志使用白名单输出：工作流阶段、平台、分片、测试总数/通过数/失败数、错误类别、源码 SHA、最终结论。具体错误可在私有端使用 Check Run annotations 或其他私有渠道提供；若无安全传输方式，只输出通用失败提示。
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
- `python_build_scripts` 对私有 emo_master 的源码读取权限、独立手动触发、artifact 往返必须在 Private 状态下验证。
- 不因迁移而公开产品安装包、Runtime ZIP、内部诊断附件或构建日志。

## 8. required checks 与结果关联

- 私有 dispatcher 在 dispatch 前为原始 commit SHA 创建 pending Check Run，名称固定（例如 `Public CI / source-validation`），external_id 记录 request ID。
- 公共 CI callback job 使用 Checks: write 更新同一个 check run；必须校验 source SHA、check_run_id、external_id/request_id、预期 App 身份和 workflow run 对应关系。
- callback job 只依赖 GitHub job result，不信任测试脚本写入的 PASS 文件或 artifact。
- 公共 workflow 失败、取消、超时、未回调、重复请求或 SHA 不一致时，私有 check 不能显示 success。
- 分支保护规则要求此 Check Run 来自指定 GitHub App；确认 PR merge commit 与 head SHA 选择规则，避免结果挂到错误提交。
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

## 10. 今天的实施顺序

1. 核对最新 main、PR #21 和工作流；修订本 OpenSpec 并严格校验。
2. 创建公共 `emo_master_ci` 仓库、许可证、保护规则和两个 GitHub App 的最小权限设计。
3. 先实现 Source Reader、dispatcher 和 Check Run callback 的端到端合成项目；验证无真实源码、无凭据泄漏。
4. 将主 CI 迁移至公共 Runner，先跑 Linux/Windows 四分片并确认检查绑定正确 SHA。
5. 迁移 Web、Display Session、Runtime 诊断和 Windows 包验证；逐项审查日志和 artifact。
6. 拆分 Release：公共端做验证，私有端持有发布权限和正式资产。
7. 逐一验证 12 个活动工作流的触发、结果、覆盖和权限；归档的 4 个 YAML 保持原样。
8. 盘点公开 Fork/Clone/Release/Packages/Pages/外部镜像/历史 artifacts；说明 Private 不能撤回既有副本。
9. 所有关键链路通过后，经用户明确确认切换 emo_master 为 Private。
10. 在 Private 状态复验 PR、push、手动触发、回调、Windows 构建和 Release 控制；读取 Billing usage 比较迁移前后实际消耗。

## 11. 尚未核实

- `emo_master_ci` 仓库是否创建及其名称可用性。
- GitHub App 创建权限、安装范围、可授予权限和账户策略。
- 当前 Actions artifacts 在公开仓库中的具体访问控制与存储计费表现。
- 当前所有测试/诊断脚本输出是否能安全白名单化；需按真实失败样本审计。
- 外部 fork PR 是否需要支持公共 CI；默认不支持自动公共执行。
- Release 发布是否必须保留现有构建产物，及私有重建的时间成本。
- 迁移前后真实 Runner minutes 与存储费用；不能从当前 YAML 推算准确节省额。
