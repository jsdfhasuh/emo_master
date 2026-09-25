# 开发指南

适用分支：`agent/runtime-workflow-architecture-v1`。本次文档核对基线为 `33fc53e`（应用版本 `0.6.1`），整理日期为 2026-09-25。后续命令以所在提交的源码和依赖文件为准。

本指南负责从源码开发和调试；交付 EXE、安装包和正式发布见 [部署与发布指南](deployment-guide.md)，架构边界见 [工程师导读](engineer-guide.md)。

## 1. 准备环境和源码

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

## 2. 每个新终端都要配置源码路径

以下及后续源码命令均在 **emo_master 仓库根目录** 执行。IDE 终端也需要相同配置。

```powershell
conda activate emo_master
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:HUARAY_CAMERA_SMOKE = "0"

python -c "import sys, emo_master; assert sys.version_info[:2] == (3, 10); print(sys.executable); print(emo_master.__version__); print(emo_master.__file__)"
if ($LASTEXITCODE -ne 0) { throw "解释器或源码路径不正确。" }
python scripts/gen_proto.py --check
if ($LASTEXITCODE -ne 0) { throw "protobuf 检查失败，请查看第 5 节。" }
```

`emo_master.__file__` 应指向当前仓库的 `src/emo_master/__init__.py`。第三方依赖安装成功并不代表项目自身已经安装；目前 `scripts/dev.py` 只包装模块启动，不会给子进程补 `src` 路径。本指南显式配置 `PYTHONPATH`，不依赖 IDE 的隐式路径或其他机器遗留的可编辑安装。

这里用赋值将当前终端的 `PYTHONPATH` 限定为本仓源码，避免串到其他工作副本。不要使用 `setx` 或全局系统环境变量固定某个工作副本。终端关闭后配置失效；新终端要重新执行。本指南尚未将 `pip install -e .` 作为经过验证的标准安装流程。

## 3. 默认方式：Designer 内嵌 Runtime

在完成第 2 节配置的同一终端中执行：

```powershell
Remove-Item Env:EMO_RUNTIME_TARGET -ErrorAction SilentlyContinue
Remove-Item Env:EMO_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
$env:EMO_RUNTIME_DATA_DIR = Join-Path (Get-Location).Path "manual_test_workspace/runtime-embedded"
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
python scripts/dev.py run-designer
```

Designer 未配置外部目标时直接创建 `RuntimeService()`。这是同一应用进程内的服务调用，不会额外监听 gRPC 端口；正式 Job 仍在 Runtime 管理的 spawn 子进程中执行。关闭 Designer 时会关闭它拥有的内嵌 Runtime。

`manual_test_workspace` 用于本机隔离数据，不应提交。`EMO_RUNTIME_DB_PATH` 的优先级高于 `EMO_RUNTIME_DATA_DIR`，所以示例先清除它，避免数据仍写到之前指定的数据库。

第一次启动使用本地图像或不涉及设备的流程。打开项目本身不等于已授权执行设备动作；含相机、PLC、TCP 算子的流程只应在确认设备和参数安全后运行。

## 4. 可选方式：同机分离调试

这一方式使用两个 PowerShell 终端。两边都应位于同一版本的源码根目录，并完成第 2 节的环境准备。不需要启动第三个内嵌实例。

### 终端 A：先启动 Runtime

```powershell
conda activate emo_master
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:HUARAY_CAMERA_SMOKE = "0"
Remove-Item Env:EMO_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
$env:EMO_RUNTIME_DATA_DIR = Join-Path (Get-Location).Path "manual_test_workspace/runtime-external"
python scripts/dev.py run-runtime
```

保持该终端运行。入口默认监听 `127.0.0.1:50051`，此时只是等待客户端请求，不会自动加载项目和开始检测。

### 终端 B：再启动 Designer

