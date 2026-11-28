# Runtime 总体能力 V1 R3 实施记录

执行者为 Codex。以远端 `main` 的 `48b30191884ad5ef5d96e8d26591104d16d54a58` 为固定基线。拉取后核对计划基线与目标 `main` 一致。原目录 `D:/jsdfhasuh/documents/my_project/emo_master` 位于旧 PR19 分支，HEAD 为 `78a547270324553b8e88dea5a05258e562fcfb08`，检查时工作区干净。已从目标提交创建 `D:/emo-runtime-r3` 与分支 `codex/runtime-overall-r3`。下列测试执行的是该基线加 R3 未提交修改的工作树。

目标基线已包含 PR20 的算子可读语义及 SQLite 后台回执。共享工作台沿用这些语义和既有日志持久化。原型 HTML 的 SHA-256 仍为 `a1a95d637957e2904291c0a960732110281f7d00a4d7d295b9738d5dc4bf81cd`。

## 实施范围

- Operator 加载和状态刷新采用 Runtime 对目标工作流提供的轻量启动许可。手动开始和正式工程 `production.autoStart` 共用该许可。加载完成时因名额受阻的自动启动就此结束，名额释放后状态刷新恢复手动开始。原 StartJob 完整预检继续生效。
- 生产页面按真实 Job 保存和恢复页面位置，仍校验工程及 Presentation 配置。查看与控制各自保留 Job 身份。日志目录只观察任务，生产页保留明确停止目标。
- Designer 与 Operator 共享事件归一化、查询边界、Job 窗口、执行树、筛选和详情。Designer 使用原完整 `RuntimeClient`，Operator 通过拥有的 Runtime 接入共用读取和现有设备诊断。Operator 日志工具可分别查看运行任务、Runtime 系统事件、本机 JSONL。
- 当前筛选在点击时固化已加载事件，完整 Job 固定第一条查询返回的上界并分页读取。设备下钻依据 Runtime、工程、加载代次和设备标识，匹配失败时保留原持久化事件。关闭 Operator 时撤销并排空本宿主日志读取。
- Operator 使用 `LIGHT_THEME` 的局部样式。启动/设置、生产控件、日志与设备窗口沿用原导航和操作入口。工程页面、工程自定义 QSS 与 Designer 的共享通信默认样式各自生效。

## 定向验证

测试使用 Windows 上的 Python 3.10.21、PySide2 5.15.2.1、pytest 9.1.1 和 gRPC 1.78.0。执行时从实施工作树加载 `src`，设置 `HUARAY_CAMERA_SMOKE=0`。临时工程使用当前工程 2.10、页面 1.3 和设备 1.0 格式，测试使用本机回环、软件 Job 和模拟设备。

| 证据层次 | 实际执行与结果 | 记录 |
| --- | --- | --- |
| 源码路径 | Operator 文件日志、启动设置与独立入口自检，23 通过 | [operator-source.xml](verification/operator-source.xml) |
| 源码路径 | Job 目录、并发批次与精确停止相关定向回归，14 通过 | [production-source.xml](verification/production-source.xml) |
| Qt 离屏 | Operator 起始页、数据目录偏好、本机日志工具、基础运行反馈，70 通过 | [operator-ui.xml](verification/operator-ui.xml) |
| Qt 离屏 | Operator 并发名额释放、更新包 `autoStart=false/true` 分别新增 0／1 个 Job、双 Job 页面与结果身份、精确停止及独立进程启动，37 通过 | [production-offscreen.xml](verification/production-offscreen.xml) |
| Qt 离屏及相关源码路径 | 工程重载和远程页面发布、设备控制与 owner、History RPC 身份，11 通过 | [transition-scope.xml](verification/transition-scope.xml) |
| Qt 离屏 | Operator Job/系统/本机来源与实际导出、退出时取消并等待进行中的系统查询，2 通过 | [r3-operator-log-sources.xml](verification/r3-operator-log-sources.xml) |
| Qt 离屏 | Designer Job 树、时间、筛选、详情、原值复制与表格、导出、历史、游标断连/恢复和单算子调试，203 通过 | [r3-designer-logs.xml](verification/r3-designer-logs.xml) |
| 静态门禁 | `git diff --check`、`python -m ruff check src tests` 及 `python -m mypy --config-file mypy.ini src` 通过，mypy 检查了 677 个源码文件 | [工作树记录](verification/worktree.json) |
| 规格 | OpenSpec CLI 2.0.0，`openspec validate runtime-platform-overall-v1 --strict --json` 返回 0，问题数 0 | [openspec-implementation-strict.json](verification/openspec-implementation-strict.json) |
| 本地归档 | 本期验收确认后归档 Change，更新三个正式规格 | [归档记录](verification/openspec-archive.json) · [正式规格 strict](verification/openspec-specs-strict.json) |

### Windows 原生 Qt 与软件 Runtime

使用 Windows QPA、PySide2 Qt Widgets 和实际显示的窗口。QTest 点击现有控件，通过测试夹具选定文件和运行对象，工作流、Runtime RPC、图像、日志、诊断、剪贴板和关闭过程实际执行。目录按钮核对向桌面服务提交的路径。验收不记为人工操作。

