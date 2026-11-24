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
- [ ] F3. Public 阶段完成联合 release 构建／包验证 dry-run（不上传产品明文包）；产品转 Private 后由 G3 在该产品仓库完成 draft Release 上传／读取，正式生产发布须另获用户明确确认。
- [ ] F4. 在 G3 的 Private 复验通过后完成 required statuses 切换及停用／移出旧 12 个重任务；产品仅留短 dispatcher，4 个历史归档不恢复。
- [ ] F5. 保存旧工作流、门禁、packager pin 与回滚命令；回滚继续使用 GitHub 托管 Runner，不自动改回 Public。

## G. Private 与最终交付

- [ ] G1. 盘点公开源码历史、Release、Packages、Pages、Fork 和已知副本，说明 Private 不撤回已下载内容。
- [ ] G2. 核实 Token 授权包含产品仓库、账户门禁能力和回滚准备；得到用户最终确认后才将产品转 Private，不把尚未发生的 Private 验证设为此次转换的前置条件。
- [ ] G3. Private 后实际验证产品取源／请求读取／状态、PR／push／手动／tag 控制，并仅在获批范围内于 emo_master 的 ci-migration-validation-* draft Release 上传／读回验证包；完成后执行 F4，失败按回滚方案处理，不以切换前证据替代。
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

### 影子运行增量

- [Web 首轮](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204466083) Linux Web 通过，Windows 三路在 recipe 步骤失败；失败未被忽略。产品 `f370753` 明确 Git Bash/PowerShell 启动并新增可公开的固定失败类别，5 个定向用例通过。[公共复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45205566980) 固定工作流 `17f364a`，真实 Windows 修复结论待验证。
- 同一产品提交增加 dispatcher 请求来源身份验证（4 项定向用例通过）；尚未接到生产 dispatcher 或状态回写。
- [诊断影子运行](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204631640) 的 Display Session 与 SQLite append Windows 作业已通过。分别保留其显示契约／诊断含义，其他作业结果未核对前保持待验证。


### 影子运行终态与修复排查

