# TCP 设备集成 v1.5 实施与正常使用验证记录

日期：2026-10-21，Asia/Shanghai。日志中的 ISO 时间及 Runtime JSONL 文件名使用 UTC，因此部分文件名日期为 2026-10-20。

本轮已实现计划第 11 节的四个阶段；A1—A6 在源码模拟环境中通过，其中设备表单、绑定、保存重开、首次写入及操作端设备窗口使用 Windows 原生 Qt 自动化。三个包导入后均通过正式 Job 入口完成了一次运行。真实相机／PLC、人工 UI、冻结产品和跨机器现场交付未执行，不能用本记录替代。

## 固定版本与工作区边界

| 项目 | 实际版本／结果 |
| --- | --- |
| 仓库／分支 | `jsdfhasuh/emo_master`，`agent/runtime-workflow-architecture-v1` |
| 审查基线 | `59f3f63b653d6c021ceb7c33700bdfcd3e7e4e6a` |
| 计划提交 | `f47a9e584904e098f43c9431d461f093594ceb60` |
| 计划文件 | [TCP 通信纳入设备管理实施计划](../plans/2026-10-20-tcp-device-integration-plan-v1.md)，正文版本 v1.5 |
| 实施源码提交 | **`ba779fe356e61997b1b776802aa06b363412d037`**，`feat(devices): integrate TCP devices across Runtime and project delivery` |
| 源码 Git tree | `a3f5ef2c24722589daf75580b88f24265aa64922` |
| 实施候选 | 119 个显式选择的文件；[候选内容及 SHA-256](../evidence/2026-10-21-tcp-device-integration/verification/source-candidate.json) |
| 证据目录 | [2026-10-21-tcp-device-integration](../evidence/2026-10-21-tcp-device-integration/)；追加本轮记录，历史证据未覆盖 |

实施前已读取适用的根 `AGENTS.md` 和固定计划，拉取并核对目标分支。`git diff --stat 59f3f63b653d6c021ceb7c33700bdfcd3e7e4e6a f47a9e584904e098f43c9431d461f093594ceb60` 仅有计划文件新增 844 行，没有后续设备实现需要重复。基线已有的独立设备窗口、菜单归属、启动窗口关系、首次写入和校验反馈继续使用，本轮在这些入口扩展。交付收尾再次读取远端引用，分支仍为计划提交 `f47a9e5…`。

开发在 `D:/jsdfhasuh/documents/my_project/emo_master` 进行，`C:/Users/jsdfhasuh/my_scripts/emo_master` 是同一工作区入口。开始时受跟踪工作区干净；只显式暂存本轮候选。未操作现场工程、凭据、真实相机／PLC、物理网络或发布系统，也未推送。没有为本轮声称执行过全体私有／忽略文件的哈希基线。

本报告及原始证据在独立文档提交中保存；它们不改变上述已验证源码 SHA。

## 四阶段实施结果

| 阶段 | 已交付行为 | 对应实现／证据 |
| --- | --- | --- |
| 1. 契约与新工程可用 | 工程 2.7、流程包 1.4、DeviceType／驱动 API 1.1；三类 TCP 注册；静态 selectors、能力和槽位校验；TCP 通道表参与原子草稿提交；新建、保存重开和首次候选版本门一致 | `core/contracts/device_driver.py`、`core/plugin/devices.py`、`core/project/devices.py`、`core/workflow/compiler.py`、`designer/ui/tcp_device_form.py`；A1、绑定直接反例及注册／工程回归 |
| 2. 首次发布与实际执行 | 工厂统一创建受管设备；普通设备按各自 FIFO 执行，常驻服务按动作并发准入；四个 TCP 算子经 `executeDevice` 调用；TCP 24 MiB 有界桥承载正常 16 MiB 报文；停止／取消／释放及 COMMITTED 后激活结果进入原回执 | `runtime/devices/{tcp,manager,lifecycle,service_executor}.py`、设备及 TCP IPC、Worker／Job 入口、配置协调器；A2、A3、16 MiB 报文和 A4 |
| 3. 更新、两端管理和包流转 | 通道与节点绑定依赖选择发布；只替换受影响后端；Designer 与操作端共享无 Qt 设备客户端和状态／日志呈现；设备包、流程包和完整运行包导入后可运行；第三方持续服务及管理动作走相同机制 | `clients/runtime/devices.py`、`ui/device_management.py`、设备表单、包导入导出和 `examples/device_service/`；A4—A6、流程包离线导入及 CLI |
| 4. 定向验证与交付 | 修复实际正常路径阻断；保留首次失败和修正证据；使用固定提交的独立源码快照验证；更新三份使用／契约指南、第三方示例与本报告 | 下列测试记录、截图、回执、Job 摘要及设备日志；不扩大为旧工程兼容或性能专项 |

