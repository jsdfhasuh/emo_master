# Runtime / Designer / Web 接入实施记录

日期：2026-10-30。对应[本地实施计划](../../plans/2026-10-30-runtime-web-integration-plan.md)。

实施开始时分支为 `codex/logging-and-designer-improvements`，源码基线为
`0fa2115e337e7499688e4d0efd50539e34104a6a`。本文记录的新增能力位于未提交工作区，
不是基线提交已经包含的行为。没有执行提交、推送、合并、安装或部署。
原有 2.8/1.1 响应式页面能力继续复用，其历史记录见[画布实施记录](../2026-10-30-canvas-responsive/README.md)。

## 已落地的功能

| 范围 | 实现与正常行为 |
| --- | --- |
| 接入 | Runtime 默认本机动态端口；可显式开启局域网固定监听，端口占用明确失败。控制、Display、Dispatch、日志及历史使用选定 ConnectionProfile；每 Job 展示订阅默认 4，允许 1—16 |
| 运行事实 | GetRuntimeInfo / ListRuns / GetRunState 复用既有仓库和 Supervisor；桌面识别外部新运行，不依赖本地旧 session 推断 READY；停止可携带实例、工程和运行组身份，准确作用于所选 Job |
| 远程工程 | PrepareProjectImport、候选资源上传、校验及显式提交复用现有配置请求 owner；工程写入 Runtime 受管目录，首次导入不自动运行。ReadProjectResource 只读声明资源，Designer 明确选择本地下载目录并校验摘要 |
| 工程与编辑 | 新建工程 2.9 / Presentation 1.2；增加页面 desktop/web targets 和历史/固定图片数据位置。选中行图片继承表格位置；普通打开旧工程保留版本，明确使用新语义才升级。目标、来源与布局支持编辑、保存、撤销及重做 |
| 历史 | QueryHistoryPage / QueryHistoryAggregate / ReadHistoryImage 只读正式在线声明，检查实例/工程/historyDefinitionRevision；无 Job 也可查询。图片使用有限期 opaque ref 或固定组件句柄，不接受客户端文件路径 |
| Qt 查看 | 表格、统计和历史图片通过相同历史 RPC；高级连接观察 B 时历史也来自 B，reload 保持端点，停止观察关闭独立通道，历史模式恢复主连接 A |
| 页面发布 | `.emoweb` 包含 Web 目标页、绑定/scope、动作、完整布局/props 和声明静态 assets；剔除数据库/动态图片路径，不带模型、业务数据库或输出。页面默认入口和导航遵守目标可达性 |
| SDK / 交接 | `emo_panel_protocol` 独立安装，包含生成 protobuf、DTO、解码、canonical 与 Coverage。统一生成 schemas、TypeScript、合成 fixtures、canonical 向量和当前格式样例 |
| Web 预览接口 | Designer 负责临时页面包、loopback 预览进程、机器可读 ready URL、打开浏览器及进程清理；真实 `emo-web-panel preview` CLI/renderer 由 Dot 提供 |

Runtime 仍唯一拥有执行与设备，Web 网关是普通 RPC 客户端。当前受控局域网使用普通 gRPC；
本轮没有增加 TLS、证书管理、第二套任务数据库或 Web 设备 owner。

## 验证结果与证据层级

解释器为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。以下结果是不同范围的定向验证，
存在重叠，不应相加解释为一套完整回归。

