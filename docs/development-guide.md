# 开发指南

2026-09-27增量：P5-A测试项目可从Designer“导出测试项目包”交付到独立目录，
导入/启用/源码Runtime/只读页面命令见 [P5-A契约](runtime-pages-p5-contract.md)。
这不改变下述默认Designer/Runtime开发路径，不代表EXE已更新或现场验收通过。

适用分支：`agent/runtime-workflow-architecture-v1`。源码启动说明核对基线为 `ff417e7128f793dd2f67ddb40c97d1c949c32869`（应用版本 `0.6.1`），更新日期为 2026-10-02。后续命令以所在提交的源码和依赖文件为准。

本指南负责从源码开发和调试；交付 EXE、安装包和正式发布见 [部署与发布指南](deployment-guide.md)，架构边界见 [工程师导读](engineer-guide.md)。

## 0. 已有环境：直接从源码启动

本机已经安装 `emo_master` Python 3.10 环境时，从本节开始即可。源码启动会读取当前工作副本的 `src`，不需要编译 EXE。仓库中的 `start_designer.cmd` 也是源码启动包装入口。

### 方式 A：直接使用 Python 启动

在 PowerShell 执行下面两行。仓库放在其他位置时，替换第一行路径；默认解释器位置与本机现有 Conda 环境一致。

```powershell
Set-Location 'D:\jsdfhasuh\documents\my_project\emo_master'
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" scripts/dev.py run-designer --local
```

该写法直接调用已有解释器，无需先激活 Conda。如果已经在终端执行过 `conda activate emo_master`，第二行可简写为：

```powershell
python scripts/dev.py run-designer --local
```

`scripts/dev.py` 自动配置当前源码路径；`--local` 自动选择可见 Qt 窗口、内嵌 Runtime 和隔离的数据目录，无需手动设置 `PYTHONPATH` 或逐项清理旧环境变量。平时使用同一命令启动即可，不必每次安装依赖、生成 protobuf 或运行完整 CI。

### 方式 B：双击或一条命令启动

在资源管理器中双击仓库根目录 `start_designer.cmd`，或者在任意 PowerShell 目录执行：

```powershell
& 'D:\jsdfhasuh\documents\my_project\emo_master\start_designer.cmd'
```

它调用的也是方式 A 的 Python 源码入口。解释器不在默认位置时，按第 3 节设置 `EMO_MASTER_PYTHON`，或在方式 A 中直接填写实际解释器路径。

### 启动后怎么使用

1. 出现“项目入口”窗口后，选择“打开项目”或“新建空白”。
2. 打开项目后编辑流程或页面；需要执行时，明确点击“开始运行”。打开界面本身不会开始检测。
3. 本模式由 Designer 管理内嵌 Runtime，不用另外启动服务；关闭前先停止正在执行的 Job，再关闭 Designer。

本模式的运行数据库和日志位于仓库的 `manual_test_workspace/runtime-embedded` 下；项目文件按用户新建或打开的项目位置保存。不要把运行数据目录当作项目目录，也不要同时用两个内嵌实例占用同一运行数据目录。

### 检查、更新代码与重新启动

启动异常或刚更新源码时，可先执行下面的检查。正常日常启动不必重复检查。

```powershell
.\start_designer.cmd --check
```

看到 `Designer startup check passed` 表示当前源码与依赖能导入；这条命令不打开窗口，也不验证设备或执行任务。需要项目回归验证时使用第 5 节命令。

修改 Python 源码并保存后，停止当前 Job、关闭 Designer，再运行上述启动命令。默认入口不提供源码热重载。只有依赖文件发生变化时才按需更新现有环境的依赖；只有修改了 protobuf 定义时才按第 5 节生成协议代码。

更新远端源码前先关闭运行中的 Designer，并执行 `git status --short --branch` 检查分支和本地修改。工作区干净且位于本工作分支时可执行 `git pull --ff-only origin agent/runtime-workflow-architecture-v1`；遇到本地修改或分叉时先处理具体差异，不用 reset、自动 stash 或强制覆盖继续。

## 1. 首次准备环境和源码

已有可用环境时跳过本节，不要重复创建环境或重装依赖。

