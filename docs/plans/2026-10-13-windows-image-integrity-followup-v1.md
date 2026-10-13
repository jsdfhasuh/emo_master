# Windows 图像完整性诊断与最小修复计划

- 日期：2026-10-13；版本：v1。
- 状态：用户已批准实施；P0/P1 取证及 P2 诊断／确定性测试已完成，候选本地回归通过。未证明需要修改产品算法，P3 不强行制造修复。精确候选提交的 Actions 状态以交付结果及 run 为准，不继承旧 SHA 的通过。
- 目标：优先确认 Windows 是否仍存在真实源图像不完整，区分捕获、传输、live 合并和 GUI 绘制；仅对证据证明的缺陷作局部修复。
- 既有分支：`agent/runtime-workflow-architecture-v1`；PR #1 不自动合并、发布或部署。
- Debian 已验收基线：`5410737a561e1f192ee9a081b3f208c9b27d9886`。
- Debian 证据归档：`c891b8a69d1d0f4ddbcb4d83ed90b54eac82d189`，父提交为上述基线，仅增加验收归档。
- Windows 核查、必要修复和精确 SHA 验证由 Codex 负责；不要求 dot 再重复整套 Debian 验收。

## 1 已成立的结论与未关闭事项

固定 5410737 的 Debian 五个真实 GUI DisplaySession 生命周期及原 ABBA 四组全 1–96 编号链路已通过。此次核对已确认归档、包内清单和 1022 个源码清单文件与固定 Git blob 一致。结论不外推到当前工作区的未提交修改或后续提交。

| 事项 | 当前结论 | 本计划处理方式 |
| --- | --- | --- |
| W01 五路订阅配额冲突 | 修复后在本轮 Debian 五路场景通过；旧失败保留 | 不预设需要继续改订阅容量或关闭逻辑 |
| Debian 当前 A02 | 四组源结果、双消费者接收／解码／live、双窗口 commit／paint 全 96 完整 | 不删预热，不要求 dot 为交接再次全跑 |
| 旧 8c A02 缺号根因 | 旧决策瞬间字段不足，历史因果证明仍有缺口 | 不用新通过或确定性注入替代旧证据 |
| 历史 Windows 捕获额度拒绝、导出超时 | 历史失败及部分证据不足继续保留 | 独立追踪，不与 GUI latest 替代混为一谈 |
| 直接关闭后的 supervisor ownership | 缺少关闭后直接字段采样；没有确认新的泄漏 | 非阻塞可选补证，不预设产品修复 |

### 已保存的 Windows 起点

以下是本地保留的 Actions 结果与产物，不是本轮实时查询结果。实施阶段首先刷新远端运行身份和状态，保留旧缓存，不覆盖首次失败。

