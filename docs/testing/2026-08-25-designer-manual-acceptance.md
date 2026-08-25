# EmoMaster Windows Designer 人工验收指南

本指南用于 agent/runtime-workflow-architecture-v1 分支的 Windows 桌面人工验收。自动化门禁通过不等于桌面人工验收通过；窗口、交互、进程回收和 DPI 结果必须由用户本人观察并记录。

## 交接信息

- 仓库：https://github.com/jsdfhasuh/emo_master
- 分支：agent/runtime-workflow-architecture-v1
- 测试 HEAD：以交接时 git rev-parse HEAD 输出的 FINAL_HEAD 为准；本轮起始 HEAD 为 0198351244976ac2c5c217c06b7203b04ae613f1
- Conda 环境：emo_master
- 测试工作区：<repo>\manual_test_workspace\
- 自动化门禁：AUTOMATED_GATE=PASS（以最终交接报告和 CI 结果为准）
- 手工测试状态初始值：FRONTEND_MANUAL_TEST=PENDING
- 结果模板：docs/testing/designer-manual-result-template.md

所有命令都在仓库根目录执行。PowerShell 命令默认使用 Windows PowerShell 5.1 或 PowerShell 7；不要在人工测试终端设置 QT_QPA_PLATFORM=offscreen。测试脚本只管理 manual_test_workspace，不会触碰用户正常目录 %USERPROFILE%\.emo_master\runtime。

## A. 测试前准备

### A.1 检查分支

~~~powershell
git fetch origin
git checkout agent/runtime-workflow-architecture-v1
git pull --ff-only origin agent/runtime-workflow-architecture-v1
git branch --show-current
git rev-parse HEAD
git rev-parse origin/agent/runtime-workflow-architecture-v1
git status --short
~~~

确认当前分支正确、本地 HEAD 与远端一致，并确认没有未提交的用户改动。若工作树不是 clean，先保存或处理改动，再开始人工验收。

### A.2 准备隔离工作区

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode Prepare
~~~

Prepare 会检查 conda、emo_master、Python 3.10、PySide2、grpcio、protobuf、OpenCV、NumPy 和 pip check，但不会启动 GUI 或 Runtime。预期生成以下结构：

~~~text
manual_test_workspace/
├── input/
│   └── test_input.png
├── output/
├── screenshots/
├── logs/
├── evidence/
│   └── environment.txt
├── projects/
└── runtime-data/
~~~

input/test_input.png 必须为固定的 1280 x 720 PNG，黑色背景上包含白色矩形、白色圆和 EMO MASTER 文本。脚本使用 OpenCV 生成，重复执行会覆盖为同一内容。issue-template.md 和 manual-result.md 只在不存在时创建，以免覆盖已经填写的记录。

检查图片和环境清单：

~~~powershell
Get-Item manual_test_workspace/input/test_input.png
Get-Content manual_test_workspace/evidence/environment.txt
~~~

### A.3 启动前诊断

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode Diagnose
~~~

诊断模式只读输出：分支和 HEAD、Conda 环境、Qt 和 Runtime 环境变量、50051 端口、包含 emo_master 的 Python 命令行、工作区、SQLite 文件、Runtime 锁文件和输出文件。它不会结束进程。若发现旧的人工测试进程，先正常关闭它；不要杀死与本仓库无关的 Python 进程。

## B. 内嵌 Runtime 启动

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode Embedded
~~~

脚本会清除 QT_QPA_PLATFORM、EMO_RUNTIME_TARGET、EMO_RUNTIME_DB_PATH 和 EMO_MASTER_RUNTIME_DB_PATH，并设置：

~~~text
EMO_RUNTIME_DATA_DIR=<repo>\manual_test_workspace\runtime-data
~~~

Designer 使用真实 Qt 桌面和进程内 Runtime。命令在当前终端前台运行，控制台输出同时写入 manual_test_workspace/logs/；不要关闭终端来隐藏异常。

预期：

