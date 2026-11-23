# 完整实施任务

终点：12/12 产品计算在公共 GitHub 托管 Runner 执行，产品 Private、门禁与私有发布有效；产品仅保留短 dispatcher。合成不是交付终点，不评估自托管 Runner。

## A. 计划与基线

- [x] A1. 按产品 main 82a1aa6 复核 12 活动／4 归档、上下文耦合与外部 packager 固定版本，记录在 workflow-migration.md。
- [x] A2. 将方案纠正为完整真实产品迁移，采用公共 Secrets Token 直连；移除 synthetic-only 终点及外部控制平台。
- [x] A3. ChatGPT Web 对本次完整计划实际运行 OpenSpec 2.1.1 strict 和 git diff --check，均退出 0；本次提交 PR 评论记录准确内容与验证范围，不复用旧版本结果。
- [x] A4. dot 开工核对最新 main / PR head 与未提交修改；比较后续产品／workflow 差异，记录实际执行基线。

## B. 最小基础设施和权限

- [ ] B1. 按 preflight.md 只核实／创建 Public emo_master_ci，初始化默认分支；核实 dot 实际工作环境可使用已有登录或同一管理 Token；配置可信 workflow、控制任务 Environment 和固定 Action SHA，本轮不增加逐次人工审批。
- [ ] B2. 接收用户的一枚足够权限 Token；统一 EMO_CI_TOKEN，将同一值写入产品与公共 CI 的 Actions Secret。推荐限定本任务仓库；记录实际共享权限、到期日与轮换方式，不要求用户生成四枚凭据。
- [ ] B3. 生成 age 公私钥；公钥供产品 jobs，私钥仅 ci-control / release 并离线备份；完成两平台加密／可信解密和失败不上传明文验证。
- [ ] B4. 核实 GitHub 账户的 Private branch protection 能力与当前实际门禁；费用／权限缺口列明，不将未配置项假称成功。

## C. 调度、取源与结果协议

- [ ] C1. 实现唯一产品 dispatcher：原触发路由、元数据请求 Artifact、pending、公共 dispatch；不安装产品依赖，不等待重 CI。
- [ ] C2. 实现公共 preflight：核实 dispatcher run / workflow / Artifact、固定源仓库、源事件、head / base / merge / execution / check SHA、批准的公共 workflow 和 packager SHA。
- [ ] C3. 实现共享 EMO_CI_TOKEN 直接 checkout、persist-credentials:false、所需 Git 历史预取；修改 ci_scope 等显式源事件接口。
- [ ] C4. 实现最小输出捕获、age Artifact、可信解析与报告读取工具；密文来源和 digest 与 run / attempt / job 绑定。
- [ ] C5. 实现独立 Commit Status reporter、并发和旧结果拒绝；取消未回传保持 pending 阻断，支持原生重跑。
- [ ] C6. 在现有两仓库内完成最小 Linux／Windows 取源、状态回写、加密／恢复联调；必要合成文件就地生成，不创建额外仓库。当前 Public 成功不代表私有权限已验证，实际 Private 与 draft 验证由 G3 完成。

## D. 全部真实产品任务

- [ ] D1. 主源码 CI：scope、Linux/Windows 四分片、原生 Qt 与两平台集合汇总。
- [ ] D2. Web：两平台原自动／手动差异、Node / SDK、浏览器、wheel、HTTP/WS 与 owning Runtime。
- [ ] D3. Display Session：原 8 测试入口、5 Sessions、96-frame 契约与证据。
- [ ] D4. Runtime A18 诊断：原顺序、observer、JSON 与 WAL plain assertions。
- [ ] D5. Runtime capture 诊断：原资源边界、observer 与完整判断。
- [ ] D6. Runtime credit 诊断：原生 Qt、ABBA、产品 expected SHA 与原结论。
- [ ] D7. SQLite append controls：原 7 controls、产品路径和 JSONL。
- [ ] D8. SQLite failure 诊断：原 observer / controls / timeout 与错误证据。
- [ ] D9. Runtime stop 诊断：仅手动、原证据语义，inconclusive 如实报告。
- [ ] D10. Runtime Windows：source regression、构建与 archive/native/provenance 同目标 job，publish=false。
- [ ] D11. Designer frozen：PR head 语义、AST 版本、构建与冻结包验证同 job，不发布。
- [ ] D12. 联合 release：产品 v* tag、版本／源码／ONNX／Qt，Designer+Runtime 构建验证，独立 publisher 上传。

## E. 外部 packager 与契约

