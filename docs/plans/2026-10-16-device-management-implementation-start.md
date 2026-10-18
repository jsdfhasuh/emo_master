# 设备管理与在线配置 R2.1：实施启动记录

- 日期：2026-10-16（Asia/Shanghai）。
- 当前进展以[软件验证报告](../testing/2026-10-18-device-platform-software-validation.md)和[实机前交接检查表](../testing/2026-10-18-device-platform-handoff.md)为准。下面保留启动时的 M1/M2 基线与历史接入说明，不作为当前未完成清单。
- 用户最初请求查看压缩包并准备实施，随后明确要求按计划开始实现。附件是方案资料，不构成提交、推送、部署或现场设备操作授权。
- 实施正文：[R2.1 完整方案](device-management-r2_1/2026-10-16-device-management-online-config-plan-r2_1.md)。
- 配套资料：[交接说明](device-management-r2_1/CODEX_HANDOFF.md)、[修订记录](device-management-r2_1/REVISION_NOTES.md)、[离线含图阅读版](device-management-r2_1/device-management-plan-r2_1.html)。
- 跟踪：[基线验证](../testing/2026-10-16-device-platform-baseline.md)、[88 项验收台账](../testing/2026-10-16-device-platform-acceptance.md)。

## 1. 来源与当前基线

压缩包六个文件原样保存在 `docs/plans/device-management-r2_1/`，保留相对图片链接、历史说明与 HTML。不再复制第二份实施正文；本文件记录当前核对结果，不覆盖原作者的历史基线说明。

| 项目 | 核对结果 |
|---|---|
| 输入 ZIP | `D:/jsdfhasuh/Downloads/emo_master_device_management_plan_r2_1_with_ui.zip` |
| ZIP SHA-256 | `e5a4bb9b307ad49b6e7902a51c399c2097728df75d3f8c1646636962157683e3` |
| 正文 SHA-256 | `6ebc3403147e092358eb95d207d2fa116a77866ffc1b10c675e34de0697bd012` |
| 原图 SHA-256 | `b3b12569716731496c7093345afca2fb018f4355d02f26a564e57ea92ce2a0a8` |
| 原图尺寸 | 1672 x 941；已查看，仅是设计参考，不是当前软件截图 |
| 本地仓库 | `C:/Users/jsdfhasuh/my_scripts/emo_master` |
| 当前分支 | `agent/runtime-workflow-architecture-v1` |
| 当前 HEAD | `63070277133b89d61a5d27dde6d5f95494b4dce0` |
| 方案基线 | `096d021c3cf48516fc60289878f94b031a1c602c` |
| 初始工作区 | `git status --short` 为空 |
| 仓库规范 | 仓库文件搜索与父目录检查未发现 `AGENTS.md` |
| 远端状态 | 未 fetch、未查询远端实时 SHA；本地 origin 引用不作为远端最新状态证明 |
| 解释器 | `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe` |
| 已核对版本 | Python 3.10.21、PySide2 5.15.2.1、pytest 9.1.1、pydantic 2.13.5 |

方案基线之后有两次提交，共改动 27 个文件：

1. `963e356`：MC 3E ASCII 支持及 PLC 调试控件改进。
2. `6307027`：双 PLC 现场与模拟测试工程。

后续设备化必须保留 `frameEncoding=binary/ascii`、端点路由、地址跨度、数值类型及读写回执，不把 PLC 连接简化为只有 IP/port。现场工程只作为静态兼容样本；本轮测试把全部执行端点替换为 loopback，不运行现场地址。

## 2. 当前源码与实施接入点

以下均在上述 HEAD 核对，不是附件中的拟新增 API 已经存在。

