# Complete Public Product CI

## ADDED Requirements

### Requirement: 全部产品计算使用公共托管 Runner

系统 MUST 将 12 个活动产品工作流的测试、诊断、构建、包验证、汇总与发布 job 迁移到公共 emo_master_ci 的标准 GitHub 托管 Runner；MUST NOT 引入自托管 Runner。

#### Scenario: 完整迁移验收

- GIVEN 用户要求完整迁移并排除私有 Runner
- WHEN 本变更宣布实施完成
- THEN MUST 提供 12/12 真实产品任务的公共运行证据
- AND MUST NOT 以合成测试通过替代产品迁移
- AND 产品仓库 MUST 只保留轻量调度控制任务，其计算开销 MUST 单独披露
- AND MUST NOT 用私有调用方的 reusable workflow 冒充公共计费

### Requirement: 公共 Secrets Token 直接取源

公共产品 job MUST 使用仅产品仓库只读权限的 fine-grained PAT 拉取明确完整 SHA，产品源码 MUST NOT 提交到公共 Git 历史。

#### Scenario: 获取真实产品源码

- GIVEN 公共可信工作流已取得经过核实的产品执行描述
- WHEN checkout 指定产品版本
- THEN MUST 固定产品 repository 和完整 execution SHA
- AND MUST 使用 persist-credentials: false，Token 不得进入产品测试环境、输出或产物
- AND MUST 预取原 scope 所需 base / before 提交和必要 Git 身份
- AND MUST NOT 将公共 github.sha 当作产品 SHA

### Requirement: 可信轻量调度

产品 dispatcher MUST 只处理元数据、pending 状态和公共调度，不执行产品测试／构建；公共端 MUST 验证请求来源。

#### Scenario: 产品提交或 PR 触发

- GIVEN 产品事件符合某个原工作流的触发条件
- WHEN dispatcher 生成请求 Artifact 并触发公共 workflow
- THEN MUST 保存原事件、head / base / merge、suite、固定 CI / packager 版本和源 run 身份
- AND MUST 将请求绑定到正确公共 run，不在私有 job 等待重任务完成
- AND 公共 preflight MUST 核实固定产品仓库、dispatcher workflow、Artifact 所属 run 和当前产品身份
- AND MUST NOT 接受任意输入仓库或未经核实的源地址

#### Scenario: 元数据 PR 调度

- GIVEN dispatcher 使用 pull_request_target 获取目标分支可信控制逻辑
- WHEN 处理 PR 元数据
- THEN MUST NOT checkout 或执行 PR 内容
- AND 未信任 fork MUST 在明确批准具体 SHA 前不进入持有产品源码权限的执行链路

### Requirement: 原事件与平台语义保留

迁移 MUST 保留原 12 项的分支／路径过滤、手动输入、并发策略、平台、测试集合、断言及诊断语义。

#### Scenario: 公共 dispatch 执行原 PR 或手动任务

- GIVEN 公共事件均为 workflow_dispatch
- WHEN 选择 scope、Web FULL_WEB_LOGIC、诊断或包验证路径
- THEN MUST 使用原产品事件决定行为
- AND 主源码 PR MUST 保留 test-merge 执行，冻结包 PR MUST 保留 head 执行
- AND MUST 保留 Windows 原生 Qt 和原有桌面要求
- AND MUST NOT 将仅手动诊断改为全部 PR 自动执行，或将 inconclusive 写成通过
- AND 4 个历史归档工作流 MUST 保持归档

### Requirement: 原始证据加密公开传递

公共执行 MUST 保留完整原始证据并在上传公共 Artifact 前使用成熟公钥加密；公开可见输出 MUST 只包含允许的运行摘要。

#### Scenario: 测试成功或失败后留证

- GIVEN 产品 stdout / stderr、JUnit、诊断、截图或包已生成
- WHEN 上传公共 Artifact
- THEN MUST 只上传完整加密后的允许文件，详细名称与 manifest 放在密文内
- AND 产品执行 job MUST 只取得加密公钥，不取得解密私钥
- AND MUST NOT 公开上传明文源文件、报告、产品包或源码 cache
- AND 加密失败 MUST 导致明确失败，不得回退明文上传
- AND 报告 MUST 说明密文公开可下载、保密依赖私钥安全

### Requirement: 汇总不得执行产品代码

持有解密私钥或状态写权限的汇总 job MUST 在独立 Runner 只执行固定可信控制代码，不执行产品脚本或 Artifact 内程序。

