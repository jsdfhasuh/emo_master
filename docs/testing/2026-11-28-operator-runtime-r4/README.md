# Operator Runtime R4 实施与验证

**R4 功能开发收尾，按用户决定跳过人工试用。正式 Source CI 待验证，现场测试由用户后续安排。**

- 合入远端 R3 前的 R4 工作区在 Windows 和 Linux 上的 Operator UI 均为 117/117 通过。该工作区的 Windows 原生 Qt 在 100% 和模拟 150% 缩放下通过。
- Linux `runtime` 最终复跑 3,468 通过、10 跳过。Linux `other` 随任务暂停，在约 95% 处停止，已记录 4 项 Designer 失败，归因待定。
- Windows 两个本地完整分片分别记录 `runtime` 2 项失败和 `other` 9 项失败。下文保留首轮结果及复跑记录。

## 基线

- 实施基线为 `78a547270324553b8e88dea5a05258e562fcfb08`，分支 `codex/pr19-ci-closeout-20261124`。开始时工作区只有未跟踪的 OpenSpec R4 目录，源码与测试无未提交改动。
- 本机 `main` 为 `9f3ac8550e7fbfcdd02510d5a1ad7395c82bef11`，当前分支比它多一个提交。本机 `origin/main` 引用为 `48b30191884ad5ef5d96e8d26591104d16d54a58`，比当前分支多 20 个提交。比较两端源码快照，独立运行端、共享设备管理文件及 `tests/ui/operator_view` 没有净差异。本次沿用户当前分支实现。
- 核对了未归档的通用 PageShell 规范和实际渲染入口。临时项目通过当前 `WorkflowStore` 生成，使用 2.10 工程、1.3 页面、1.0 设备结构，经保存重读、编译和实际 Operator 打开路径验证。
- 历史 R4 结果基于固定实施基线和当时的未提交工作区修改。源码检查使用隔离快照，原生探针在同批源码上执行。干净基线单独复核时另列。
- 主分支交付以 `13fb4ddb2d32b676d478e867fa87bfd5adae408f` 为整合基线，保留已合入的 R3 启动页运行目录、日志来源和设备诊断能力。历史 117/117 属于整合前快照，整合提交的正式结果另行记录。

## 实施内容与文件

| 范围 | 交付内容 | 主要文件 |
| --- | --- | --- |
| 顶栏与控制 | 工程菜单、工具历史、项目状态、上下文反馈。同一对全局控制在业务页和顶栏停靠，保留原工作流目标和命令链路。 | `apps/operator_runtime/main.py` |
| 主题与窗口 | 宿主与工具区浅色外观，独立进程启用 DPI。共享图标、字体、省略文字和屏幕适配，Designer 保留原导入路径。记忆最近五次成功工程和窗口状态。 | `ui/{theme,widgets,icons,_lucide_data}.py`，`apps/designer/ui/{theme,widgets,icon_map,_lucide_data}.py` |
| 本机日志 | 四条件组合筛选，本机时间，脱敏复制，Qt 表格模型及阅读锚点，按发起条件及文件范围快照导出。窄高窗口可独立滚动工具内容。 | `apps/operator_runtime/{log_reader,log_view}.py` |
| 设备 | 设备列表和真实健康概览，结构化状态与完整 ID，原始状态折叠。保留原动作、TCP 通道、串行调用和日志入口。 | `ui/{device_management,communication_panel}.py` |
| 起始与设置 | 路径省略、提示与复制，显示实际工程名，区分本次监听、下次已保存值和草稿。只有实际使用的启动字段标为覆盖项。 | `apps/operator_runtime/{startup_page,settings}.py` |
| 测试与文档 | Windows 探针、Operator 测试、TCP/监听回归与使用说明。 | `tests/ui/operator_view/`，`tests/runtime/test_{tcp_device_integration,operator_runtime_settings}.py`，[操作员 Runtime 说明](../../operator-runtime.md) |

源文件路径相对于 `src/emo_master/`，测试和文档路径相对于仓库根目录。

