# emo_master：F02 两项 P2 定点收尾与实机验收安排

日期：2026-10-19。状态：**计划已编写；本轮未修改产品代码、未运行新的软件或硬件测试。**

## 1. 决策与范围

**可以开始受控的实机分项验收，不必等这两项 P2 清零。** 相机连接、正常采集、曝光继承／覆写、设备排队、同图传递、PLC 只读等，可在设备及测试点已确认的环境中推进。PLC 写入只在现场负责人确认的测试地址／隔离工位验证，不把示例夹具中的地址当作现场写入许可。

两项 P2 限于 Designer 的资源上传客户端：一次断连后的请求收尾和批次进度报告。上传中断／取消恢复这一软件验收项待本补丁后关闭；不得把它们扩大成设备平台不能启动的依据，也不能把“允许开始验收”写成“已经通过实机验收或可以直接切换正式生产”。

| 项目 | 本轮约定 |
|---|---|
| 仓库／分支 | `jsdfhasuh/emo_master` / `agent/runtime-workflow-architecture-v1` |
| 本计划代码基线 | `a556a1d0a52a684153ebdfc500dd3dc90b4ef84d`；重新读取分支时 HEAD 与之相同 |
| 历史比较范围 | `51ccf699` → `a556a1d`，其中 `bcc37b7` 为 F02 实现；这里只修其两项剩余缺口 |
| P2-A | 上轮 F02-01：COMMIT 前上传断连，清理 CANCEL 未确认，客户端遗失原请求 |
| P2-B | 上轮 F02-02：后一资源在 LOOKUP／BEGIN／CHUNK 等早期阶段失败，已确认的批次结果未送达界面 |
| 不动的部分 | 服务端资源所有权、配置事务、协议和工程 schema、设备锁、周期继承、Dispatch/Join、PLC 夹具 |
| 暂缓风险 | F01 继续保持用户已接受的 `DEFERRED_NON_BLOCKING`，不重新作为开发／正常实机分项验收前置条件 |
| 环境 | 遵循仓库 Python 3.10、PySide2／Qt QWidget；本次代码开发及自动测试使用模拟设备与临时资源 |

沿用根目录 `AGENTS.md`。本文件是正常操作收尾计划的补充，不替换之前的证据，不增加新的 AGENTS 规则。实施时若 HEAD 已前进，先比较相关文件，保留本地工作，不回退或重复覆盖已完成的修复。

## 2. 本次重新核对的源码

下列位置均以完整基线 SHA 为准，路径共同前缀为 `src/emo_master/`。

| 文件／位置 | 当前事实 | 修改目的 |
|---|---|---|
| `apps/designer/services/runtime_client.py:448–482` | 已有逐请求分类、查询和显式取消；READY／CANCELLED 释放自己的 pending | 继续复用，不再建设第二套状态机制 |
| 同文件 `484–525` | LOOKUP 在内部 try 外；请求直到 COMMIT 前才进入 pending | 覆盖从首次可能创建远端状态到明确结束的整个上传寿命 |
| 同文件 `529–547` | 只有 COMMIT／STATUS 错误附带 `resourceResult`；之前的 CANCEL 异常被忽略 | 早期异常同样保留原身份和既有进度 |
| `apps/designer/ui/project_configuration.py:249–263, 337–348` | 已能展示 `resourceResult`，不具备它的异常只显示文字 | 优先让客户端返回完整结果；只补必要文案，不重写界面 |
| `apps/runtime/project_config/transfer.py:56–82, 110–193` | 已有原 transfer 的 STATUS／CANCEL 和在线引用保护 | 复用接口，不放宽删除保护，不增加服务端状态或新 RPC |
| `tests/runtime/test_targeted_resource_repair.py:21–31, 111–140` | 原 helper 手动预置 pending；异常用例主要在 COMMIT 注入 | 新反例必须从真实上传入口开始，不能人为替实现补登记 |

这次是定向代码重读与修复计划编写，不是新一轮全量审查，也没有重新运行此前报告的 17／60／285 项测试。

## 3. 共同修法：补齐原请求的开始与结束，补齐整个批次的异常出口

两项问题建议放在一个聚焦补丁中处理。主要改 `uploadProjectResources()` 的登记时机和异常边界，复用现有 `_recordResourceTransfer()`、`_queryResourceTransfers()`、`RuntimeClientError.resourceResult`。

### 3.1 P2-A：登记提前到 BEGIN，直到明确结果才结束