#### Scenario: 主 CI 分片汇总

- GIVEN 四个产品分片已生成加密报告
- WHEN 可信汇总器解密并核实报告
- THEN MUST 绑定 run / attempt / job / Artifact ID 和 digest
- AND MUST 验证同平台 collected 相等、selected 互斥、并集完整及选中项确实开始执行
- AND MUST NOT 仅凭测试总数或 PASS 标记宣告完整
- AND MUST 保留失败、跳过与未执行的区别

### Requirement: 产品状态准确回写

系统 MUST 通过独立 reporter 使用产品 Commit statuses 写权限，将真实公共结果回写正确产品门禁 SHA。

#### Scenario: 产品 PR 结果完成

- GIVEN head / base / test-merge 与公共 run 映射已核实
- WHEN reporter 处理最终结果
- THEN MUST 根据 GitHub API 的预期 jobs 与必需证据决定状态
- AND MUST 使用统一 check SHA 和不与旧 Check Run 冲突的 context
- AND MUST 分别记录 suite 的 execution SHA，不能混淆 frozen head 和 source merge
- AND MUST NOT 让旧 head / base / request / attempt 的结果放行新组合
- AND MUST NOT 把取消、超时、缺 job 或缺证据写为 success

#### Scenario: 整条运行被取消

- GIVEN dispatcher 已写入 pending 而 reporter 没有执行机会
- WHEN 用户查看门禁
- THEN MUST 保持未满足状态并说明需重跑
- AND MUST NOT 声称 always 能保证回调完成
- AND MAY 使用 GitHub 原生重跑恢复，不要求新增外部对账平台

### Requirement: 构建验证与发布权限分开

全部构建和发布计算 MUST 在公共标准托管 Runner；发布写权限 MUST 仅进入不运行产品代码的独立 publisher。

#### Scenario: 产品包验证与正式上传

- GIVEN 某目标需要构建并执行冻结包验证
- WHEN 运行该目标流水线
- THEN MUST 在同一产品执行 job 完成构建及运行验证，再加密产物
- AND publisher MUST 解密确定产物、核实产品 SHA / tag / 版本 / digest 后上传明确目标 Private Release
- AND MUST NOT 在 publisher 中运行产品程序或构建脚本
- AND build MUST NOT 取得 Release 写 Token 或共享解密私钥
- AND Designer 与 Runtime 联合发布 MUST 等待两个目标的原验证成功

### Requirement: 外部打包工具显式迁移

系统 MUST 修改外部 packager 的受控新入口以支持显式源身份、加密输出与独立发布，并固定实际新提交。

#### Scenario: 公共仓库调用 packager

- GIVEN 原固定 packager 会上传明文报告与包
- WHEN 改为公共 caller
- THEN MUST 替换这些上传和 caller 默认 repository / SHA 假设
- AND MUST 移除向构建传播的 secrets: inherit 与发布 Token
- AND MUST 保留原工具链、产品验证及其他项目兼容
- AND MUST 更新相关 workflow contract 测试而非删掉约束

### Requirement: 源码风险与费用如实说明

方案 MUST 承认可信公共 job 会读取源码，且同 VM 恶意代码外传风险不能由 Token、日志捕获或加密 Artifact 消除；费用 MUST 按实际组成报告。

#### Scenario: 评估安全和节省

- GIVEN 标准公共 Runner 执行真实产品任务，私有侧只有 dispatcher
- WHEN 交付迁移报告
- THEN MUST 说明剩余供应链／恶意代码风险及维护者信任边界
- AND MUST NOT 要求无限的绝对无外传证明或退回合成阶段作为终点
- AND MUST 分列公共计算、私有控制用量、存储和账户方案费用
- AND MUST NOT 将当前 Public 产品仓库虚构为已有私有付费基线

### Requirement: 完成切换并可回滚

系统 MUST 在真实产品全量验收后切换门禁和执行位置，按已有授权完成 Private 相关动作并单独复验。

#### Scenario: 从影子运行切换正式链路

- GIVEN 全部迁移路径已获得同版本对照和真实私有权限 dry-run 证据
- WHEN 切换产品 CI
- THEN MUST 核实 Private 分支保护能力，配置新 required contexts 后停止旧产品重任务双跑
- AND MUST 保留旧工作流及门禁的可恢复版本
- AND MUST 对 Private 后的 PR / push / 手动 / tag 与发布控制另行验证
- AND MUST NOT 把计划校验、合成自测或切换前结果写成最终产品迁移完成
