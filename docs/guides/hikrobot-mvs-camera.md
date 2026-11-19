# 海康 MVS 相机采集

原相机算子 `vision.io.huaray_camera` 的 `camera` 槽可以绑定大华／华睿 IMV 或海康 MVS。Runtime 按绑定设备的类型选择驱动，每次执行节点输出一张图。同一工程中的不同节点可以分别绑定两家的相机。

## 在 Designer 采第一张图

1. 在运行 Runtime 的 Windows 电脑上安装海康 MVS，安装时选上 64 位运行库和开发组件中的 Python 示例。打开 MVS 客户端，记下目标相机的序列号（SN），再关闭客户端里的相机连接，让 Runtime 可以独占打开设备。
2. 在 Designer 的设备管理器中新建“海康机器人 MVS 工业相机”，点击“发现设备”。按厂商、型号和 SN 核对设备，再点“采用所选序列号”。列表支持 GigE（含通过 GenTL 网卡识别的 GigE）和 USB3。离线登记可以手动填写 SN，保留前导零。Runtime 在发现和打开时都核对设备报告的厂商。点击“测试连接”可确认所填 SN 对应的海康相机能够打开。
3. 在“工作参数”保留默认的软件触发和 BGR 输出。在工作流中打开原有的相机节点，或从“图像采集”添加“工业相机采集（大华华睿 IMV / 海康 MVS）”，将 `camera` 绑定到刚登记的设备。原节点上的 `image`、`frame`、`blockId`、`deviceTimestamp`、`actualExposureUs` 连线可以继续使用。
4. 保存工程，通过现有工程加载或“写入 Runtime”流程使设备定义生效。单次运行该工作流，查看采集节点的图像输出。

已绑定大华设备的节点可以继续运行。手动改绑海康时，向导会列出将改为继承新设备的节点参数，可在完成前重新设置所需覆写。

旧工程中直接填写了 IMV 参数的节点，可以双击节点或从设备迁移向导进入。即使工程已升级，向导仍会检查节点保留的旧参数和绑定。改用海康时，请核对目标 SN、将移除的 IMV 参数和变量绑定，再确认。IMV 的 `cameraKey` 与海康 SN 分开填写，旧 `gainRaw` 的值也不会自动换算成 dB。取消会保留原草稿。确认后保存工程，再按正常流程加载到 Runtime。

如果旧节点通过现场绑定提供 `cameraKey`，或通过变量提供 `gainRaw`，直接改绑海康会提示该来源无法映射。可先将旧节点迁移到 IMV 设备，保留原有绑定，再改绑已登记的海康设备，按其 SN 和 dB 单位设置所需来源。

## 触发与输出

| 工作方式 | 每次执行节点时的行为 |
| --- | --- |
| 软件触发 | 发送一次 `TriggerSoftware` 并等待该次采集的图像。 |
| 硬件触发 | 按所选输入线和边沿等待外部信号。请按具体机型和接线选择 Line0 至 Line3 中可用的一路。取图超时设为 0 时持续等待，可通过停止任务取消。 |
| 连续采集 | 相机自由运行，节点从正在采集的流中取一帧。 |

驱动启动采集时会将支持配置的 `AcquisitionMode` 设为 `Continuous`，以支持多次执行同一节点。软件和硬件触发模式仍各自按触发信号产生图像。

**硬触发等待被停止或最终超时时**，如果相机已经开始等待本次外部触发，Runtime 会关闭并隔离这台相机，避免迟到的上一件工件信号被下一件取走。先停止外部触发，确认下一次触发与工件重新对齐，退出使用该设备的任务与诊断窗口，再到设备管理器选择该相机，读取在线状态并点击“恢复设备会话”。随后重新启动任务，待相机就绪后恢复触发。恢复操作不会补拍或重放上一任务。正常连续硬触发时仍按顺序取帧。

曝光和增益默认沿用相机当前设置，也可在设备工作参数中改为手动。曝光单位为微秒，增益单位为 dB。输出可选三通道 BGR 或单通道灰度。`frame` 提供图像坐标系和相机 ROI 的偏移，`blockId` 为设备帧号，`deviceTimestamp` 为相机时钟的原始计数。

`actualExposureUs` 优先取该帧附带的曝光时间，此时诊断预览显示“本帧曝光”。相机未提供逐帧曝光信息时，驱动读取 `ExposureTime`，预览显示“曝光回读（相机当前值）”。自动曝光时，回读值可能和刚收到的那一帧不同。

## SDK 位置与连接检查

