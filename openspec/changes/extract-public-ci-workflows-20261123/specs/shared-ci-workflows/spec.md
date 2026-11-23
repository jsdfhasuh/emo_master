# Shared CI Workflows

## 需求：公共 CI 能力与产品源码分离

系统 MUST 将可跨项目复用的 GitHub Actions 编排能力放在独立公开仓库，并将 emo_master 的产品源码、产品测试、产品诊断和发布策略留在私有仓库。

### 场景：私有产品仓库调用公开工作流

- GIVEN emo_master 是 Private，且公开 CI 仓库中的 reusable workflow 已固定到完整 Commit SHA
- WHEN emo_master 的 PR、push 或手动事件触发源码检查
- THEN workflow MUST 在调用方仓库上下文中检出对应源代码
- AND MUST NOT 将产品源码复制或上传到公共 CI 仓库
- AND MUST 使用调用方的最小权限 token 完成只读源码测试

## 需求：保留现有源码检查覆盖

公共工作流 MUST 保留现有 Python 3.10、Linux/Windows 两个平台、runtime/other 分片、JUnit/失败证据上传和分片覆盖汇总语义。

### 场景：完整源码检查

- GIVEN 变更范围选择器判断需要完整源码检查
- WHEN 公共 reusable workflow 执行
- THEN Linux 和 Windows 的 runtime/other 两个分片 MUST 全部运行
- AND 每个平台 MUST 独立证明完整收集、分片互斥、分片并集覆盖完整测试集及实际执行集合完整
- AND 缺失报告、分片失败或不完整覆盖 MUST 使对应平台检查失败

### 场景：仅文档或 Web 独立文件变更

- GIVEN 私有产品的 scope 选择器明确判断无需完整源码检查
- WHEN 调用方跳过公共源码工作流
- THEN 现有 required check 名称 MUST 仍有明确成功的 scope/summary 结果
- AND 摘要 MUST 明确说明完整源码测试未运行，不得把 skipped 标记成完整测试通过

## 需求：公共工作流安全

公共工作流 MUST 固定到经过审核的 SHA，使用最小权限，不得接收任意 shell 命令或私有凭据。

### 场景：升级公共工作流

- GIVEN 公共 CI 仓库有新提交
- WHEN emo_master 计划升级引用
- THEN 必须先在公共仓库合成项目上完成自测
- AND emo_master 通过独立 PR 更新完整 SHA 并验证 Linux/Windows 结果
- AND 不得直接跟随 main、latest 或可变 tag

## 需求：产品专属验证留在私有仓库

Web、Display Session、Runtime 诊断、Windows 包验证和产品 Release 工作流 MUST 留在 emo_master。

### 场景：运行产品诊断或发布

- GIVEN 触发产品专属诊断、冻结包验证或 Release
- WHEN 工作流执行
- THEN 测试代码、证据解析和发布权限 MUST 保留在私有仓库边界
- AND 公共 CI 仓库 MUST NOT 获得发布 token 或产品诊断脚本

## 需求：Private 切换前验证

系统 MUST 在仓库改为 Private 前完成外部依赖、Actions policy 和历史公开内容检查。

### 场景：切换仓库可见性

- GIVEN 公共 CI 调用、python_build_scripts 私有源码读取、构建/发布、分支检查和备份均已验证
- WHEN 管理员将 emo_master 改为 Private
- THEN 必须在 Private 状态下重新执行 PR CI、手动 CI 和至少一个不发布构建验证
- AND 任一关键链路失败时不得宣告迁移完成
- AND 必须明确说明历史 Fork/Clone/下载内容无法由可见性切换收回