- [ ] E1. 在 python_build_scripts 新入口实现明确产品身份、build-only、同 job build+verify、age 输出和干净 publisher，保留其他项目旧入口。
- [ ] E2. 移除原始 upload-artifact / release-state 明文上传与 secrets:inherit，显式使用共享 Token；构建路径正常不调用发布 API，不虚构 PAT 权限隔离。
- [ ] E3. 保留原 Python 3.11 编排／3.10 产品及工具链，固定实测新 commit SHA，同步所有调用。
- [ ] E4. 更新 release workflow contract 和相关 scope／报告契约，保留真实断言，不删除测试掩盖迁移差异。

## F. 等价验收与切换准备

- [ ] F1. 按 acceptance.md 在同一受审产品版本完成真实测试、诊断、构建、包验证及旧路径对照；12 项中的实际私有发布上传在 G3 补齐，记录所有版本与证据。
- [ ] F2. 实测 PR 成功、失败、新 head、base 更新、取消／重跑；核实源 SHA、状态 contexts 和门禁行为。
- [ ] F3. Public 阶段完成联合 release 构建／包验证 dry-run（不上传产品明文包）；产品转 Private 后由 G3 在该产品仓库完成 draft Release 上传／读取，正式生产发布按已有授权执行。
- [ ] F4. 在 G3 的 Private 复验通过后完成 required statuses 切换及停用／移出旧 12 个重任务；产品仅留短 dispatcher，4 个历史归档不恢复。
- [ ] F5. 保存旧工作流、门禁、packager pin 与回滚命令；回滚继续使用 GitHub 托管 Runner，不自动改回 Public。

## G. Private 与最终交付

- [ ] G1. 盘点公开源码历史、Release、Packages、Pages、Fork 和已知副本，说明 Private 不撤回已下载内容。
- [ ] G2. 核实 Token 授权包含产品仓库、账户门禁能力和回滚准备；按已有授权将产品转 Private，不把尚未发生的 Private 验证设为此次转换的前置条件。
- [ ] G3. Private 后实际验证产品取源／请求读取／状态、PR／push／手动／tag 控制，并在 emo_master 的 ci-migration-validation-* draft Release 上传／读回验证包；完成后执行 F4，失败按回滚方案处理，不以切换前证据替代。
- [ ] G4. 记录公共真实计算、私有 dispatcher 用量、Artifact 存储和账户方案费用；当前实测与未来／迁移前预测分开。
- [ ] G5. 交付 12/12 矩阵、执行 SHA / run / attempt / 密文证据、读取方式、Secrets 名称与权限、未验证项和回滚说明。
- [ ] G6. 按实际执行者身份在推送对应 PR 下评论；本计划修订用 ChatGPT Web，dot 实施用 dot。未经授权不合并或发布。

权限缺失时完成仍可执行的代码、配置模板与验证，准确列出被阻断的具体动作。不能再次把整体交付缩成合成项目，也不能无依据宣称完整迁移已完成。


## 执行检查点：dot，2026-11-23

- 核对产品 main `82a1aa6`、计划 `4bd1e42`；未合并，未变更可见性。现有 12 项活动、4 项归档无修改。
- **B1/B2 部分完成**：公共仓库、README、`main` 保护、同名 Secret 已核对。[只读预检](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45203312880) 通过现有公共凭据的认证及两仓库受限 Secret 元数据读取；未读取 Secret 值，不能据此认定两值相同、写权限齐全或未来 Private 取源已通过。
- **C1/C5 部分完成**：[产品草稿 #22](https://github.com/jsdfhasuh/emo_master/pull/22) 提交 `15aa2bf` 按原 12 个工作流选择事件及可信 job/attempt 结论核验，21 项定向用例通过。提交 `cc68df5` 增加源码、Web、诊断、Windows 打包运行适配器；Web 4 项、本地诊断 3 项、分片集合 2 项定向模拟通过。未启用正式 dispatcher、Check 回写或生产 Artifact 管线。
- **D1 影子运行**：[四个真实产品 Source 分片](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45203988805)，公共工作流 `c1cbfef`，产品 `82a1aa6`。跨 job 原始清单的加密收集和两平台集合核对尚未验证。
- **D2 影子运行**：[Linux / Windows 自动和手动全量 / owning Runtime](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204466083)，公共工作流 `1bf05da`，产品 `cc68df5`。
- **D3–D5/D7–D9 影子运行**：[Display 及五项 Windows 诊断](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204631640)，公共工作流 `6efe88f`，产品 `cc68df5`。D6 credit 的旧架构分支条件不能借用新分支冒充，尚未执行。
- **D10/D11 及 D12 构建部分**：[Designer / Runtime build-only](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204870674)，公共工作流 `17d8e7b`，产品 `cc68df5`、打包器 `91766f6`。未执行正式标签发布、签名、Release 写入或私有资产回读。
- 以上影子运行使用已公开产品提交，不注入 PAT、不公开上传原始产物、不替代旧门禁。启动和本地模拟均不算已迁移通过；队列内和未独立核验的条目保持未完成。合并最终 PR、正式发布、转 Private 仍需用户明确确认。