- 冷启动显示 Runtime 已就绪、未加载工程。损坏工程报告实际解析错误，再用正常工程重试、重载并核对工程 ID、Runtime instance 和 revision。记忆目录设置在下次启动生效，`autoStart=false/true` 重启分别创建 0／1 个新 Job。
- 同一工作流两个实际运行 Job。A 的 `review` 图像来源为 A，B 的 `main` 图像来源为 B。返回后恢复 A 的 `review` 和 A 的图像，记录包含图像大小与 capture plan revision。操作过程中实际冻结并恢复实时结果。
- 保持生产页观察 A，在日志工具选择 B 后返回，仍显示 A 的 `review` 和 A 的 320×240 图像，生产控制目标继续是 A。此后才在生产页切换查看 B，进入后续停止场景。
- 再次查看 B 后选择 B 的日志，验证 Job、系统事件和两类本机 JSONL 可达。核对设备诊断代次匹配和不匹配、返回所选事件和阅读位置，以及返回生产页的焦点。Designer 使用独立连接观察同一 Runtime 和 B 的事件，保留合法停止操作。关闭 Designer 后 A、B 与 Operator 的观察会话继续运行。
- 实际从设备失败事件复制原始记录。从成功事件打开表格，核对包括 `0` 与 `false` 的两行数据，再切换到原始 JSON 并从剪贴板核对同一事件 ID 和数据类型。
- 继续查看 B 时使用生产控制明确停止 A。Runtime 读到 A `ABORTED` 并释放执行资源，B `RUNNING`。随后关闭 Operator，确认其拥有的 Runtime 和剩余 Job 正常关闭。根布局中的生产控制在日志工具打开时隐藏，返回生产页恢复。另验证 `RuntimePages.diagnostics` 中的控件与工程自定义 QSS。

记录和实际尺寸见 [native-runtime-r3.json](verification/native-qt/native-runtime-r3.json)。默认截图为 [起始/设置](verification/native-qt/01-startup-settings.png)、[生产](verification/native-qt/03-production-review.png)、[日志](verification/native-qt/04-job-log-workbench.png) 与 [设备](verification/native-qt/05-device-controls.png)。前三张为 1060×800，设备窗口为 920×620。附 [900×700 起始页](verification/native-qt/02-startup-900x700.png)、[900×700 日志页](verification/native-qt/06-job-log-900x700.png)、[设备下钻](verification/native-qt/07-job-device-diagnostics.png) 与 [Designer 同任务](verification/native-qt/08-designer-same-job.png)。

从实施工作树用 `QT_QPA_PLATFORM=windows`、`PYTHONPATH=src;.`、`HUARAY_CAMERA_SMOKE=0` 运行 Python 3.10 `-m tests.ui.operator_view.native_runtime_r3_probe --output <记录目录>` 可重走原生验收。

### 系统事件与导出文件

固定记录的一次执行中，所选 Job 为 `be314975-c02b-4532-9060-28f780f3fb4c`，另一 Job 为 `479565e5-26be-4b9e-a46b-f2223cd192e2`。Runtime 系统页先遇到一页被归属过滤掉的记录，继续使用服务端游标读取并显示无 Job 归属的事件 `244`。`__runtime__` 分区中明确归属所选 Job 的事件 `243` 位于其任务页。该次测试通过真实 Runtime 注册表核对算子 ID 和版本，另查未登记的版本。

| 实际文件 | 结果 |
| --- | --- |
| [current-filter.json](verification/export/current-filter.json) | 198 个事件 ID 与点击时已加载且级别为 WARN 的集合完全一致。选择文件期间改变筛选，导出仍使用点击时的快照。 |
| [whole-job.json](verification/export/whole-job.json) | 223 个事件 ID 与固定上界 `247` 内仍可查询的集合完全一致。包含窗口外的 WARN `38` 和窗口内被筛掉的 INFO `245`、ERROR `246`，排除了另一 Job 的 WARN `247` 与上界后追加的 `248`。后续页沿用同一上界。导出进行中切换到另一 Job，仍可取消，导出范围仍属于原 Job。 |

执行输出见 [pytest.log](verification/export/pytest.log)。两份文件均来自软件夹具。早先并跑的一次夹具执行遇到 worker SQLite 忙，另一次 Windows Runtime 初始化越过 25 秒测试等待。最终单独执行时先等待前一 Job 资源退出，并给夹具初始化保留 60 秒上界，两个用例通过。

## 按实际影响选取的回归

| 任务 | 范围判定与证据 |
| --- | --- |
| P2.4、P5.4 | 生产宿主按 Job 恢复页面，影响工程切换和查看对象的衔接。执行重载、发布时观察对象保持、已移除工作流回退和 History RPC 身份，见 `transition-scope.xml`。 |
| P2.6 | 为启动许可、页面恢复和日志分流/导出新增或扩展定向断言，沿用生产工程夹具与现有包更新用例。 |
| P3.2 | 启动许可和明确停止目标涉及调用接线。运行并发名额、批量启动与精确停止回归，见 `production-source.xml`。 |
| P4.3 | 新增日志下钻读接线并核对缓存范围，复用模拟设备、远程控制代次及 sibling owner 回归，见 `transition-scope.xml` 与原生诊断。 |
| P7.2 | 共用查询保留 `UNIMPLEMENTED` 反馈。实际执行 Designer 的 `testLegacyJobSourceRestoresAfterUnimplementedAndDoesNotLeakAcrossTargets`、`testShortJobBeforeQuerySupportConfirmationUsesOneSource` 和断连恢复用例，见 `r3-designer-logs.xml`。Operator 嵌入本版本 Runtime，正常产品入口使用当前服务实现。 |
| P1.6、P2.3、P3.5、P4.4、P7.6 | 监听和端口接线、配置候选/传输、后台任务服务、TCP 控制及 Web 使用的协议未受影响。运行验收用回环端口和正式临时工程。 |

本期的操作记录来自软件 Job 和模拟设备。真实相机、PLC、现场工程、人工桌面验收、冻结包和发布未执行。测试使用临时工程和本机目录，运行参数及结果见各证据文件。
