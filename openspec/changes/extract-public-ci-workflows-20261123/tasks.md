# 任务清单

## A. 迁移前盘点

- [ ] A1. 固定当前 main HEAD、仓库可见性、工作流清单、活动 PR、分支和最近 CI 结果；保留当前 Actions YAML 与关键证据备份。
- [ ] A2. 核对 12 个活动工作流和 4 个归档 YAML 的触发器、权限、Secrets、checkout 源、Action 版本、测试入口、产物和 required-check 名称。
- [ ] A3. 盘点 Releases、Packages、Pages、Fork、公开 artifact、外部镜像和文档直链；记录 Private 切换不能撤回的公开历史风险。
- [ ] A4. 核对账户 Actions policy、Reusable workflow access、第三方 Action allowlist 和 fork PR 权限；记录当前 main 未启用 branch protection 的状态。

## B. 建立公共 CI 仓库

- [ ] B1. 确认并创建 `jsdfhasuh/emo_master_ci`（或用户确认的名称），设置 Public、默认分支、许可证和基础保护规则。
- [ ] B2. 实现最小 `python-source-ci.yml` reusable workflow；固定 checkout 调用方源码、Python 3.10、调用方依赖安装、runtime/other 双分片、测试报告上传和覆盖汇总。
- [ ] B3. 不把产品专属 scope 选择器、测试脚本、诊断观察器、Web 测试、包验证或发布逻辑复制到公共仓库。
- [ ] B4. 建立合成 Python fixture 和 self-test workflow，覆盖成功、失败、报告缺失、分片覆盖不完整等直接契约；测试数据不得来自 emo_master。
- [ ] B5. 核对公共工作流无任意 shell 输入、无 PAT/Secrets、默认只读权限，固定所有第三方 Action 版本；记录首个可用完整 SHA。

## C. emo_master 接入

- [ ] C1. 在本迁移分支将 `ci.yml` 的通用 Runner 编排替换为调用公共 reusable workflow；产品 scope 选择器及其测试仍在私有仓库。
- [ ] C2. 保留原有 required check 名称、PR/push/manual 触发、并发分组、跳过说明和手动运行完整覆盖行为。
- [ ] C3. 固定公共工作流完整 SHA；禁止使用 main、浮动 tag 或未审核分支。
- [ ] C4. 核对调用方 checkout 实际检出事件对应的私有源代码，不产生公共仓库源码副本、不向公共仓库上传测试 artifact。

## D. 双仓库联调与产品流程验证

- [ ] D1. 公共仓库合成 fixture 的 Linux/Windows Python 3.10 self-test 全部通过。
- [ ] D2. emo_master PR 完整源码检查：Linux/Windows runtime/other 分片全部运行，报告上传、下载、覆盖汇总通过。
- [ ] D3. emo_master 手动运行完整源码检查；仅文档/Web 变更路径明确说明未运行全量源码测试，required checks 不失效。
- [ ] D4. Web CI、Display Session contract、6 个 Runtime 诊断工作流分别保留原触发和产品测试能力。
- [ ] D5. validate-windows-package、runtime-windows 和 release-windows 完成不发布验证；Release 发布只在用户明确授权时执行。
- [ ] D6. 将 `release-windows.yml`、`runtime-windows.yml` 的 `secrets: inherit` 改为显式最小 Secrets 映射；核对 `python_build_scripts` 对私有 emo_master 的只读源码 token、Release 写入 token、artifact 往返及跨仓库手动触发路径。

## E. 闭源切换与收尾

- [ ] E1. 核对当前工作区/分支没有未处理的产品代码变更；备份 main、tag、Release 和关键 CI 证据。
- [ ] E2. 用户确认公开历史风险及新仓库名称后，将 emo_master 可见性改为 Private；不得删除或重写历史作为本计划的隐含动作。
- [ ] E3. 在 Private 状态重新执行 PR CI、手动 CI、Web CI 和不发布的 Windows 构建验证。
- [ ] E4. 如外部打包仓库独立手动构建需要读取私有源码，配置最小权限只读 `SOURCE_REPO_TOKEN`；Release 写 token 单独限制。
- [ ] E5. 更新 README/CI 文档，说明公共工作流版本固定方式、调用方权限、失败排查和产品专属工作流边界。
- [ ] E6. 记录迁移前后固定 SHA、实际运行链接、测试覆盖、未验证项及 Private 切换时间；只有所有关键链路通过才标记完成。