当前 `pyproject.toml` 要求 `>=3.10,<3.11`，因此使用 **Python 3.10**。Windows 打包目标为 x64；不要用 Python 3.11 及以上或 32 位解释器替代本指南的环境。GUI 使用 PySide2/Qt5，YOLO 使用 ONNX Runtime CPU，并不需要为普通开发安装 PyTorch 或 CUDA。

Windows 示例统一使用 **PowerShell**，不是 CMD。先安装 Git 和 Conda，在可以激活 Conda 环境的终端中执行。已有源码仓库时跳过克隆，先检查并保留自己的未提交修改，再获取远端分支；不要使用 `reset --hard` 清理工作区。

```powershell
git clone --branch agent/runtime-workflow-architecture-v1 --single-branch https://github.com/jsdfhasuh/emo_master.git
Set-Location .\emo_master
git branch --show-current
git rev-parse HEAD

conda create -n emo_master python=3.10 -y
conda activate emo_master
python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败。" }
```

已有仓库的分支切换流程是：先运行 `git status --short`，确认修改已妥善保存，再执行 `git fetch origin --prune` 和 `git switch agent/runtime-workflow-architecture-v1`。若本地没有该分支且自动跟踪未生效，可用 `git switch --track origin/agent/runtime-workflow-architecture-v1` 创建跟踪分支。

`requirements.txt` 是运行依赖；`requirements-dev.txt` 已通过 `-r requirements.txt` 包含运行依赖，并增加 pytest、Ruff、mypy 等开发工具。开发和 CI 统一安装后者。Conda 负责管理解释器环境，项目依赖按仓库文件通过 `python -m pip` 安装，不要自行改换 gRPC、protobuf 或 NumPy 版本来绕过错误。

## 2. 源码路径与启动检查

以下相对路径命令在 **emo_master 仓库根目录** 执行。已完成第 0 节启动的用户，可在排查问题时使用本节。

```powershell
conda activate emo_master
python scripts/dev.py run-designer --local --check
if ($LASTEXITCODE -ne 0) { throw "解释器或源码路径不正确。" }
python scripts/gen_proto.py --check
if ($LASTEXITCODE -ne 0) { throw "protobuf 检查失败，请查看第 5 节。" }
```

`--check` 使用真实 Designer 模块、PySide2 和 gRPC 验证导入，不创建窗口、Runtime 或 Job；输出的源码路径应属于当前仓库。`scripts/dev.py` 现为工具子进程设置本仓 `src` 路径并切换到仓库根目录，不依赖 IDE、当前 cwd 或其他工作副本的 `PYTHONPATH`，也不修改父终端环境。

直接执行 `python -m emo_master...` 或需要在终端导入项目时，仍可设置 `$env:PYTHONPATH = (Resolve-Path .\src).Path`；不使用 `setx` 或全局系统环境变量固定工作副本。不需要额外的 `pip install -e .`。`HUARAY_CAMERA_SMOKE` 只控制真实相机测试，不能用它禁止正式流程中的设备操作。

## 3. 默认方式：Designer 内嵌 Runtime

环境已安装时，双击仓库根目录 `start_designer.cmd` 即可。PowerShell 也可执行：

```powershell
.\start_designer.cmd
```

双击入口默认使用 `%USERPROFILE%\.conda\envs\emo_master\python.exe`，不会重新安装或创建环境；解释器在其他目录时设置 `$env:EMO_MASTER_PYTHON = '实际路径\python.exe'`。从其他 cwd 可用启动脚本的绝对路径。`./start_designer.cmd --check` 可检查启动配置，失败返回非零退出码。

已经激活 Python 3.10 环境时，等价命令是 `python scripts/dev.py run-designer --local`。`--local` 只在子进程中清除外部 Runtime 地址、数据库/旧数据库路径、日志目录覆盖和 Qt 平台覆盖，并指定 `manual_test_workspace/runtime-embedded`。这使遗留的 `offscreen` 或外部服务配置不会影响本次可见内嵌启动；关闭后父终端原设置仍然保留。

Designer 未配置外部目标时直接创建 `RuntimeService()`。这是同一应用进程内的服务调用，不会额外监听 gRPC 端口；正式 Job 仍在 Runtime 管理的 spawn 子进程中执行。关闭 Designer 时会关闭它拥有的内嵌 Runtime。

