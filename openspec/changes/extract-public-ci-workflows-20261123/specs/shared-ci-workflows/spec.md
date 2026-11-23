# Public CI Execution

## 需求：验证计算实际运行在公共仓库

系统 MUST 将目标产品验证任务的 runner job 放在公开 `emo_master_ci` 仓库执行，而不只是从私有仓库调用一个 reusable workflow。

### 场景：私有仓库提交触发公共 Runner

- GIVEN emo_master 是 Private，且 dispatcher 已验证提交来源可信
- WHEN 私有仓库发生受支持的 push、pull_request 或手动触发
- THEN dispatcher MUST 只向公共仓库发送固定 repo 标识、完整 commit SHA、必要 PR 元数据和 check_run_id
- AND 公共仓库 MUST 在自己的标准 Linux/Windows Runner 上执行实际测试
- AND 该 runner 的计算使用量 MUST 归公共仓库，不得误称为私有调用方的 Reusable Workflow 费用

## 需求：私有源码安全读取

公共 CI MUST 通过最小权限短期凭据读取指定私有提交。

### 场景：获取私有源码

- GIVEN 公共 workflow 收到合法 source SHA
- WHEN Source Reader 获取源码
- THEN 仓库标识 MUST 固定为 `jsdfhasuh/emo_master`
- AND SHA MUST 是完整且实际存在的提交
- AND Token MUST NOT 出现在 dispatch payload、日志、artifact、GITHUB_OUTPUT/GITHUB_ENV 或被测进程环境
- AND Token MUST 在测试开始前撤销或销毁
- AND checkout 后必须验证实际 HEAD 与请求 SHA 完全一致

## 需求：不可信代码隔离

公共 Runner MUST NOT 自动执行未信任的 fork PR 代码。

### 场景：外部 fork PR

- GIVEN PR head repo 不等于私有 emo_master
- WHEN 私有 dispatcher 判断是否启动公共 CI
- THEN 默认 MUST 拒绝自动 dispatch
- AND MUST NOT 使用 `pull_request_target` 执行 fork 源码
- AND 只有经过明确人工审查并进入受信任提交后才允许公共执行

## 需求：公开日志与产物安全

公共 workflow MUST 只输出公开安全的摘要。

### 场景：测试成功或失败

- GIVEN 产品测试在公共 Runner 上执行
- WHEN 生成日志和报告
- THEN stdout/stderr 与原始诊断证据 MUST 留在临时 Runner，不得上传公共 Artifact
- AND 公开日志 MUST 仅包含白名单状态、平台、分片、计数、提交 SHA 和通用失败类别
- AND MUST NOT 包含私有源码片段、工程数据、设备配置、内部路径、原始异常堆栈或凭据

## 需求：结果回传私有仓库

公共 CI MUST 将结果关联回正确的私有 commit。

### 场景：Check Run 回调

- GIVEN 私有 dispatcher 已创建 pending Check Run
- WHEN 公共测试完成
- THEN 独立 callback job MUST 使用最小 Checks: write 权限更新对应 Check Run
- AND MUST 校验 check_run_id、head_sha、external_id/request_id 与预期 App 身份
- AND MUST 根据 GitHub job result 决定 conclusion，不得信任测试代码生成的 PASS 标记
- AND 失败、取消、超时、未回调、SHA 不匹配 MUST NOT 产生成功门禁

## 需求：覆盖保留与私有发布边界

系统 MUST 将 12 个活动验证工作流逐项迁移到公共 Runner 或记录明确的私有发布例外。

### 场景：全量 CI 迁移

- GIVEN 当前 12 个活动工作流
- WHEN 迁移完成
- THEN 主 CI、Web CI、Display Session、Runtime 诊断、Windows 包验证等验证任务 MUST 在公共 Runner 执行
- AND Release 写入、签名凭据和正式私有发布资产 MUST 留在私有边界
- AND 不得公开上传产品二进制或原始诊断证据
- AND 4 个归档工作流 MUST 保持归档，不重新启用

## 需求：公共仓库安全与计费验证

公共仓库 MUST 使用标准 GitHub-hosted Runner、固定 Action SHA、受保护默认分支和最小权限。

### 场景：迁移后核对费用

- GIVEN 公共 CI 工作流已运行
- WHEN 对比迁移前后 GitHub Billing / Actions usage
- THEN 必须区分公共标准 Runner 免费计算与 Artifact/缓存/外部服务存储费用
- AND 必须报告实际迁移的 runner minutes，不得仅凭配置宣称节省金额
