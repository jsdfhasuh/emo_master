# 设备管理易用性与草稿操作链路实施记录

日期：2026-10-21。依据：[设备易用性计划](../plans/2026-10-21-device-usability-improvement-plan-v1.md)。

U01—U10 已落实；C1—C6 对应的软件链路、受影响回归及原生 Windows Qt 自动化通过。
已完成自动化驱动的真实桌面关键路径走查，未把它记作人工完整 UI、冻结产品或真实设备验收。
本轮没有访问真实相机／PLC，没有改现场工程、网卡、凭据或底层通信协议。

## 固定源码与工作区

| 项目 | 实际值 |
| --- | --- |
| 仓库与分支 | `jsdfhasuh/emo_master`，`agent/runtime-workflow-architecture-v1` |
| 实施基线 | `756e526c1d2c657b3ff793fed7abe904d38b8d48`，开始及交付前均核对实际远端，后续已完成的单次日志修复未重复实现 |
| 最终被测源码 | `e2a9d867dc3a7069c7e91d2e68da5dfa23ee1fbf` |
| 最终源码 tree | `38a0f6252ddb7acf3019c4766d1af59f98614e55` |
| 最终测试导入位置 | `C:/Users/JSDFHA~1/AppData/Local/Temp/emo-f-huz7f4e2/source/src/emo_master/__init__.py` |
| 最终证据 | [final-snapshot-delivery](../evidence/2026-10-21-device-usability-improvement/final-snapshot-delivery/validation.json)，含每条 argv、cwd、开始时间、退出码、环境和文件 blob |
| 文档交付标识 | 包含本报告的提交；`git log -1 --format=%H -- docs/testing/2026-10-21-device-usability-improvement.md`，实际 SHA 与推送核验另随交付回复给出 |

适用指令为根 `AGENTS.md`。实际仓库为 `D:/jsdfhasuh/documents/my_project/emo_master`，
`C:/Users/jsdfhasuh/my_scripts/emo_master` 为同一工作区入口。
工作区同时存在图像保存、视频算子及调试资源等并行工作，未纳入本轮提交。
用独立 archive 快照验证本轮提交，避免把这些未提交源码混作交付源码。
10 个无关已跟踪文件以 SHA-256 保护；最终核对和显式提交候选见本轮证据。
`test_device_definition_feedback.py` 的本轮反例早于保护清单采集，已在清单说明中明确剔出无关文件核对。

## U01—U10 最终行为

下表源码路径以 `src/emo_master/` 为根，其中 `ui/`、`state/`、`page_designer/` 简写位于 `apps/designer/`，
`runtime/` 简写位于 `apps/runtime/`；测试以 `tests/` 为根。每项状态均为“已实施／本轮软件验证通过”。