| 领域 | 当前入口 | 实施要求 |
|---|---|---|
| 插件 | `core/plugin/models.py`、`registry.py:scanRoots` | 当前是算子注册；扩展设备声明和独立枚举，纯元数据扫描不得连接硬件或加载厂商 DLL |
| 工程 | `core/project/models.py:WorkflowNode/ProjectDevices/ProjectDocument` | 当前最高 2.5，`devices` 只有通用 `bindings`；2.6 可作为当前拟用版本，M1 修改前再次核对 |
| 迁移 | `core/project/migration.py` | 所有版本门禁、序列化白名单与资源/production/globalVariables/communication 继承需同时扩展，不能只加 Literal |
| 状态与历史 | `apps/designer/state/workflow_store.py`、`project_edit_session.py` | 设备定义与节点绑定沿用同一工程编辑/撤销/保存边界；在线状态不写进工程 |
| 图投影 | `state/flow_graph_model.py`、`controllers/workflow_controller.py:captureActiveWorkflow/_renderActive` | 当前显式列出节点字段，新增绑定/覆写/动态来源须双向保存，切图不能丢字段 |
| 工作页 | `page_designer/coordinator.py:PageCoordinator`、`chrome.py:WorkspaceChrome`、`workspace_switch.py` | 当前两个页面；第三页接入原 stack/action/session，菜单定位同一页，不加平行状态源 |
| 流程包 | `state/workflow_package.py` | 现有导出结构包含空设备依赖槽；实现设备依赖闭包与显式映射，不能只让模型接收新字段 |
| 变量与现场绑定 | `core/project/global_variables.py:VariableBinding`、`resources.py:ParameterTarget` | 当前目标是节点参数；扩展类型化目标、冲突校验和包读回，不另建变量求值器 |
| 编译计划 | `core/workflow/compiler.py`、`models.py:CompiledNode` | 保留现有冻结参数机制；增加设备引用、周期和分发字段，不能静态禁止多节点共用设备 |
| 正式 PLC | `plugins/builtins/_communication_operators.py`、`_slmp.py` | 正式读写仍创建 Slmp3EClient；Slmp3ESession 已用于调试，作为统一持久会话适配基础 |
| 相机 | `plugins/builtins/huaray_camera/operator.py`、`_huaray_imv.py:CameraSession` | 会话仍在算子实例；`actualExposureUs` 来自 ExposureTime 回读，不能当作逐帧曝光证明 |
| Worker IPC | `apps/runtime/jobs/supervisor.py`、`worker_main.py`、`communication/ipc.py` | 保留 spawn 与现有有界可取消代理模式；设备所有权放 Runtime，不把进程内锁当跨 Job 锁 |
| 在线配置 | `grpc_server/service.py:LoadProject`、`context/sqlite_store.py`、`context/migrations/` | 目前 LoadProject 拒绝活跃 Job/未释放资源；新增 SQLite 权威配置/回执，不能删除检查冒充热更新 |
| 周期 | `workflow/context.py:RunContext`、`runner.py`、`loop_runner.py` | 当前有调用/迭代身份，无计划版本 token；新增 cycleId/planRevision/runGroupId，子调用不丢身份 |
| 调试 | `preview/live.py`、`preview/plc_debug.py`、`operator_debug/` | 保留 UI/确认/退休机制；后续硬件请求统一进入 DeviceExecutor |
| 接口与冻结包 | `apps/designer/services/runtime_client.py`、`proto/runtime.proto`、`scripts/gen_proto.py`、现有 package tests | 内嵌/远端行为一致，设备声明/UI/Host 资源随包；本轮不生成 proto、不打包 |

上表路径除另行说明外相对于 `src/emo_master/`。

### 必须收口的旧行为

1. `MainWindow._syncRuntimeProjectBeforeRun` 当前先保存再 LoadProject。M3 改成明确选择运行在线版本或先写入，不把本地文件当在线权威。
2. 普通同路径保存本身没有调用 LoadProject；但 `ProjectController.saveProjectToDirectory` 的另存为路径会调用 `releaseCommunicationForReplacement`。M3 必须核对另存为/切换本地工程对在线生产的影响，不能只测同路径保存。
3. `CommunicationRpcMixin.ControlCommunication(action=SYNC)` 当前独立更新 `loadedDocument/loadedPayload`。它与 LoadProject、UNLOAD、项目替换等改变在线上下文的路径共同纳入配置写入仲裁，不能遗漏旧接口。
4. TCP 保留现有 CommunicationManager、连接代次及发送回执；设备页只适配展示/控制，不新增第二份 TCP 配置或监听所有者。

## 3. 按 R2.1 固定的实施边界

### 数据与设备请求