| 默认安装路径 | 用途 |
| --- | --- |
| `C:\Program Files (x86)\MVS\Applications\Win64` | MVS 客户端，用于查看相机、序列号和相机端配置。 |
| `C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport` | 官方 Python 绑定，含 `MvCameraControl_class.py`。MVS 安装程序通过 `MVCAM_COMMON_RUNENV` 指向 `Development`。 |
| `C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64` | 64 位 `MvCameraControl.dll` 及运行库依赖。 |

序列号匹配失败时，先在 **Runtime 所在电脑**的 MVS 客户端核对 SN 和厂商名。驱动识别 HIKROBOT、HIKVISION 及已知中英文公司全称。仅上报其他厂商名的设备会被过滤。若多台海康设备报告相同 SN，打开时会提示冲突。设备占用时，关闭其他客户端的相机连接。硬件触发等待超时时，检查相机输入线、触发边沿和外部信号。首张图可用软件触发检查采集链路。

## 冻结程序的相机自检

在安装了 MVS 64 位运行库和 Python 示例的 Windows 电脑上，可从 PowerShell 对两个便携程序分别运行相机自检。以下命令使用当前目录下的两个产品目录，并把结果写到新建的临时目录。

```powershell
$cameraCheck = Join-Path $env:TEMP ('emo-camera-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $cameraCheck | Out-Null
foreach ($product in 'EmoMaster', 'EmoMasterRuntime') {
    $report = Join-Path $cameraCheck ($product + '.json')
    $process = Start-Process -FilePath ".\$product\$product.exe" -Wait -PassThru -WindowStyle Hidden `
        -ArgumentList @('--self-test', '--camera-drivers', '--result-json', ('"{0}"' -f $report))
    if ($process.ExitCode -ne 0) { throw "$product 自检失败，详见 $report" }
    (Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json).checks.cameraDrivers
}
```

`cameraDrivers` 会核对原算子对两种设备的注册，在独立海康 Host 中读取官方 SDK 版本，并由模拟会话生成两张图像交给原算子核验。报告记录 `sdkVersion`、`syntheticFrames`、`hostPid` 和 `hostFrozen`。普通自检可以省略 `--camera-drivers`，无需安装 MVS。

## 验证记录

2026-11-18 在 Windows、Python 3.10.21 上验证，工作区基线为 `ce8f84caf4d0fd779e579b23ff82fa4e80de978d`。测试包含未提交的双品牌接入改动，以及同一工作区中的 IMV 打开失败提示及其测试。

203 项定向回归通过。随后将工作流用例扩展为大华和海康在同一工程内各采一帧，连同海康 SDK 和原生 Qt 绑定用例复测 18 项通过。两次正式 Job 都通过各自的模拟驱动生成了预期的灰度图和彩色图，并经原有图像保存算子落盘。本机官方 SDK 返回版本 4.8.0.3。尚未进行真机取图或冻结 EXE 验收。

同日审查修复后的未提交工作区仍基于上述提交。342 项定向回归通过，其中 SDK、算子和工程设备模型 135 项，Runtime 110 项，Designer 离屏 Qt 97 项。另在 Windows 原生 Qt 平台复测相机绑定、迁移、发现及诊断 21 项通过。旧节点分别从双击和迁移向导进入，执行了取消、确认、保存、重开和工作流编译。正式 Runtime 配合模拟相机验证了硬触发任务停止后，下一任务无法消费上一工件的迟到帧，确认同步恢复后只保存新触发帧。

源码设备注册自检和 Ruff 通过。本机 MVS 版本查询与官方结构体离线检查均未访问相机。全仓 mypy 仍报告一处既有类型错误，位于 `src/emo_master/ui/presentation/appearance.py` 第 13 行。

### 2026-11-18 Windows 便携程序

从同一基线及未提交的相机修复和自检代码构建了 Designer 和独立 Runtime 0.6.6。使用仓库指定的打包器提交 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`、两份正式目标配置和 PyInstaller 6.22.2。逐一比对了两个 EXE 内的海康适配器和相机自检模块与最终源码。

两个 EXE 从工程源码之外的工作目录运行，清除了 Python 环境变量，PATH 只含 Windows 系统目录。两者均在独立的冻结相机 Host 中读到官方 MVS 版本 4.8.0.3，通过原算子校验了两张合成 BGR 图及五个输出端口。Designer 的 Qt 图标检查通过。独立 Runtime 启动三轮测试任务，每轮正常停止并退出工作进程，未导入 Designer 模块。构建期间，一次源码 Runtime 的 Dispatch 自检曾等待超时，随后源码与冻结版本各自单独运行完整 Runtime 自检均通过。

两个便携目录可分别运行，各自需要随附的 `_internal` 目录。验证范围包括 SDK 加载、模拟采集、进程通信及 Qt 离屏自动化。实物相机的发现、接线、触发和取图仍待现场验收。
