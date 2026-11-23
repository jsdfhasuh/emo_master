# Public Synthetic CI

## ADDED Requirements

### Requirement: 只使用 GitHub 托管 Runner

本变更 MUST 只使用标准 GitHub 托管 Runner；MUST NOT 评估、部署或切换到自托管、自建、本地、租用服务器或其他私有 Runner。

#### Scenario: 执行或评估 Runner 方案

- GIVEN 用户已排除私有 Runner
- WHEN dot 实施公共自测或记录后续费用建议
- THEN MUST 仅采用标准 GitHub 托管执行方式
- AND MUST NOT 将自托管路线作为评估任务或备用部署方案
- AND MUST 区分仓库 Private 可见性与 Runner 托管方式

### Requirement: 公共仓库独立执行合成 CI

公开 `emo_master_ci` MUST 在自身工作流上下文使用标准 Linux / Windows GitHub 托管 Runner 执行独立合成项目。

#### Scenario: 公共合成项目运行

- GIVEN 公共仓库内存在从零编写的合成 Python 项目
- WHEN 公共仓库的 push、pull_request 或 workflow_dispatch 触发自测
- THEN MUST 在 Linux / Windows 使用 Python 3.10 执行该合成项目
- AND MUST 记录真实 workflow、run、提交 SHA、平台和 job 结论
- AND MUST NOT 将私有调用方引用公共 Reusable Workflow 记成公共仓库免费执行

### Requirement: 产品材料与权限不进入公共自测

公共自测 MUST 仅使用可公开的独立 fixture、虚构输入和公开依赖；MUST NOT 读取、下载、复制或执行 emo_master 产品材料，也不得持有产品访问权限。

#### Scenario: 构建合成自测输入

- GIVEN 产品仓库当前仍为 Public，或将来已经转为 Private
- WHEN 建立 fixture、测试、依赖、workflow 输入、日志或产物
- THEN MUST NOT 引入产品源码、产品测试、工程样例、配置、历史、原始诊断或产品构建包
- AND MUST NOT 配置产品 GitHub App、Source Reader、跨仓库 Token 或产品 Secrets
- AND MAY 使用仅当前公共仓库作用域、最小只读权限的 GITHUB_TOKEN

### Requirement: 合成测试结果真实传播

公共自测 MUST 在 Linux / Windows 分别证明成功路径与可控测试失败路径，保留测试退出码和实际 job 结论。

#### Scenario: 正常合成测试通过

- GIVEN 使用正常合成输入
- WHEN 测试命令执行完成
- THEN MUST 退出 0 且对应 job 成功
- AND MUST NOT 将未执行、跳过、取消或超时表述为测试通过

#### Scenario: 手动可控失败

- GIVEN 手动选择明确的合成失败开关
- WHEN 一个预定断言失败
- THEN 测试命令 MUST 非零退出，相关 job 和 workflow MUST 真实失败
- AND MUST NOT 通过 continue-on-error、忽略退出码或汇总覆盖将失败记为成功
- AND MUST 保留两平台实际执行证据，不能以一个平台失败后另一个被取消代替

### Requirement: 公共权限与输出可审查

公共 workflow MUST 使用最小权限和经核实的完整 Action SHA，所有输入与输出均符合独立合成范围。

#### Scenario: 公共 PR 或 fork 自测

- GIVEN 自测可能执行公共 PR 代码
- WHEN 工作流运行
- THEN MUST 不授予产品权限，不触发产品流程
- AND MUST NOT 使用 pull_request_target 执行外部 PR 代码
- AND MUST 核实维护权限、默认分支保护及 Actions 设置，未配置项如实报告

#### Scenario: 输出合成诊断

- GIVEN fixture、测试数据和异常均来自独立合成项目
- WHEN 生成测试日志或必要产物
- THEN MAY 输出可公开的断言信息和普通错误堆栈
- AND MUST NOT 包含产品材料或真实凭据
- AND MUST NOT 将日志重定向、Token 撤销或合成成功当作产品源码保密证明

### Requirement: 保留产品覆盖和检查边界

系统 MUST 保留 emo_master 的 12 个活动工作流、原有产品检查门禁和 4 个归档工作流；公共合成结果 MUST NOT 替代任何产品验证。

#### Scenario: 公共合成测试通过

- GIVEN 公共 Linux / Windows 自测已通过
- WHEN 记录本轮完成状态
- THEN MUST 只标记公共合成基础设施验证通过，产品迁移数 MUST 为 0
- AND MUST NOT 创建或更新产品 Check Run，或触发产品构建与发布
- AND MUST 保留产品原平台、测试集合、断言和报告；不得将 skipped 记为 passed
- AND MUST 保留原分片 collected 一致、selected 互斥、并集完整和选中项已开始执行的证明
- AND MUST 保持 4 个历史 YAML 归档

### Requirement: 费用报告区分实测与预测

费用报告 MUST 区分标准公共托管计算、私有仓库包含用量、存储、实际耗时与未来估算；MUST NOT 在未迁移产品任务时宣称节省了产品额度。

#### Scenario: 合成验证完成后报告

- GIVEN 仅公共合成任务实际运行，产品执行方式保持原样
- WHEN 记录时长、存储和费用
- THEN MUST 报告实际 run 数据及缺失的账单权限或数据
- AND MUST 说明产品迁移数为 0，合成自测未带来已验证的产品额度节省
- AND MUST NOT 将当前 Public 产品仓库虚构为已有私有付费基线
- AND MUST 将未来 Private 情形和托管 Runner 优化建议单列，未实施项不得计入已节省金额

### Requirement: 后续产品迁移和可见性变更独立处理

任何未来产品公共执行、产品 CI 修改或 Private 切换 MUST 作为独立后续事项；MUST NOT 因本轮合成成功自动实施。

#### Scenario: 交付本轮验证

- GIVEN 最新 OpenSpec 已严格校验且公共合成验证已有报告
- WHEN dot 提交执行结果
- THEN MUST 保持本轮未授权的产品源码接入、产品权限、发布与可见性修改未实施
- AND MUST 区分计划校验、合成实跑、产品源码保密和 Private 后复验的证据
- AND MUST NOT 将后续安全架构、闭源 dry-run 或自托管评估变成本轮隐藏完成条件