- 工程目录使用 `devices.definitions[deviceId]`，保留已有 `devices.bindings`；节点使用 `deviceBindings`、`deviceOverrides`、`deviceValueBindings`。
- 每个字段只能是继承、显式常量或动态来源之一；同值覆写仍保留来源。旧 keep 不自动变为默认值，不编造硬件基准，不自动合并同端点节点。
- 一个设备一个权威 FIFO DeviceExecutor。SLMP 用 IO 线程，相机用固定受管 Host；Job 只持代理，不序列化 SDK/socket/锁。
- 请求固定本次周期、参数、代次和预算；排队取消不执行。参数应用、触发/等待、确认原图归属及 SDK 帧释放构成一次操作；下游算法、分发和容量等待不占设备操作权。
- 未确认写入或触发不自动重放；迟到帧、流代次、硬触发缓存和无法退出的 SDK 调用必须显式处理。设备请求参数错误不等于设备失效。

### 在线提交与周期

- 一个 Runtime 一个写入名额；候选仅由在线基线、选中变更和已确认依赖组成。最终提交点再次核对 Runtime 身份及 documentRevision。
- 完整配置、documentRevision、executionRevision 和回执在同一 SQLite 事务提交。文件导出只是副本；软件提交结果与硬件准备结果分开。
- 普通参数不增加全运行组屏障；旧周期和已排队消费者持旧不可变计划，新周期取新计划。连接/流/资源不兼容时只处理受影响范围。
- 当前 RunContext 的 job/workflowRun/nodeRun/iteration/坐标身份保留；Loop condition 与本次 body 同 token。状态型实例无迁移契约时按 requires_stop。
- 配置写入不执行 PLC 节点、不触发拍照；结果丢失查询原 requestId，不自动换 ID 再提交。

### 图像与独立流程

- Runtime 是共享内存 FrameStore 的唯一最终释放者；FrameRef 带 dtype、shape、连续 strides、nbytes、颜色/坐标/原图身份和代次。
- 限制帧数与字节，排队阶段即持图像/计划引用，实际退出后释放。未知可写插件获得独立副本，不只靠 ndarray 的 writeable 标志。
- Dispatch 目标为本运行组明确 targetSlot；保留 dispatchId/deliveryId/invocationId/cycleId/planRevision。受理不等于完成，Join 按本次分发收集，不拿最新结果凑数。
- 容量在首次硬件动作前确认；不每帧 spawn，不跨组广播，部分投递不重发整批，崩溃未知副作用不重跑。

### 界面

- 原图左树、中表、右详情映射为正式第三工作页；保留本仓库原生 Qt/图标/样式，不照搬模态外壳。
- 树、表、详情共用 deviceId；离线编辑不连接设备，状态刷新不拍照，生产观看默认订阅已有图像。
- 本地保存、读回在线工程、硬件实际回读、写入 Runtime 四者分开；在线监视使用实际周期快照，不用本地新图解释旧结果。
- 本期真实驱动范围为华睿 IMV、三菱 SLMP 和既有 TCP；图中其他品牌/设备不是新增驱动要求。

## 4. 下一阶段任务拆分

| 顺序 | 工作包 | 进入下一步的条件 |
|---|---|---|
| M1.1 | 设备描述符/Registry 扩展、schema 2.6、唯一参数来源、策略校验、未知插件保留 | 离线无 SDK 可加载/校验/往返；旧 2.0-2.5 行为保留 |
| M1.2 | Store、FlowGraph、复制/切图、编辑历史、节点向导原子提交/取消 | 新节点和新设备一次提交，取消不留半成品；已存在设备不复制连接配置 |
| M1.3 | 正常 Designer 第三页、三列选中联动、引用跳转、插件表单、模拟设备 | 从正常入口添加/绑定/覆写/保存/重开；没有在线能力的动作明确不可用，不用假状态 |
| M2 | Runtime FIFO/Job proxy、取消、IO/Host 接口、FrameStore | 两个模拟 Job 共用设备，取得原图即释放操作权；窗口退出不释放他人连接 |
| M3 | SQLite 快照/比较/选择写入/回执、周期 token、在线只读图及旧 mutation 收口 | 保存与运行隔离；旧周期旧版/新周期新版；覆盖失联与提交竞态 |
| M4 | 真实驱动、迟到帧/恢复、生产预览订阅、PLC/TCP 诊断整合 | 无旧直连后门；真实 SDK/现场证据独立标注 |
| M5 | 显式 Dispatch/Join、目标槽、持久消费者及资源释放 | 两独立流程收到同帧；部分投递、崩溃、晚到消费者和结果身份闭合 |
| M6 | 全类型更新分类、迁移预览、导入导出、siteBindings、资源和显示版本 | 全路径不丢字段；本地未选变更不夹带上线 |
| M7 | 完整功能回归、原生桌面、Windows 冻结包、文档交付 | 逐项有证据；NOT_RUN/UNSUPPORTED 不算通过 |