在只读 LOOKUP 和本地前置校验完成后，确定要上传时，**发送 BEGIN 前就将原 `(snapshot, resourceId)` 写入现有 `pendingResourceTransfers[transferId]`**。这一步只登记同一次尝试，不发送请求、不另建客户端持久数据库。

本地校验失败或 LOOKUP 失败，没有尝试 BEGIN 时，不制造远端上传 pending。每次仍使用一个 transferId，不因异常生成第二个 ID。

| 阶段／结果 | 约定行为 |
|---|---|
| 尚未 BEGIN，本地或 LOOKUP 失败 | 报告原错误及已经完成的批次结果；不登记不存在的上传 |
| BEGIN 确认成功 | 登记已在发送前完成；CHUNK、COMMIT 期间保持原 project／instance／transfer 身份 |
| BEGIN 收到明确的“尚未建立上传”业务拒绝 | 仅在已核实为修改前拒绝的错误上撤销本次登记，例如现有实例前置拒绝或新传输容量拒绝；保留错误，不伪报取消 |
| BEGIN 响应丢失／是否受理不确定 | 保留原身份，只用原请求核对或结束；不自动重发 BEGIN／COMMIT |
| COMMIT 前 CHUNK／本地读取／取消检查失败 | 保留原来最多一次的临时上传清理尝试；复用 CANCEL 分类，确认 CANCELLED 才移除本请求 |
| 清理 CANCEL 失败／响应未知 | 保留原身份、原上传错误及清理结果，网络恢复后原有查询和取消入口即可接管 |
| COMMIT 已发出但结果不确定 | 保持已实现的 STATUS 原请求核对；不因为超时自动 CANCEL 或重发 COMMIT |
| 明确 READY／CANCELLED | 仅结束该请求；PENDING／未确认错误不抹掉它，也不清空其他请求 |

“明确拒绝”的处理应以目标代码能证明的修改前错误为准，少量局部判定即可；**不能把所有业务异常、所有非网络异常或一次 UNKNOWN 都当作从未建立记录**。若无法确认，保留已有原请求核对，不为此新增跨重启幂等、后台补偿或通用错误类型体系。

继续使用当前 pending 数据结构和现有 `committing` 等局部控制；最多补 BEGIN 是否尝试等必要局部信息，不扩展为新的状态账本。发送前登记不意味着要增加正在传输时的新取消界面，本轮只接通已存在的待核对上传入口。

### 3.2 P2-B：所有阶段共享同一个批次异常出口

保留当前 `uploaded / cancelled / pending / errors` 四类结果，不换返回协议。

将每项的取消检查、LOOKUP、修复确认、BEGIN、打开／读取文件、CHUNK、COMMIT，以及失败后的收尾纳入统一异常汇总范围。批次 results 在外层创建，一直累积到本次调用结束。

采用现有接口的确定处理方式：

- 正常完成或已核对得到明确状态，继续沿用现有结构化结果。
- 发生需要抛出的异常时，保留原错误 code、message 和 cause，并附上**整个当前批次**的 `resourceResult`。普通异常可包装成现有 `RuntimeClientError`，不新增异常层次框架。
- errors 中标明当前资源及阶段，不能只附上一份错误为空的结果，使界面显示“0 完成、0 取消”却看不到失败原因。
- 清理成功取消第二项，也不能抹掉其最初失败，更不能丢掉第一项已经 READY 的结果。
- 同一请求的状态结果只汇总一次；重复查询不重复计完成。原错误与该请求已取消可以同时说明，不能把两者当成两个不同文件。
- 第一次无法继续的错误之后，仍可中止本批后续上传。**不要求失败后继续所有文件，不自动重传、不做断点续传。** 未开始的文件不放入 pending，提示“本批中止，未开始项尚未上传”。
- 下一次由用户明确点击上传，允许已有正常 LOOKUP 复用前面完成的对象，不重新发送这些文件的字节；这不是自动重放。

优先一个批次异常出口，加一个有限的当前请求收尾分支；不要为 LOOKUP／BEGIN／CHUNK／COMMIT 各复制一套状态处理。

### 3.3 与现有查询、取消及界面的衔接

已修好的 READY／CANCELLED／PENDING 分类保留。手动混合查询继续逐请求处理，不能让上传补丁倒退这条路径。

`ProjectConfigurationDialog.poll()` 已经能消费 `error.resourceResult`。先把客户端异常送完整，若原展示已经足够就不改界面；需要时只补阶段、部分完成以及批次中止提示。例如：

> 完成 1 项，取消 0 项，仍待核对 1 项。second.bin 分块上传中断，原请求已保留；可查询或取消。本批已中止，后续文件未上传。在线配置未改变。