| 范围 | 结果 | 层级与说明 |
| --- | --- | --- |
| LAN、首次远程导入、声明资源下载及首次写入 UI | **31 passed in 17.39s** | 实际本机 gRPC + 原生 Qt 控件路径；命令见下文，日志 `build/runtime-web-remote-regression.txt` |
| 首次导入、运行目录、连续生产、配置与边界组合 | **46 passed in 106.12s** | 本机 gRPC 和模拟生产；包含远程首次导入与既有生产合同 |
| 已有运行查看、基本反馈与 production window | **21 passed in 53.66s** | 新增 4 项 Qt 回归实际查看并发 J2 但默认停止 J1、外部 J3 状态接管、历史非启动流程页面、共享 Dispatch 双图与 Web 专用来源采集 |
| Designer 共享 Dispatch 页面完整文件 | **4 passed in 19.96s** | 原正常 Run、ALL/NONE legacy policy、两路 capture、观察和停止观察不停止生产等断言均保留；显式初始化原工程来源，cleanup 按既有设备未知结果合同核对 |
| 运行目录末次组合 | **12 passed in 18.89s** | 10 项 run catalog + 2 项 Dispatch 阻塞时独立更新；另一次独立 run catalog 为 10 passed in 10.63s，结果重叠 |
| presentation core / Qt 页面定向组合 | **326 passed** | 模型、迁移、编辑、布局、目标过滤及 Qt 页面；不是另一台实体电脑验收 |
| 合同/目标模型/属性/当前运行查看组合 | **36 passed in 14.97s** | schema 与原生 Qt 定向测试；后续增量见最后整合结果 |
| 历史 RPC 和 Qt 表格/统计/选中图片 | **3 passed** | 本机实际 gRPC，Designer 工程根与 Runtime 数据根故意不同；不使用真实业务库 |
| 页面包、历史、合同组合 | **11 passed in 3.66s** | 路径隔离、完整性、资源与 schema；发布器末次修改另做定向验证 |
| 预览进程与页面包/历史组合 | **7 passed in 8.93s** | QProcess 使用合成 CLI helper；不是 Dot renderer 或浏览器画面验收 |
| SDK 独立安装 | **PASS** | 临时干净 venv 安装 wheel 及声明依赖，`-I` 并主动阻断 emo_master/PySide2/cv2/numpy/onnxruntime/PIL 导入，检查 DTO、协议、Coverage 及连接模型 |
| TypeScript | **PASS** | TypeScript 5.8.3，`tsc --noEmit --target ES2020` 检查生成类型 |
| 样例工程 | 模型 / 保存重读 / 编译 **PASS** | 使用 WorkflowStore 新建结构和真实保存 API；实际 Designer 打开 **NOT_RUN**，manifest 单独记录 |
| 全 src mypy | **PASS，604 source files** | 使用仓库 mypy.ini；未标注函数体仍遵循现有配置，不解释为所有动态调用都已证明；日志 `build/runtime-web-mypy.txt` |
| Ruff / proto 漂移 | **PASS** | `ruff check src tests scripts/gen_proto.py scripts/build_panel_contract.py`；`python scripts/gen_proto.py --check` |

本轮远程操作整合命令：

```powershell
python -m pytest tests/runtime/test_runtime_remote_access.py tests/runtime/test_remote_project_import.py tests/runtime/test_project_resource_download.py tests/designer/test_remote_project_import_ui.py -q
```

最后整合、静态检查和最终交接包的结果在本文件后续段落记录；先前失败不会因后续通过而被删除。

## 已观察失败与修正

- 初次 GUI 扩大组合为 **83 passed / 7 failed**：暴露旧夹具把新 Presentation 默认值放进旧工程、当前版本/文案断言落后及旧 1.0 页面漏写 grid。夹具保留原有兼容版本，当前新建断言同步 2.9/1.2；没有放宽格式校验。
- 后续 GUI 组合 **39 passed / 2 failed** 暴露真实运行选择回归：工程 reload 后自动选回已结束的历史 Job。已移除终态记录的自动兜底选择，直接回归及运行目录组合 **12 passed**。
- 目标/渲染初轮 **54 passed / 1 failed**：编辑中的 selected-row 来源暂时缺失时渲染器抛错。现允许合法草稿继续编辑，由引用验证器指出缺失，后续扩大组合 326 项通过。
- 高级连接 A/B 历史用例最初两次因使用 2.2 夹具加入远程字段而失败，已改用当前 WorkflowStore 新建工程；串站修复未放宽旧版本模型。
- 最后 preview/history/Dispatch 组合曾为 **31 passed / 2 failed in 36.37s**，失败均位于首次提交后直接运行的比较入口，发生在预览接入之前。定位与处理结果继续保留在下文。
- shared-page 夹具补入原 `.emoproj` 的 `projectPath` 后，首次联合运行没有产出 pytest 统计，发生一次 Windows access violation；栈位于测试停止清理期间的 Designer 失败通知路径。随后单独 `[ALL]` 为 **1 passed in 6.13s**，测试捕获到的业务失败列表为空；独立运行目录组 **10 passed in 10.63s**。这次原生崩溃为已观察、暂未复现、原因未确认，不能写成“已修复”；没有因此修改产品异常处理或增加新的容错框架。
- 审计发现样例把 runtime_status 当作 capture 工作流输出，以及发布器未复用 publish 语义校验。修正和针对性结果在末次交接段记录。

