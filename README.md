# emo_master

EmoMaster 是一个参考 VisionMaster 思路实现的机器视觉流程设计与运行平台，当前代码版本为 `0.6.1`。

> 项目仍处于实验性阶段，适合学习、验证和二次开发，尚未面向生产环境。它是独立实现，与 VisionMaster 及其厂商不存在隶属或官方关联。
>
> 本文描述 `agent/runtime-workflow-architecture-v1` 分支的代码。开发该版本时请明确切换分支，不要将本页能力视为尚未合入的 `main` 已具备的能力。

## 项目由什么组成

| 部分 | 职责 | 代码位置 |
| --- | --- | --- |
| Designer | 项目管理、流程编排、算子参数编辑，以及运行状态、日志和结果展示 | `src/emo_master/apps/designer` |
| Runtime | 加载项目、扫描算子、调度工作流、管理 Job、记录事件及提供预览服务 | `src/emo_master/apps/runtime` |
| core | 项目、工作流、插件和执行数据的共享契约与校验 | `src/emo_master/core` |
| plugins | 具体算法、设备和通信算子，以及可选编辑器与图标资源 | `src/emo_master/plugins` |

使用上是 **Designer 设计端 + Runtime 执行端**，不是必须分别打开的两个软件：

- **默认内嵌模式**：只启动 Designer，由它直接创建并调用 `RuntimeService`，不经过网络 gRPC。每个正式 Job 仍使用独立的 `multiprocessing.spawn` 子进程。
- **外部服务模式**：独立启动 Runtime，Designer 设置 `EMO_RUNTIME_TARGET` 后通过 gRPC 连接。源码入口默认监听 `127.0.0.1:50051`。

当前 Windows 包无参数启动 `EmoMaster.exe` 会进入 Designer，未提供 `EmoMaster.exe --runtime` 模式。独立 Runtime 启动后等待请求，不会自动加载并循环运行现场项目。详情见 [工程架构](docs/engineer-guide.md) 和 [部署与发布](docs/deployment-guide.md)。

## 从源码开始开发

以下命令用于 **Windows PowerShell**，需要先安装 Git 和 Conda，并在能够执行 `conda activate` 的终端中操作。当前项目要求 **Python 3.10**，不是任意 Python 3.x。

```powershell
git clone --branch agent/runtime-workflow-architecture-v1 --single-branch https://github.com/jsdfhasuh/emo_master.git
Set-Location .\emo_master

conda create -n emo_master python=3.10 -y
conda activate emo_master
python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，请先解决错误。" }

# 本项目使用 src 布局；当前 dev.py 不会自动设置源码搜索路径。
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -c "import sys, emo_master; print(sys.executable); print(emo_master.__version__); print(emo_master.__file__)"
if ($LASTEXITCODE -ne 0) { throw "源码导入失败，请检查解释器与 PYTHONPATH。" }

# 日常开发使用独立数据目录，不接触默认运行数据。
Remove-Item Env:EMO_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
$env:EMO_RUNTIME_DATA_DIR = Join-Path (Get-Location).Path "manual_test_workspace/runtime-embedded"
Remove-Item Env:EMO_RUNTIME_TARGET -ErrorAction SilentlyContinue
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
$env:HUARAY_CAMERA_SMOKE = "0"

python scripts/gen_proto.py --check
if ($LASTEXITCODE -ne 0) { throw "protobuf 检查失败，请按开发指南排查。" }
python scripts/dev.py run-designer
```

`requirements-dev.txt` 已包含运行依赖，不需要再二选一或重复安装。环境变量只对当前终端及其子进程生效；新开终端后要重新激活环境、设置源码路径和运行模式。已有仓库不要重复克隆，也不要为切换分支丢弃本地修改。

完整的新环境准备、双终端分离运行、IDE 配置、Linux 检查及故障排查见 [开发指南](docs/development-guide.md)。

## 第一次运行项目

在项目入口新建空白项目或打开已有 `project.json`，添加算子并连接端口。双击节点打开独立工作区，设置参数并应用，保存后点击“开始运行”，在节点详情、结果预览和日志中检查输出。文件输出位置由对应算子参数决定。

第一次验证应使用本地图像或不涉及设备的流程。相机预览需要显式点击连接，不会自动连接现场设备；执行包含相机、PLC 或 TCP 算子的正式流程则可能产生真实设备操作。不要把常规软件自检当作现场联调许可。

## 当前能力与边界

