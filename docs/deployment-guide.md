# 部署与发布指南

本指南适用于 `agent/runtime-workflow-architecture-v1`，核对基线为应用提交 `33fc53e`、版本 `0.6.1`；中央打包仓核对提交为 `ab4a33e`。整理日期为 2026-09-25。发布时必须重新核对实际源码提交与打包仓配置，不能把本文版本号当作“最新 Release”。

源码环境准备见 [开发指南](development-guide.md)，整体职责见 [工程师导读](engineer-guide.md)。本页说明已有交付流程及限制，不意味着已经完成生产环境认证或硬件验收。

## 1. 先选择运行和交付方式

| 场景 | 当前方式 | 边界 |
| --- | --- | --- |
| 开发、修改和调试 | Python 3.10 源码环境，启动 Designer 或独立 Runtime | 需要依赖与 `src` 路径配置 |
| 单机交付 | 完整便携 ZIP 或当前用户安装包；无参数启动 `EmoMaster.exe` | 默认 Designer + 内嵌 Runtime |
| 同机分离验证 | 源码启动 Runtime；Designer 设置 `EMO_RUNTIME_TARGET` 连接 | 默认 `127.0.0.1:50051`，详细双终端步骤见开发指南 |
| 跨机、后台常驻、开机自动检测 | 不作为本版可直接照抄的部署流程 | 当前 EXE 没有 `--runtime` 模式，独立源码入口不会自动加载并运行项目 |

Windows 入口 `src/emo_master/apps/windows_entry.py` 只分派 `--self-test` 或无参数 Designer 启动，其他参数会被拒绝。程序支持连接外部 Runtime，并不等于已经提供独立 Runtime EXE、Windows 服务安装器或自动运行现场项目的命令。

## 2. Windows 现场安装和数据准备

当前打包目标是 Windows x64。使用匹配源码版本的完整发布产物，并核对随包清单中的 SHA256。不要仅凭文件名判断包来自哪个源码提交。

便携版应完整解压并保留所有依赖、插件和资源目录，不能只复制 `EmoMaster.exe`。安装版为当前用户安装，默认目录为 `%LOCALAPPDATA%\Programs\EmoMaster`，应用安装不要求管理员权限。使用者无需为了运行冻结包额外搭建 Conda 开发环境。

华睿 MV Viewer/MVSDK 及设备驱动仍需单独安装，驱动安装可能需要额外权限。项目、图片、模型和标定文件按现场配置另行准备，不能把开发机的绝对路径直接视为现场可用路径。模型和现场数据不是默认内置资源，也不应上传到公开 Release。

默认启动 Designer 后先打开项目、核对路径和设备参数，再进行离线验证。只有确认安全条件后才连接相机、执行 PLC 写入或联动设备。新版本上线前保留旧程序和数据备份，异常时先停止任务再回退。

## 3. 数据目录、备份与回退

未覆盖配置时，Runtime 使用 `~/.emo_master/runtime/emo_master.db`，Windows 通常位于 `%USERPROFILE%\.emo_master\runtime\emo_master.db`。Job workspace 位于同一数据目录的 `jobs/` 下，不在安装目录。

| 配置 | 含义与优先级 |
| --- | --- |
| 显式 `RuntimeService(dbPath=...)` | 构造参数，优先于环境变量 |
| `EMO_RUNTIME_DB_PATH` | 完整 SQLite 文件路径，优先于数据目录变量 |
| `EMO_RUNTIME_DATA_DIR` | 数据目录，数据库名固定为 `emo_master.db` |
| 不设置 | 使用用户目录下的默认路径 |

路径支持 `~` 展开。同一数据目录同一时刻只允许一个跨进程 Runtime 实例。外部模式由服务端进程的配置决定路径；给客户端改数据目录不会迁移服务端数据。

备份前停止 Job 并退出相关 Runtime，再备份项目目录、模型/标定等外部资源和 Runtime 数据目录。不要在 SQLite 仍被使用时只复制一个 `.db` 文件作为可靠备份，也不要删除锁文件绕过占用检查。`jobs/` 是临时工作区，存在终态及关闭清理逻辑，不应作为长期结果归档目录。