## 整合前 Windows 原生 Qt 走查

以 Python 3.10.21、PySide2 5.15.2.1、原生 `windows` QPA 运行 QTest。物理显示为 1920×1080，100% 的可用逻辑区域为 1920×1032，DPR 为 1.0。150% 使用 `QT_SCALE_FACTOR=1.5`，Qt 实测 DPR 为 1.5，可用逻辑区域为 1280×688。这里记录的是原生 Qt 自动化、截图和结果断言。仓库保留使用合成数据的代表截图和结果记录。含本机目录的其余原图及下文 `build/` 中的原始日志保留在本机。

| 项目 | 执行与结果 | 证据 |
| --- | --- | --- |
| A 起始与恢复 | 通过。初始无工程，缺失的最近工程显示可复制错误并保留入口，之后从最近列表显式加载有效项目。另验证初始化失败时仍能进日志。 | [100% 结果](native-100/result.json) · [150% 结果](native-150/result.json) |
| B 工具往返和停止 | 通过。单流程连续运行，日志、设置、返回不改变 Job、页面、会话、Hub 和当前回看状态。打开日志后单活动 Job 直接在原链路停止。实际冻结票据由源码回归验证。 | [运行中日志](native-100/running-logs-custom.png) · [停止结果](native-150/software-stop-from-logs-complete.png) |
| C 窄窗与样式 | 通过。长工程名、作者按钮文字和 QSS、`showControls=false`、两种 DPI。日志页停止和返回可达，业务页恢复规则与外观。关闭和正在停止时的可见、禁用状态另有定向回归。 | [隐藏启停 100%](native-100/running-logs-hidden-controls.png) · [隐藏启停 150%](native-150/running-logs-hidden-controls.png) · [业务按钮](native-150/returned-authored-running-page.png) |
| D 多流程和观察目标 | 复用 Operator 回归实际可用流程级按钮，检查多目标选择、取消、全局全部停止及观察和控制分离。 | `test_parallel_workflow_window.py`、`test_run_view_selection.py`，见源码检查记录 |
| E 组合过滤、复制和导出 | 通过。级别、模块、关键字和设备组合；普通往返保留；查看全部清除；核对本机时间、脱敏复制和三个导出范围。设备下钻在 Operator 集成回归中验证。 | [100% 结果](native-100/logs-result.json) · [150% 结果](native-150/logs-result.json) · [组合筛选](native-150/logs-combined-filters.png) |
| F 近 10,000 条阅读位置 | 通过。两种纵滚模式，选中、失效记录清理、横向位置、停跟随后阅读及恢复跟随。窄高窗口可在常驻顶栏下独立滚动日志内容。 | [100% 日志结果](native-100/logs-result.json) · [150% 日志结果](native-150/logs-result.json) · [原始对照](log-refresh-measurement.txt) |
| G 设备状态与诊断 | 通过。模拟六设备覆盖五类健康状态，TCP 正常监听并等待客户端，校验完整设备 ID、选择保持、原状态、控制及非 Operator 回退。 | [100% 结果](native-100/device-result.json) · [150% 结果](native-150/device-result.json) · [设备列表](native-150/device-dialog.png) |
| H 监听与窗口记忆 | 通过。运行中保存下次设置，实际监听与同一 Job 继续运行。接受正常关闭后重开恢复最大化和正常几何。CLI 来源用独立入口定向验证。 | [运行中保存](native-150/running-listener-next-start-saved.png) · [窗口恢复结果](native-150/result.json) |

同机原生 Qt 使用相同的预生成合成日志记录，每项三次取中位数。首次显示 9,800 条为 377.0 → 37.8 ms，追加 100 条为 363.5 → 22.4 ms，达到 10,000 条时淘汰 250 条并追加 350 条为 392.0 → 20.2 ms。计时覆盖列表更新与 Qt 事件处理，磁盘读取和解码在计时范围外。[完整样本](log-refresh-measurement.txt)

## 首轮源码与定向回归