- [Web 修复复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45205566980)：Linux Web、Windows 自动 Web、Windows 手动全量 Web、Windows owning Runtime 均通过。同一公开 SHA 证据不替代 Private 验收。
- [主 CI 公共影子运行](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45203988805)：Linux 和 Windows 的 runtime／other 均在原检查步骤失败。公开摘要不包含内部失败详情；覆盖集合核验未验收。
- [最新产品原门禁](https://github.com/jsdfhasuh/emo_master/actions/runs/45205481370)（产品草稿 `f370753`）：四分片均未通过。Linux 和 Windows 都有设备包导入及资源生命周期问题；日志确认共享依赖计划的调用缺少 `sourceRoot`，以及设备包模型缺少被读取的 `workflows`。Windows 双图测试另外报 `EXPORT_TIMEOUT`；不得据此修改时限。上述实证来自产品自身 CI，不把公共影子任务一律归为同一原因。
- [诊断终态](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204631640)：Display Session、SQLite append 通过；A18、采集、SQLite failure、stop 失败；credit 未执行。
- [Windows 打包终态](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45204870674)：Designer build-only 构建与冻结包验证通过，Runtime build-only 失败；发布未执行。

### 修复提交与复跑

- 产品草稿 #22 的 `d0b2ba5` 修复设备包资源助手兼容问题，按节点路径契约更新了测试数据且未降低断言；Linux Python 3.10 的 39 项定向测试、9 项 TCP 模块测试、Ruff、mypy 通过。独立的 Windows 双图导出超时未修。[产品四分片复跑](https://github.com/jsdfhasuh/emo_master/actions/runs/45286899752) · [公共四分片复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45286937868)（工作流 `9f0bf64`）。
- 产品草稿 `9edd522` 修复 SQLite 诊断模块导入、补足冻结包标识并引入固定失败阶段。4 项定向诊断单测通过。[诊断复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45286827329)（工作流 `2f677a6`） · [不发布的打包复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45286826636)（工作流 `ce03137`）。以上新运行仍待验收，未变更旧门禁。

### age 证据与统一候选

- `3cfb1fe` 修复 Runtime 自测图片路径（改前 127 通过、1 失败、6 跳过；改后完整 Linux Runtime 源码回归 128 通过、6 跳过）；`e78d146` 修复导致两平台 other 分片阻塞的测试替身默认参数，保留提交断言，并新增异常弹窗检测。定向 Linux Qt 92 项通过，Windows 双图导出超时仍未认定修复。
- B3/C4 **部分通过**。公共源／诊断／打包影子工作流已暂存限量日志、JUnit／报告、阶段文件并经 age 加密；失败仍运行封装，上传路径固定单个密文。已取回 [GitHub Windows SQLite append 密文](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291043429/artifacts/14874960196)（产品 `3cfb1fe`、公共 `670493c`），核实外层摘要、run/attempt/repo/产品/工作流/任务和内层 4 个文件摘要；本地拒绝错密钥、错 run、改动 ZIP。Linux、失败运行、原始集合汇总、生产受保护解密钥与离线备份尚需单独验证，不能把一次成功视为整项通过。
- 统一候选产品 `e78d14692dd4558c7391e05f6b6e7eee523c4f22`：[源码](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291515095) 工作流 `4ee07dd968b326f2fa30970930e4ea5c3493491d)；[Web](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291511587) 工作流 `f3a054d9a994b90a07a3fdd5e5c2ec7ca71d8942)；[诊断](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291518025) 工作流 `6868b61930deeb613c4cb93d3f3dee09b075ef05)；[打包](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291529991) 工作流 `e84470994f3fb85b0221a8a5d8ff650ea9770f17`、打包器 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`。所有新结果仍待同版本复核；不借用旧 SHA 的通过结果。
- 状态写回控制器已用模拟 API 验证初版（尚未远端启用）；credit 缺真实旧分支触发，仍未验证。旧门禁未移除，未合并、发布或切 Private。

### Windows 修复和失败密文实测