`manual_test_workspace` 用于本机隔离数据，不应提交。需要自定义数据库、目录、Qt 平台或连接外部 Runtime 时，继续使用 `python scripts/dev.py run-designer`，不加 `--local`；原环境变量优先级和连接行为保持兼容。不要同时让多个内嵌实例访问同一数据目录。

第一次启动使用本地图像或不涉及设备的流程。打开项目本身不等于已授权执行设备动作；含相机、PLC、TCP 算子的流程只应在确认设备和参数安全后运行。

## 4. 可选方式：同机分离调试

只有需要独立服务或调试 gRPC 时才使用本节。普通源码启动使用第 0 节即可。

这一方式使用两个 PowerShell 终端。两边都应位于同一版本的源码根目录，并使用已准备好的 Python 3.10 环境。不需要启动第三个内嵌实例。

### 终端 A：先启动 Runtime

```powershell
conda activate emo_master
Remove-Item Env:EMO_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
Remove-Item Env:EMO_MASTER_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
$env:EMO_RUNTIME_DATA_DIR = Join-Path (Get-Location).Path "manual_test_workspace/runtime-external"
python scripts/dev.py run-runtime
```

保持该终端运行。入口默认监听 `127.0.0.1:50051`，此时只是等待客户端请求，不会自动加载项目和开始检测。

### 终端 B：再启动 Designer

```powershell
conda activate emo_master
$env:EMO_RUNTIME_TARGET = "127.0.0.1:50051"
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
python scripts/dev.py run-designer
```

外部模式的数据目录由 **终端 A 的 Runtime** 决定，不由 Designer 终端的数据库变量决定。结束调试时先停止 Job、关闭 Designer，再在 Runtime 终端按 Ctrl+C；关闭外部客户端不等于关闭独立服务。

恢复内嵌模式时，先关闭当前 Designer，再按第 0 节使用 `--local` 或双击入口启动；无需手动清除父终端的 `EMO_RUNTIME_TARGET`。连接外部 Runtime 时则不要加 `--local`，否则会明确改用内嵌模式。每个独立 Runtime 实例必须使用不同数据目录；不要同时让两个服务访问同一数据库，也不要通过删除锁文件强行启动。

PowerShell 使用 `$env:EMO_RUNTIME_TARGET = "..."`；CMD 对应写法为 `set "EMO_RUNTIME_TARGET=..."`，不能混用。本页没有提供跨机一键部署：当前入口没有 `--host` / `--port` 命令行解析，gRPC 使用非 TLS 连接。改变监听、认证、路径可达性等要求见 [部署限制](deployment-guide.md)。

## 流程画布自动整理（2026-10-02）

源码启动后，在当前流程点击工具栏“自动布局”或“编辑 → 自动布局”。
它按实际连线将上游放左、下游放右，分支上下展开；未连接节点放在下方。
整次整理可用“撤销项目编辑 / 重做项目编辑”（Ctrl+Z / Ctrl+Shift+Z）恢复。
保存项目后重新打开，节点位置保留；加载、接线及运行不会自动重排节点。

正式连线采用圆角折线，拖动节点时更新关联线，松开后全图重新避障。
如果手动叠放节点封住端口通道，会显示橙色虚线；悬停查看受阻说明，
移动节点或再次自动布局可恢复正常连线。复杂图仍可能有线线交叉。
全部端口保留，布局不改变连接关系、执行语义或页面绑定。

可重现真实 Designer 整理前后截图、保存重开和 40 节点测量：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" scripts/validate_flow_layout.py --output "manual_test_workspace/flow-layout-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

该脚本打开并关闭专用测试窗口，使用正式算子清单中的端口及临时项目，
不启动 Runtime、YOLO 推理或设备。输出路径必须尚不存在，以保留前次证据。
生成 `before.png`、`after.png`、`project/project.json` 和带 HEAD、dirty、源码 SHA-256
及逐轮耗时的 `validation.json`。`before.png` 为旧网格位置配新连线样式，
不是旧版本曲线截图。完整说明及检查结果见
[流程整理验证记录](testing/flow-auto-layout-2026-10-02.md)。

## 5. 日常检查与 protobuf 更新

在第 2 节准备好的终端中运行：

```powershell
python scripts/ci_check.py
if ($LASTEXITCODE -ne 0) { throw "项目检查失败，不应继续发布。" }
git diff --check
```

