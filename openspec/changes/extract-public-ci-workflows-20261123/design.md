# 设计说明

## 1. 审查基线

- 仓库：`jsdfhasuh/emo_master`
- 分支：`main`
- HEAD：`b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`
- 仓库可见性：Public
- 默认分支保护：当前读取结果为未启用
- 仓库大小：GitHub 元数据约 402,486 KB
- 活动 PR：#19（`plan/runtime-background-task-service-20261123`）仍在运行 CI；本计划从 main 单独分支，不改动该 PR。
- 活动 Actions 工作流：12 个；`docs/ci/archived-workflows` 另有 4 个历史归档 YAML。

## 2. 现有工作流逐项处置

| 工作流 | 现有职责 | 迁移决定 |
|---|---|---|
| `.github/workflows/ci.yml` | 变更范围判断、Linux/Windows Python 3.10、runtime/other 双分片、JUnit/失败日志上传、完整覆盖汇总 | **部分抽取**：产品专属 scope 选择和测试分片脚本留私有；把稳定 Runner 编排提取为公共 reusable workflow。保留调用方必需检查名称 |
| `web-panel-ci.yml` | Web 后端、前端、浏览器、HTTP/WS、SDK wheel、软件 Runtime 集成 | 留私有。测试命令、SDK、前端构建和产品 Runtime 耦合明显 |
| `display-session-contract.yml` | Windows 原生 Qt、Display Session/实时数据契约回归 | 留私有。测试集合及验收语义均为产品专属 |
| `runtime-a18-diagnostics.yml` | A18 线程来源诊断和 SQLite 失败上下文 | 留私有。诊断脚本、内部观察协议及测试均属产品实现 |
| `runtime-capture-diagnostics.yml` | 原始 pytest 顺序下的采集诊断 | 留私有。依赖产品专属观察器和测试 |
| `runtime-credit-diagnostics.yml` | Windows 原生 Qt 导出额度对照诊断 | 留私有。包含产品专属实验及报告验证 |
| `runtime-sqlite-append-controls.yml` | SQLite append 生命周期对照实验 | 留私有。固定实验与断言不可抽成通用公共测试 |
| `runtime-sqlite-failure-diagnostics.yml` | SQLite 故障点观察和独立控制 | 留私有。诊断实现与产品内部测试强绑定 |
| `runtime-stop-diagnostics.yml` | Runtime stop 生命周期观察 | 留私有。停止语义和证据解析均为产品专属 |
| `runtime-windows.yml` | 手动构建 Operator Runtime，调用外部打包仓库并验证归档 | 留私有。发布权限、产品版本和制品验证属于产品边界 |
| `validate-windows-package.yml` | PR/手动构建冻结包并验证 Designer/Runtime | 留私有。产品冻结产物验证与产品脚本强绑定 |
| `release-windows.yml` | Tag 发布前源码验证，再调用 `python_build_scripts` 发布 Designer/Runtime | 留私有。写权限、tag、Release 和产品发布流程不能迁入通用公开仓库 |

### 归档工作流

`docs/ci/archived-workflows/` 下的 4 个 YAML 是历史诊断基线，保留原样和原说明，不迁移、不重新启用、不删除。归档内容不应被公共 CI 仓库当作活动工作流。

## 3. 产品专属 CI 代码边界

以下文件必须留在私有 emo_master：

- `scripts/ci_scope.py`：理解产品目录和 Web CI 覆盖范围的变更选择器。
- `scripts/ci_check.py`：proto、Ruff、mypy、pytest 的产品检查入口。
- `scripts/ci_pytest_reports.py`、`scripts/ci_pytest_shard.py`、`scripts/ci_shard_summary.py`：pytest 报告、测试分片及覆盖证明。
- `tests/test_ci_*.py`：上述产品 CI 选择、报告和分片行为的契约测试。
- `scripts/r3_*.py`、`scripts/diagnostics/*`、Runtime/Qt 测试与验证脚本：保留私有。
- `scripts/verify_frozen_runtime.py`、`verify_runtime_artifacts.ps1`、产品包验证脚本：保留私有。

这些文件中有大量产品行为、运行边界和内部诊断细节，不应为了“开源 CI”而公开。

## 4. 新公共仓库结构

暂定仓库：`jsdfhasuh/emo_master_ci`（需确认名称可用并由用户创建/授权）。

建议最小结构：

```text
emo_master_ci/
├── .github/
│   └── workflows/
│       ├── python-source-ci.yml       # workflow_call，固定通用源码检查编排
│       └── self-test.yml              # 在本仓库合成项目上自测
├── actions/
│   ├── setup-python-project/          # 可选：只有确实被多个 workflow 复用时才抽取
│   └── upload-test-evidence/          # 可选：稳定的通用产物上传约定
├── tests/
│   └── fixtures/minimal-python-project/
│       ├── requirements-dev.txt
│       └── scripts/ci_check.py
├── docs/
│   ├── reusable-workflows.md
│   └── compatibility.md
├── README.md
└── LICENSE
```

首期不建立庞大的 Action 集合。优先一个 reusable workflow；只有存在重复步骤且接口稳定时再抽 composite action。公共仓库需要明确开源许可证，避免“Public 但无明确授权”的歧义。

## 5. 调用方式与源码访问