- OpenSpec 严格校验通过。使用 `openspec validate improve-operator-runtime-ui --strict`。源码和测试的 Ruff 检查、`git diff --check` 也通过。
- Windows Qt 定向检查覆盖实际单流程停在工具页、关闭时可见但禁用、作者控件恢复、冻结票据、多流程独立启停与全部停止、原选择和取消、最近工程与窗口边界。第一次覆盖这些新增断言的 Windows offscreen 选择为 7 通过、21 按表达式未选。补齐监听 CLI 来源及等待取消对话框的断言后，定向重跑为 3 通过、21 按表达式未选。
- 本地 Windows 原生 Source runtime 分片中的整个 `tests/ui/operator_view` 组已完成，117 通过、0 失败、0 跳过。
- Linux 本地文件系统定向补测为 5 + 4 通过。覆盖按钮停靠、冻结票据、窗口几何、最近工程、双运行选择取消、流程级停止与全部停止、局域网参数来源及实际监听。初次从 Windows 文件系统挂载路径运行时有两项失败。一个启动超时在 Linux 本地文件系统未复现，另一个取消断言改为等待实际对话框后通过。
- Linux 受影响范围补测中，整个 `tests/ui/operator_view` 为 112 通过、5 失败。三项未能在测试等待期限内完成启动，两项五会话用例遇到变量仓或 SQLite 锁。Linux Runtime 定向组合为 34 通过、6 失败、1 个 Windows 专用用例跳过。TCP 文件单独重跑为 7 通过、2 失败，改动涉及的 A6 已通过，其余失败在 TCP Worker 结果和 Runtime JSONL writer 关闭路径。UI 结果 `build/operator-runtime-r4/linux-20261128-111724-117/affected-final-r4/ui` · Runtime 结果 `build/operator-runtime-r4/linux-20261128-111724-117/affected-final-r4/runtime` · TCP 单独复跑 `build/operator-runtime-r4/linux-20261128-111724-117/focused-r4-tcp-file`
- 三项 Linux UI 启动超时用例在 R4 单独复跑均通过。五会话两项在 R4 和干净 HEAD 上均失败，关闭中用例在两端均达到 `window.done` 等待期限，完整生命周期用例分别遇到流程再启动超时和数据库锁。现有结果不能将这些 Linux 失败归因于本轮 UI 修改。R4 五会话 `build/operator-runtime-r4/linux-20261128-111724-117/focused-r4-five-sessions` · 干净 HEAD 五会话 `build/operator-runtime-r4/linux-20261128-111724-117/clean-head-five-sessions` · 启动超时复跑 `build/operator-runtime-r4/linux-20261128-111724-117/focused-r4-ui-startup-deadlines`
- 本机日志定向回归覆盖组合过滤、真实选择、复制、取消和导出快照。组合执行 `python -m pytest -q tests/ui/operator_view/test_runtime_log_view.py tests/runtime/test_operator_runtime_logs.py tests/ui/operator_view/test_startup_settings.py` 为 73 通过。
- 设备 UI 定向检查涵盖完整控制 ID、列表刷新、健康状态和日志下钻，3 项通过。完整 `python -m pytest -q tests/runtime/test_tcp_device_integration.py` 为 9 通过，使用模拟设备及本机回环。
- 原生探针命令为 `python -m tests.ui.operator_view.native_runtime_tools_probe --output <证据目录>` 和 `python -m tests.ui.operator_view.native_runtime_logs_probe --output <证据目录>`。两轮分别设置 `QT_QPA_PLATFORM=windows`、`QT_SCALE_FACTOR=1` 和 `1.5`。设备证据使用同平台、同倍率及模拟状态。
- 两平台按 `.github/workflows/ci.yml` 的 Source 入口在本地隔离快照运行 `python scripts/ci_check.py --shard runtime` 和 `python scripts/ci_check.py --shard other`。源文件选择检查在 Windows 和 Linux 各通过 9 项。整合前本地快照未在 GitHub Actions 托管环境运行。
- 对比了两份快照与交付工作区的 15 个变更产品源文件和 13 个变更测试、探针文件。产品源码字节一致。快照捕获后只调整了 `test_run_view_selection.py` 的取消断言等待时机，两平台已独立复跑新断言。
- 本地日志与快照记录位于 `D:/emo-r4-windows-source-final/results` 和 Linux 结果目录 `build/operator-runtime-r4/linux-20261128-111724-117`。