- 项目入口窗口出现；
- Designer 主界面可以打开；
- 窗口标题、菜单和工具栏可见；
- 没有数据目录锁错误；
- 没有未处理异常或 Python traceback；
- 关闭 Designer 后命令返回，Runtime 数据目录的锁可以释放。

完成基础检查后关闭 Designer，再继续下一节。若启动失败，保留当前日志并把问题记录为阻断项，不要直接填写 PASS。

## C. 基础界面

在真实窗口中逐项观察并在结果模板记录：

- 窗口可以移动；
- 最大化、还原和关闭可用；
- 左侧面板和右侧面板可见且内容不重叠；
- 工作流标签可以切换；
- 节点可以选中，选中态清楚；
- 参数弹窗可以打开、编辑和关闭；
- 日志窗口可以打开、滚动和查看增量内容；
- 画布可以缩放和拖拽；
- 自动布局可执行且节点不会被裁切；
- 工具栏按钮文字和图标没有溢出；
- 文件选择器可以打开并选择 manual_test_workspace/input/test_input.png。

保存截图：manual_test_workspace/screenshots/01-main-window.png。

## D. 新建项目

使用以下目录作为本轮项目目录：

~~~text
manual_test_workspace/projects/ui-acceptance/
~~~

在 Designer 中新建项目并保存到该目录。验证：

1. 项目包含 Main 工作流；
2. Main 被标记为入口工作流；
3. Workflow Input 出现在 Main 画布中；
4. Workflow Output 出现在 Main 画布中；
5. 边界节点可以选中但不能删除；
6. 项目保存成功，project.json 存在且可读取；
7. 关闭项目或 Designer 后重新打开，项目仍可加载。

保存截图：manual_test_workspace/screenshots/02-boundary-nodes.png 和 09-reloaded-project.png。如果保存或加载失败，保留 project.json、日志和截图。

## E. 创建数据型 Body 工作流

在项目中创建名为 Body 的数据型工作流。目标连接关系如下：

~~~text
Workflow Input.value:string
    ↓
内部节点
    ↓
Workflow Output.result:string
~~~

按以下顺序操作：

1. 创建 Body；
2. 设置 Body 的输入接口 value:string 和输出接口 result:string；
3. 添加一个项目中可用、且能承接字符串输入并产生字符串输出的算子；
4. 从 Workflow Input.value 拖线到内部节点的输入端口；
5. 将内部节点的输出端口连接到 Workflow Output.result；
6. 确认连线两端端口类型一致，连线没有穿过错误端口；
7. 保存项目；
8. 切换到 Main 或其他工作流；
9. 切回 Body，确认连线和端口仍在；
10. 再次保存并关闭、重载项目。

如果当前可用算子没有合适的字符串输入/输出组合，不要凭空构造业务算子；记录为“前置能力不足”并附上算子列表和日志。若连接成功，保存截图：manual_test_workspace/screenshots/03-body-links.png。

## F. 创建 Subflow

在 Main 中创建以下数据流：

~~~text
Workflow Input.value:string
    ↓
Subflow(Body)
    ↓
Workflow Output.result:string
~~~

验证：

- Subflow 的输入、输出端口来自 Body 接口；
- 修改 Body 的接口名称或类型后，Subflow 端口刷新；
- 修改接口造成不兼容时，失效边被清理，不保留幽灵连线；
- 项目保存、关闭、重载后，Subflow 和有效连线仍正常；
- Runtime 可以加载并执行该项目；
- Main 和 Body 的同名节点不会在状态显示中串联。

保存截图：manual_test_workspace/screenshots/04-main-subflow.png。

## G. Repeat

在 Main 或可运行的测试工作流中配置 Repeat：

~~~text
bodyWorkflowId=Body
repeatCount=3
maxIterations=3
timeoutMs=30000
~~~

验证：

1. Body 恰好执行三次；
2. 事件或日志中的 iterationPath 依次为 [0]、[1]、[2]；
3. Designer UI 在运行期间仍可移动和操作日志；
4. 最终 Job 状态为 COMPLETED；
5. 结果和日志没有多一次或少一次迭代；
6. 新 Job 开始时旧 Job 的节点颜色和日志状态不会残留。