设备日志带 Runtime、工程加载 scope、deviceId／generation；节点调用增加执行时的 slot、selectors 与 owner 归属。管理动作保持真实管理来源，不生成伪 Job／节点。设备诊断复用原事件及 JSONL 通路，采用计划中的有界缓存和分页；窗口关闭停止观察，后端继续运行，重开不重复挂钩。

首次校验只处理候选，不创建连接或监听。首次提交成功后才执行所选设备连接策略；失败仍保留 COMMITTED，连接失败附在原 requestId。设备停止／维护和 Job 退休保留正常出口，未知发送结果不自动重发，取消不补执行。

设备／流程复用包的新建设备默认禁用，现场字段与依赖需要补齐；静态 selectors、共享设备映射、变量和资源保留。复用目标依据目标注册声明校验能力及 selectors。完整运行包保留启用与连接策略。操作端的导出、安装和校验同步使用目标插件注册目录。

## 验证环境与方法

Python 为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`（3.10），PySide2／Qt QWidget，`QT_QPA_PLATFORM=windows`。使用临时工程、隔离 SQLite、实际生成的 Worker 子进程、本机回环 TCP、脚本式 SLMP 模拟端及模拟相机；详细环境和原始文件位置见 [provenance.json](../evidence/2026-10-21-tcp-device-integration/provenance.json)。未连接真实硬件。

A1—A5 共用同一份保存的工程：

- 原始工程：`C:/Users/jsdfhasuh/AppData/Local/Temp/pytest-of-jsdfhasuh/pytest-2672/tcp-device-chain0/engineering/acceptance.emoproj`。
- 仓库留存：[acceptance.emoproj](../evidence/2026-10-21-tcp-device-integration/acceptance/engineering/acceptance.emoproj) 和同目录 `sample.png`。
- A6 原始工程：`C:/Users/jsdfhasuh/AppData/Local/Temp/pytest-of-jsdfhasuh/pytest-2672/testA6ThirdPartyServiceUsesSam0/service.emoproj`；[仓库留存](../evidence/2026-10-21-tcp-device-integration/third-party/service.emoproj)。

连续路径测试在 [test_tcp_device_integration.py](../../tests/runtime/test_tcp_device_integration.py)。夹具建立初始流程图和模拟端；真实 Qt 控件执行新建设备表单、错误反馈、通道编辑、绑定 Finish、保存重开及 Runtime 菜单校验／写入，确认弹窗由测试应答。主链通过嵌入式 RuntimeClient 调用真实 RuntimeService，Worker 仍实际 spawn；受影响回归另外覆盖已有 loopback RPC／连接切换。该方法属于源码模拟及原生 Qt 自动化，未执行人工逐步操作或真实远程部署。

## A1—A6 结果

| 组别 | 实际执行及结果 | 固定证据 |
| --- | --- | --- |
| A1 创建与首次写入 | **通过**。非法通道 IP 在表单内拒绝；删除仍被引用的通道时草稿保持原子性；改显示名称保留 channelId；重复 Finish 可用；保存重开为 2.7 且资源、参数、绑定完整。Runtime 菜单校验不监听；首次写入后 server 为 LISTENING，Job 数仍为零，未替用户改写磁盘草稿 | 原生 Qt 截图、快照测试；首次回执 `195bdae25992431dbee612e3db5d00fc` |
| A2 相机与 PLC | **通过，软件模拟**。同一工程执行 8×6 模拟取图 → Blob → count → SLMP D100 写入，模拟端收到值 1；相机与 PLC 各用独立设备线程，先后由图依赖确定 | 快照测试；`camera-plc` Job `1f036bc3-0299-414d-afed-25411a164175` 为 COMPLETED |
| A3 TCP 实际运行 | **通过，回环 TCP**。正式 Job 使用 session、session_send、client_once、receive_once；一个 Job 等通道 b 时另一个通道 a 能发送；停止等待 Job 后监听仍在。单次 PING/PONG、分帧、ONE/ACK、会话／回执输出正常；另有完整 16 MiB 报文经 TCP 桥逐字节比对通过 | 快照测试、Job 摘要和原始 Runtime JSONL；普通停止产生 ABORTED 属预期 |
| A4 日常控制与更新 | **通过**。共享客户端读取同一后端；单次设备维护后 resume 恢复准入；server 停止后可再启动；选中节点绑定更新只加入所需通道依赖，不发布无关 client 草稿。一次端口占用后原回执保持 COMMITTED 并报告 FAILED；端口释放后明确重连，更新后 Job 完成 | `a4-channels`、`a4-occupied` 持久化回执，导出前设备诊断，更新后 COMPLETED Job `22a91fc4-0316-4d4b-a169-7fc6f8e0641d`；去重观察由相关 Qt 回归验证 |
| A5 包交付后运行 | **通过，本机新目录**。导出设备包、流程包和完整运行包；复用包生成新设备 ID、保留 selectors 和共享关系，补齐所需字段后分别从正式入口运行；资源和变量保留。完整运行包保留启用策略，导入后继续运行。额外执行操作端 CLI 默认内置插件与第三方插件目录的导出／安装／校验 | 三个原始包、三个导入工程及其资源、每次导入后的 Job／scope／设备诊断；见下表 |
| A6 第三方持续服务 | **通过，软件服务**。描述符与驱动注册 ticker，无 DeviceManager 类型特判；后台事件、节点日志及未主动重复打日志的失败可定位；设备页管理动作成功和失败均有真实来源，owner／cycle 退出；窗口隐藏重开保持同一后端与 generation，启动日志仅一次，后续节点仍可运行 | 第三方工程、`third-party-diagnostics.json`、原始 JSONL；最终 Job `33812d46-60ff-406d-8bf6-4106db0e3609` 为 COMPLETED |

两张实际控件截图已打开查看：[TCP 连接／通道表单](../evidence/2026-10-21-tcp-device-integration/acceptance/qt-device-connection.png)、[首次写入完成后的配置窗口](../evidence/2026-10-21-tcp-device-integration/acceptance/qt-first-write.png)。截图证明采样时的窗口状态；行为判定由相应控件、回执与运行断言共同支持。

### 导入后最后一次实际运行

| 产物 | 导入后 Job | 工作流／设备与发送结果 |
| --- | --- | --- |
| [devices.emodev.json](../evidence/2026-10-21-tcp-device-integration/acceptance/devices.emodev.json) | `00e4c5da-a5c6-4799-8808-2d5d1b2e4b80`，COMPLETED | `main`；新设备 `device-62ddfc41fece42379b2f6e535043ac72`；channel `a`，14 字节 SENT_TO_TRANSPORT |
| [workflow.emowf.json](../evidence/2026-10-21-tcp-device-integration/acceptance/workflow.emowf.json) | `c83617b6-0f89-40a7-9ab1-219d4c1ebcb0`，COMPLETED | `main-2`；新设备 `device-211f858f149c4bc49f7fb3c813d4015c`；channel `a`，16 字节 SENT_TO_TRANSPORT |
| [runtime-5e793a6ffa324a7083693ec93c82478e.vxpkg](../evidence/2026-10-21-tcp-device-integration/acceptance/runtime-package/runtime-5e793a6ffa324a7083693ec93c82478e.vxpkg) | `cedce0fd-4d82-4ad8-826e-bce527d054e2`，COMPLETED | `main`／`server`；channel `a`，15 字节 SENT_TO_TRANSPORT |

每次发送都由回环客户端实际接收并比较；`bytesSent == bytesRequested`。回执 `deviceConfirmed=false`，表示本地 TCP 传输栈接收完成，不声称物理设备确认。各次 scope、完整 ID、generation、requestId 与回执见 [acceptance-results.json](../evidence/2026-10-21-tcp-device-integration/acceptance/acceptance-results.json)。对应导入目录的 `device-diagnostics.json` 保留同设备事件；[job-results.json](../evidence/2026-10-21-tcp-device-integration/acceptance/job-results.json) 是从模拟 Runtime SQLite 只读提取的 Job 终态，未复制数据库或 16 MiB 业务载荷。

首次写入和两次更新的原请求回执见 [write-receipts.json](../evidence/2026-10-21-tcp-device-integration/acceptance/write-receipts.json)：首次和通道更新均为 COMMITTED＋LISTENING；`a4-occupied` 为 COMMITTED＋`FAILED: [WinError 10048]`，document／execution revision 为 3。该失败后没有重写同一请求掩盖结果；释放端口、显式重连后再运行成功。更详细的连接失败、动作与节点失败归属见 [导出前设备诊断](../evidence/2026-10-21-tcp-device-integration/acceptance/device-diagnostics-before-export.json)。

## 测试与静态检查

以下批次有重叠，**不相加**，也不称作全仓测试。

| 范围 | 结果 | 版本及原始日志 |
| --- | --- | --- |
| 46 个受影响测试文件：设备、生命周期、配置、资源、包、子流程／循环／Dispatch、Designer、TCP 和 Worker | **565 passed in 211.29s** | [final-regression.log](../evidence/2026-10-21-tcp-device-integration/verification/final-regression.log) 首行有完整命令；此批在最终离线导入回退和 ticker ClassVar 修正之前完成，后两项由下两批覆盖 |
| 流程包设备及无 Runtime 的离线导入 | **25 passed in 2.04s** | [offline-import-final.log](../evidence/2026-10-21-tcp-device-integration/verification/offline-import-final.log)；入口 `tests/designer/test_workflow_package_devices.py` |
| 固定源码提交的独立导出快照：连续路径 9 项，加流程包／注册／首次配置／DeviceManager／替换／生命周期 | **106 passed in 50.93s**，proto check 通过 | [source-snapshot.log](../evidence/2026-10-21-tcp-device-integration/verification/source-snapshot.log)；明确记录 SHA、tree、源码导入路径和命令；9 项包含 A1—A6，以及 16 MiB 传输、selector 校验与未知回执三个补充场景 |
| 操作端实际 CLI：内置插件默认目录、显式内置＋第三方目录，各自 export／install／check | **6 个命令均成功** | [cli-checks.log](../evidence/2026-10-21-tcp-device-integration/verification/cli-checks.log)；安装及 check 均未创建 Job；加载仍遵循设备 on_project_open 策略，不能当作无 I/O 的候选校验 RPC |
| 受改动 Python 文件 Ruff、mypy、compileall；生成协议及差异空白检查 | **均通过** | [static-checks.json](../evidence/2026-10-21-tcp-device-integration/verification/static-checks.json) 保存精确命令／退出码；[原始输出](../evidence/2026-10-21-tcp-device-integration/verification/static-checks.log) |

mypy 使用 `--follow-imports=silent`，报告 97 个文件；默认不检查所有未注解函数体，日志中的 `annotation-unchecked` 为该检查范围说明。Ruff 使用仓库已有 exclude，未把生成代码当作手写代码重新格式化。`scripts/gen_proto.py --check`、compileall、`git diff --check` 和 `git diff --cached --check` 均实际执行。

独立快照从固定提交以 `git -c core.autocrlf=false archive` 导出。119 个候选文件的 SHA-256 与提交内容及快照逐一匹配，测试导入路径确认为 `build/tcp-device-v1/source-snapshot-lf/src/emo_master/__init__.py`，而非工作区源码；见 [source-snapshot-check.json](../evidence/2026-10-21-tcp-device-integration/verification/source-snapshot-check.json)。该文件的 archive 摘要对应本机忽略目录中的复核归档；未把大体积源码 tar 或 Runtime DB 作为本轮证据提交。

复现连续路径可在上述 Python 环境设置 `PYTHONPATH=src`、`PYTHONIOENCODING=utf-8`、`QT_QPA_PLATFORM=windows` 后执行：

```powershell
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' -m pytest -q -s tests/runtime/test_tcp_device_integration.py --tb=short
```

需要完整复现固定快照批次时使用日志首行的七个测试入口；所有数据仍须使用测试自动创建的临时目录及模拟端，不向现场工程替换这些回环端口。

## 首次失败与修正轨迹

原始失败按原字节保存到 [first-failures](../evidence/2026-10-21-tcp-device-integration/first-failures/)，没有把中间失败记为基线故障或最终通过。早期部分 PowerShell 日志本来含编码显示问题，留存时未重编码或删除诊断。

| 首次／中间证据 | 原因与处置 | 最终对应验证 |
| --- | --- | --- |
| `vertical-first.log` | 设备事件 sink 的位置参数 `kind` 与事件字段重名，首次激活误报失败；修正回调参数，保留事件归属 | 固定快照 A1、A4、A6 |
| `integration-ui-first.log`、`contracts-final.log` | 旧的 1.0 描述符、2.6 工程／1.3 包及测试驱动签名未同步；按本轮统一接口更新示例及夹具，当前版本门与绑定断言同步。旧工程迁移断言退出本轮范围，没有加兼容层 | 注册、工程、资源、包及 565 项相关回归 |
| `runtime-final.log` | 恢复路径把无 requestId 的快照送入写入连接观察，出现 `onlineRecoveryError="'requestId'"`；只对真实写入回执附加观察，启动恢复独立激活。配置准备预约之前的显式停止保留暂停意图，实际替换预约仍限制受影响操作 | 配置 owner／恢复、循环、设备替换及 565 项回归 |
| `transport-worker-first.log`、`contracts-final.log` | 原单例通信代理测试仍按旧桥签名调用；改为 DeviceManager 桥夹具。保留原始报文字节、未知回执、取消和不重放断言；重复请求由严格序号拒绝，不保留第二套旧回复缓存 | 托管 TCP／Worker 回归及快照的未知回执直接反例 |
| `regression-third.log`、`regression-fourth.log`、`management-first.log` | 元数据、selectors 与当前工程反馈夹具需要同步；移除旧通信入口后遗留的 `communication` 变量导致 Runtime 切换 NameError，已清除；连接结果只读观察的 scope／request 关联及终态退出同步修正 | 相同测试文件进入最终 565 项；连接切换与晚到 FAILED 去重反馈通过 |
| `native-first.log` | Qt 自动化试图对端口 QSpinBox 调用 setText；用通道非法 IP 执行真实可输入的反例，未放宽产品校验 | 快照 A1 原生 Qt |
| `final-acceptance.log` | ticker 算子的 dataclass 把 inputPorts 字典当作可变字段；改为 `ClassVar[dict]` 后能够实际注册并运行 | 固定快照 A6 和第三方 CLI |
| `source-snapshot-first.log` | 首次 Git archive 受 autocrlf 转换影响，候选哈希检查在测试前失败；显式 `core.autocrlf=false` 重新导出，不删原失败 | 119 文件摘要匹配，固定提交快照 106 项通过 |
| `cli-first.log` | 复核脚本提取第三方临时路径时遗漏多行正则标志，尚未运行 CLI 即退出；修正脚本后执行实际六个命令，产品源码未因此变化 | `cli-checks.log` |

最终通过日志仍有等待连接取消和 ticker 主动拒绝产生的 Worker traceback。这些分别对应 ABORTED／被断开 Job 和 `E_SAMPLE_REJECTED` 直接反例，相关断言通过；不能描述为“无任何异常输出”。第三方服务在拒绝后继续可用，且最后一次 Job 为 COMPLETED。

## 证据范围、未执行项与交付

[artifact-manifest.json](../evidence/2026-10-21-tcp-device-integration/artifact-manifest.json) 覆盖 49 个证据文件的原始字节 SHA-256（manifest 自身除外）。证据包括最终及首次失败日志、候选／快照记录、同一模拟工程及资源、三个导出包、三个导入工程、回执、Job 摘要、原始 Runtime JSONL、第三方服务和两张截图。新增 `.gitattributes` 规则保持这些原始字节和诊断空格，不覆盖历史目录。没有复制整个 SQLite、预览缓存、私有工程或 16 MiB Job 数据。

本轮必修的正常创建、保存、写入、运行、停止／恢复、更新和包导入后再运行均有上述软件证据，未保留已发现但尚未修复的正常主路径阻断。按计划范围，以下项目明确未执行或不包含：

- 真实华睿 IMV 相机、真实 SLMP PLC、机器人／第三方物理 TCP 设备联调；硬触发准备与 PLC 握手仍遵循指南中的已有边界。
- 人工 UI 操作验收、冻结 EXE 构建／自测、干净机器、跨机器远程空 Runtime 安装与现场部署。A5 使用同机隔离目录；远程空 Runtime 仍应先在执行端导入完整运行包，未把本地资源路径当作跨机安装协议。
- 全仓测试、旧工程兼容／历史 TCP 参数迁移、旧驱动适配、旧包往返、跨独立 Runtime 资源独占及强杀／压力／高 DPI／性能专项。
- 推送、PR、tag、Release、产品打包和部署。本轮仅本地源码及验证记录提交。

已更新 [设备契约](../guides/device-catalog-contract-v1.md)、[TCP 使用指南](../guides/project-tcp-service.md)、[设备与在线配置指南](../guides/device-dispatch-online-configuration.md) 及 [第三方持续服务示例](../../examples/device_service/README.md)。现有运行进程不会热加载这些源码，需要退出后重新启动对应源码程序；冻结产品需按既有构建方式重新构建后再验收，本轮未替换运行产品。