| 项目 | 原因与最终行为 | 修改位置 | 本轮证据 |
| --- | --- | --- | --- |
| U01 分阶段创建 | 共用创建窗口显式传递 enabled；禁用空通道 Server 可创建、保存、重开且不监听。启用空通道及已有坏通道行仍拒绝 | `apps/designer/ui/device_wizard.py`、`tcp_device_form.py` | `test_device_definition_feedback.py` 两个直接反例；C1—C6 连续测试从节点向导原子创建禁用 Server |
| U02 发布引导 | 当前输入、文件保存和当前设备与在线配置分别显示。设备页先同步有效输入、退出观察模态，再转入唯一发布窗口；scope 有效时返回原设备、页签及滚动位置 | `ui/device_workspace.py`、`page_designer/coordinator.py` | `test_device_usability_publication.py`；C3 未保存文件即发布；桌面取消返回；24 个副本列表反例及回归 |
| U03 可读差异 | 主表展示设备名称／类型、动作、字段摘要和影响；原 path 保存在 UserRole，完整前后 JSON 在详情，行高有界 | `ui/project_configuration.py` | 可读摘要／原路径／校验后行高断言；连续链真实模态发布；桌面摘要与完整详情 |
| U04 编辑与恢复 | 有效编辑自动应用；固定“立即应用到草稿”“恢复当前输入”和统一工程撤销／重做。历史恢复后刷新基线与控件，避免旧值回写；名称同步树、表、标题 | `ui/device_workspace.py`、`page_designer/coordinator.py` | `test_device_workspace_usability.py`、尺寸回归；桌面空名称被拒→恢复→进入发布 |
| U05 中文定位 | IP／相机标识／通道行列错误保留输入并定位；真实 localhost 拒绝连接沿错误 cause 识别为 `E_DEVICE_CONNECTION_REFUSED`，保留原技术详情 | `ui/device_wizard.py`、`tcp_device_form.py`、`runtime/devices/tcp.py`、`plugins/builtins/_tcp_transport.py` | 原生 Qt 焦点、IMV 缺项、异常兜底、正式 RPC 拒绝连接与 owner 清理；未改重试和网络行为 |
| U06 待绑定流程 | Designer 明确允许缺 slot／必需 selector，已填坏 ID、类型、selector、来源仍拒绝。节点保存重开、位置、端口、边、0 常量及模板重映射保留。元数据加载后现有画布刷新缺项提示 | `state/device_catalog.py`、`state/workflow_package.py`、`ui/device_wizard.py`、`ui/main_window.py`、`core/plugin/devices.py` | `test_device_usability_drafts.py`、C4／C5；catalog 首次失败与修复；完整编译／候选／运行包仍拒绝不完整绑定 |
| U07 导入辨识 | 冲突名依次加 `（导入副本）`／序号，并为后缀预留 160 字符上限。批内名称参与冲突判断，保留新 ID、默认禁用和一次导入一次撤销；复用或完整运行包不改原名 | `state/device_package.py`、`state/workflow_package.py` | 副本名／长名称／批内冲突／原子撤销；包回归和桌面实际导入副本 |
| U08 表单便利 | 设备表单以描述符 `xAdvanced` 折叠高级项，保留完整值与校验；错误自动展开定位。中文枚举只变标签，节点来源常量编辑器仍直接显示；类型选择依据 combo data | `ui/param_form.py`、`device_wizard.py` 及 6 个内置设备声明 | 高级值保留与实际 Qt 焦点、中文枚举值、同名类型及普通表单回归 |
| U09 测试反馈 | 两个设备入口使用真实 `result.message`；端点可用、远端连接和业务成功明确分开；只清除对应字段过期反馈，跨 scope 丢弃旧异步结果。IMV 发现沿既有支持能力判断 | `ui/device_wizard.py`、`device_workspace.py` | 端点测试消息一致、scope 反例、背景反馈与 IMV 发现回归；没有新增发现／改址接口 |
| U10 菜单归属 | “设备列表操作”与“设备维护”清楚分工；继续走原删除引用保护、维护与诊断路径 | `ui/device_workspace.py` | 现有引用／维护回归、固定操作条与桌面截图；没有增加确认链 |

## C1—C6 连续验收

验收由一个新增连续工程测试和既有三类包／A1—A6 夹具共同覆盖，未把每条另建为重复 runner。
`testC1ThroughC6PendingDraftPublicationAndRun` 使用真实内嵌 Runtime、DeviceManager、驱动、正式 spawned Job、
Qt 模态发布窗口与 localhost，另有定向 Qt／状态测试补齐恢复、错误焦点及图结构。

| 场景 | 实际执行内容和结论 |
| --- | --- |
| C1 分阶段 TCP | 空通道禁用设备与待 channel 节点一起提交、保存重开；未建立监听且端口可绑定。新建与设备端点消息一致。空 IP 行、启用空通道、高级值与焦点由受影响表单测试通过 |
| C2 编辑恢复 | 改名自动应用、统一撤销重做、无效输入恢复、不重新写回旧值、固定按钮尺寸均通过；真实桌面复验恢复入口和导入副本 |
| C3 发布运行 | 从设备页排队进入真实发布模态；草稿未保存即可校验和 COMMITTED，磁盘仍保持旧内容。窗口关闭后 listener 仍就绪；正式 TCP Job 实际发送 `USABILITY-BYTES`，模拟相机输出逐像素等于预期 8×6×3 数组 |
| C4 局部发布 | 同一在线工程保留未选连接草稿与不完整相机节点，只选 gain 成功；候选等于 selectedCandidate，在线 serial 未混入草稿值。选择不完整节点时拒绝并带 workflow/node/fieldPath，原在线流程随后仍可运行 |
| C5 待绑定补齐 | 当前向导新建禁用 Server，保留 deviceId、缺 channelId；严格完成拒绝而“保存待绑定节点”成功。保存重开／模板流转保留。补通道、启用并补 selector 后发布并正式运行；相机重开与移动保留 ID／边／端口／0 常量，失效引用及错值仍拒绝 |
| C6 包与再次运行 | 同名设备包连续导入、新 ID、禁用和整次撤销通过；模板缺绑定继续编辑、完整运行包缺绑定准确拒绝。复用本轮 A5，设备／流程／完整运行包分别在新目录、隔离 Runtime 导入后正式运行，记录实际业务字节、状态与诊断 |