保存运行中截图 05-repeat-running.png 和完成截图 06-job-completed.png。

## H. ForEach 和 While

本轮至少分别检查 ForEach 和 While 的编辑与持久化行为，不要求为它们构造复杂生产数据：

- 节点可以添加到画布；
- 参数弹窗可以打开和编辑；
- ForEach 的 body 可以选择；
- While 的 condition 和 body 可以选择；
- 配置可以保存；
- 关闭并重载后配置仍存在；
- body 或 condition 不允许引用当前工作流自身；
- 非法设置会给出可理解的错误，不会静默保存为有效配置；
- 修改引用工作流后，相关端口和连线状态可解释。

若已有方便的业务输入，可做一次最小运行；否则只记录编辑、校验、保存和重载结果。不要为了满足本项引入新的视觉算法或业务输入。

## I. 实时事件和节点颜色

运行一个可观察的 Job，并在日志窗口、节点状态和工作流标签中验证：

- RUNNING 状态出现；
- 正常结束后 COMPLETED 出现；
- 被循环策略跳过的节点显示 SKIPPED；
- 非法配置或失败算子显示 FAILED；
- 日志以增量形式出现，而不是只在 Job 结束后一次性出现；
- 切换工作流后再切回，当前状态可以恢复；
- 不同工作流中的同名节点不串色；
- 新 Job 开始时旧 Job 的颜色被清除；
- 事件中的 Job ID、工作流 ID 和节点 ID 与当前界面一致。

## J. graceful stop

选择一个足够长、可以观察停止过程的 Job，点击一次 Stop。验证：

- 点击后立即出现 STOPPING；
- Start 在停止期间保持禁用；
- 窗口、画布和日志仍可操作；
- 最终状态为 ABORTED；
- 事件流和日志没有未处理异常；
- 停止完成后可以重新开始一个新的 Job。

保存截图：manual_test_workspace/screenshots/07-stopping.png。

## K. force stop

在一个正在运行且能进入 STOPPING 的 Job 上执行：

1. 第一次点击 Stop，确认进入 graceful stop；
2. 在 STOPPING 期间第二次点击，确认升级为 force stop；
3. 最终状态为 ABORTED；
4. UI 不冻结，日志仍可查看；
5. 原 Job ID 不被新请求覆盖；
6. 停止结束后没有残留 Job worker 或 Runtime 锁；
7. 可以正常启动下一个 Job。

保存截图：manual_test_workspace/screenshots/08-force-aborted.png。

## L. 运行中关闭窗口

在 Job 运行期间关闭 Designer。验证：

- Designer 可以退出，不出现死锁或无响应窗口；
- Event Stream 被关闭；
- Designer 侧 RuntimeWorker 退出；
- Job 子进程被回收；
- 内嵌 Runtime 的锁文件可以释放；
- 再次启动 Embedded 不报告 Runtime 锁错误；
- Diagnose 中没有残留的本仓库 Runtime/Designer Python 进程。

关闭后运行：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode Diagnose
~~~

不要因为进程列表暂时为空就宣称所有桌面行为通过；同时保留 Diagnose 输出作为证据。

## M. 外部 Runtime

外部 Runtime 与 Designer 必须使用两个 PowerShell 终端，并且先启动 Runtime。

终端 A：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode ExternalRuntime
~~~

该命令检查 127.0.0.1:50051。若端口已被占用，只报告占用进程并退出，不会杀死未知进程。端口可用时，Runtime 在当前终端前台运行；保持终端打开以查看日志。

终端 B：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode ExternalDesigner
~~~

该命令清除 QT_QPA_PLATFORM，设置：

~~~text
EMO_RUNTIME_TARGET=127.0.0.1:50051
EMO_RUNTIME_DATA_DIR=<repo>\manual_test_workspace\runtime-data
~~~

验证：

