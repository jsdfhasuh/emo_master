# 12 项真实产品工作流迁移矩阵

产品审查 SHA：`82a1aa6d8499ee203ea8e452c2f016db37ea7b3f`。下列是明确的待实施映射，不是已经运行通过的报告。所有实际产品 job 均在公共 `emo_master_ci` 标准 GitHub 托管 Runner 执行；产品只留短 dispatcher。

## 逐项映射

| 原工作流 | 触发与平台保持 | 迁移必须保留／修改 |
|---|---|---|
| `ci.yml` | push main / agent/runtime-workflow-architecture-v1；PR main；手动。Linux + Windows，Python 3.10 | scope；Linux / Windows 各 runtime、other 两分片；ci_check 原检查；Linux offscreen，Windows 原生 Qt ≥1920×1080；JUnit、失败日志、two-job、ci-shard 全部加密；两平台独立集合汇总 |
| `web-panel-ci.yml` | push main / codex/logging-and-designer-improvements 与原 paths；PR main 与 paths；手动。Linux / Windows + Windows owning Runtime | Python 3.10、Node 22、SDK 1.10.0；Linux 完整 Web 逻辑，Windows 自动原子集／手动全套；浏览器、HTTP/WS、独立 wheel、无 Qt 导入、两 Job 图像及自动重启；wheel、SDK、截图、HTML、demo 等加密 |
| `display-session-contract.yml` | 手动；架构分支原 paths；Windows，15 分钟 | 原生 Qt、5 Sessions、96-frame live contract、8 个原测试入口与 JUnit；关闭真实相机；source-sha 记录产品 execution SHA；原 14 天证据保留 |
| `runtime-a18-diagnostics.yml` | 手动；架构分支原 paths；Windows offscreen，30 分钟 | 原 pytest 顺序、A18 observer、bounded JSON 与随后 WAL plain assertions；原始 JSON／线程信息移入加密报告；保留 diagnostic_only / inconclusive |
| `runtime-capture-diagnostics.yml` | 手动；架构分支原 paths；Windows offscreen，30 分钟 | 资源边界、原 pytest 顺序与 capture observer、完整 JSON 校验和原退出码；原始输出加密 |
| `runtime-credit-diagnostics.yml` | 手动；架构分支原 paths；Windows 原生 Qt，20 分钟 | ABBA control、r3_export_credit_report 原判定和 7 天报告；expected-sha 显式使用产品 SHA；已有 sanitized 报告默认仍加密 |
| `runtime-sqlite-append-controls.yml` | 手动；架构分支原 paths；Windows offscreen，10 分钟 | 7 个 isolated controls、JSONL 与生命周期判定；repo 参数指向 product/，不使用公共 CI 根目录；输出加密 |
| `runtime-sqlite-failure-diagnostics.yml` | 手动；架构分支原 paths；Windows offscreen，30 分钟 | 原 pytest 顺序、failure observer、call details、独立 durable controls，case-timeout 30 不变；产品 repo 路径与失败上下文保留 |
| `runtime-stop-diagnostics.yml` | 仅手动；Windows offscreen，30 分钟 | stop + A18 observer、source-stable / observer-retired 等证据；未捕获证据不得写为通过；不新增 PR 自动触发 |
| `runtime-windows.yml` | 手动 source_ref / packager_ref；Windows | 先解析为完整产品 / packager SHA；Runtime source regression、构建、解压、native cycles / provenance；同 job 完成产品执行后加密；保留 fieldAcceptance=NOT_RUN、publish=false |
| `validate-windows-package.yml` | PR main / 架构分支与原 paths；手动；Windows | 保留 PR head 构建与 AST 版本读取；Designer build-only、psutil/pywinauto/Pillow、workflow / frozen contracts、verify_frozen_runtime expected-source-sha；同 job build+verify 后加密；不发布 |
| `release-windows.yml` | 产品 v* 标签；Windows | 产品 tag / 应用版本一致、完整源码和 ONNX 检查、原生 Qt；保留 fetch-depth: 0、Designer 自定义图标验证及 collectAssets 原六文件选择；Designer / Runtime 并行构建与验证；两个目标通过后干净 publisher 解密并写明确的 Private Release |