已有来源路径断言冲突单独保留：
`testRunAndImagesUseOnlineIdentityWhenLocalFileDiffers[embedded-True]` 首次初始化没有传入原 `.emoproj` 的
`projectPath`，随后 Designer 文件与 Runtime 工程来源文件不同。基线 HEAD 已存在的
`localProjectSourceChange` 会要求用户明确比较/写入该本机来源切换；测试仍期望直接开始。
本轮没有删除该确认条件或弱化该断言，作为原有测试与既有业务语义的冲突记录。
相关 shared-page 测试使用相同初始化方式，其夹具修正和最终结果另列，不把前次失败抹去。

shared-page 夹具最终显式传入原工程的 `projectPath`，保留“未修改工程直接运行”的原断言。
主业务步骤（正常 Run、两路采集/共享页、停止查看后生产仍运行）全部通过。
新增诊断最初要求 finally 停止后也绝无失败通知，出现 **2 failed in 10.75s**，其中均为
`E_DEVICE_OUTCOME_UNKNOWN`。复核基线和现有 `test_cancellable_device_wait.py` 确认：
已经发送的设备采集在取消时可能失去原生回复，必须保留 `deviceOutcome=UNKNOWN`，不能假称未执行或自动重放。
这不是本轮新增的设备传输回归；DeviceProxy、pipe_io、工作流 runner 和 Designer 失败通知均未因此修改。

测试现将“无失败”限定在主业务步骤，cleanup 仅允许既有明确的
`E_DEVICE_OUTCOME_UNKNOWN` + `diagnostics.deviceOutcome.outcome=UNKNOWN`，其他失败仍拒绝。
四项最终 **4 passed in 19.96s**；没有删除原业务断言或把未知设备结果改成成功。
两次诊断的实际通知保留在 [ALL 记录](./evidence/dispatch-designer-stop-all-policy-failure.json) 和
[NONE 记录](./evidence/dispatch-designer-stop-none-policy-failure.json)。一次 Windows 原生崩溃的原因仍未确认，
后续未再次复现；业务未知结果原因的确认不等于该崩溃已修复。

## 末次发布审计与 Dot 交接

样例状态组件已改为原生状态，不把 runtime_status 当作工作流采集输出；结果 fixtures 仅使用正常支持的 count/verdict。
生成器增加实际 `freezeNormalCapture` 准备，manifest 记录 `normalCapture: PASS`。
发布器已在 Web 派生页面及其实际引用的来源/scope 上复用 `validateBindings(..., publish=True)`，
检查绑定、SQLite、图片允许根目录与引用；桌面独占的未完成组件和未用 scope 不阻塞合法 Web 导出。
Designer 传当前算子目录，脚本入口使用已安装内置算子元数据，不执行设备或算子。

发布器、合同生成及 QProcess 预览的末次定向回归为 **14 passed in 10.24s**。
最终生成类型通过 TypeScript 5.8.3 检查，manifest 文件长度/摘要、ZIP 对应内容与 `testzip()` 全部通过。

最终交接为 `build/panel-contract-v1.zip`，84,595 字节，31 个交付文件及 manifest。
ZIP SHA-256 为 `7a3b459c0e699b090b71d51d723ac38b88ff88484e6e64d7706c0b4de01fc26a`；
SDK wheel SHA-256 为 `58fe64dfa4386d7eda46814c04c7bedc8c74ed042d04d295a3a1ddd6acc552f2`。
这些摘要替代较早的临时交接包摘要；manifest 的 `status=complete` 只表示合同交接材料完整。
`sourceSha` 为本文基线、`sourceDirty=true`，不假称工作区实现已经包含在提交中。

样例最终为 model/saveReload/workflowCompile/normalCapture **PASS**、designerOpen **NOT_RUN**。
SDK 源码模型未在末次发布校验修改中变化，未重复先前已通过的干净 venv 安装检查。
该轮对 L1/L3/L5 的审计当时未发现新的本地功能遗漏；后续独立复查又确认了下文记录的五项缺口。
该历史结论不能代替后续修复记录；真实 Web renderer 与网关责任仍保留在下节。

## 外部交付与未执行范围