```powershell
conda activate emo_master
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:HUARAY_CAMERA_SMOKE = "0"
$env:EMO_RUNTIME_TARGET = "127.0.0.1:50051"
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
python scripts/dev.py run-designer
```

外部模式的数据目录由 **终端 A 的 Runtime** 决定，不由 Designer 终端的数据库变量决定。结束调试时先停止 Job、关闭 Designer，再在 Runtime 终端按 Ctrl+C；关闭外部客户端不等于关闭独立服务。

恢复内嵌模式时，先关闭当前 Designer，再清除 `EMO_RUNTIME_TARGET` 并按第 3 节启动。每个独立 Runtime 实例必须使用不同数据目录；不要同时让两个服务访问同一数据库，也不要通过删除锁文件强行启动。

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

IDE 使用 `emo_master` 环境中的 Python 3.10，工作目录设为仓库根目录，环境变量按第 2 至 4 节配置。调试入口可选模块 `emo_master.apps.designer.main` 或 `emo_master.apps.runtime.main`；不要直接执行包内部文件来绕过导入问题。启动类型必须是模块，而不是把带点模块名当作文件路径。

源码和可共享示例可提交；`manual_test_workspace`、运行数据库、模型、现场相机参数及原始验收资料不能因未跟踪就直接上传。保留现场 `project.json`，不要为测试覆盖真实项目。提交前检查 `git status --short` 和 `git diff --check`。

新增算子按 [注册流程](plugin-registration-flow.md) 和 [图标资源](operator-icon-assets.md) 操作；新增项目字段同步模型、迁移、Designer store 和 Runtime loader；新增 RPC 同步 proto 及生成物。不要把业务执行逻辑放进 Designer 或薄 RPC 适配层。目录与数据约定见 [工作区指南](workspace-guide.md)。

## 7. 常见问题

| 现象 | 首先检查 |
| --- | --- |
| `No module named emo_master` | 是否在源码根目录、激活正确环境并设置当前终端的 `PYTHONPATH`；用第 2 节检查实际导入文件 |
| 找不到 Ruff、mypy 或 pytest | 是否只安装了 `requirements.txt`；开发应安装 `requirements-dev.txt` |
| PowerShell 设置外部地址后仍走内嵌模式 | 是否误用了 CMD 的 `set`；环境变量是否设置在启动 Designer 的同一个终端 |
| 无法连接 `127.0.0.1:50051` | Runtime 终端是否存活、端口是否被其他程序占用、两端协议是否匹配；`Test-NetConnection 127.0.0.1 -Port 50051` 只能验证 TCP 可达，不代表 RPC 正常 |
| 数据目录被占用或启动失败 | 是否已有内嵌/外部 Runtime 使用同一目录；检查更高优先级的 `EMO_RUNTIME_DB_PATH`，不要删除活跃实例的锁或数据库 |
| 进程存在但没有窗口 | 是否继承了 `QT_QPA_PLATFORM=offscreen`；恢复可见 GUI 前清除该变量 |
| EXE 报 `unsupported arguments` | 现有包不支持 `--runtime`、`--host` 等参数；使用支持的自检参数或按源码入口调试 |

## 8. 文档验收与依据

本次更新依据仓库入口、依赖和工作流进行核对，不宣称重新完成 Windows 干净环境安装、GUI、相机、PLC 或冻结包验收。交付前应在干净 Python 3.10 环境依次验证：源码导入、内嵌运行、外部分离运行、完整检查及 Windows 包自检，并保存各自结果。

代码依据：`scripts/dev.py`、`scripts/ci_check.py`、`scripts/gen_proto.py`、`pyproject.toml`、`requirements-dev.txt`、`apps/designer/main.py`、`apps/runtime/main.py`；后两者位于 `src/emo_master` 下。

环境变量和进程行为参考 [Python 3.10 命令行与环境](https://docs.python.org/3.10/using/cmdline.html) 及 [PowerShell 环境变量](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables)。
