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

## 8. 文档验收与依据

本次更新依据仓库入口、依赖和工作流进行核对，不宣称重新完成 Windows 干净环境安装、GUI、相机、PLC 或冻结包验收。交付前应在干净 Python 3.10 环境依次验证：源码导入、内嵌运行、外部分离运行、完整检查及 Windows 包自检，并保存各自结果。

代码依据：`scripts/dev.py`、`scripts/ci_check.py`、`scripts/gen_proto.py`、`pyproject.toml`、`requirements-dev.txt`、`apps/designer/main.py`、`apps/runtime/main.py`；后两者位于 `src/emo_master` 下。

环境变量和进程行为参考 [Python 3.10 命令行与环境](https://docs.python.org/3.10/using/cmdline.html) 及 [PowerShell 环境变量](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables)。