| 首轮本地分片 | 结果 |
| --- | --- |
| Windows `runtime`，原生 Qt | 3,474 通过，2 失败，2 跳过，6,372 分片未选。两项跳过由原压力验收开关控制。两项失败均在原 R3 资产回环 RPC 的 0.5 秒期限内未返回，分别在干净 HEAD 与 R4 快照中隔离复跑，两次各 2 通过。完整分片仍为失败。日志、JUnit、失败记录和分片清单在 `D:/emo-r4-windows-source-final/results/runtime`。 |
| Windows `other`，原生 Qt | 6,359 通过，9 失败，4 跳过，3,478 分片未选，另有 66 个子测试通过。失败包括 4 项设备状态悬停提示、2 项连线悬停，以及 3 项旧 SQLite 诊断探针的 Git 子进程配置错误。日志、JUnit 和分片清单在 `D:/emo-r4-windows-source-final/results/other`。 |
| Linux `runtime`，offscreen | 选中 3,478 项。本地按现行 hosted job 的 60 分钟上限收尾，边界处 1,097 项完成，1,086 通过、10 失败、1 项清理异常，另有一项正在执行。完整分片覆盖未获验证。四项旧 Runtime 回环/观测失败在同一 R4 快照中定向复跑通过。60 分钟边界及原始日志 `build/operator-runtime-r4/linux-20261128-111724-117/runtime` · 定向复跑 `build/operator-runtime-r4/linux-20261128-111724-117/focused-existing-runtime-failures` |
| Linux `other`，offscreen | 选中 6,372 项，控制台记录 1,585 通过、1 跳过。随后原 Designer 双 PLC 工程打开测试的模拟参数在 Qt 事件循环等待时超过 120 秒，pytest 终止，余项未运行，未生成 JUnit 和分片清单。干净 HEAD 与 R4 上各单独复跑两个参数均通过，两者均出现 60 秒等待告警。R4 共享主题、图标、窗口与设备工作区等 8 项定向通过。部分计数与超时栈 `build/operator-runtime-r4/linux-20261128-111724-117/other` · 定向复跑 `build/operator-runtime-r4/linux-20261128-111724-117/focused-r4-designer` |

- 两平台在各分片进入 pytest 前均通过协议生成检查、全量 Ruff 和 mypy。Windows 两片合计收集并执行 9,850 个测试，测试划分无重复或漏项。首轮两平台的 `ci_shard_summary.py` 均返回 1，原始摘要与跳过项原因为本机记录的一部分。
- Windows Designer 在同一原生桌面用干净 HEAD 与 R4 分别定向运行六个悬停用例。两端均是四项提示不可见、两项连线悬停通过。提示问题在干净基线上已复现，完整运行中的连线失败在隔离环境未复现，诱因未确定。Designer 打包图标门槛也在本次完整 `other` 分片中通过。对照日志位于 `D:/emo-r4-windows-source-final/results/{baseline-native-hover-focused,source-native-hover-focused}`。
- Windows SQLite 三项诊断探针在同一 R4 快照，仅移除额外的 `GIT_CONFIG_*`／`GIT_CONFIG_PARAMETERS` 环境覆盖后，定向复跑 3 通过。正式分片原结果保留。
- 早期 Linux 快照缺少 `.git`，造成构建契约检查命令报错。后续两 Linux 分片均使用带干净基线 Git 元数据的快照。早期 Windows 和 Linux 运行因更新到最终产品源码快照而保留为早期证据。

## 源码验收续跑