- Designer 可以连接并加载 D 节创建的项目；
- 项目运行成功；
- 关闭 Designer 后终端 A 的外部 Runtime 继续运行；
- 再次启动 ExternalDesigner 可以重新连接；
- 关闭 Designer 不会终止外部 Runtime；
- 最后回到终端 A，由用户按 Ctrl+C 结束外部 Runtime；
- Runtime 结束后再运行 Diagnose，确认没有残留进程和锁。

若 Runtime 终端中的异常来自已有占用进程，不要强制结束该进程；记录 PID、命令行和端口状态。

## N. DPI

在 Windows 显示设置中分别执行 100%、125% 和 150% 缩放下的启动与基础操作。每次调整后重新启动 Designer，不要把一次缩放下的观察复制到其他缩放级别。

每个缩放级别都检查：

- 菜单文字和菜单项间距；
- 工具栏按钮和图标；
- 工作流标签；
- 节点标题和节点边界；
- 端口位置和端口文字；
- 参数弹窗的标签、输入框和按钮；
- 日志窗口和滚动条；
- 文件选择器；
- 右侧详情面板；
- 按钮文字不裁切、不重叠；
- 节点连线仍准确落在端口上；
- 最大化、还原和拖拽后布局没有错位。

保存 150% 的证据截图：manual_test_workspace/screenshots/10-dpi-150.png。在结果模板分别填写 DPI_100、DPI_125、DPI_150；没有实际观察的级别保持 PENDING，不要填写 PASS。

## O. 证据和结果回填

建议保存以下截图，文件名固定，便于 PR 交接：

~~~text
manual_test_workspace/screenshots/
├── 01-main-window.png
├── 02-boundary-nodes.png
├── 03-body-links.png
├── 04-main-subflow.png
├── 05-repeat-running.png
├── 06-job-completed.png
├── 07-stopping.png
├── 08-force-aborted.png
├── 09-reloaded-project.png
└── 10-dpi-150.png
~~~

同时保留：

- manual_test_workspace/evidence/environment.txt；
- manual_test_workspace/logs/ 中的启动和 Runtime 日志；
- manual_test_workspace/issue-template.md 中的问题记录；
- manual_test_workspace/manual-result.md 中的最终字段；
- manual_test_workspace/projects/ui-acceptance/ 中的可复现项目。

填写结果模板：

~~~text
docs/testing/designer-manual-result-template.md
~~~

只有全部关键项均已实际观察、没有阻断缺陷、证据已保存时，用户才可以填写：

~~~text
FRONTEND_MANUAL_TEST=PASS
FINAL_RESULT=PASS
~~~

存在阻断缺陷时填写：

~~~text
FRONTEND_MANUAL_TEST=FAIL
FINAL_RESULT=FAIL
~~~

人工测试没有执行或仍有未判断项时保持 PENDING。FRONTEND_MANUAL_TEST=PASS、WINDOWS_DESKTOP_SMOKE=PASS、DPI_100=PASS、DPI_125=PASS 和 DPI_150=PASS 只能由用户在实际 Windows 桌面观察后填写。

## 清理和重新开始

确认所有 Designer 和 Runtime 窗口已经正常关闭后，可清理本次隔离工作区：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_designer_test.ps1 -Mode CleanWorkspace -Force
~~~

脚本会先检查本仓库的人工测试进程。只要发现仍在运行，就停止清理并列出 PID/命令行；它不会自动结束进程。清理的唯一目标是 <repo>\manual_test_workspace，不会删除源码，也不会删除 %USERPROFILE%\.emo_master\runtime。

## 交接完成条件

用户完成并回填结果后，向 PR #1 提供：

- FRONTEND_MANUAL_TEST 和 FINAL_RESULT；
- Windows 桌面 smoke 结果；
- 100%、125%、150% DPI 结果；
- 阻断问题编号、截图和日志路径；
- 最终 HEAD 和测试日期。

在用户回填之前，本指南的人工测试状态始终为 PENDING，PR 继续保持 Draft，不能据此合并。
