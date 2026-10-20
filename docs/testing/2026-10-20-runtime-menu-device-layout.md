# 主界面 Runtime 菜单、设备管理布局与首次写入验证记录

日期：2026-10-20。复审后三项源码修正完成，固定最终源码的 37 文件受影响回归为 **`393 passed in 211.79s`，0 失败、0 错误、0 跳过**。初次实施的 `372 passed in 196.71s` 属于复审前快照，不覆盖后续修正。最新证据见 §6；真实桌面、冻结与实机验收未执行。

关联计划：[主界面 Runtime 菜单与设备管理布局整改计划](../plans/2026-10-20-device-manager-layout-runtime-entry-plan.md)。
使用入口：[设备、Dispatch 与在线配置操作入口](../guides/device-dispatch-online-configuration.md)。

## 1. 源码身份与环境

| 项目 | 实际值 |
|---|---|
| 工作区 | `C:/Users/jsdfhasuh/my_scripts/emo_master` |
| 基线与当前 HEAD | `ef5af5066599bc57a5182067e31775f8798e8107` |
| 实施状态 | 基于上述 SHA 的未提交工作区修改；未创建新提交，不能将 HEAD 当成已包含本轮修改的交付 SHA |
| Python | `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，3.10.21 |
| Qt | PySide2 5.15.2.1，`QT_QPA_PLATFORM=offscreen` |
| 工程与设备 | 临时工程、合成图片、模拟设备或 mock 命令；内嵌 Runtime 与本机隔离 loopback gRPC |
| 初次实施源码快照 | [source-manifest.json](../evidence/2026-10-20-runtime-menu/source-manifest.json)，34 个本轮源码、协议、测试与渲染脚本文件的 SHA-256；复审后快照见 §6 |

原先已有的两项无关脏文件没有纳入本轮修改，结束时再次校验确认 SHA-256 未变：

| 文件 | 保留的 SHA-256 |
|---|---|
| `src/emo_master/apps/designer/ui/project_entry_dialog.py` | `C55ECE45F7F3474EB49D04B514C6CDB2217F2149763982821E3D99A109B2C40D` |
| `tests/designer/test_startup_project_entry.py` | `D0CBA92F078B17ABA249D01FB8F5C6D61FD12CCD294B080A4406E8FBD9D54FDE` |

保留既有现场工程、数据、记录和当前运行进程。未构建、提交、推送、部署、改址或访问真实相机 / PLC。

## 2. 已实施的界面与行为

```text
菜单栏：文件 | 运行 | 编辑 | 视图 | Runtime | 设备管理
工作区切换：[流程设计 / 页面设计]
```

- 文件：保留新建、打开、保存、另存、最近工程；工程资源 / 现场绑定从设备管理器移入主界面。
- 运行：运行流程、停止运行、停止全部及既有运行管理继续归此菜单，不合并到 Runtime。
  `F5` 运行、`F6` 停止沿用同一 QAction；两种工作区均保留运行菜单，按实际任务状态使能。
- 编辑：撤销、重做、整理流程。沿用 `Ctrl+Z`、`Ctrl+Shift+Z` / `Ctrl+Y`、`Ctrl+L`，
  不另建撤销栈，不向画布工具区新增布局页。
- Runtime：连接、写入、在线监视、页面发布。写入与页面发布复用同一工程配置窗口，窗口归主窗口所有。
- 顶部主工具栏仅工作区切换；没有打开、保存、运行、停止、撤销、重做、写入、图片测试或整理流程副本。
  原有运行摘要、结果面板和画布自身导航不属于此次撤除范围。
- 删除全局图片测试入口。使用 `vision.io.image_loader` → 处理节点 → 输出的普通流程，
  执行端仍是当前 Runtime，而非 Designer 的独立通用图片测试器。
- 在线监视是独立、非模态、只读窗口。重复打开复用现有窗口；关闭后可重开，连接切换时关闭旧观察窗口。
  不替换编辑区，不检查或提交编辑输入，不自动保存、写入或启动任务，普通监视不展示停止命令。
- 设备管理器只保留列表和所选设备动作。列表上方为新建、导入、导出、更多、刷新；
  详情区为测试连接、按实际状态交替的连接 / 断开、诊断、更多；应用 / 放弃靠近属性。
  底栏只有“保留草稿并关闭”，没有隐藏的整工程比较或写入链接。

### 2.1 空 Runtime、首次写入及资源

基线的主要问题不是“所有保护都太严格”，而是整工程写入藏在设备窗口中，且比较窗口要求已有在线快照，
没有首次下发出口。保留显式写入、设备串行访问、版本与影响校验；补齐正常状态和正确入口，而不是删除必要保障。

1. `GetDeviceStates` 增加可识别错误码；未下发、在线工程不同、连接失败分开展示。
   空 Runtime 不再作为普通设备故障，本地草稿仍可编辑。
2. 对 schema 2.6，增加局部 `InitializeProjectConfiguration` RPC，使用当前有效草稿、资源根目录和真实来源文件身份。
   不用“保存工程 + LoadProject”覆盖磁盘，也不把草稿写进临时目录后丢失相对资源上下文。
3. 校验不激活设备或启动 Job；确认写入后复用现有准备及私有资源副本机制。
   首次配置与成功回执处于现有 SQLite 的同一提交，丢回复后查询原请求，不自动重发。
4. 成功保留真实来源工程身份，随后正常运行识别同一工程，不因虚拟文件名误报工程副本未加载。
5. 正式提交遵循已有设备连接策略；不创建 Job、采集图片或执行 PLC 节点，
   但不能笼统声称按 `on_project_open` 配置的设备也绝不会连接。
6. 提交时若已有在线工程，转回比较；激活阶段异常保留已提交事实和发布错误，不伪装回滚或允许再次首次覆盖。
7. 本轮支持本机内嵌及共享本机文件系统的 loopback Runtime。跨机器空 Runtime 安装不在本轮能力内，
   非回环地址明确拒绝发送 Designer 本地路径。端口转发本身不证明共享文件系统。
   远程已有工程沿用既有比较、上传、取消与显式写入；本轮没有真实跨机器验收。

设备发现不再要求先下发工程：明确点击后只读获取 Runtime 发现能力，再进入现有 discovery-only 窗口；
该窗口不显示实物改址。只有再次点击发现才枚举设备，采用结果仅修改草稿。
正常打开 / 刷新设备管理器不会隐式发现、连接或改址。旧 Runtime 不支持时显示升级原因，
不会被先前“当前工程未下发”的提示遮盖。

### 2.2 回归直接发现的正常停止问题

真实隔离 Runtime 并行任务回归曾出现：Runtime 已 `ABORTED`，Designer 仍停在 `STOPPING`。
根因是普通停止先取消事件观察，停止 RPC 的非终态回复可能成为最后状态。
局部修正为正常停止继续使用原订阅观察到终态，并阻止较早的 `STOPPING` 回复覆盖已观察终态；
关闭清理仍显式退订。没有增加轮询框架、自动重发或全局停止要求。

## 3. 已执行的软件验证

| 验证批次 | 结果与范围 | 证据 |
|---|---|---|
| 最终合并回归，含最后离线发现修正 | **`372 passed in 196.71s`**；37 个受影响测试文件，失败 / 错误 / 跳过均为 0 | [acceptance-regression.log](../evidence/2026-10-20-runtime-menu/acceptance-regression.log)、[XML](../evidence/2026-10-20-runtime-menu/acceptance-regression.xml) |
| 最后补充的离线发现 / 旧 Runtime 提示测试所在文件 | `12 passed in 8.01s`；仅聚焦证据，不与合并计数相加 | [offline-discovery-final.log](../evidence/2026-10-20-runtime-menu/offline-discovery-final.log) |
| 上一轮合并快照，尚不含最后离线发现修正 | `369 passed in 196.05s`；保留历史，不替代最终快照结果 | [final-regression.log](../evidence/2026-10-20-runtime-menu/final-regression.log) |
| Proto 生成代码同步 | `scripts/gen_proto.py --check` 退出码 0 | [static-checks.log](../evidence/2026-10-20-runtime-menu/static-checks.log) |
| Ruff | 本轮 Python 源码 / 测试及渲染脚本通过；排除生成代码和两项无关脏文件 | 同上 |
| 差异格式 | `git -c core.safecrlf=false diff --check` 退出码 0 | 同上 |
| 原生 Qt offscreen 渲染 | 7 张最终布局图；DPR=1；渲染自身未加载在线工程、未启动 Job、未创建设备驱动 | [manifest.json](../evidence/2026-10-20-runtime-menu/manifest.json)、[render_offscreen.py](../evidence/2026-10-20-runtime-menu/render_offscreen.py) |

这是**受影响范围的合并定向回归**，不是全仓完整测试。包含原生 Qt 控件 / 菜单 / 快捷键信号、
模拟设备与临时工程，以及真实启动的本机隔离 Runtime / Worker；不等于人工真实桌面、冻结产品或实机验收。

### 3.1 失败证据与修正

- [regression.log](../evidence/2026-10-20-runtime-menu/regression.log) 原样保留 `335 passed, 2 failed`：
  一项旧测试要求已经被规范 QAction 替代的 legacy Delete QShortcut 重新使能；
  改验现有 QAction 与工作区可用性，不修改产品以复活第二套快捷键。
  另一项为上文正常停止卡滞，保留当时 Runtime 终态和 Designer 状态不一致的实际输出。
- 停止修正后聚焦 `63 passed in 90.64s`，见 [stop-regression.log](../evidence/2026-10-20-runtime-menu/stop-regression.log)；
  最终仍重新合并执行，不用这 63 项替代或累加最终回归。
- 从精确基线 SHA 导出的隔离副本，原并行停止 / 陈旧版本两项测试 `2 passed in 16.64s`，
  单凭该时序未稳定复现。复制确定性的“停止前不应取消观察”辅助反例后得到预期失败，见
  [baseline-stop-counterexample.log](../evidence/2026-10-20-runtime-menu/baseline-stop-counterexample.log)。
  这是源码辅助反例，不是已执行的产品或真实硬件验收。
- 所有中间失败日志保留，不弱化节点、资源、任务范围、撤销和磁盘隔离等仍然有效的断言。

### 3.2 UI-01—UI-16 证据映射

下表“通过”均限本节的软件范围；涉及人工桌面和设备的部分单列。

| ID | 软件证据 | 结论 / 边界 |
|---|---|---|
| UI-01 | `test_main_window_menubar.py`、`test_runtime_menu_publication.py`、`test_workspace_modes.py` | 菜单顺序、作用域、单一工具栏切换、配置窗口所有者通过 |
| UI-02 | `test_device_workspace.py`、`test_device_workspace_usability.py` | 移除工程级命令、分组、唯一关闭底栏通过 |
| UI-03 | `test_runtime_menu_publication.py::testEmptyRuntimeDeviceManagerKeepsNormalDraftEditing`（两种传输） | 未下发提示、有效编辑、无自动写入或 Job 通过 |
| UI-04 | `test_initial_project_configuration.py`、`test_runtime_menu_publication.py` | 当前草稿、相对资源、来源身份、磁盘与撤销隔离、显式确认通过 |
| UI-05 | `test_project_configuration_ui.py`、Runtime configuration / transition / owners / resource lease 测试 | 选中变更、未选中保留、影响确认及既有策略通过 |
| UI-06 | 不同在线工程 UI 测试、initial intervening-online 测试、`test_online_run.py` 陈旧快照测试 | 完整替换、在线版本变化和首次写入不能覆盖中途加载通过 |
| UI-07 | initial invalid-input / schema 测试、unreachable UI、旧 Runtime 离线发现提示测试 | 校验失败可修正、不可达不误报首次状态、旧端升级提示通过 |
| UI-08 | device workspace / usability / plan-completion UI 测试 | 草稿应用、切换、放弃、关闭重开和非法属性定位通过 |
| UI-09 | device workspace UI、Runtime simulated device 测试、offline discovery mock UI | 合并刷新、按所选对象操作、收纳后的能力可达通过；实物诊断 / ForceIP 未执行 |
| UI-10 | `testResourcesAndPagePublicationDoNotConstructDeviceManager`、`test_online_run.py` | 资源、页面发布独立于设备窗口，写入与运行边界通过 |
| UI-11 | toolbar / small-window / device-sizing 测试及 7 张渲染图 | 逻辑尺寸、滚动与关闭入口通过；150% / 200% 真实桌面 DPI 未执行 |
| UI-12 | resource upload-cancel / targeted repair UI、Runtime transfer / journal / lease 测试 | 既有上传、取消、查询与明确终态通过；没有真实远程主机更新验收 |
| UI-13 | initial image graph 测试、real menu run 测试，两种传输实际执行 `image_loader → blur → output` | 普通流程输入和执行通过；移除旧全局入口；没有 YOLO 模型专项验收 |
| UI-14 | `test_flow_layout_qt.py`、`test_flow_layout_routing.py`、`test_designer_shortcuts.py` | 整理、撤销、重做菜单 / 快捷键及原撤销栈通过，不新增工具区页 |
| UI-15 | reusable read-only monitor UI、连接切换 UI、`test_online_graph.py` | 独立非模态、复用 / 关闭 / 重开、草稿不变与只读通过 |
| UI-16 | real menu / F5-F6 run-stop UI（内嵌 / loopback × 菜单 / 快捷键）、parallel Runtime UI、controller / worker 测试 | 实际启动、切到页面设计后停止、所选与全部范围、正常停止终态通过 |

### 3.3 可复现命令

从仓库根目录运行；不使用现场工程或真实设备。完整文件清单由下面命令给出，
JUnit XML 保存每个测试名称及实际计数。

```powershell
$p = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
$evidence = Join-Path $env:TEMP ('emo-runtime-menu-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$files = @(
  'tests/designer/test_main_window_menubar.py',
  'tests/designer/test_main_window_layout.py',
  'tests/designer/test_toolbar_width.py',
  'tests/designer/test_visual_layout.py',
  'tests/designer/test_small_window_layout_repair.py',
  'tests/designer/test_device_workspace.py',
  'tests/designer/test_device_workspace_usability.py',
  'tests/designer/test_device_workspace_sizing.py',
  'tests/designer/test_device_plan_completion_ui.py',
  'tests/designer/test_project_configuration_ui.py',
  'tests/designer/test_designer_shortcuts.py',
  'tests/designer/test_flow_layout_qt.py',
  'tests/designer/test_flow_layout_routing.py',
  'tests/designer/test_runtime_connection_ui.py',
  'tests/designer/test_runtime_menu_publication.py',
  'tests/designer/test_online_run.py',
  'tests/designer/test_project_resource_upload_cancel_ui.py',
  'tests/designer/test_targeted_resource_repair_ui.py',
  'tests/designer/test_runtime_controller.py',
  'tests/designer/test_runtime_worker.py',
  'tests/ui/page_designer/test_workspace_modes.py',
  'tests/ui/page_designer/test_workspace_switch.py',
  'tests/ui/page_designer/test_workspace.py',
  'tests/ui/page_designer/test_preview.py',
  'tests/ui/page_designer/test_parallel_runtime.py',
  'tests/runtime/test_initial_project_configuration.py',
  'tests/runtime/test_device_runtime.py',
  'tests/runtime/test_project_configuration.py',
  'tests/runtime/test_configuration_transition.py',
  'tests/runtime/test_configuration_export_copy.py',
  'tests/runtime/test_configuration_owners.py',
  'tests/runtime/test_configuration_resource_leases.py',
  'tests/runtime/test_configuration_resource_journal.py',
  'tests/runtime/test_prepared_resources.py',
  'tests/runtime/test_online_graph.py',
  'tests/runtime/test_project_resource_transfer.py',
  'tests/runtime/test_targeted_resource_repair.py'
)
& $p -m pytest @files -q --tb=short --junitxml="$evidence/regression.xml"
& $p scripts/gen_proto.py --check
$manifest = Get-Content docs/evidence/2026-10-20-runtime-menu/source-manifest.json -Raw | ConvertFrom-Json
$pythonFiles = @($manifest.files.path | Where-Object { $_.EndsWith('.py') -and $_ -notlike '*/generated/*' })
& $p -m ruff check @pythonFiles
git -c core.safecrlf=false diff --check
$env:PYTHONPATH = 'src;.'
& $p docs/evidence/2026-10-20-runtime-menu/render_offscreen.py
```

渲染显式加载 Windows 微软雅黑字体，解决 offscreen 字体库不枚举桌面字体的前提问题，
不修改产品字体代码。主界面尺寸 1280×720 / 642×452，设备窗口 1200×760 / 642×452，
另有 Runtime 菜单、首次写入和空监视窗口；实际大小与 DPR 见渲染 manifest。

## 4. 适用范围内的人工走查

以下为**待执行步骤**，不是已完成结果。仅使用副本、临时图片和无真实设备的工程：

1. 用包含本轮源码的独立测试进程打开临时 2.6 工程，核对六项菜单和仅工作区切换的主工具栏。
   核对打开 / 保存归文件、运行 / 停止归运行、撤销 / 重做 / 整理归编辑。
2. 保持本机 Runtime 为空，打开设备管理器、刷新并修改模拟设备名称；确认未下发为正常状态，
   关闭后草稿保留，磁盘未保存。真实设备发现和测试连接不在无硬件走查中点击。
3. 从 Runtime 写入进入配置窗口；修改草稿后校验并取消，再打开明确确认；
   检查草稿、资源、在线版本、来源路径及不自动创建任务的结果。
4. 使用图像加载算子及无副作用的处理节点，通过运行菜单或 F5 执行。
   对可持续的模拟流程切到页面设计，通过运行菜单或 F6 停止；核对停止所选与停止全部的范围。
5. 在流程中整理、撤销、重做，确认位置变化可撤销，节点参数与其他流程不变。
6. 打开在线监视，重复打开、关闭、重开；确认不替换画布，不保存草稿或停止任务。
   验证资源与页面发布不需要打开设备管理器。
7. 在可控真实桌面以实际可用的常见窗口尺寸和 100% / 150% / 200% 缩放走查，逐项记录实际执行组合；
   不能将本轮 DPR=1 的 offscreen 图替代这些结果。

正在运行的旧 Python 进程或冻结 EXE 不会热加载这些源码。要看新界面需后续由用户安排重启源码进程；
冻结版本需另行构建，Designer 和独立 Runtime 应同步更新协议实现。本轮未替用户重启、构建或替换现有运行程序。

## 5. 未执行、暂缓与非阻断边界

- 未执行全仓完整回归、真实桌面人工 UI / 高 DPI、冻结自测、安装包验证、跨机器部署或真实设备验收。
- 跨机器空 Runtime 的工程安装是明确的后续能力，不以发送本地路径冒充已实现。
- 本轮不改现场工程、设备连接策略、周期配置归属、生产调度或已接受暂缓的 F01 组合风险。
  未执行的实机项目不等于已证实软件缺陷，也不要求先清空无关风险才推进适用范围的人工走查。
- 源码功能和定向软件验证完成后，可以推进上述无设备人工验收；是否构建、发布或实机操作另需明确授权。

## 6. 复审后的三项修正与复测（2026-10-20）

### 6.1 复审证据与修正范围

原复审反例为 `4 failed in 4.15s`，对应三项真实缺口（停止失败含两种回调）。失败证据复制保留于
[review-before.log](../evidence/2026-10-20-runtime-menu/review-fixes/review-before.log) 及同名 XML，
没有覆盖初次实现的 372 项通过结果，也没有用旧通过结果否认新反例。

| 缺口与触发条件 | 最小修正及保留行为 | 正式回归 |
|---|---|---|
| 首次写入资源准备持续进行时，工程锁阻塞查询与取消，取消请求超时后仍可能提交 | 首次写入加入现有协调器的请求表 / 活动写入者 / 后台线程，重用取消 Event；准备阶段不长期占工程锁，提交才持锁重验并写入配置、变量定义、回执；取消清理本次准备资源，不安装配置或激活设备；失败 / 取消的原 ID 不重放 | `test_initial_project_configuration.py`：内嵌和 loopback 的复制中 / 复制后取消、查询、重复原 ID、竞争首次写入、旧 LoadProject、失败可查询与取消后明确新写入；`test_runtime_menu_publication.py`：真实按钮查询与取消，草稿 / 磁盘 / Job 隔离 |
| 观察器已收到终态，较早的停止 RPC 随后报传输错误或失败回包 | 仍记录错误和消费回包，但不把已确认的 ABORTED / COMPLETED / FAILED 改回 RUNNING；未观察到终态的停止失败处理不变 | `test_runtime_controller.py`：三种终态 × 两类失败；原停止失败和 STOPPING 回包回归保留 |
| 离线发现能力请求未返回时关闭设备管理器，响应晚到仍打开模态子窗口 | 成功关闭撤销该次待弹窗意图；关闭再重开不会复活它；不关闭或另建元数据读取器，非法属性导致关闭被拒绝时不撤销当前意图 | `test_device_plan_completion_ui.py`：关闭、关闭后重开、非法输入导致关闭失败，及后续新的显式发现请求 |

首次写入校验仍不创建适配器或连接设备。正式提交继续执行原 `on_project_open` 连接策略，
适配器准备占用先释放再允许连接事件；新增模拟设备测试同时覆盖 `on_project_open` 和
`on_first_use`，断言连接策略结果、采集计数仍为 0、没有 Job。提交后激活报错仍保留 COMMITTED，
并核对持久回执中的发布错误。显式写入、设备串行访问、已有在线写入和资源归属没有删除。

本次相对复审前源码快照修改 10 个源码 / 测试文件，另更新本计划、报告和指南；新增的两个 Runtime 文件是
`project_config/coordinator.py` 与 `project_config/rpc.py`。首次下发不再保留旧同步草稿加载分支，
旧 `LoadProject` 文件加载行为保留，只在已有配置写入未结束时返回忙碌。

### 6.2 复测记录与源码身份

本节各轮结果分别记录，不相加，也不把中途测试覆盖为最终快照结果：

| 轮次 | 实际结果 | 说明 |
|---|---|---|
| 首轮正式定向回归 | `70 passed, 4 failed in 46.66s` | 新增取消测试错误地要求资源目录绝对为空，误把 Runtime 启动时已有 `.owners` 租约文件当作残留。改为核对取消前后文件集合完全一致；未删除租约、未放宽资源回收断言。见 `focused-1.log/xml` |
| 定向复测 | `78 passed in 47.54s` | 包含连接策略回归；随后新增原生 Qt 首次写入查询 / 取消按钮测试及持久回执断言，故该结果不单独覆盖最终快照。见 `focused-2.log/xml` |
| 原审查脚本复测 | `4 passed in 2.51s` | 原四个反例不修改断言直接通过；正式测试另外覆盖更多正常分支。见 `review-counterexamples.log/xml` |
| 中间合并回归 | `393 passed in 211.41s` | 原 37 文件清单加本次用例；运行中补回首次 RPC 原有的 `assertMutationAllowed` 准入调用并补持久回执断言，因此另执行固定最终源码的合并复测。见 `regression-1.log/xml` |
| **最终固定源码合并回归** | **`393 passed in 211.79s`，0 失败 / 错误 / 跳过** | 原 37 文件清单，新增 21 项；涵盖最后的准入和回执断言修正。见 [acceptance-regression.log](../evidence/2026-10-20-runtime-menu/review-fixes/acceptance-regression.log) 和同名 XML |
| 最终静态及协议检查 | Proto 同步、作用域 Ruff、`git diff --check` 全部 exit 0 | 复用原清单并加入本次 coordinator / rpc；本次不新增协议字段。见 `validation-summary.json` 与对应日志 |

所有本节日志与 XML 位于 [review-fixes](../evidence/2026-10-20-runtime-menu/review-fixes/)。
固定最终源码的复测使用 §3.3 相同 37 个测试文件，输出改为该目录下的 `acceptance-regression.log/xml`；
无须扩大至全仓或访问现场设备。源码身份以
[review-fixes/source-manifest.json](../evidence/2026-10-20-runtime-menu/review-fixes/source-manifest.json) 为准，
包含 36 个源码 / 协议 / 测试 / 渲染脚本哈希、10 个本次修正文件清单，以及保留的 2 个无关脏文件哈希。
HEAD 仍为 `ef5af5066599bc57a5182067e31775f8798e8107`，未创建提交；36 项是验证范围快照，不是全仓或待发布文件集合。
最终逐项复核 36 个快照文件与 2 个无关脏文件，38 项 SHA-256 全部匹配。

本次没有改变菜单归属、工具栏、快捷键或窗口布局，所以没有重拍原 UI 截图。
原 offscreen 图仍只代表对应初次快照的视觉证据；本次窗口行为通过原生 Qt offscreen 事件验证。
未执行全仓完整源码测试、真实桌面 / 高 DPI、冻结自测、构建、部署或真实相机 / PLC 验收，
也未重启用户正在使用的程序。源码进程需后续重启，冻结 EXE 需另行构建才能包含这些修改。