“原 paths”必须从上述固定版本逐项转录为路由契约并与目标最新差异比较，不能凭目录名称猜测。六项诊断全部可通过公共端运行，但保持其原触发与诊断含义，不全部改成每个 PR 的必需检查。

## 主 CI 的真实完整性

保留 `scripts/ci_check.py`、`ci_pytest_reports.py`、`ci_pytest_shard.py` 的执行语义；可信公共汇总器按原 `ci_shard_summary.py` 数据格式验证：

- 同平台两分片 collected 集合相等；
- selected 集合互斥；
- selected 并集等于 collected；
- 每个选中项都实际开始执行；
- 失败、跳过、未执行分别记录，不能只核对总数。

测试数量随产品版本变化，验收使用同一固定产品版本对照新旧，不能沿用历史数量作固定期望。新增控制代码的正常路径与直接反例测试足够，不扩展线程穷举或无关性能专项。

## 必须适配的真实代码耦合

| 位置／行为 | 当前依赖 | 迁移改法 |
|---|---|---|
| `scripts/ci_scope.py` | GITHUB_EVENT_NAME / PATH；PR base.sha；本地 checkout merge；push before | 增加显式源事件输入；源提交预取齐全；保留未知默认 full 与既有选择器测试 |
| Web FULL_WEB_LOGIC | 是否原始 workflow_dispatch | 从源事件判定；不能用公共调度事件覆盖 |
| credit expected-sha | 公共运行时会读错 GITHUB_SHA | 改用产品 execution SHA |
| SQLite controls repo | GITHUB_WORKSPACE | 显式 product/ 根目录 |
| 构建 source_ref / tag / release_repo | github.sha / ref_name / repository | 全部从经过核实的产品请求取值 |
| `tests/test_release_workflow_contract.py` | 产品 YAML 路径、exact jobs、固定 packager、github.sha、无 secrets | 改测新 dispatcher / 公共固定 manifest / build-only / 独立 publisher 的真实边界；不删除必要约束 |
| 原始 Artifact producer / consumer | 同 caller run 的明文 artifact | job 内先完成需要执行产品代码的消费；跨 job 仅密文，可信汇总／发布按准确 ID 与 digest 读取 |

## 外部 packager 必改项

固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a` 的静态问题：

- `release-windows.yml` always 上传 release-output/*，其中有 build.log、runtime-build.json、source / archive-check。
- `release-emo-master.yml` 上传 release-state、请求／notes、中间包与 public/ 六文件。
- SOURCE_REPO_TOKEN || github.token 在公共 caller 下不能用默认 token 读取 Private 产品。
- RELEASE_REPO_TOKEN || github.token 曾进入构建环境，secrets: inherit 会扩大传播。
- caller repository 与产品同仓判断将因新公共 caller 改变。
- 编排 Python 3.11；产品 Python 3.10、PyInstaller 6.22.2、Inno Setup 6.7.3，迁移不顺带升级。

新受控入口需要：显式源身份、只读取源 Token、build-only、同目标 build+verify、本地原始输出、age 加密 Artifact、独立 publisher。所有调用固定真实新 SHA；产品／工具仓库两边同时更新契约。保留其他产品旧入口，避免影响 VisionWorkshop。

## 门禁映射与归档

主源码旧汇总的 Linux / Windows 语义分别映射到 `public-ci/source-linux` / `public-ci/source-windows`。Web / frozen 等只有原先要求的场景才进入门禁，具体 context 清单随配置交付。使用新前缀避免旧 Check Run 与新 Commit Status 同名。

4 个历史归档工作流保持原样。迁移完成后原 12 个活动产品工作流不再计算；保存其固定版本／回滚位置，不把它们与原 4 个历史归档混淆。

## 实施证据表

每项填写：产品 execution SHA、PR head/base/check SHA（适用时）、公共 workflow SHA、packager SHA、run ID / attempt、OS、原始结果、密文 Artifact ID / digest、状态回传 URL、对照结论。未执行项保持未完成，不以合成项目或 YAML 静态审查凑齐 12/12。