可复核产物保留在 `final-snapshot-delivery/acceptance/`、`usability-chain/`。
三类包导入的正式 Job 分别为：

- 设备包：`9f725a95-f9f4-448e-acb7-aba0c00317ac`。
- 流程包：`e3b7844d-5738-4bd3-9a60-6567eadb3d3a`。
- 运行包：`11c8d19f-25e4-4ab0-b0c0-52096e3564c8`。

这些是本轮最终快照的实际 Job，详见 `acceptance-results.json`，不是历史报告的 Job。

## 实际环境、命令和结果

环境：Windows `10.0.26200`、conda Python **3.10.21**、PySide2 **5.15.2.1**、Qt **5.15.2**。
所有测试使用临时工程、模拟相机／PLC、第三方测试驱动及 localhost。Qt 平台为 `windows`。

最终完整入口（工作目录为仓库根）：

```powershell
$env:QT_QPA_PLATFORM='windows'
$env:PYTHONIOENCODING='utf-8'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  docs/evidence/2026-10-21-device-usability-improvement/run_validation.py `
  --repo D:/jsdfhasuh/documents/my_project/emo_master `
  --source-sha e2a9d867dc3a7069c7e91d2e68da5dfa23ee1fbf `
  --output-dir D:/jsdfhasuh/documents/my_project/emo_master/docs/evidence/2026-10-21-device-usability-improvement/final-snapshot-delivery