M1 不把真实相机/PLC 算子提前切换到尚不存在的 DeviceProxy；早期模拟链用于打通正常 UI。M2-M4 完成后，正式设备路径统一迁移，不能把临时旧路径当最终兼容方案。动态来源输入映射的具体字段形状在 M1.1 随模型和 compiler 测试一起确定，不在 UI 私有字典中先行发明。

## 5. 当前状态与禁止误报

2026-10-18 最新状态以 [增量实现与验证报告](../testing/2026-10-18-device-platform-dispatch-publication.md)
及 [验收台账](../testing/2026-10-16-device-platform-acceptance.md) 为准。持久 Dispatch/Join、
定向停止/排空/重连、历史只读图、迁移及资源绑定界面、页面发布、配置 owner 集成、
可取消无限硬触发等待、远端资源分块上传和准备资源所有权日志均已有实现及分层测试。
后文关于“尚未实现 Dispatch/上传/打包”的叙述是早期阶段记录，不代表当前范围。
目前仍不能宣布完整实机交接：MAC 发现/硬件改 IP、页面专用静态资产、完整恢复/重试矩阵、
异常退出后全部资源使用者确认及完整冻结 GUI/干净机器验证仍有缺口。

### 历史阶段记录

后续进展（2026-10-16）：已实现设备第三工作页/节点向导、模拟设备 Runtime/spawn 链、
在线配置 SQLite 原子提交及读回/比较 UI，并接通模拟设备默认值的顶层周期换版。
后续增量已完成流程包 1.2 的设备依赖导出、跨工程显式映射及同工程复用复制，
带预览源/目标指纹校验、原子回滚和单步撤销。又已接通设备资源/siteBindings 类型化目标、
生产目录准备及 2.6 Runtime 工程包读回，覆盖元数据锁、资源完整性与业务输出保护；
专用绑定 UI、页面发布快照、在线资源替换及冻结交付仍待实现。
proto 已扩展并重新生成，以下 M0/M1 描述保留作为历史阶段记录，不代表最新实现范围。
2026-10-18 增量接通原 PLC 读写 ID 的受管 SLMP、同锁诊断、显式恢复与节点迁移确认，
Binary/ASCII 回环和 Windows 原生 Qt 证据见受管 SLMP 增量报告。现场设备未连接。
当前 88 项为 54 PARTIAL / 34 NOT_RUN / 0 PASS；详见
[UI/Runtime 进展与证据](../testing/2026-10-16-device-platform-ui-runtime.md)。
完整 M3-M7 尚未交付，尤其真实驱动、Dispatch/Join、迁移导出和冻结包；未到最终人工验收阶段。

- M0 只新增原始资料和记录；后续 M1 第一批已修改源码并新增测试，未修改 proto 或现场工程。当前工作区尚未提交。
- 分组基线只是旧能力的定向回归，不是 R2.1 新能力验收，也不是全仓测试通过证明。
- 88 个功能验收项初始均为 NOT_RUN；M1 已有部分源码证据的项目更新为 PARTIAL，完整功能及真实 UI/Runtime 验收未完成。
- 未进行 commit/push、依赖安装、打包/发布/部署、真实设备连接/写入或生产进程控制。
- 不建立后台自动推进任务。下一实现范围是补齐 M1 契约并接入第三工作页/可视向导；M2-M7 仍属于完整交付，不以底层模型或 UI 骨架宣称整体完成。