网络恢复后查询得到 CANCELLED：结束对应 pending，现有上传按钮恢复。不要求重启 Designer／Runtime，不自动开启设备或修改配置。

当前“取消上传”实际面向待核对请求；名称可收敛为“取消待核对上传”，属于可随补丁处理的非阻断文案，不扩展为新的上传管理页面。

## 4. 不做的事情

不修改 Runtime 的资源 pin、持久对象选择或提交事务；不重新引入 F01 组合；不添加全局锁、持续文件监控、逐周期哈希、自动修复、无限重试、额外启动条件或必须停全线的规定。

不重做 N01 配置继承／ownsCycle、N02 流程包／Join、设备串行访问、PLC 夹具、协议或工程 schema。不为本补丁迁移 Qt、不增加依赖、不新建上传服务。

保留已有输入／大小／哈希校验和在线引用保护。所谓“彻底解决”是把这两条正常失败路径的起点、进度、收尾和后续操作接全，不宣称消除所有理论故障。

## 5. 代码与测试范围

| 文件 | 预计改动 |
|---|---|
| `src/emo_master/apps/designer/services/runtime_client.py` | 主要修改点：BEGIN 前登记、未确认清理保留原请求、批次统一错误汇总 |
| `src/emo_master/apps/designer/ui/project_configuration.py` | 仅在现有展示不足时做少量提示适配；不增加操作门槛 |
| `tests/runtime/test_targeted_resource_repair.py` | 从正常 helper 开始的两个直接反例和 BEGIN 边界参数化 |
| `tests/designer/test_targeted_resource_repair_ui.py` | 真正发起字节上传后的错误显示、查询／取消和再次上传 |
| `docs/testing/` 的新验证记录 | 固定实现 SHA、命令、原始失败／通过、未执行项及实机分项状态 |

已有 `AGENTS.md` 和收尾计划保持，不重复创建规则文件。旧审查报告、测试和日志保留，不覆盖历史证据。

## 6. 有限验证：六组目标，不扩展故障排列组合

以下均为**待执行**；参数化数量以实际实现为准，不用测试总数定义完成。

| ID | 触发 | 关键断言 |
|---|---|---|
| C-01 原断连反例 | 真实 helper 上传合法 600 KiB 文件，首块已接收；同一次断连导致第二块与一次 CANCEL 均不可达，再恢复 | 服务器仍 PENDING 时客户端保存同一 ID；恢复后真正发出原 STATUS／CANCEL；终态后移除 pending；未自动 BEGIN／COMMIT；用户可再次上传新文件 |
| C-02 BEGIN 登记边界 | BEGIN 已受理但响应丢失；另一个参数化分支为明确修改前拒绝；再检查 LOOKUP 失败 | 前者保留原 ID 并可收尾；明确拒绝／未 BEGIN 不留下假的待处理上传；不以异常类型猜测请求成功 |
| C-03 原批次反例 | first 真正完成上传；second 分别在 LOOKUP／BEGIN／CHUNK 或文件间取消检查中失败 | 异常／结果均含 first 的确认结果、second 的原因；first 字节不受损；未开始项无 pending；清理已确认与未确认都正确分类 |
| C-04 既有收尾回归 | 沿用 F02 的 READY／CANCELLED／PENDING、混合 STATUS、COMMIT 丢回复和 CANCEL 丢回复、在线引用拒绝测试 | 已修行为不倒退；原 project／instance／transfer 不替换；不自动 COMMIT；无重复计数或清空别人 pending |
| C-05 原生 Qt 实际入口 | 从上传按钮真正传新字节，不仅 LOOKUP 复用；展示部分完成和错误，恢复后查询／取消，再点击上传 | 界面完成数与调用结果一致，错误不被吞；按钮可继续；本地带修改草稿、非空 undo／redo 和磁盘保持；在线配置不变 |
| C-06 正常无故障对照 | 正常单文件／多文件、复用已有资源、后续显式配置写入 | 无新错误、无多余恢复／确认或启动限制；服务端完整性／引用保护仍通过；不自动开停任务 |

C-01／C-03 必须调用真实 `uploadProjectResources()`，不能像旧 pendingUpload 夹具那样手工填入 pending 后替代码证明登记正确。正常原件保持不变，不注入人为删文件、并发修复和提交交错。

软件测试的断连由 loopback／RPC 包装器有控制地模拟，别为了验证资源上传去拔正在生产的相机／PLC 网络。分层记录 helper、真实 Runtime／RPC 和 Qt 证据。桌面操作测试缺环境就写未执行，不用函数替身冒充原生 Qt。