```

输出目录要求未存在。重跑时使用新目录，保留首次失败和已有快照。
入口复用原 TCP closure runner 的 archive、CHAIN、REGRESSION、导入校验和产物保留逻辑；旧 runner 未修改。
各子命令完整 argv 和退出码在 `validation.json`，不是事后按测试名称推测命令。

| 最终快照批次 | 本轮实际结果 | 证据 |
| --- | --- | --- |
| 导入、环境和改动文件 blob | exit 0；所有导出文件与固定提交 blob 一致 | `environment.log`、`validation.json` |
| CHAIN | **61 passed，86.88s** | `chain.log`、`chain.xml` |
| REGRESSION | **348 passed，2 deselected，133.30s** | `regression.log`、`regression.xml` |
| Ruff | 21 个受改 Python 文件，exit 0 | `ruff.log` |
| mypy | 13 个受改源码文件，exit 0 | `mypy.log` |
| 协议生成一致性 | `python scripts/gen_proto.py --check`，exit 0 | `proto.log` |
| 源码差异 | `git diff --check 756e526c… e2a9d867…`，exit 0 | `diff-check.log` |
| 本轮证据脚本 | `python -m ruff check .../run_validation.py .../desktop_demo.py`，exit 0 | `delivery-checks.json` |

最终计 **409 passed**；没有新增排除项。唯一排除仍是
`test_runtime_validation_feedback.py::testUpgradedDraftResourceFailureCanBeFixedAndRevalidated` 的两个旧迁移参数分支。
它们在本轮旧工程范围外，不能记为通过。原报告的历史通过数不计入这里。
本轮较早快照 `848ad46` 的 61+345 和 `ae74080` 的 61+347 保留为过程证据，不能代替最终 61+348。

### 既有行为保护

同一最终批次实际重跑了 A1—A6：创建／首次写入／另存为、模拟相机→处理→PLC、三类 TCP／四个内置及第三方算子、
16 MiB 桥、停止／维护／恢复／选中发布／COMMITTED 后连接反馈、三类包再次运行、第三方服务／健康／日志。
R01 的周期冻结身份、历史 generation 和 JSONL 单次持久化，R02 的选择性发布错误定位与补选，
R03 的多 owner 有界活动摘要也在本轮 CHAIN／REGRESSION 中通过。
`cdd6900` 的 client_once／receive_once 512 和 1025 字节预览保护四个参数，以及
Server 32／512／1025 字节预览三个参数均重跑通过，原始 JSONL 保存在 `once-byte-logs/`、`byte-logs/`。
未改通信字节、回执、缓存上限、日志写入通路、取消和未知副作用不重放语义。

## 首次失败与修正过程

原始失败输出和 JUnit 均保留在[独立证据目录](../evidence/2026-10-21-device-usability-improvement/README.md)。
`evidence-index.json` 对各批次逐个列出真实计数和失败用例，不把文件名中的 fixed 当作通过依据。

| 证据 | 结论与处置 |
| --- | --- |
| `batch1-first-failure` | 2 个失败，证实缺 enabled 控件、空 IP 返回原始校验文本；修复后定向与完整回归通过 |
| `batch1-fixed`、`batch1-layout-fixed`、`usability-components` | 暴露整体最小高度及最小窗口超出问题；改局部滚动和尺寸约束，最终尺寸用例通过。中间 fixed 文件仍有失败，原样保留 |
| `batch2-first-counterexample` | 缺发布按钮、摘要仍为内部路径；另有关闭清理连带错误。`batch2-first-failure` 含夹具错误属性 currentFile，改为实际文件字节断言 |
| `batch3-first-failure`、`batch3-selector-counterexample` | 缺草稿入口、失效引用被自动清空、导入同名、高级组缺失；首个 selector 夹具错误 KeyError 单独修正后，直接确认缺 allowMissingSelectors；没有拿夹具错误冒充产品缺陷 |
| `preexisting-catalog`、`preexisting-project-open` | 在未改 `756e526c` 独立快照复现 4 个旧 schema／包版本／测试选择问题，以及 1 个 selectors 序列化断言；按现有 2.7／1.4 契约更新断言并保留严格设备测试，没有增加排除 |
| `edit-publish`、`feedback-first`、`ui-feedback-final`、`unknown-feedback-first` | 确认 IMV 发现能力判断、真实连接拒绝丢失 cause、字段更正后父提示残留、未知错误缺中文兜底；分别修复后已有通过输出，最终批次再次通过 |
| `draft-boundaries-first`、`usability-chain-*`、`affected-working-tree` | 包含 moveNode／端口元数据／调用返回结构／错误测试路径等夹具或收集问题；同时完善 selector 叶子位置。修正夹具后仍断言真实 TCP 字节、图像、图结构及正式 Job，没有只断言连接成功 |
| `viewport-first-failure`、`viewport-counterexample` | 前者 1 个失败是夹具 `maximum()>2`；修正后的仅横向两项均通过，不能称为缺陷复现 |
| `viewport-list-counterexample` → `viewport-fixed` | 24 个正常副本列表直接复现 2 个垂直位置归零；保存树／表和表单的横纵滚动条，完整发布文件 4 项通过 |
| `catalog-marker-first` → `catalog-marker-fixed` | 异步目录到达后画布缺项提示未更新，1 failed；复用 refreshPresentations，草稿与目录测试 15 passed，最终快照再次通过 |
| `staged-check-first.log` | 文档暂存检查发现复制计划末尾多一个空行；只修正文档格式，重新检查通过，未改源码或测试 |

## 真实桌面关键路径

使用 `computer-use` 的 Windows `sky` API 驱动实际桌面，隔离工程和内嵌 Runtime；
未操作用户原本打开的 Designer。夹具脚本只准备临时工程和数据，实际点击、键盘、模态返回及运行入口由桌面自动化完成。

第一段走查源码为独立快照 `848ad465aed1d1b1c32da7969ccbcd2da10fc29f`，证据在 `desktop/`：

1. 空名称点击发布仍留在设备窗口、显示中文错误；“恢复当前输入”恢复名称。
2. 发布摘要显示导入副本和字段改动，点摘要正文显示完整 JSON，没有隐式勾选；取消回到同一相机。
3. 待绑定相机重开当前绑定向导，“保存待绑定节点”后画布显示缺 camera。
4. 从实际“运行流程”入口，明确选择“运行在线版本”，正式 Job `8b212515…` 到达 COMPLETED。
   `13-camera-completed-event.json` 保留 capture 完成事件、8×6 图像数据、144 原始字节及冻结身份。
   夹具启动前的另一笔后台模拟 Job 不冒充这个 UI 运行证据。
5. Ctrl+S 保存本地待绑定草稿后正常关闭测试进程。

走查发现的列表视口和异步画布提示经过上述直接反例后，另提交局部修正。
最终源码 `e2a9d867dc3a7069c7e91d2e68da5dfa23ee1fbf` 追加桌面复查，证据在 `desktop-final/`：

- 通过真实设备包导入共 26 个副本，使树和列表都可滚动；滚动两列后打开统一发布窗口，28 项差异默认未选。
- 取消返回仍是在线相机，树与列表滚动位置保留，固定动作可见。
- 目录显示 70 个算子；未经重新编辑，待绑定相机已显示“待选择设备：camera”。
- 测试窗口正常退出，`close-result.json` 为 true，所属测试进程已退出。

桌面 API 曾出现 set_value 不可用和控件索引点击未命中；复查截图后改用当前截图坐标，未将其记作产品失败。
部分 Qt accessibility 名称仍沿原控件名，按钮实际文字与焦点以截图及原生 Qt 断言为准。
`desktop-final/02-publication.json` 是索引未命中时的设备窗口，真正发布窗口为 `03-publication-ready`；不覆盖前一记录。

## 文档、提交与用户复验

同步更新设备契约、设备／Dispatch／在线配置、TCP 使用指南和原 v1.5 首页覆盖说明；
本计划从原“待实施”更新为实际状态，保留历史设计依据、SHA 和原验收要求。

| 源码提交 | 内容 |
| --- | --- |
| `674c6d3d02c4afdfc2f8d7b8953d7ce13a05437f` | 禁用 TCP 草稿与字段错误定位 |
| `848ad465aed1d1b1c32da7969ccbcd2da10fc29f` | 编辑／恢复／显式发布、待绑定、包和反馈闭环及连续链测试 |
| `ae74080243f871bb2086005d11b7e25d6a716144` | 发布返回的树／表及表单视口保留 |
| `e2a9d867dc3a7069c7e91d2e68da5dfa23ee1fbf` | 元数据加载后待绑定提示刷新；最终被测源码 |

文档与原始证据随后单独提交；交付时比较提交后的 `src/`、`tests/` 与上列最终源码一致。
只暂存本轮明确文件，正常推送目标分支并用 `git ls-remote` 核对，不强推、合并或发布版本。
精确文档提交 SHA 和远端同步结果随交付回复提供，避免在报告内部循环记录自身 SHA。

用户可按以下正常路径复验：

1. 新建临时工程，设备管理中新建 TCP Server，取消启用并留空通道；保存重开，检查未监听。
2. 改名并切换设备检查自动应用；清空名称后点发布，应定位错误；点“恢复当前输入”退出错误状态，再撤销／重做合法修改。
3. 添加模拟相机及 TCP session 节点，先保存待绑定；保存重开或导出模板导入，检查节点位置和已填内容保留。
4. 补客户端 localhost IP、通道、启用和真实绑定，从设备页进入发布；查看摘要及完整详情，明确选择、校验和写入。
5. 保留一个未完成本地节点，只发布另一合法参数；在线版本继续运行。选择未完成节点时应定位且在写入前拒绝。
6. 正式运行模拟采集与回环收发，检查实际结果和设备日志；设备包连续导入两次检查副本名，完整包在新目录导入后再次正式运行。

## 未执行范围

| 层次／范围 | 状态 | 具体原因 |
| --- | --- | --- |
| 原生 Windows Qt 自动化 | RUN／通过 | 指定 Python／PySide2 和 windows 插件，含真实焦点与模态事件 |
| 真实桌面关键路径 | RUN／通过 | 自动化驱动实际桌面；原走查及最终局部复查各有固定 SHA |
| 人工完整 UI | NOT_RUN | 本轮由自动化执行关键路径，没有用户或测试人员逐页人工签收 |
| 真实相机／PLC／外部 TCP 设备 | NOT_RUN | 本轮授权边界是模拟设备和 localhost，未访问现场硬件 |
| 冻结 EXE | NOT_RUN | 未构建或替换冻结产品；源码改动不会热加载到旧 EXE |
| Linux／跨机器 | NOT_RUN | 本轮实际可用验证环境为 Windows；未准备独立 Linux 用户环境或跨机器工程 |
| 压力／额外故障矩阵 | NOT_RUN／范围外 | 按计划完成受影响正常路径和直接反例后停止扩测 |
| 旧工程迁移 | 范围外 | 只保留原两个明确排除分支，未作迁移验收结论 |

本轮范围内的软件要求已落实；以上不同证据层次各自保留边界，不能据此声明真实硬件或冻结产品验收完成。
