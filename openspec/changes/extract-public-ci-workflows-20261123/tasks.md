# 任务清单

## A. 修订与基线

- [ ] A1. 核对最新 main、PR #21、12 个活动工作流、4 个归档工作流、CI 脚本及 python_build_scripts；更新实际审查 SHA。
- [ ] A2. 完成 OpenSpec strict validation；修正旧方案中“Reusable Workflow 即可免费”的错误架构。
- [ ] A3. 记录迁移前 Actions usage、Runner OS/minutes、Artifact/Cache storage 基线；没有权限读取账单时标注需用户提供。
- [ ] A4. 盘点公开 Fork/Clone/Release/Packages/Pages/Artifacts/外部镜像，记录 Private 切换不能撤回历史副本。

## B. 公共仓库与权限

- [ ] B1. 创建 `jsdfhasuh/emo_master_ci`，配置 Public、明确许可证、默认分支保护和仅可信维护者写入。
- [ ] B2. 建立 Dispatcher App：私有端私钥；仅授予触发公共 workflow 所需 Actions: write，以及必要的私有 Checks: write。
- [ ] B3. 建立 Source Reader / Check Reporter App：公共端私钥；私有 emo_master 仅授予 Contents: read、Checks: write；不授予发布或写源码权限。
- [ ] B4. 核对 App 安装范围和 token permission override；若无法做到 job 级最小权限，拆成两个更窄 App，不扩大权限凑合。
- [ ] B5. 公共仓库 self-test 不使用 Source Reader Secret；fork PR 的 self-test 与私有源码执行 workflow 完全隔离。

## C. 端到端调度与安全

- [ ] C1. 私有 dispatcher 为原始 commit 创建 pending Check Run，并向公共仓库 workflow_dispatch 发送完整 SHA、PR 元数据和 check_run_id；不传 Token。
- [ ] C2. 公共 workflow 校验固定 source repo、完整 SHA、PR/ref/check_run_id/request_id，拒绝任意 URL、仓库或命令输入。
- [ ] C3. 实现 Source Reader fetch action，在一个隔离步骤内获取短期 token、checkout 精确 commit、清除凭据；token 不进入 outputs/env/artifact/logs。
- [ ] C4. 测试 job 不继承任何 App 私钥、私有仓库 token、Release token；验证测试进程环境与 Git 配置没有认证残留。
- [ ] C5. 测试输出写入临时文件；只发布白名单摘要，不上传产品源码、原始日志、JUnit、诊断 JSON、截图或二进制包。
- [ ] C6. 独立 callback job 在干净 Runner 上按 GitHub job result 更新私有 Check Run；校验 SHA、check_run_id、request_id、App 身份；失败/取消/超时/缺失回调不得绿灯。
- [ ] C7. 同仓库受信任 PR 可自动触发；fork PR 不自动 dispatch、不使用 pull_request_target；验证手动审批路径。

## D. 迁移全部活动验证工作流

- [ ] D1. 主 CI：Linux/Windows、Python 3.10、runtime/other 四分片、pytest 报告覆盖汇总完整运行。
- [ ] D2. Web CI：Linux/Windows 后端、前端、浏览器、HTTP/WS、wheel 和软件 Runtime 集成覆盖完整运行。
- [ ] D3. Display Session contract：Windows 原生 Qt 与全部原有契约用例执行；原始 evidence 留在 Runner。
- [ ] D4. 六个 Runtime 诊断工作流全部有公共 Runner 对应执行入口；只输出脱敏摘要。
- [ ] D5. Windows package validation 在公共 Windows Runner 内构建、验证并清理，不上传公开包。
- [ ] D6. Runtime package 验证在公共 Runner 执行；正式资产仍由私有发布链路构建/保存。
- [ ] D7. Release workflow 拆分为公共 tag 源码/测试验证与私有发布；发布 token、签名和二进制资产不进入公共 CI。
- [ ] D8. 4 个历史归档工作流保持原样，不恢复、不复制到公共仓库。
- [ ] D9. 修正现有 `secrets: inherit`，按实际使用点显式映射最小凭据；核对 python_build_scripts 私有源码读取与 Release 权限。

## E. 私有仓库门禁与计费

- [ ] E1. 在 emo_master 配置 required Check Run，指定 GitHub App 来源，验证 PR merge commit 与 head SHA 绑定。
- [ ] E2. 验证 push、PR、手动触发、重复提交、取消、超时、失败和重试。
- [ ] E3. 在 emo_master 转为 Private 前，完成所有公共 Runner 真实源码测试、日志安全审查和权限审查。
- [ ] E4. 经用户明确确认后，将 emo_master 切换为 Private；不删除或重写历史作为隐含动作。
- [ ] E5. Private 状态下重新执行 PR、push、手动 CI、Windows 构建验证及 Release dry-run。
- [ ] E6. 对比迁移前后 Billing / Actions usage，统计公共标准 Runner 实际分钟、私有 dispatcher 分钟、Artifact/Cache 存储和实际节省额。
- [ ] E7. 更新 README、docs/ci/README.md、AGENTS/安全约定（仅必要范围），记录调度方式、权限、日志边界和故障排查。
- [ ] E8. 记录所有工作流状态、SHA、run 链接、CI 覆盖、未验证项和 Private 切换时间；全部关键门禁通过后才标记完成。