| 精确 5410737 的运行 | 已保存状态 | 证据边界 |
| --- | --- | --- |
| [捕获诊断 38347057040](https://github.com/jsdfhasuh/emo_master/actions/runs/38347057040)，job 115130797996 | FAIL；observation COMPLETE、measurement INVALID、performance NOT_ASSESSED | 四组源图像、两个网络消费者接收／解码均 96/96；失败不能直接归因为源捕获丢图 |
| [Windows 原生专项 38347060373](https://github.com/jsdfhasuh/emo_master/actions/runs/38347060373)，job 115130811768 | success | 原生 Qt 自动化，不是人工桌面或生产设备验收 |
| [CI 38347057046](https://github.com/jsdfhasuh/emo_master/actions/runs/38347057046) | Windows 与 Linux job 均 success | 不替代独立捕获诊断结论 |
| [冻结包验证 38347064093](https://github.com/jsdfhasuh/emo_master/actions/runs/38347064093)，verify job 115132184774 | success | no release 验证，不是发布或部署 |

已保存诊断中：trial0 两消费者的 ordinal68 为 SUPERSEDED，双窗缺 commit／paint 68；trial1 双窗缺 paint 60、94；trial2 完整；trial3 双窗缺 commit／paint 38。这些是定位入口，不能将其余缺号未经核对一概解释为相同原因。

本地原记录入口为 `build/w01-repair/actions-head-*-result.json`、`actions-head-credit-audit.json` 和 `actions-head-credit-artifact/`。Debian 离线审阅记录为 `build/5410737-debian-review/independent-readonly-audit.json`；这些 build 文件不是新的产品运行，也不应成为交付后唯一可取得的证据。

## 2 范围与禁止事项

### 本轮纳入

1. 精确 SHA 的 Windows Actions、原始失败日志、脱敏产物及源码身份核对。
2. 逐编号关联源结果、导出、双网络消费者、live 决策和两个 Qt 窗口的显示记录。
3. 针对已确认缺陷的确定性复现、局部修复及相关回归；若无产品缺陷，可交付零产品改动结论。
4. 有必要时补充有界、只读、不会主动完成清理的诊断；关闭后 ownership 单独作为可选补证。

### 本轮不做

- 不把本地 operator_debug、workflow_debug、工程迁移、连线或 PLC 工作混入本轮；保留所有无关未提交文件。
- 不增加捕获配额、display 工作线程、队列上界或内存限制来换取通过；不提前释放尚被实际 owner 使用的资源。
- 不增大原期限，不删 1–8 预热，不排除缺失编号，不放宽原断言，不重复重跑择优。
- 不把完整结果审计改成必须由实时 GUI 逐帧绘制；也不把 live latest 契约作为源缺图的免责理由。
- 不静默修改原 ABBA 判定。若原全帧显示要求与 latest 契约存在差异，保留原 FAIL／INVALID，并列报告各层结论；调整验收契约需另行确认。
- 不扩展为 D01–D08、SQLite/O20、32 Job／64 流性能、真实设备、全 DPI、长时间稳定性或可靠永久图像归档项目。
- 不 force push、改写分支历史、合并 PR、创建 Release、部署或连接生产设备。

## 3 分层判定与验收标准

| 层次 | 必须核对的证据 | 判定方式 |
| --- | --- | --- |
| 源结果与捕获 | 原始 1–96、全部预期 source、终态、不可用原因、owner／result／调用身份 | 基线负载完整性要求为每个编号完整；BUDGET_EXCEEDED、EXPORT_TIMEOUT 不得从分母扣除 |
| 导出与资产 | 实际 acquire、snapshot／export 边界、释放事件、超时阶段、图像 owner 与 hash | 有明确且相容的所有权；不重复释放、不提前复用、不以终态替代真实退休 |
| 两个独立网络消费者 | 各自 received／decoded 编号、失败原因、Runtime／Job／scope／invocation／result 身份 | 不能以一个消费者或一个窗口完整替代另一条链路 |
| live latest | APPLIED／SUPERSEDED 等决策、generation、high ordinal／key、expiry、先后事件 | 拒绝旧结果倒灌；每个未应用事件有当时的直接证据，不能统一标成正常替代 |
| Qt commit 与 paint | 各窗口按 resultKey 的集合覆盖、commit／paint 时点及实际 hub 拓扑 | 原全 96 要求独立保留；重复事件不能补分子；允许报告契约行为正确但原全帧要求未满足 |
| 生命周期 | admission／retirement 配对、在途工作、回调、reader／lease／exporter／Session 退出 | 取消请求不等于退休；字段、进程和线程的采样时点分开 |

当前 ABBA 是静态输入，hash 相同只能证明一致性，不证明跨 ordinal 新鲜度。变化内容及逐像素、owner 校验使用已有双消费者 96 图像契约测试；需要新增变化负载时另建具名实验，不替换原 ABBA。

跨进程时间使用原时钟来源和明确边界；不能把包含时间直接说成纯锁等待、GIL 占用或磁盘耗时，也不能仅凭峰值内存认定触发了内存上限。

## 4 阶段和退出条件

### P0 固定身份与保存已有证据

- 重新记录本地 HEAD、dirty 清单、远端分支 HEAD、与 5410737 的差异；新提交若改变相关代码，单独列影响，不继承旧版本验收。
- 只读刷新上述四个 workflow 的 SHA、run、attempt、job 和结论，取得可用日志／artifact，记录哈希。过期或未上传的原始 trace 标为不可取得，不用新运行伪装恢复。
- 避免覆盖共享工作区；需要执行产品测试时使用经检查可复用的干净隔离检出，记录源码身份及干预清单。
- 新证据使用独立目录，按 SHA／run／attempt 命名；原首次失败保持只读。

退出条件：形成版本与运行索引，知道实际调查的是哪次失败。没有必要时不启动新的 ABBA。

### P1 逐编号失败分类

- 先离线审计 38347057040 的 68、60、94、38，并回查其余全部 1–96 编号，避免只解释已知样本。
- 对齐源、导出、接收、解码、live、commit、paint；两个窗口共享 consumer0 的事实必须保留。
- 将问题分为：真实源不完整、传输／解码异常、live 决策异常、GUI 提交／绘制异常、符合契约的替代、观测不足、环境／工具失败。
- 对更早额度拒绝和超时另建历史问题表，标明仍缺哪些因果字段；一次完整不能证明历史根因已修复。

退出条件：每个当前缺号有证据支持的分类，或明确缺少的最小证据。若没有产品缺陷，不进入强制修改路径。

### P2 必要时补最小诊断与确定性复现

- 只有 P1 无法区分关键假设时才增加诊断；优先使用现有 credit、export、asset、live 和 retirement 事件。
- 缺少额度持有者证据时，记录实际 acquire／release、lane／owner／调用身份和失败时状态，避免只记录总量或总耗时。
- 构造最小事件闸门／故障注入，分别验证正常释放、超时、异常、取消、重复收尾、旧结果迟到；不靠任意 sleep 或更长等待制造通过。
- 保留修复前首次失败；明确注入实验不等于原负载性能复验。
- 对只读观测增加有界／脱敏／无副作用检查；原报告 1 MiB 上界、原读取预算不扩大。原始数据安全不足或超界时保留拒绝原因，不静默截断。

退出条件：有可复现缺陷及因果证据，或有充分理由判为契约行为／证据不足。后两者允许停止产品修改，报告原状态。

### P3 仅针对已确认缺陷局部修复

- 捕获问题优先检查实际持有与归还、导出阶段和错误传播，不预先规定增加缓存、阻塞生产者或强制逐帧显示。
- live／GUI 问题仅在发现违反已确认契约时修改；符合 latest 的合并不能为全帧指标改成无限积压。
- 每处修改配同一缺陷的修复前失败和修复后通过证据，保留相邻安全断言。
- 若只需补诊断或没有必要改代码，分别交付“诊断增强”或“无需产品修改”，不得宣称捕获根因已修复。

退出条件：改动必要性、涉及模块、复现和回归一一对应，无无关重构。

### P4 精确候选版本回归

- 定向检查捕获 lane 生命周期、mailbox 恢复、多图／多路捕获、导出错误、额度归还、结果关闭失败、订阅与 live 决策。
- Windows 原生 Qt 自动化检查五真实 Session、逐 tab／快切、共享 hub、断流恢复、单停四路继续、主路重启、全停、运行中直接关闭；与 offscreen、人工桌面分开记录。
- 已确认候选修改需要原负载验证时，对该 SHA 执行一次原 off/on/on/off ABBA：1080p、计划 5 Hz、96 输入含 8 预热、两消费者与两窗口。不修改源码约定的 500 ms 服务端／资产期限和 550 ms 客户端期限、4 GiB 聚合提交内存约束及既有容量。
- 保存所有四组及失败产物。额外稳定性实验必须在运行前声明假设、轮数和停止条件，不得失败后无差别追加运行直到变绿。
- 对实际候选提交执行精确 SHA 的 CI／原生专项；涉及运行时或打包路径时再做 no release 冻结包验证。不用 PR 合并 SHA 或“src 相同”替代最终提交身份。
- 工作流默认检出分支时，触发前后核对实际 headSha；若分支移动，记录新身份，不把结果冠到预期旧 SHA 上。
- 本轮外的 SQLite／启动超时等失败如重现，独立保留并评估是否阻断；不删除测试或自动扩展成全产品维修。

退出条件：候选 SHA、实际测试范围、PASS／FAIL／SKIP／INVALID／NOT_RUN 与未关闭事项清楚；不得把一轮通过写成长时间稳定性保证。

### P5 交付和停止条件

- 若有必要修改，按用户既有要求仅提交本轮文件并正常推送到原分支；远端若有并行修改，先核对集成，不强推覆盖。
- 记录远端 SHA 与同步状态，跟踪该精确 SHA 的 CI，报告失败项，不能以推送成功替代验证完成。
- 若结论为无需产品修改，不制造修复提交；给出证据审阅结果、当前契约判断和历史 OPEN。
- 只有改动影响 Debian 行为时才给 dot 最小复验范围；否则明确“不要求重复整套 Debian 验收”。用户自行转交，不自动联系其他聊天。
- 不自动合并、发布或部署。计划阶段的完成不等于上述实施完成。

## 5 可能涉及的代码接点

下表仅是调查入口，不是预先批准全部修改。

| 位置 | 用途 |
| --- | --- |
| `src/emo_master/apps/runtime/presentation/collector.py`、`normal_capture.py`、`mailbox.py` | 捕获准入、lane／共享图像所有权和恢复 |
| 同目录 `exporter.py`、`service.py`、`assets.py`、`store.py` | 导出阶段、结果关闭、资产／租约、错误传播及退休 |
| `src/emo_master/clients/runtime/display_session.py` | 接收／解码／live 决策身份和迟到结果处理 |
| `src/emo_master/ui/presentation/` | GUI commit／paint 与共享 hub 链路 |
| `src/emo_master/apps/runtime/grpc_server/display_subscriptions.py`、`aio_entry.py` | 有界订阅、工作槽和实际退休；当前不预设再修 W01 |
| `scripts/r3_export_credit_report.py`、`r3_export_credit_control.py`、`r3_hosted_credit_guard.py` 及现有 trace 脚本 | 保留原诊断边界的分层取证与报告 |
| `.github/workflows/runtime-credit-diagnostics.yml`、`display-session-contract.yml`、`ci.yml`、`validate-windows-package.yml` | 精确提交的 Windows 证据及必要的 no release 构建 |

## 6 可选 ownership 补证

优先级低于 Windows 图像完整性调查，不阻塞已成立的 Debian 限定验收。

如需补齐：在固定 SHA 上只执行一次“五路运行中标题栏 X 关闭”。关闭前保存 Runtime 身份、五个 Job ID 和 supervisor 引用；真实 close 成功返回后、进程退出前，直接只读记录五个 `ownsJobResources()` 及采样时点。不要通过再次 status／reap／collect 或额外等待主动完成清理。

将内部字段快照、Session／exporter 退出、最终操作系统 PID 检查分别记录；旧最后一次关闭前 owns=true 不能作为关闭后值。若出现真实残留或 close 失败，保留首次现场，再转为独立产品缺陷；若均为 false，仅关闭观测缺口，不宣称修复了此前已存在的泄漏。

## 7 交付清单

- 精确 SHA／run／attempt／job 索引及 artifact 哈希。
- 全 1–96 分层覆盖表、双消费者身份核对、缺号分类和证据链接。
- 历史失败、新失败、契约行为、取证缺口、未验证范围分账。
- 必要时的最小补丁、修复前失败、确定性回归、精确候选 Windows 结果。
- 改动提交与远端 SHA，或“无需产品修改”的明确结论。
- 仅在必要时给 dot 的局部复验关键词，不重复已完成的整套 Debian 验收。

建议实施顺序：P0 → P1 → 按证据决定 P2／P3 → 必要的 P4 → P5。不能跳过分类直接进行“再维修一次”。

## 8 2026-10-13 实施记录

完整记录、运行索引及可离线重算的证据位于 `docs/testing/2026-10-13-windows-image-integrity/`。

- 已只读刷新 5410737 的四类 Actions，重新取得捕获诊断原 public JSON，SHA256 与旧缓存一致，未覆盖首次失败。
- 当前远端 c891b8a 与 5410737 仅差 Debian 验收归档；保留六个无关 tracked dirty 文件及全部无关 untracked 项。本轮在干净 c891b8a 隔离检出上注入具名测试／诊断文件。
- 已审计四组全 384 个 ordinal：源及双消费者 receive/decode 均 96/96；trial0/68 的 live SUPERSEDED 有直接证据；trial1/60、94 的 paint 缺口和 trial3/38 的 commit 缺口根因仍 NOT_PROVEN。
- 新增 stdlib-only、有界、只读离线审计器及 28 项纯数据测试；新增 2 项受控原生 GUI 时序测试，证明 hub latest 采样与 commit/paint 可分离，但不倒推历史缺号必为正常合并。
- 为旧 Windows CI 中 inspection 第二 Job 只有 FAILED、没有错误上下文的问题，补既有只读诊断消息，不改成功条件或原 deadline。
- 本地 Windows 原生相关回归：197 passed、1 skipped、39 subtests passed；新增 30 项属于其中，不能相加。旧 CI 四个失败入口各一次本地定向复核 4 passed，之后修改的诊断调用点及安全测试 7 passed；这些不是旧 CI 根因修复证明。
- protobuf drift、全源码／测试 Ruff 和 mypy（412 source files）通过。首次 sandbox 临时目录／复制工具错误独立保留，不记为产品失败。
- 新刷新 c891b8a 的 push CI：Linux success、Windows inspection Job FAILED，根因 OPEN。另一个 PR CI 实际检出 b003905（不是 c891b8a），Linux NG 图像 BUDGET_EXCEEDED、Windows 双图终态等待／自检失败；分别保留，不混成精确 SHA 结论。
- 原 ABBA 保持 FAIL / INVALID / NOT_ASSESSED。没有再跑 ABBA、没有修改 src、容量、期限、预算或原判定；冻结包本轮不重建。没有新增 Debian 人工操作，旧 ownership 可选补证仍 NOT_RUN。
- 仅提交本轮诊断／测试／workflow／文档；正常推送到原分支并核对远端 SHA。新精确 SHA CI／原生专项在提交后验证，未完成前不得写全部通过；不合并、发布或部署。