`ci_check.py` 按顺序执行 protobuf drift 检查、`ruff check src tests`、mypy 和 pytest；任一步失败就返回。脚本为自身及测试子进程设置默认 `QT_QPA_PLATFORM=offscreen`，不需要真实设备。

仅运行测试可用 `python scripts/dev.py test` 或 `python -m pytest -q`。pytest 配置独立添加了 `src` 搜索路径，因此“pytest 通过”不能替代第 2 节的普通解释器导入检查。

仅当修改了 `proto/runtime.proto`、需要有意更新生成物时执行：

```powershell
python scripts/gen_proto.py
if ($LASTEXITCODE -ne 0) { throw "protobuf 生成失败。" }
python scripts/gen_proto.py --check
if ($LASTEXITCODE -ne 0) { throw "生成结果仍不一致。" }
python scripts/ci_check.py
```

协议文件和 `src/emo_master/apps/runtime/grpc_server/generated` 中对应生成文件应一起提交。干净检出时 `--check` 失败，要先核对依赖版本和仓库差异，不能直接重生成来掩盖问题。仅运行 `--check` 不会覆盖仓库生成文件。

### 界面与图标检查

在已安装 PySide2 的开发环境中执行：

```powershell
python scripts/designer_visual_check.py --output manual_test_workspace/designer_visual/local-check --scale 1
python scripts/operator_icon_visual_check.py --output manual_test_workspace/operator_icons/local-check --scale 1
```

图标截图参数可通过 `--help` 查看。Designer 截图工具支持 `--project <项目路径>` 只读复现，默认本机相机项目不存在时使用模拟节点。不要启用 `HUARAY_CAMERA_SMOKE` 来跑常规 CI。截图、模拟检查、冻结包自检和真实硬件验收分别记录，不可互相替代。

### Linux 软件检查

仓库 CI 配置包含 Ubuntu 和 Windows、Python 3.10。Linux 上已有 Python 3.10 Conda 环境时，可在源码根目录执行以下 **Bash** 命令；这不是 Linux 现场硬件部署承诺。

```bash
conda activate emo_master
python -m pip install -r requirements-dev.txt
export PYTHONPATH="$PWD/src"
export HUARAY_CAMERA_SMOKE=0
export QT_QPA_PLATFORM=offscreen
python scripts/ci_check.py
```

手动启动桌面界面前要有可用显示环境，并清除 `QT_QPA_PLATFORM=offscreen`。Windows 的清除命令为 `Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue`，Bash 为 `unset QT_QPA_PLATFORM`。

## 6. IDE、工作区和改动边界

IDE 使用 `emo_master` 环境中的 Python 3.10，工作目录设为仓库根目录。只需启动程序时，可在 IDE 终端运行 `python scripts/dev.py run-designer --local`。

需要给 Designer 进程直接打断点时，调试入口使用模块 `emo_master.apps.designer.main`，并显式配置 `PYTHONPATH=<仓库绝对路径>/src`、`EMO_RUNTIME_DATA_DIR=<仓库绝对路径>/manual_test_workspace/runtime-embedded`；内嵌可见模式应不设置外部目标、数据库路径覆盖或 `QT_QPA_PLATFORM=offscreen`。模块启动不会经过 `dev.py` 的环境准备。调试独立 Runtime 则选择模块 `emo_master.apps.runtime.main` 并按第 4 节配置。不要直接执行包内部文件或把带点模块名当作文件路径。

源码和可共享示例可提交；`manual_test_workspace`、运行数据库、模型、现场相机参数及原始验收资料不能因未跟踪就直接上传。保留现场 `project.json`，不要为测试覆盖真实项目。提交前检查 `git status --short` 和 `git diff --check`。

新增算子按 [注册流程](plugin-registration-flow.md) 和 [图标资源](operator-icon-assets.md) 操作；新增项目字段同步模型、迁移、Designer store 和 Runtime loader；新增 RPC 同步 proto 及生成物。不要把业务执行逻辑放进 Designer 或薄 RPC 适配层。目录与数据约定见 [工作区指南](workspace-guide.md)。

## 7. 常见问题