Dot 尚需实现 Web 网关、浏览器 renderer、会话/权限、共享订阅、页面包导入激活/回退和真实 preview CLI。
本地 QProcess 接口已具备，但没有收到该程序，不能把合成 helper 的通过写成真实 Web 预览通过。
浏览器与 Qt 三方共同控制、页面更新和回退仍需 Dot 使用同一 manifest 联调。

另一台实体电脑的连接、真实浏览器、冻结 EXE / 安装包、人工现场 UI、真实相机 / PLC、生产数据和部署均为 **NOT_RUN**。
没有修改防火墙、网络地址、现场工程或凭据；本地 gRPC 测试仅使用临时工程、模拟执行和合成数据。
没有执行全仓库 pytest 或额外性能专项。

使用入口见[Runtime 远程访问指南](../../guides/runtime-remote-access.md)。
Dot 的固定接口、版本与交付材料见[Panel Contract v1](../../development/panel-contract-v1.md)。

## 复查后的功能修复（2026-10-30）

用户要求修复复查确认的五项本地功能缺口。源码基线仍为
`0fa2115e337e7499688e4d0efd50539e34104a6a`，下列实现位于未提交工作区，未提交、推送或部署。

| 复查问题 | 本次实现 |
| --- | --- |
| 远端 Designer 关闭导致独立 Runtime 的运行被停止 | 区分客户端连接与执行宿主所有权；远端关闭只取消观察、释放通道，本窗口自有 embedded 宿主保留停止和释放；观察取消与明确 pending Stop 分开 |
| 兼容页面发布后活动展示掉订阅 | 保留活动默认/多流程会话和显式查看会话；重建页面、资源及历史客户端，按原 Job 的冻结 Coverage 绑定；显式查看 J2 不改变停止目标 J1，原默认完成态结果仍退役 |
| Start/Stop 的版本和身份未完整贯通 | 正式生产 Start 在同一在线快照下读取 inputs/capture/revision 并携带 CAS，冲突拒绝且不自动重试；Designer Stop 保存原实例/工程/runGroup 身份，元信息重查仍限定原实例 |
| Web 预览只有 fixtures 交接 | 增加示例与当前 Runtime 只读两个入口；后台核对正式工程/历史声明，固定 endpoint/options/instance，通过临时 `--context` JSON 交给 Dot；SDK 1.1.0 同步 DTO/schema/TS/fixtures |
| 历史刷新丢失稳定选择 | 按稳定 rowId 在新结果中重新定位，使用新图片引用；不稳定身份、记录缺失、失败和上下文切换清空；客户端明确遵循 stableRowIdentity 标志 |

新增来源导致采集定义变化的发布仍遵循现有影响分析及显式配置更新流程。本次“发布不中断”针对名称、
布局/组件属性、历史定义等兼容修改，不据此声称支持任意执行/采集定义热更新。

Web 预览的两个正常生命周期问题一并修复：创建本机只读端点时，与连接切换/关闭协调，避免旧线程覆盖新连接；
关闭最后一个 Designer 窗口时等待子进程退出与临时目录清理后自动继续关闭，不增加确认弹窗。
合成 CLI helper 仍不等于 Dot 的真实 renderer。

交叉核对还补齐了合法删除流程的发布分支：正在显式查看已完成流程 A 时，新工程删除 A 并保留 B，
界面退役旧查看并返回当前页面，给出原因；不启动或停止其他 Job，也不改变 B 的运行选择。
后台已采用新工程而 GUI 尚待退役旧查看时，流程标签使用 ID 过渡，避免旧名称索引抛错卡住页面刷新。

本次验证（各组有交集，不汇总为不重复总数）：