- 私有 emo_master 使用 `jobs.<job_id>.uses: jsdfhasuh/emo_master_ci/.github/workflows/python-source-ci.yml@<完整SHA>`。
- reusable workflow 运行在调用方的 GitHub Actions 上下文中；其默认 `actions/checkout` 检出调用方仓库的事件提交，不需要把源码镜像到公共仓库。
- 公共工作流只读取调用方仓库的依赖清单和固定入口脚本；不使用任意 `run-command` 输入，不接收 PAT，不上传源码。
- 产品调用方保留 scope job，按 `run_full` 决定是否运行完整检查。公共工作流接收明确的布尔值和 Python/依赖文件参数；测试分片名、报告目录、汇总规则固定，不接受任意脚本命令。
- 所有公共 reusable workflow 和第三方 Action 固定到完整 Commit SHA；升级通过 PR 审查和合成项目自测。
- `permissions: contents: read` 为默认最小权限；发布权限不进入公共 CI 工作流。调用方可对 job 进一步收紧权限。
- 公共仓库不主动 checkout 私有 emo_master，不使用 repository_dispatch + 私有 PAT 作为主方案。

## 6. 现有外部打包仓库依赖

emo_master 的发布工作流已经调用公开仓库 `jsdfhasuh/python_build_scripts`，并固定打包器提交 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`。

审查发现其 `release-emo-master.yml` / `release-windows.yml` 会再次 checkout `jsdfhasuh/emo_master`，并使用 `SOURCE_REPO_TOKEN || github.token`；发布还使用 `RELEASE_REPO_TOKEN || github.token`。切换 Private 后：
- 当前 emo_master 的 `release-windows.yml` 和 `runtime-windows.yml` 对外部 `python_build_scripts` reusable workflow 使用了 `secrets: inherit`。这会把调用方可继承的 Secrets 集合交给外部工作流，权限范围过宽；迁移时应改为显式映射所需的 `SOURCE_REPO_TOKEN`、`RELEASE_REPO_TOKEN`，不再整体继承。
- 从 emo_master 自身触发的 reusable workflow，必须验证调用方 token 对同一私有仓库的读取能力；对外部打包器 checkout 私有源码时，优先使用最小权限只读 `SOURCE_REPO_TOKEN`。
- 从 python_build_scripts 独立手动触发的流程，默认 `github.token` 不应假设有权读取私有 emo_master；应配置仅针对 emo_master 的只读 `SOURCE_REPO_TOKEN`。
- 向 emo_master 发布 Release 需要单独的写权限 `RELEASE_REPO_TOKEN`；只传给实际创建 Release 的 job，不得传给公共 CI 仓库或无关 job。
- 验证 Release、Runtime package、冻结包 PR 验证和 artifact 下载链路；没有验证通过前不切 Private。

## 7. 重要风险与控制

### 7.1 公共历史无法靠 Private 撤回

将仓库改为 Private 只能限制未来访问，不能撤销已有 Fork、Clone、缓存、Release、下载文件或外部镜像。切换前盘点 Releases、Packages、公开 artifacts、Pages、外部文档和仓库 Fork；如需彻底移除历史公开内容，需要另行制定历史清理/凭据轮换方案，不能把改可见性当成历史清除。

### 7.2 公开工作流是可执行代码

公共 reusable workflow 在私有仓库上下文执行，等同于把公共仓库中的代码放入私有 CI 执行环境。必须固定 SHA、审查每次升级、最小化 token 权限、不使用不可信 PR 可控的动态 shell 输入。不得从可被外部修改的浮动分支加载工作流。

### 7.3 私有仓库的 Actions 策略

切换前检查组织/账户 Actions policy、允许的 Actions、Reusable workflows 访问策略和 PR fork 设置。确认私有仓库允许引用该公共仓库；若设置不允许，先调整策略或停止切换。

### 7.4 Required checks 与并发

现有 main 分支读取到 `protected=false`。迁移不能假定已有 required checks；必须保留现有检查名称，并在迁移后配置分支保护/规则集，避免 CI 迁移期间无门禁合并。原并发取消策略、手动运行不被取消的规则保持不变。

### 7.5 版本和工具运行时

当前主 CI 使用 checkout/setup-python/upload-artifact 的 v7 线及 download-artifact v8；部分诊断和产品验收工作流仍使用 checkout v4、setup-python v5、artifact v4。首期只做提取，不顺便升级全部 Action 版本，避免把 CI 迁移和运行时升级混为一项。后续单独评估 Node 运行时和版本兼容。

## 8. 今天的迁移顺序

1. 盘点并备份当前 main、Actions YAML、分支、PR、Release 和关键 CI 证据。
2. 创建公共 CI 仓库，先实现合成 Python 项目自测。
3. 固定公共仓库首个可用 SHA。
4. 在 emo_master 独立迁移分支接入公共 reusable workflow，保留原有本地 scope 选择和产品检查脚本。
5. 对照旧工作流执行 Linux/Windows 全量测试、分片覆盖、artifact 汇总；PR/手动入口分别验证。
6. 验证 Web、诊断、冻结包、Runtime 构建和 Release 仍由私有工作流运行。
7. 检查 python_build_scripts 的 token/权限及跨仓库调用。
8. 备份并核对公开内容风险，用户确认后切换 Private。
9. 在 Private 状态下重新跑 PR、手动 CI 和一个不发布的构建验证；全部通过后关闭迁移。

## 9. 当前尚未核实

- `emo_master_ci` 仓库名是否可用，以及用户是否已创建该仓库。
- GitHub Actions policy 是否允许私有 emo_master 引用该公开仓库。
- `python_build_scripts` 在 Private 后通过调用方 token 读取 emo_master 的实际行为。
- GitHub Releases、Packages、Pages、Fork、外部镜像和公开 artifacts 的完整数量。
- 当前所有 Action 版本是否在账户允许列表中；本计划不将升级作为迁移前置。
- Private 切换后实际 CI 和 release 运行结果。