| 现象 | 首先检查 |
| --- | --- |
| 双击入口提示 `Python not found` | 确认已有 Python 3.10 环境的解释器路径；设置 `EMO_MASTER_PYTHON` 或使用第 0 节方式 A 的实际路径，不必重新安装环境 |
| `No module named emo_master` | 优先用 `scripts/dev.py ... --check` 核对源码入口；直接模块启动或 IDE 调试时才需显式设置 `PYTHONPATH=<仓库>/src` |
| 找不到 Ruff、mypy 或 pytest | 是否只安装了 `requirements.txt`；开发应安装 `requirements-dev.txt` |
| PowerShell 设置外部地址后仍走内嵌模式 | 是否使用了强制内嵌的 `--local` / 双击入口；外部模式使用第 4 节命令，并用 `$env:EMO_RUNTIME_TARGET` 设置地址 |
| 无法连接 `127.0.0.1:50051` | Runtime 终端是否存活、端口是否被其他程序占用、两端协议是否匹配；`Test-NetConnection 127.0.0.1 -Port 50051` 只能验证 TCP 可达，不代表 RPC 正常 |
| 数据目录被占用或启动失败 | 是否已有内嵌/外部 Runtime 使用同一目录；检查更高优先级的 `EMO_RUNTIME_DB_PATH`，不要删除活跃实例的锁或数据库 |
| 进程存在但没有窗口 | 是否继承了 `QT_QPA_PLATFORM=offscreen`；恢复可见 GUI 前清除该变量 |
| EXE 报 `unsupported arguments` | 现有包不支持 `--runtime`、`--host` 等参数；使用支持的自检参数或按源码入口调试 |

### 提示运行数据目录已被占用

`runtime data directory is already in use` 或“Designer 已在运行或数据目录被占用”表示另一个进程正在持有该目录的排他锁。源码入口遇到此情况会显示目录和处理方法，并以退出码 2 结束本次启动。它不会关闭原来的 Designer、停止它的任务或改用另一套计数数据库。

先查看任务栏是否已有 Designer 或项目入口窗口，有则直接使用它。需要重新启动时，先停止原窗口的 Job 并正常关闭，等待进程退出后再启动。若窗口已消失但进程尚在，先核对任务管理器中的 Python/EmoMaster 进程对应的命令和任务；不要批量结束所有 Python 进程。

锁文件存在本身不代表仍被占用，进程退出后操作系统会释放文件锁。不要删除 `.jobs.runtime.lock`、数据库或整个运行数据目录来解除占用。`--check` 只验证导入与配置，不会检查或抢占正在使用的运行目录。

## 8. 文档验收与依据

### 页面设计器的可视化编辑

从源码正常启动 Designer：`python scripts/dev.py run-designer --local`。打开项目默认进入 **流程设计**，通过主工具栏的 **流程设计 / 页面设计** 两个按钮切换工作区。
旧项目首次启用页面时仍会提示格式兼容性；本次界面改进没有增加新的项目字段或格式版本。

1. 左上“页面列表”管理最终展示界面的多个页面，例如检测总览、结果详情。“新建”添加页面，“更多”提供重命名、复制、排序、设为首页和删除。页数少时列表收紧，较多时滚动查看。
2. 左下“组件”支持搜索并拖入图像、数值、文字等缩略图；“流程结果”按流程、节点及输出名称组织，未运行也能选择数据来源。编辑画布始终显示 **示例数据 · 非检测结果**；示例值不保存到项目。
3. 点击组件选择，拖动正文移动；右侧、底部及右下角蓝色手柄调整占用列数、占用行数及两者。蓝色网格表示可放置，红色表示重叠、越界或类型问题；松开提交一次撤销，Esc 取消。
4. 右侧按“内容、位置与大小、外观、数据来源”分组。顶部固定组件名称，底部固定 **应用修改**；复制、删除在标题旁“操作”和画布右键菜单。未选中组件时显示页面属性。无效输入会保留原文、显示错误，并阻止切页、切换工作区和保存。
5. 主工具栏顺序是“当前工作区 → 打开/保存 → 撤销/重做 → 工作区操作”。撤销/重做作用于整个项目，悬停可查看操作说明。窄窗口将打开/保存收为图标，完整名称仍在提示和“文件”菜单中。
6. 三栏边界可拖动；统一的 **视图** 菜单控制当前工作区的面板。窄窗口默认收起左栏，可通过“视图 → 组件与页面栏 / 属性栏”切换。面板宽度只保存为本机偏好，不进入项目撤销历史。
7. **运行流程、停止运行、图片测试、整理流程** 只出现在流程设计中，切换到页面设计后相关快捷键也禁用。结果采集设置在 **运行 → 结果采集设置**；测试项目包导出在 **文件 → 导出测试项目包…**。