读取旧项目会进行内存迁移；保存升级后的项目会备份并原子替换。回退时应恢复对应版本的项目和运行数据备份，不能假设旧程序可以读取新版本写入的格式或数据库。卸载程序保留用户数据的验收不等于数据已经有备份。

## 4. 冻结包自检

所有示例为 **PowerShell**。必须区分源码测试和冻结包测试：前者通过不能证明 EXE 的 DLL、插件、图标资源均被收集。

### 已解压或安装的 EXE

进入包含 `EmoMaster.exe` 的目录，在一个专用验收终端中执行。GUI 可执行文件的自检使用 `Start-Process -Wait -PassThru` 等待完成并取得退出码，不要刚启动就读取结果文件。

```powershell
$exe = (Resolve-Path .\EmoMaster.exe).Path
$checkDir = Join-Path $env:TEMP ("emo-master-check-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $checkDir | Out-Null
$env:QT_QPA_PLATFORM = "offscreen"
$env:HUARAY_CAMERA_SMOKE = "0"
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
try {
    $process = Start-Process -FilePath $exe -WorkingDirectory $checkDir -Wait -PassThru -ArgumentList @("--self-test", "--gui-icons", "--result-json", "self-test.json")
    $reportPath = Join-Path $checkDir "self-test.json"
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) {
        throw "包自检失败，退出码：$($process.ExitCode)，报告路径：$reportPath"
    }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if ($report.status -ne "ok" -or $report.checks.guiIcons.status -ne "ok") {
        throw "自检报告未通过：$reportPath"
    }
    Write-Output "自检报告：$reportPath"
} finally {
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
}
```

正式 GUI 启动前不要保留 `QT_QPA_PLATFORM=offscreen`。本例清除了验收终端的源码搜索路径，后续源码开发请重新按开发指南配置。

真实模型测试还可给自检入口传入 `--model`、`--image`、`--expected-detections` 和 `--result-json`。模型使用受支持的 ONNX detect 格式，期望检测数量应来自已知样本，不要直接照抄示例数量作为现场验收标准。软件自检不替代相机、PLC、机器人联动和长时间运行测试。

### 发布 ZIP 的图标门禁

在已取得发布 ZIP 的情况下，从源码根目录执行：

```powershell
# 路径按实际产物修改；这里的 v0.6.1 只是文件名示例。
.\scripts\verify_windows_package.ps1 -Archive "D:\packages\emo-master-windows-v0.6.1.zip"
```

该脚本在新临时目录解压，等待冻结程序自检，要求 GUI 图标状态和 9 项样本检查通过，并设有超时。默认不覆盖已有输出目录。脚本在当前终端设置 `QT_QPA_PLATFORM=offscreen`；验收后关闭该终端，或在启动 GUI 前显式清除该变量。检查失败不应通过跳过门禁来发布。

## 5. 本地构建：在中央打包仓操作