- 已核对 GitHub 密文的来源、摘要与解密： [Linux other 失败](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291045754/artifacts/14875215916)（超时杀死 pytest，只有过程日志、未产生 JUnit）；[Windows Runtime 失败](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291047857/artifacts/14874701311)（认定为 runtime-source、134 项中 1 项失败，核实了 JUnit）；[Windows A18 失败](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45291043429/artifacts/14875105791)（原测试停在同一个 Qt 模拟弹窗；不是诊断断言通过）。以上数据为 `3cfb1fe` 的诊断证据，不算新候选结果。
- `baf307c` 按 Windows 真实路径比较工程内外，识别短名别名并保持工程外文件被拒绝，Linux 定向 10 项通过。[仅此修复的原生打包复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292268926)，公共工作流 `268fb0fe07fdd55d8acbead49e43d35c7b892789`。
- `2e46f24b88f8624937b4465ddaf4490cbe831fb7`：双路图像编码不再等待前一路的资产登记，保持 500 ms、信用、回调顺序与关闭约束。Linux 扩展 122 项通过，最终新用例 6 项通过，Ruff/mypy 通过。诊断保留原测试范围，单独加密原始观察 JUnit、WAL 对照、定向 JSON 和阶段。Windows 原生行为待验收。
- 新统一候选 `2e46f24b88f8624937b4465ddaf4490cbe831fb7`：[源码](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292689839) 工作流 `eb0bafc3a5662514dcaee54b16916d2e09a08926`；[Web](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292696280) 工作流 `7ae9b07c61ce28428eeea778f296eb79d45d5949`；[诊断](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292752311) 工作流 `2aae07dcf5f22a15ff9f9f141dec0e9d527d384e`；[打包](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292692908) 工作流 `c1e67004b93029d355e42ff249308ae9906b5276`。打包工具固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`。不得借旧版本判断新候选通过。
- [已有 PAT 的非门禁写入探针](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292560375) 返回成功，只在 `baf307c` 写入 `dot-ci-migration/token-preflight`，并未赋予或修改门禁。正式调度、起源绑定、自动结果返回、可信分片集合核对、credit、受保护生产解密密钥以及 Private 验证仍待完成。

### Windows、诊断恢复及调度演练

- 产品 `2e46f24b88f8624937b4465ddaf4490cbe831fb7`、公开源码 `eb0bafc3a5662514dcaee54b16916d2e09a08926`：[源码四分片最终全通过](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292689839)。Windows 与 Linux 分别用解密报告核对 9,795 个收集用例，原有跳过不算通过；Windows 原 500 ms 双图用例及 6 个新增用例都执行且通过。相同产品 [Web 4/4](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292696280)、[原生冻结包 2/2](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292692908)，打包工具固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`；打包密文已取回核对。
- [原 Stop 失败密文保留](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45292752311/artifacts/14876058260)：观测阶段在前序设备联调用例的 Windows 离屏 Qt 弹窗原生崩溃，未到 Stop 被观测测试；验证阶段因为没有 Stop 专用 JSON 正确拒绝通过，不改判定。产品 `fe00762` 只给该用例补已有离屏保护，原断言、测试范围和原生行为不变；Linux 定向通过。
- 原 A18、Capture、SQLite Failure 被 GitHub 30 分钟作业总时限取消，进入加密时已无完整执行预算，未生成 Artifact。新产品 `50a49af989afb6ca101cad4e4e3314d9cf50e769`（5 项定向模拟通过）及公开 `099e61db13f1b135e1b6ce1b8d8b0849783da296` 把原诊断按作业拆开并预装 age，给子进程及收证保留独立预算：[A18](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45299081757) · [Capture](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45299089291) · [SQLite Failure](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45299083821) · [Stop](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45299087300)。这些运行无打包步骤；不能用 `2e46f24` 的通过结果宣布新 SHA 全量通过。
- [独立 Windows 收证演练](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45298623941) 公共 `28f4bdc9aab95ff6f3a6a8d62d1dcc749267d75b`：主动失败及 Actions 实际一分钟步骤超时均保持失败；两份密文已下载、核对来源、解密并验证合成内容。外层作业被硬取消后不承诺还能上传；取消或缺证绝不算成功。
- [调度控制草稿 #23](https://github.com/jsdfhasuh/emo_master/pull/23) 配套 [公共调度试验草稿 #9](https://github.com/jsdfhasuh/emo_master_ci/pull/9)，仅定向产品临时提交 `966d6821357b0380d328ee0b1c86243826247cdd`、4 个本地约束测试通过。已查公共默认分支没有该工作流，GitHub 的默认分支前置条件未满足；未启动真实 dispatch，也未写入门禁。所有合并、正式发布和 Private 变更留待确认。
- [Credit Windows 原工作负载](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45299837841) 使用产品 `50a49af989afb6ca101cad4e4e3314d9cf50e769` 与公共 `3c6f1a3e2b4a1cf9eb8def282cf70811ea2272ad`，此独立可信分支运行原 ABBA 及报告校验；未伪造退役的产品架构分支，路由仍待验证。无打包步骤，证据取回待核对。

### 用户授权的真实调度试验

- 仅合并 [公共 #9](https://github.com/jsdfhasuh/emo_master_ci/pull/9)，默认分支合并 SHA `6284d57659b840f5b852b33540f524ff8c9c2ff7`，文件 blob `fd8ef49acd96b1f5b2b309cc7a35b1599cd4d6d6`，非发布标签 `ci-dispatch-pilot-20261124-v1`；控制分支 `056fe9c789e0fa6b98fbb1d50c76fca3d373cd8d` 的 [实际 GitHub 控制运行](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45302311141) 经 API 取得 [success](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45302337177) 与 [预期 failure](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45302341185) 两个不同的 attempt 1。均通过 SHA 绑定，同一个临时产品提交 `966d6821357b0380d328ee0b1c86243826247cdd` 的 `dot/dispatch-pilot/pass` 与 `dot/dispatch-pilot/fail` 已由独立读回核对状态与链接。无打包器或产品源码执行。对应正式调度、可信结果归集与 required 门禁未启用，不写作生产验收。没有合并任何正式迁移 PR、未发布、未转 Private。

- Credit [旧运行密文](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45299837841/artifacts/14877489426)（产品 `50a49af`）已回读：384 张图都在采集前判无效，控制及报告校验失败。两处诊断包装器未接收或转发新参数 `targets`，已在 `1e74f3a3c94d13e542a9780e725e845196e9d924` 修复；Linux 关／开观测各 12/12，专项 60 项通过，不改生产代码或阈值。[新 Windows 全量 Credit 复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45302868739) 公共 `ecfb941ec561b52ec8eda60eb73e2b0a85495737`，无打包器，未验收；旧架构分支路由仍另计。

### 正式事件准备与外部打包边界

- 实查默认分支：产品 main `82a1aa6d8499ee203ea8e452c2f016db37ea7b3f`，公共 main `6284d57659b840f5b852b33540f524ff8c9c2ff7`，外部打包 master `702fca523be0aae306763cde6f19742a96c4a0cb`。活动草稿与运行核对过；旧构建固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a` 不冒用新打包提交。
- [产品 #23](https://github.com/jsdfhasuh/emo_master/pull/23) `84c627ed885b99d2ba9d40152b5c616807080ee6`：只读、默认关闭的产品 push／同仓 PR／手动元数据入口和精确调度回执，9 项模拟及 Ruff 通过。结果规则 `3473f36d20b12145c2aab7990d659674e856e8e2` 需验证固定 run/attempt、所有必要任务和解密证据，拒绝过期和漏证，4 项模拟通过；没有实装受保护的线上 reporter。
- [公共 #12](https://github.com/jsdfhasuh/emo_master_ci/pull/12) `e96bb917764df43021b8788a85f0069e43ecfcde`：独立核对可信产品请求，接入原四分片及 age 证据；5 项模拟与 YAML/Python 校验通过。先只允许已核实的 main push 的源码非正式验证，默认关闭；手动入口未接通，同仓 PR 的 REST 绑定及代码授权边界尚待真实核对；当前公共执行无私有取源凭据，也没有启用门禁。
- [外部打包 #3](https://github.com/jsdfhasuh/python_build_scripts/pull/3) `23b6774c4f99c6ad7fffbf13b3ebbc8a453d7984`：专用流程拆开只读构建和受精准产品 tag 限制的写权限发布，不继承 PAT；旧通用和 VisionWorkshop 流程未改。既有回归 11 通过、2 跳过，验证模式模拟确认不发布；正式接入／真实 Private 构建和产物回读仍未验证。
- [Credit 复跑加密报告](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45302868739/artifacts/14879961466)（产品 `1e74f3a`）已验证四臂 384 张可用；一组序号覆盖仍不成立，超过旧 stdout 完整报告上限。产品 `5ade74707331f1f95df81615a7cfec43a7afccfd` 加入只加密的 8 MiB 文件报告及两项参数转发测试，旧 stdout 与判定不变。[下一趟 Windows](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45304316942) 公共 `3fc2c7c60f71f22ebf030588b194aa5baffe195e`，无打包，不能当作已绿。四项独立诊断仍需复核，旧绿不与新 SHA 拼接。

### 可信分支真实事件、取源与新版打包复验

- [临时 PR #24](https://github.com/jsdfhasuh/emo_master/pull/24) 不以 main 为目标。真实 [push](https://github.com/jsdfhasuh/emo_master/actions/runs/45305741019)（job `138149023344`）和 [手动](https://github.com/jsdfhasuh/emo_master/actions/runs/45305884004)（job `138149501236`）都固定 `698b4cce24840f16ca1fcdcbb0c56f6d43494944`；[普通同仓 PR](https://github.com/jsdfhasuh/emo_master/actions/runs/45306710545)（job `138152221614`）实际合并测试提交 `ebbc7c7e0e504c49d306371115a55a7749573c0d` 的两个父提交经 Git 验证，分别为基准 `698b4cce` 和 head `0c8a33ca`。原正文 `merge_commit_sha` 等值假设在首跑红，保留 [失败记录](https://github.com/jsdfhasuh/emo_master/actions/runs/45306515482)。普通 PR 成功不能替代 `pull_request_target`；后者没有从临时分支触发，正式跨仓库链路仍待验证。以上仅验证真实事件/身份，不运行真实产品测试、不写门禁。
- #12 的源码工作负载仍未显式传 `EMO_CI_TOKEN`（只有独立入口控制步骤用它）。已用 [独立认证检出](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45306041434) 验证同一个现有 Token、固定公开产品 `2e46f24b88f8624937b4465ddaf4490cbe831fb7`、公共 `e5a64d4c830412c69dfb4bb0fde8d4299da8ad86`，Linux `138150009814`/Windows `138150010101` 皆通过，未留下 checkout 本地凭证绑定。Private 后需证明匿名拒绝、隔离认证步骤、固定 SHA 检出并再确认后续无凭证；在确认前不更改可见性、不开放 Private 取源。
- 外部 `23b6774c4f99c6ad7fffbf13b3ebbc8a453d7984` 的 [实际只读可重用流程负例](https://github.com/jsdfhasuh/emo_master/actions/runs/45305920668)（job `138149613548`）按设计在 Private 守卫失败后跳过 checkout、构建、验证、上传，没有发布。独立 [公开源只读构建首跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45306131447) 两份密文均确认适配器旧打包 SHA 导致 checkout 比对失败，未进入构建。已仅允许两个审阅过的打包 SHA 显式提供，严格检查实际 HEAD；[复跑](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45307171030) 固定产品 `6a3efa0f512d6889b3f5f345685724d03f4dc54e`、公共 `4d8c1b21d8a354cd98563cd0f9584f442ee990ef`、外部 `23b6774c4f99c6ad7fffbf13b3ebbc8a453d7984`，真实构建待核验；Private 与写权限实测仍未做。
- [Credit 完整密文](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45304316942/artifacts/14880755851)：产品 `5ade74707331f1f95df81615a7cfec43a7afccfd`、公共 `3fc2c7c60f71f22ebf030588b194aa5baffe195e`，job `138144283629`。摘要、身份与内层成员均验证；四臂各 96 个完整唯一序号、共 384 张可用，观测覆盖均通过。不算旧 PR 路由或性能证明。
- 四份旧 Windows 观测失败密文已验证：A18 `14881475228`、Capture `14880692276`、SQLite Failure `14880947038`、Stop `14881395292` 均于 50 分钟子预算耗尽后完成收证，不是整项取消或加密失败。Stop 观测目标通过，但整套仍失败；Capture 证据验证成功，A18/SQLite 主验证因证据缺失正确不通过。SQLite 独立复用对照旧连接已关闭，修复只限对照脚本，本地 33 项及全部 5 个独立对照通过。新观察仅加测试名/停滞线程栈、不改变范围、顺序、断言和预算，固定产品 `6a3efa0f512d6889b3f5f345685724d03f4dc54e`、公共 `a1786ee9e5d6da4b911dbedb9201e27024edc263`、无外部打包：[A18](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45307146823) · [Capture](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45307148531) · [SQLite Failure](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45307143824) · [Stop](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45307150589)。不与之前的通过记录拼接。

### 2026-11-24: isolate Windows failures, align dispatcher, exercise public package caller

- **Windows diagnostic baseline (not passed).** Product `6a3efa0f512d6889b3f5f345685724d03f4dc54e`, public `a1786ee9e5d6da4b911dbedb9201e27024edc263`, no packager: A18 `45307146823`, Capture `45307148531`, SQLite Failure `45307143824`, Stop `45307150589`. Four artifact archives were downloaded, outer and inner SHA-256 verified, and decrypted. All four observed suites exhausted the 50-minute budget, rather than GitHub cancelling the jobs. SQLite's independent controls completed 5/5 and A18 WAL control passed. Capture's target evidence validated. Three target validators still lacked full evidence. The canvas node appeared failed in both Capture and SQLite, but the prior 256 KiB records are not evidence of the active stage at the deadline.
- **Narrow Windows experiment (passed individually, no root-cause claim).** [Run 45329089113](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45329089113), public `8e243fa4bc7e381c8d7f005dc3aee96c90c53022`, same product and no packager, Windows/Python 3.10/Qt offscreen: unchanged canvas node with the required normal Capture witness passed (job `138225617534`, age artifact `14890818253`, 2/2). Original canvas module with the same witness passed (job `138225617879`, age artifact `14891321512`, 14/14). Both artifacts independently recovered and exact testcase checked in decrypted JUnit. Initial `45328634301` selected no required observer target and ran zero tests; it provides no product assertion result.
- **Instrumentation (implemented; full timed test pending).** Product [PR #22](https://github.com/jsdfhasuh/emo_master/pull/22) `b7e1d0bb2d9fe46c894c1a15eb11828f9d9a2166` adds bounded independent pytest phase, failure and stack journals; Python 3.10 local tests include forced child termination. Public [PR #8](https://github.com/jsdfhasuh/emo_master_ci/pull/8) `4496702d8ccf7dee25d01396cee800c9d5df2c56` keeps full observed logs up to 3 MiB (marks truncation above the bound), requires journals and correct test completion before a green result, and gates full tests on deliberate activation. Only [Capture 45329601183](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45329601183) is running: public `d77e6097488118fbaae9f4d4f8e144dc8eaf2cca`, same product as #22, no packager; original test order, coverage and budget. Recover authenticated evidence and use unfinished stage plus stack/time before activating other suites.
- **Dispatcher identity (draft corrected, production not proven).** Product [#23](https://github.com/jsdfhasuh/emo_master/pull/23) `84c627ed885b99d2ba9d40152b5c616807080ee6` owns `.github/workflows/public-ci-dispatch.yml`. Public [#12](https://github.com/jsdfhasuh/emo_master_ci/pull/12) `229e4d9edcc365c1786520488f10a3b623e30c02` checks that path, not the public caller filename. Six local tests pass. Trusted synthetic [45329083263](https://github.com/jsdfhasuh/emo_master/actions/runs/45329083263) / job `138225597810` at `505b671ca9bb50c389bbe39c8d831fc1b37575b4` independently verified actual REST workflow ID `428620385`, blob `e560a05573c7b30f0c15b5aaf07253202ee9a451`, and exact artifact `14891271319`/digest. Synthetic branch did not call production ingress or reporter, and production main-only validation remains fail-closed.
- **Public-triggered Windows package and logs (two-target public fixture passed).** [External #3](https://github.com/jsdfhasuh/python_build_scripts/pull/3) `9657775338b0c4afa19ccab3a45792c146752203`, public caller `01903c320a46ef562277a37bd34d12ba27e03e3b`, approved already-public source fixture `2e46f24b88f8624937b4465ddaf4490cbe831fb7`, [public run 45329340482](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45329340482). Two Windows targets; exact allowlists for ciphertext-only packages and independently sealed logs. Six local transport tests, including age round trip and tamper rejection. Designer job `138226449608` (package `14891032920`, logs `14891052959`) and Runtime job `138226449961` (package `14891033886`, logs `14890904303`) succeeded. All four artifacts were downloaded, outer digests and age authentication checked, and exact binary SHA-256 entries checked against the two manifests. Runtime archive and native validation both reported PASS, bound to the three SHAs above; field acceptance was NOT_RUN. No PAT in the Windows jobs, no release call, no private source or publisher acceptance. The independent publisher uses product-job permissions, not a write token injected into the public build.
- **No cutover.** Production registration/dispatch and exact readback, private source boundary and publication, and final same-version 12-workflow acceptance are open. Public fixture or short-run success is not counted toward that final verdict. No default configuration, merges, release or visibility changes.

- **Capture startup regression isolated and corrected.** [45329601183](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45329601183) artifact `14890789318` downloaded, digest verified and decrypted: the observed invocation exited importing conftest (`ModuleNotFoundError: No module named 'emo_master'`), before collection or any phase journaling. It is not an observed test timeout. Local Python 3.10 with a src-layout fixture and an outside evidence directory reproduced failure using two argv tokens and passed with one `--option=value` token. Product `4949290ad73515bae8c388f9760412c84d295094` regenerated only the four affected argv entries and adds a regression test; three local tests pass. One corrective [Capture run 45337761457](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45337761457) / job `138253789111` is running against public `793e7d7bc09c40bc49abbf0e282e71b035659a77`, no packager. Existing order, coverage and 50-minute budget are unchanged. Original suite timeout and intermittent canvas failure remain unverified.

- **Cancellation proof with a separate checkpoint (real hosted result; not a product pass).** Public [#14](https://github.com/jsdfhasuh/emo_master_ci/pull/14), synthetic workflow `a7b5a354ff507c853a75944cedf3114b396c1610`, run [45339762355](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45339762355) / job `138260320003`, was actually cancelled during its wait by isolated same-branch successor `c44723bde1f042a85e4bcdd2ac270d7b941e47ba`. Original run's pre-cancellation artifact `14894783002` was downloaded after cancelling, verified against GitHub outer SHA-256, decrypted and its fixture checked. This complements the earlier real synthetic step timeout/intentional failure encrypted preservation (run `45298623941`). Hard kills cannot preserve still-unuploaded data. Public diagnostics [#8](https://github.com/jsdfhasuh/emo_master_ci/pull/8) `f52366aa91e1db207aadc3fd27c4bff96091ff0d` adds a distinct preflight checkpoint that binds to product `4949290ad73515bae8c388f9760412c84d295094` and states tests have not run; YAML/Python verified locally. It was not activated, and does not alter current Capture `45337761457`.
- **Source reporter draft (not registered).** Product [#23](https://github.com/jsdfhasuh/emo_master/pull/23) `7e1c1376efdb3b7114ece9c63860ff9aed48aa13`, public ingress [#12](https://github.com/jsdfhasuh/emo_master_ci/pull/12) `229e4d9edcc365c1786520488f10a3b623e30c02`: main-push-only reporter verifies exact source Sha, dispatch generation, approved workflow identity, digest-bound artifacts, exact worker run and evidence, and writes/readbacks only an owned marker. Local Python 3.10: 7 tests plus 12 subtests. No atomic GitHub status CAS; production still needs trusted evidence collection, serialized writer, event deployment, and required gate verification.
- **Private receiver draft (verified locally, not on a Private runner).** External [#3](https://github.com/jsdfhasuh/python_build_scripts/pull/3) `1cf0a8b26a5f52ff104e0675e3e6856266aa32bf`: isolate optional use of existing credential in GET-only control; receiver has no API/release token and checks exact provenance and all six delivery files. Local Python 3.10: 14 tests; actual public pilot metadata and two package artifacts admitted with receipt `pilot-only, publish=false`, not release authorization. Evidence was produced by public `01903c320a46ef562277a37bd34d12ba27e03e3b` / product `2e46f24b88f8624937b4465ddaf4490cbe831fb7` / packager `9657775338b0c4afa19ccab3a45792c146752203`; do not substitute the receiver implementation SHA for that producer SHA. No real private job, release, default configuration or visibility changes.