### 图片测试与独立页面预览

1. 保存项目并启用已有的页面/资源配置，在流程设计选择 **图片测试 → 选择测试图片**，明确选择图片输入节点。图片仍按原资源机制登记；同名节点以序号区分。
2. 选择 **图片测试 → 开始测试**，使用当前配置与测试图片，不控制设备。当前只支持已验证的本地图片处理算子和单图来源；条件不足会说明原因。测试状态和失败消息在流程设计底部可见。
3. 切换到页面设计排版，点击 **预览页面**。预览在独立窗口打开，首次显示示例数据，无需先运行；重复点击会激活同一窗口。编辑器继续保持编辑态。
4. 在预览中选择 **运行结果** 或点击 **查看运行结果**，再选择当前项目已有的运行记录。没有运行、等待结果、未选择来源、未采集数据、断开连接分别显示；失败或真实数据缺失不会自动显示示例值。连接服务地址与运行编号仅在 **更多 → 高级连接…** 中输入。
5. 预览中可切页、模拟状态、查看详情及固定当前结果。已应用的页面修改同步到预览；固定结果期间保留当时页面配置，**继续更新** 后应用最新修改。多页面按钮过多时横向滚动，不撑宽窗口。
6. **停止查看**、关闭预览或切回示例只释放查看连接，不停止正常流程或图片测试。需要停止图片测试时回到流程设计，选择 **图片测试 → 停止测试**。
7. 切回流程设计会隐藏预览；返回页面设计后，由用户再次点击“预览页面”显示原窗口。项目切换或主窗口关闭仍执行保存确认与异步收尾。

**保存项目**仍统一保存流程、页面和资源；关闭重开后布局与绑定保持。页面样例不意味着原 1080p/5 Hz 性能、A18 资源稳态、Qt 组合稳定性或现场发布已经通过。