- `project.json v2.1` 多工作流、入口工作流、Subflow、类型化 Loop，以及项目和工作流包的保存、导入导出与迁移基础能力。
- 异步 Job、spawn worker 隔离、SQLite 事件重放、实时 follow 事件流、结构化算子日志、滚动 JSONL 与可浮动日志 Dock。
- manifest 自动发现的插件算子；schema 1.2/Homography、经典视觉、强类型集合、TXT/CSV 坐标读取、点序列变换和几何测量。
- 三菱 SLMP/MC 3E PLC 读写、有界 TCP 客户端与单次接收、内嵌标量和文本输出；华睿 IMV 单帧采集、作业内连接复用和可取消硬件触发等待。
- 可选 `.ui + Controller` 算子编辑器、作业快照、纯计算预览、相机实时预览、浅色 Designer、高 DPI 适配、离线 Lucide 与插件自定义图标。
- ONNX Runtime CPU 驱动的 YOLOv8/YOLO11 detect 推理、NMS 和原图坐标叠加。

YOLO 算子使用 `onnxruntime==1.23.2`，模型路径只接受 `.onnx`，设备只接受 `auto` 或 `cpu`。支持 batch 1、3 通道 float32、NCHW、未内置 NMS 的 YOLOv8/YOLO11 detect 输出；不支持端到端 NMS、pose、segmentation、INT8、DirectML 或 CUDA。动态输入模型使用 `imageSize`，固定输入模型以模型尺寸为准。

## 检查与开发入口

在已配置开发环境的仓库根目录运行：

```powershell
python scripts/ci_check.py
```

该命令依次执行 protobuf 一致性检查、Ruff、mypy 和 pytest。修改 `proto/runtime.proto` 后才需要运行 `python scripts/gen_proto.py` 重新生成，并连同生成文件一起提交；不要为掩盖一致性检查失败而直接覆盖生成文件。

新增算子不需要修改中央注册表，见 [算子注册与执行流程](docs/plugin-registration-flow.md)。自定义图片、分类兜底和冻结资源说明见 [算子图标资源](docs/operator-icon-assets.md)。截图检查和真实硬件验收是不同层次，见 [Designer 验证记录](docs/designer-ui-validation.md)。

## Windows 部署与打包

现场使用者使用便携 ZIP 或当前用户安装包，无需另行配置源码开发环境。便携包必须完整解压，不能只复制 `EmoMaster.exe`；华睿 MV Viewer/MVSDK 仍需单独安装。安装程序默认目录为 `%LOCALAPPDATA%\Programs\EmoMaster`，应用安装不需要管理员权限，设备驱动安装权限另行处理。

本地打包在独立的 `jsdfhasuh/python_build_scripts` 仓库完成，而不是在本仓运行向导：

```powershell
# 在 python_build_scripts 根目录，完成打包环境准备后执行。
python scripts\release_wizard_emo_master.py
```

仅测试交付包时使用 `-BuildOnly`，不要上传 Release。正式发布由本仓匹配应用版本的 `v*` tag 触发，经过源码检查、中央构建和产物验证后生成：

```text
emo-master-windows-${TAG}.zip
emo-master-setup-${TAG}.exe
manifest.json
```

环境要求、本地只构建命令、冻结包自检、正式发布、数据备份和部署限制见 [部署与发布指南](docs/deployment-guide.md)。文档描述已有流程，不表示某个 tag 已经成功构建或完成硬件验收。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [开发指南](docs/development-guide.md) | 源码环境、启动、调试、检查和常见错误 |
| [部署与发布指南](docs/deployment-guide.md) | Windows 交付、本地构建、CI 发布、自检与数据保留 |
| [工程师导读](docs/engineer-guide.md) | Designer、Runtime、core 边界与执行主线 |
| [项目格式](docs/project-json-spec.md) / [工作流包](docs/workflow-package-spec.md) | 文件契约、迁移和交换格式 |
| [算子注册](docs/plugin-registration-flow.md) / [输入输出契约](docs/operator-io-contracts.md) / [图标资源](docs/operator-icon-assets.md) | 扩展算子与排查注册、类型、图标问题 |
| [Designer / Runtime 时序](docs/runtime-designer-sequences.md) / [事件流](docs/runtime-event-flow.md) | 项目、Job、预览和事件交互 |
| [工作区约定](docs/workspace-guide.md) / [UI 规范](docs/qt-widgets-qss-guidelines.md) | 源码、测试数据和界面开发规范 |
| [验证记录](docs/designer-ui-validation.md) / [截图归档](docs/testing/designer-ui-2026-09-10/README.md) | 已有验证证据及未完成项，不代表当前提交重新验收 |