- Linux 在同一 Python 3.10.22／Qt offscreen 镜像、目录内容相同的 R4 快照和最后版运行选择测试下，将工作目录与测试临时目录置于隔离容器的 `/tmp` 内存文件系统。五会话、TCP 完整文件及 Designer 双 PLC 模拟工程打开共 13 项，48.66 秒全部通过。以前的超时和失败记录保留。正式 `runtime` 与 `other` 分片按原命令串行复核，原始结果另行记录。内存文件系统复核 `build/operator-runtime-r4/linux-tmpfs-recovery`
- Linux `runtime` 按 `python scripts/ci_check.py --shard runtime` 复跑通过。3,468 通过、10 跳过、6,372 分片未选。pytest 18 分 52 秒，源码入口总计 19 分 24 秒，协议、Ruff、mypy 和 pytest 均通过。JUnit 确认 Operator UI 117 项和 TCP 集成 9 项全过。跳过为 8 项 Windows 专属路径和 2 项独立压力验收。完整分片记录 `build/operator-runtime-r4/linux-tmpfs-recovery/runtime`
- Linux `other` 按同一 Source 入口续跑，在约 95% 处随任务暂停而停止。已记录 4 项 Designer 失败，归因待定，余下用例未运行。分片日志 `build/operator-runtime-r4/linux-tmpfs-recovery/other/console.log` · 退出记录 `build/operator-runtime-r4/linux-tmpfs-recovery/result.json`
- Windows 本机的四项 Designer 提示框失败在干净 HEAD 与 R4 均复现。相同干净 HEAD 的 GitHub Actions 正式 Windows `other` 分片中，这四项、两项连线悬停及三项 SQLite 探针均通过。该托管结果用于证明环境差异下的干净基线可通过。R4 暂无托管结果。[干净基线正式 Windows 分片](https://github.com/jsdfhasuh/emo_master/actions/runs/45382159632/job/138431243483)

## 主分支整合验证

- 在 `13fb4ddb2d32b676d478e867fa87bfd5adae408f` 与本轮整合改动的隔离工作区运行 Windows 原生 Qt 定向回归。命令为 `python -m pytest -q --tb=short tests/ui/operator_view tests/runtime/test_operator_runtime_settings.py tests/runtime/test_operator_runtime_logs.py tests/runtime/test_tcp_device_integration.py tests/designer/test_package_gui_icons.py`，157 项通过。协议文件、全量 Ruff、mypy 和 OpenSpec 严格检查通过。
- 以 `QT_QPA_PLATFORM=windows` 运行 `native_runtime_tools_probe` 和 `native_runtime_logs_probe`，100% 和以 `QT_SCALE_FACTOR=1.5` 模拟的 150% 均通过。R3 原生探针和收尾探针亦在 100% 通过。本机原始结果保存在 `build/r4-main-native`，定向回归结果为 `build/r4-main-focused.xml`。

## 正式 Source CI

- 初次主分支提交 `d342167001052cdee30317ac70a7432a06d0c157` 的 Linux `runtime` 通过，Linux `other` 在 Designer 窗口缩放场景中记录手动画布中心偏移 7 像素，超过原有 2 像素断言。96 DPI 定向对照中，整合前主分支通过，R4 初版偏移 4 像素，恢复 Designer 原尺寸计算后通过。[初次 Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/46116586117)
- 兼容修正恢复 Designer 原有的窗口客户区尺寸计算，Operator Runtime 继续计入边框。Windows 原生 Qt 定向复核通过 31 项，覆盖 Designer 画布、工作区导航与 Operator 窗口恢复。

## 范围与剩余事项

- 初次窄窗走查发现日志工具的最小高度可传到主窗口。工具区现在可以单独滚动，使用 440 逻辑像素高的探针验证。
- OpenSpec 0.1–3.3、3.5 和 3.6 有相应历史证据。三阶段功能开发已收尾，人工试用按用户决定跳过。
- OpenSpec 3.4 保持未勾选，待整合提交完成正式 Windows 和 Linux Source CI 后核对。
- 现场测试由用户后续安排。现有证据覆盖自动化、模拟设备和合成数据，真实相机、PLC、生产模型、现场工程及冻结 EXE 留待后续验证。