可重复生成两个工作区、空页面、独立预览、多页面和无效输入的真实 Qt 截图：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = '1.25'
python scripts/validate_designer_workspaces.py --output manual_test_workspace/workspace-check-new --width 1600 --height 900 --physical-screen
```

输出目录必须是新目录，工具拒绝覆盖旧证据。它使用临时数据目录，不启动检测；`geometry.json` 记录实际窗口尺寸、DPR、HEAD、dirty 和源码摘要。每个 DPI 场景应新启动一个进程。`--physical-screen` 将输入尺寸视为物理屏幕像素，按 DPI 并扣除实际任务栏与窗口边框计算可用空间；不修改桌面的物理分辨率。省略该选项则使用逻辑窗口尺寸。
必须以记录的 `actual`、`targetClient` 和 `availableScreen` 判断，不能把请求尺寸当作已测尺寸。恢复正常桌面启动前清除测试用的 `QT_SCALE_FACTOR`。原拖放、数据来源和调整大小截图脚本 `validate_page_editor.py` 仍可使用。

真实本地图像调试和只读观察回归：`python -m pytest tests/ui/page_designer/test_user_path.py -q`。
该测试只使用临时项目、数据库与输出目录，不连接真实设备。工作区改造截图与原始测试结果见 [本轮验证报告](testing/workspace-ui-2026-10-03.md)；原拖放及尺寸验收保留在 [此前报告](testing/page-editor-2026-10-02.md)。

本次更新依据仓库入口、依赖和工作流进行核对，不宣称重新完成 Windows 干净环境安装、GUI、相机、PLC 或冻结包验收。交付前应在干净 Python 3.10 环境依次验证：源码导入、内嵌运行、外部分离运行、完整检查及 Windows 包自检，并保存各自结果。

代码依据：`scripts/dev.py`、`scripts/ci_check.py`、`scripts/gen_proto.py`、`pyproject.toml`、`requirements-dev.txt`、`apps/designer/main.py`、`apps/runtime/main.py`；后两者位于 `src/emo_master` 下。

环境变量和进程行为参考 [Python 3.10 命令行与环境](https://docs.python.org/3.10/using/cmdline.html) 及 [PowerShell 环境变量](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables)。

### 流程设计中查看运行结果（2026-10-04）

保存当前项目并正常关闭旧 Designer，再按第 0 节从源码启动。新代码不会热更新已经打开的窗口；外部 Runtime 需要使用同版代码才能提供运行检查会话和带执行身份的图片。已有本机环境不用重新安装。

1. 在 **流程设计** 明确点击 **运行流程 / 开始运行**，再点击画布节点。右侧统一的 **节点结果** 显示所选任务、节点、执行状态、时间与工程修订；**当前草稿配置…** 编辑现在的配置，不会改写历史结果。
2. **图像输出** 按正式输出端口选择真实图片。例如图像输入的 `image`、Blob 的 `overlay`；保存节点的注册图片在 **保存结果图** 选项中。图片尺寸、编码和时间来自资产元数据，读图通过资源 ID，不把服务器文件路径当成本机路径。声明有图像端口但未采集、失败或缺图时保留标签并显示原因。
3. **数值输出** 显示实际数值、布尔、文字及类型；`0`、`false`、空值保留原义，集合显示数量，复杂对象显示有界摘要。**执行信息** 显示节点调用、循环索引、算子上报耗时、诊断和错误。**输入数据** 使用执行时的真实摘要，首版不提供完整输入图片回放。节点“执行完成”与业务判定值分别显示。
4. **本次运行 / 上一次运行** 只在当前 Designer 会话保留最近两次明确接受的任务。未被接受的启动尝试不消耗历史，第三次接受淘汰最早任务；循环与重复调用只保留该任务中节点最后一次执行，最后失败或跳过不会回退旧成功图。画布仍表示当前任务，右侧可以独立查看上一次。
5. 点击 **放大查看** 或双击有效图片，复用一个非模态大图窗口。大图跟随右侧的任务、节点和端口；支持滚轮缩放、拖动平移、100%、适配、全屏及 Esc 退出全屏，缩放限 10%—800%。打开或关闭大图不增加任务、订阅或读图请求。窄侧栏可用标签箭头切换；短屏幕滚动图像页查看图片信息和按钮。
6. **运行日志** 保留原入口。内嵌 `--local` 模式的 Runtime JSONL 文件默认位于 `manual_test_workspace/runtime-embedded/logs/`；外部模式位于服务端数据目录。GUI 日志缓存与 Runtime 落盘日志是两个来源。选择节点、标签和历史均不启动任务；关闭大图或失去检查租约不停止任务，原 Designer 对自己明确启动的 Job 的退出收尾语义不变。
7. 项目切换、窗口关闭或 30 秒检查租约到期释放新增历史资源；客户端每 10 秒续约。旧 Runtime 不支持新接口时明确提示能力缺失，旧执行接口仍可使用。每任务至多 64 条、512 KiB 摘要和 64 MiB 编码图片；只解码当前选择，解码限 8 MiB，本地编码读图限 4 MiB，超限明确拒绝显示。

隔离的真实 Qt 验证和截图脚本：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONUTF8 = '1'
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" docs/testing/node-results-2026-10-04/capture.py --output "manual_test_workspace/node-results-$(Get-Date -Format yyyyMMdd-HHmmss)" --width 1600 --height 900 --detailed
```

脚本实际打开原生 Designer，使用真实 spawn Runner 和 typed loopback gRPC：A 图片有 2 个对象，B 有 3 个对象，第三次注入缺图失败；逐节点核对图片与数值，验证历史、大图、淘汰、工程切换和关闭清理。项目、数据库、输出和日志在仓库外的临时目录，证据输出目录必须不存在。可用 `QT_SCALE_FACTOR` 单独重启进程测试 Qt 缩放，`--main-fullscreen` 记录本机实际全屏客户区；不修改物理屏幕分辨率。

结果与限制见 [两次节点结果及大图验证](testing/node-results-2026-10-04.md)；数据及资源规则见 [正式契约](runtime-node-results-contract.md)。单图截图不替代原 1080p/5 Hz、资源稳态、A18 或 Qt 组合稳定性验收，原失败记录继续保留。此前 [仅本次摘要的验证记录](testing/flow-run-inspector-2026-10-04.md) 作为历史证据保留。