| 范围 | 结果 | 证据层次 |
| --- | --- | --- |
| 页面发布、Start CAS、查看选择、预览、合同、稳定历史选择联合组 | **33 passed，53.70s** | 源码 + 本机实际 gRPC + 原生 Qt；日志 `build/runtime-web-plan-repair/combined-regression.log` |
| Designer 远端关闭、Stop 身份、客户端/worker/controller 与 Qt 所有权 | **85 passed，21.12s** | 本机 gRPC 与原生 Qt/可控线程时序，包含 embedded 关闭、远端关闭仍 RUNNING、原身份重查与拒绝实例变化 |
| Designer queued accepted 显式停止及应用退出增量 | **42 passed，20.85s** | 固定“后台已观察、GUI 尚未收到 accepted”时序，覆盖点 Stop 后继续及立即关窗；实际 QApplication.aboutToQuit + 原生 QThread 退出 |
| 历史选择/图片/SQLite/路径及历史 RPC | **46 passed，8.26s** | 源码 + Qt + 本机历史 RPC |
| 历史客户端 stableRowIdentity 标志及 RPC 追加组 | **17 passed，4.29s** | 源码/Qt + 本机历史 RPC |
| Web 预览及最终窗口关闭 | **7 passed，16.04s** | 真实 QProcess 合成 CLI 与原生 Qt 关窗，非真实浏览器 |
| Operator 发布新增回归 | **3 passed，16.40s** | 实际本机 gRPC 发布 + Qt 继续显示、显式已完成查看及新历史组件 |
| Operator 发布末次整组（追加删除流程分支后） | **4 passed，21.98s** | 兼容发布、显式查看与删除后的正常退回；日志 `build/runtime-web-plan-repair/final-publication-regression.log` |
| Operator 原失败项复跑、多流程和 Start CAS | **11 passed，47.50s** | 本机模拟生产，包含无 online authority 旧工程兼容 |
| SDK 1.1.0 干净 venv 安装和导入 | **PASS** | 仅安装 wheel 与声明依赖，`-I` 且阻断 emo_master/PySide2/cv2/numpy/onnxruntime/PIL 导入；运行预览 DTO 与身份检查 |
| 生成 TypeScript | **PASS** | TypeScript 5.8.3，`tsc --noEmit --target ES2020` |
| ZIP/manifest/wheel 与 owning source 一致性 | **PASS** | 每文件长度/摘要、wheel Python 源码逐字节匹配、ZIP 内容及 testzip 检查 |
| 全 src mypy | **PASS，605 source files** | 继续使用仓库现有 mypy 配置；日志 `build/runtime-web-plan-repair/mypy.log` |
| 全 src/tests Ruff、proto check、diff check | **PASS** | 包含生成脚本；日志位于 `build/runtime-web-plan-repair/`，Git 仅有既有换行符提示 |

本次观察到的失败及处理保留如下：

- Designer 首轮新增组 **3 failed / 16 passed**，取消观察仍等待终态并触发 Windows `QThread.wait(-1)` 参数溢出；
  纯断开改为只读一次状态后退出，真正 pending Stop 才等终态，无限等待使用 Qt 的 `wait()`。
- Operator 扩展首轮 **36 passed / 4 failed**，初版 CAS guard 误拒绝没有在线配置 authority 的旧本地工程；
  现保留该路径 `expected_document_revision=0`，正式新工程继续携带真实 revision，后续 11 项复跑通过。
- Web 最后窗口关闭修复前新增组 **1 failed / 1 passed**；旧实现事件循环已退出但 renderer 仍运行、finished
  未处理、临时目录仍存在。现关窗等待 closed 后自动重试，修复后的 7 项通过。
- 交叉审查确认了“Start 已受理但 Qt 接受信号排队时点击 Stop”的漏停窗口；现明确 Stop 会唤醒观察 worker，
  在退出时消费停止意图并携带原身份，纯 detach 不产生 Stop。新增可控原生 Qt 回归包含在上述 42 项中。
- 删除已完成且正在查看的流程，修复前实际 RPC + Qt 回归 **1 failed**，捕获 `KeyError: 'main'` 并导致
  刷新/关闭超时；修复后同例 **1 passed，7.10s**，验证零新增 Start/Stop、旧查看退役和剩余页面/启动目标正确。
- 旧的 shared-page Windows 原生 access violation 仍是历史已观察、后续未复现、原因未确认；上述 Qt 参数修复
  不能当作已查明该原生崩溃。既有 `E_DEVICE_OUTCOME_UNKNOWN` 证据与语义保留。

本次 SDK 1.1.0 当前交接包为 `build/panel-contract-v1.zip`，87,605 字节，34 个交付文件及 manifest。
ZIP SHA-256：`d90b4b182cc2b78e095c0f896ef409442b5c7dbf0601b8e1926656426fef1ecb`。
wheel SHA-256：`669135290fcce95c0e25dd8cfd751aa785696dd695e9ed225f84c410ee359b9c`。
旧交接 ZIP 保留为 `build/panel-contract-v1-before-repairs.zip`，不删除其历史摘要。
实际校验输出为 `build/runtime-web-plan-repair/contract-verification.json`；样例的 Designer 打开仍为 NOT_RUN。