应用源码仓是 `jsdfhasuh/emo_master`；构建逻辑位于独立仓库 [python_build_scripts](https://github.com/jsdfhasuh/python_build_scripts)。不要在应用源码根目录寻找 `build.py` 或 `release_wizard_emo_master.py`。

建议两个仓库放在同一父目录：

```text
work/
  emo_master/
  python_build_scripts/
```

首次取得打包仓可在该父目录执行：

```powershell
git clone --branch master https://github.com/jsdfhasuh/python_build_scripts.git
Set-Location .\python_build_scripts
```

已有打包仓不要重复克隆；先保留未提交修改，再获取并核对需要使用的提交。正式交付记录两个仓库的 SHA，不要只记录分支名。

**中央构建包含安装、自检、卸载验收，应在专用测试账号或虚拟机内构建，不要直接在正在运行的现场电脑执行。** 这不是单纯压缩源码的操作。

### 准备打包环境

本次核对的 `configs/emo-master.json` 指定 Python 3.10、PyInstaller 6.22.2、Inno Setup 6.7.3。以下版本对应核对基线；更新打包仓后重新查看配置，不要盲目升级或混用其他目标的环境。

先安装 Git 和 GitHub CLI（`gh`）。当前发布脚本在处理 `-BuildOnly` 返回之前，也会尝试用 `gh` 查询历史 Release 来生成更新日志，因此不要等到上传阶段才准备该工具。访问受限仓库或实际上传前用 `gh auth status` 确认授权，令牌不要写入文档或源码。

以下命令在 **python_build_scripts 根目录** 执行，假定应用源码为相邻的 `../emo_master`：

```powershell
conda create -n emo_master_build python=3.10 -y
conda activate emo_master_build
Get-Command git, gh -ErrorAction Stop | Out-Null
$SourceRoot = (Resolve-Path ..\emo_master).Path
$env:PYTHONPATH = Join-Path $SourceRoot "src"
$env:HUARAY_CAMERA_SMOKE = "0"

python -m pip install -r (Join-Path $SourceRoot "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "应用依赖安装失败。" }
python -m pip install pyinstaller==6.22.2
if ($LASTEXITCODE -ne 0) { throw "打包依赖安装失败。" }

# 先自行安装与配置匹配的 Inno Setup；路径按本机实际位置修改。
$env:ISCC_PATH = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path -LiteralPath $env:ISCC_PATH)) { throw "未找到 Inno Setup 编译器。" }

git -C $SourceRoot status --short
git -C $SourceRoot rev-parse HEAD
git rev-parse HEAD
$Version = python -c "import sys, emo_master; assert sys.version_info[:2] == (3, 10); print(emo_master.__version__)"
if ($LASTEXITCODE -ne 0) { throw "无法读取正确源码的版本。" }
$ReleaseTag = "v" + $Version.Trim()
```

源码版本来自当前 `SourceRoot`，不是从手工填写的旧 tag 推断。正式发布前要求源码工作区干净，并确认 `src/emo_master/__init__.py` 和 `pyproject.toml` 中版本一致。

### 先检查，再只构建

接着在同一打包终端执行：

```powershell
Push-Location $SourceRoot
try {
    python scripts/ci_check.py
    if ($LASTEXITCODE -ne 0) { throw "源码检查失败，停止构建。" }
} finally {
    Pop-Location
}

$OutputDirectory = Join-Path (Get-Location).Path ("artifacts/emo-master-" + $ReleaseTag)
.\scripts\publish-local-release.ps1 -Target emo-master -ReleaseTag $ReleaseTag -SourceRoot $SourceRoot -BuildOnly -OutputDirectory $OutputDirectory
```

`-BuildOnly` 仅构建，不上传 GitHub Release；不等于整个准备过程可以离线执行。依赖、工具和源码要提前备齐。不要在只测试包时去掉该参数，也不要用 `v0.0.0-local` 绕过源码版本校验。

预期产物为 `emo-master-windows-${ReleaseTag}.zip`、`emo-master-setup-${ReleaseTag}.exe` 和 `manifest.json`，这里 `${ReleaseTag}` 已包含 `v`。中央流程包含常规包自检及安装验收；还要针对这份应用源码执行图标门禁：

```powershell
$Archive = Join-Path $OutputDirectory ("emo-master-windows-" + $ReleaseTag + ".zip")
& (Join-Path $SourceRoot "scripts/verify_windows_package.ps1") -Archive $Archive
```

每一步报错都应先停止并检查日志，不能把“文件生成了”当作验收通过。避免复用旧 `dist` 导致产物版本与当前源码不一致。

### 专用交互式向导

完成环境准备后也可在打包仓根目录运行：

```powershell
python scripts\release_wizard_emo_master.py
```

向导固定选择 `emo-master`，从源码读取版本并校验 tag。最终确认前核对源码路径、tag、更新日志起点和执行模式；只构建时确认实际命令包含 `-BuildOnly`。向导不是应用源码仓的脚本。实际上传 Release 需要相应仓库权限；不要把令牌写入命令示例或提交到仓库。

## 6. 正式发布：应用仓 tag 工作流

本仓 `.github/workflows/release-windows.yml` 在推送 `v*` tag 后运行：

1. 检查 tag 与 `emo_master.__version__` 一致，运行源码检查。
2. 调用中央 `release-windows.yml@master`，固定 target 为 `emo-master`、source_ref 为触发提交，并暂不由中央流程发布。
3. 下载产物，检查恰好存在便携 ZIP、安装 EXE、manifest，核对名称和 SHA256，再执行冻结包图标门禁。
4. 由应用仓创建或更新 GitHub Release 并上传三项产物。

这是有外部发布效果的流程。**本次文档修改不创建 tag、不运行发布向导、不生成或发布安装包。**

发版前先完成版本更新、测试和审核，确认目标提交确实位于所需分支；tag 去掉 `v` 后必须与应用版本一致。检查 `git tag --list` 和远端已有 tag/Release，使用未发布的版本，不要强推或移动旧 tag 来补发文档。正式源码应无未提交修改，记录应用 SHA、中央构建 SHA、tag、自检报告和硬件验收状态。

中央 workflow 当前引用可移动的 `@master`；它不是完全固定构建依赖的承诺。需要可重复交付时应记录实际打包仓提交，未来变更为固定 SHA 要作为单独的发布配置改动验证。

本地发布向导和 tag 工作流是两条发布路径。同一版本不要同时操作两者；已有 Release 的流程可能覆盖附件，必须经过明确的重新发布审核。

## 7. 外部 Runtime 和尚未交付的部署能力

独立 Runtime 的源码命令为 `python scripts/dev.py run-runtime`，需要完整的运行依赖与源码路径。当前依赖文件没有为它提供独立的精简无 GUI 安装集，不能因服务没有窗口就直接删除 PySide2 等依赖。

默认只监听本机 `127.0.0.1:50051`。`EMO_RUNTIME_TARGET` 只改变 Designer 的连接地址，不会改变服务端监听；源码入口也没有 `--host` / `--port` 参数解析。不要在 README 命令后凭空添加这些参数。

跨电脑部署还需要单独设计并验证服务端监听、认证/传输保护、网络策略、两端协议版本，以及 Runtime 能访问的项目、模型、图像和输出路径。不能假定客户端本地路径会自动出现在另一台电脑，也不要将当前非 TLS gRPC 服务直接暴露到公网。

开机启动、Windows 服务化、无界面自动加载项目、持续触发和故障恢复，需要独立的运行策略、入口及验收。目前“能启动服务等待请求”不等于这些能力已经交付。

## 8. 验收记录与源码依据

本次文档更新按源码进行核对，没有据此宣称完成新一轮干净 Windows 环境、安装器、相机、PLC 或生产长稳验收。旧验证证据见 [Designer 验证记录](designer-ui-validation.md)，新交付应另行保存源码检查、冻结包自检、安装/卸载及硬件测试结果。

应用依据：`src/emo_master/apps/windows_entry.py`、`apps/runtime/main.py`（位于 `src/emo_master` 下）、`.github/workflows/release-windows.yml`、`scripts/verify_windows_package.ps1`。

中央配置和命令以 [emo-master 构建配置](https://github.com/jsdfhasuh/python_build_scripts/blob/ab4a33e586138fb381a87ae21cc83b5e8d79adbe/configs/emo-master.json)、[发布脚本](https://github.com/jsdfhasuh/python_build_scripts/blob/ab4a33e586138fb381a87ae21cc83b5e8d79adbe/scripts/publish-local-release.ps1) 和 [中央发布手册](https://github.com/jsdfhasuh/python_build_scripts/blob/ab4a33e586138fb381a87ae21cc83b5e8d79adbe/docs/release-runbook.md) 为基线。PowerShell 等待与退出码行为见 [Start-Process](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process)。