建议沿用原五个相关测试模块复跑，不要求为两个客户端缺口重复跑全仓或全套硬件故障矩阵。新增补丁不改 PLC 生成器，不重新执行现场写入夹具。

## 7. 实施与提交顺序

1. 在干净的独立工作区或保留现有修改的工作分支确认固定代码；让原两个直接反例在基线上失败并保留结果。
2. 用一个聚焦补丁完成请求登记／收尾与批次异常汇总，优先复用现有方法，不强制拆多阶段重构。
3. 执行 C-01—C-06 的受影响验证，再记录固定修复 SHA 和未执行项。
4. 更新软件验收：只关闭有相应证据的两项 P2。实机验收按实际设备和项目分项记录，可以与修复并行。

**完成条件：**从正常 BEGIN／分块上传开始遇一次断连后，客户端仍能找到并结束原上传；后一文件出错仍显示前一文件确认成功；用户不用重启即可继续。相机、PLC 和生产配置不因补丁增加限制。

## 8. 现在如何开始实机分项验收

### 8.1 可以现在开始

使用已确认版本的源码或确实对应该版本的新构建，在既有测试工位先验证设备连接、实际参数回读和正常采集，再验证默认／覆写与多节点顺序使用。PLC 先读已确认的测试点；需要写入时，由现场控制负责人确认地址、值、影响和设备状态，在隔离工位验证。

资源在测试前正常准备完成，先比较工程与 Runtime 并明确采用哪份配置。正常单次／连续检测、同图多流程、停止与资源释放可逐项记录。每项写明实际 SHA／软件来源、设备型号、驱动或 SDK、配置及结果即可，不为本补丁加新的验收管理系统。

### 8.2 哪些暂不标通过

这两个上传异常／批次结果软件项等本补丁后补测，不应在未修版本上宣称通过。真实驱动特有时序、干净机器部署、全线联动等未验收内容继续按实际进度记录，不因为模拟测试数字而自动通过。

不需要为了开始实机读写与采集，先实现客户端跨重启恢复、资源对象事务重构或 F01 极端组合。实机发现正常路径问题时按事实定位，不默认回退到全面防御改造。

### 8.3 版本特别注意

目标提交的换机文档明确说明：先前冻结 EXE 不包含 F02。本次可用固定源码启动的 Designer 开始测试；要用 EXE，则必须核实它的构建源码。不要用旧 EXE 验收本次客户端变化，也不要把历史冻结自测当作新补丁证据。

**本计划允许推进受控分项验收，不授权直接在未确认的寄存器写值，也不是正式产线投用结论。** 没有必要因两项客户端 P2 停下所有实机工作，但正式切线仍由实际项目验收结果决定。

## 9. 固定源码与证据索引

以下链接均是本轮固定源码，不指向移动分支：

- [AGENTS.md](https://github.com/jsdfhasuh/emo_master/blob/a556a1d0a52a684153ebdfc500dd3dc90b4ef84d/AGENTS.md)
- [资源客户端：分类、上传和异常边界](https://github.com/jsdfhasuh/emo_master/blob/a556a1d0a52a684153ebdfc500dd3dc90b4ef84d/src/emo_master/apps/designer/services/runtime_client.py#L448-L548)
- [现有查询／取消与结果展示](https://github.com/jsdfhasuh/emo_master/blob/a556a1d0a52a684153ebdfc500dd3dc90b4ef84d/src/emo_master/apps/designer/ui/project_configuration.py#L230-L350)
- [服务端传输：原请求读取、取消和阶段处理](https://github.com/jsdfhasuh/emo_master/blob/a556a1d0a52a684153ebdfc500dd3dc90b4ef84d/src/emo_master/apps/runtime/project_config/transfer.py#L56-L193)
- [F02 现有测试](https://github.com/jsdfhasuh/emo_master/blob/a556a1d0a52a684153ebdfc500dd3dc90b4ef84d/tests/runtime/test_targeted_resource_repair.py#L1-L140)
- [换机交接与未执行边界](https://github.com/jsdfhasuh/emo_master/blob/a556a1d0a52a684153ebdfc500dd3dc90b4ef84d/docs/testing/2026-10-19-development-machine-handoff.md#L55-L79)

上一轮独立审查及反例：`emo_master_a556a1d_independent_review.zip` 中 `probes/test_f02_findings.py`。该包的历史辅助运行不是本次的新测试结果；将反例移入完整工作区时保留断言，按仓库声明环境执行。
