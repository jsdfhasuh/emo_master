# 独立操作员 Runtime

本页描述 2026-10-06 实现的独立运行端。源码入口、便携 EXE、完整工程包和停机更新已接入；实际设备、生产模型和长期现场验收仍需单独完成。

## 启动工程

现场将 Runtime 便携 ZIP 解压到独立目录，保留 EXE 和 `_internal` 依赖目录，不只复制 EXE。工程单独放置：

```text
D:/Apps/EmoMasterRuntime/
  EmoMasterRuntime.exe
  _internal/
D:/site/ProductA/
  project.json
  models/
  assets/
  outputs/
```

```powershell
& "D:\Apps\EmoMasterRuntime\EmoMasterRuntime.exe" "D:\site\ProductA"
# 只检查工程，不开始检测
& "D:\Apps\EmoMasterRuntime\EmoMasterRuntime.exe" "D:\site\ProductA" --check
```

运行不需要安装 Python 或 Designer。需要具体设备的驱动/MVSDK 时仍单独安装。可以把工程路径写入 Windows 快捷方式参数；不传路径时，加载上次成功选择的工程，首次启动可选择 `project.json`。是否自动开始由该工程的 `production.autoStart` 决定，而不是开机自启服务。

开发机也可以在现有 `emo_master` Python 3.10 环境中运行：

```powershell
.\start_runtime.cmd "D:\现场工程\ProductA"
# 工程文件也可以直接传入
.\start_runtime.cmd "D:\现场工程\ProductA\project.json"
# 校验流程、资源和文件路径，不开始检测，即使 autoStart=true
.\start_runtime.cmd "D:\现场工程\ProductA" --check
```

`EMO_MASTER_PYTHON` 可以指定已有解释器，启动脚本不会安装依赖。不传路径时，运行端加载上次成功选择的工程；首次启动可选择 `project.json`。保存的位置只是工程路径，不覆盖设备参数、输出或运行策略。

独立入口为 `scripts/run_operator.py`，无需导入 Designer；现有 Windows 产品入口增加 `--run-project` 分支。旧 `--runtime`、`--operator-view` 仍是受限测试宿主和测试查看器，没有改为生产模式。

冻结 EXE 同样以 `run_operator.py` 为入口，集中打包仓的目标为 `emo-master-runtime`。已在隔离构建环境生成并验证本地产物，未修改基础 Conda 环境。Actions 接入和验证命令见 [Runtime 构建与交付](operator-runtime-build.md)；源码启动器仍需要 Python，不能当作免安装 Runtime。

## 工程内配置

旧 2.1/2.2 工程可直接加载，默认单次、不自动开始，不隐式改写原件。显式设置生产策略时升级为 2.3：

```powershell
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" `
  .\scripts\configure_runtime_project.py "D:\现场工程\ProductA" `
  --mode continuous --auto-start --cycle-interval-ms 100
```

脚本校验设置后备份 `project.json.bak` 并原子保存。其他工程参数保留。之后可以使用 `--no-auto-start` 或 `--mode single` 修改策略。也可以直接编辑工程中的配置：

```json
{
  "schemaVersion": "2.3",
  "production": {
    "autoStart": true,
    "mode": "continuous",
    "cycleIntervalMs": 100,
    "inputs": {}
  }
}
```

上面只是新增字段示意，不是完整工程。2.3 同时包含 `presentation` 和 `resources`，没有页面时可以为空。Designer 保存 2.3 会保留生产设置；本批不增加生产策略编辑面板。

`continuous` 表示反复执行一次入口工作流，串行运行，最小间隔按周期开始到下一周期开始计算。如果一次执行耗时超过间隔，下一次直接继续，没有并发补帧。等待可以停止。如果工程本身已有长期循环或触发流程，应选择 `single`，不再套第二层循环。采集/通信节点继续负责其触发方式。

## 资源和输出

- 流程、设备连接、模型、输出路径和页面来自完整工程目录，不要求另外的 SiteConfig。
- 声明资源通过 `resources.parameterBindings` 物化，按已有大小和哈希契约检查。`siteBindings` 不再要求外部覆盖，必填值应已经位于目标节点参数中。
- 文件参数使用已安装算子的 `xWidget=file`、`xFileMode=open/save` schema 元数据解析，不扫描所有字符串猜路径。相对路径基于工程根目录，显式绝对路径保留。
- 输入必须存在。文件输出不得覆盖工程配置、输入资源或 Runtime 数据。业务 SQLite 继续使用既有目标检查和明确初始化要求；普通文件写失败或 SQLite 故障不会自动改存其他目录。
- 检测参数在加载时准备、开始时快照。磁盘修改不热更新，停止后点击“重新加载”才能应用。缺资源、配置错误或运行错误不会显示为业务 OK/NG。
- 默认 Runtime 数据在 `%LOCALAPPDATA%\EmoMaster\operator-runtime`，日志位于其 `logs`。`--data-root` 或 `EMO_RUNTIME_DATA_DIR` 只改变内部数据位置，不改变业务输出路径。

## 启停和页面

运行端提供工程选择、重新加载、更新工程、开始、停止和共享的只读工程页面，没有流程编辑、算子工具箱或页面设计入口。`autoStart=true` 时，加载成功后自动开始；加载失败不执行。关闭过程中不会再自动开始。

一次开始只创建一个 Job/worker，编译一次。连续周期使用新的 `workflowRunId`，生命周期算子在会话内复用并在退出时统一释放。YOLO 沿用 worker 内现有模型缓存。页面继续按已有 `resultKey/resultOrdinal` 关联图像、数值和判定，切页或冻结不创建 Job。

故障结束会话，不自动重跑流程或重复外部写入。Start 结果不确定时只核对同一请求，禁止再次开始；退出并人工核实外部输出后再运行。停止沿用取消/强制收尾机制，实际 worker 所有权退还之前不能重新开始或换工程。

关闭主窗口会在后台关闭显示连接、停止 worker 并关闭 Runtime，不阻塞 GUI；失败保留窗口和错误供再次关闭。它不是后台常驻服务，关闭后不会继续检测，也没有新增登录、远程控制或开机自启安装功能。

生产模式关闭旧中间快照和正式输出的临时副本。页面结果/图像使用现有有界缓存，SQLite 诊断事件按 `runtime.eventRetentionPerJob` 滚动保留，最多额外暂存 99 条批量删除前事件，终态立即收敛。连续任务的诊断进程队列容量为 64，消费慢时施加背压，不丢弃业务输出或计数。普通 Designer 的完整持久历史不受此设置影响。正式业务结果和计数应由工程的文件、SQLite 或计数算子保存，不能把诊断窗口当作完整产品档案。

## 工程包与更新

直接复制完整工程目录仍可运行；需要收集外部模型/图片等文件输入时，用工程包交付：

```powershell
# 导出不修改原工程，输出目录必须位于工程之外
.\start_runtime.cmd "D:\engineering\ProductA" --export-package "D:\delivery"
# 离线安装/更新前先退出 Runtime；目的地是工程目录
& "D:\Apps\EmoMasterRuntime\EmoMasterRuntime.exe" "D:\site\ProductA" `
  --install-package "D:\delivery\runtime-<id>.vxpkg"
```

也可以用 EXE 的 `--export-package` 导出。工程包收集声明资源和算子 schema 声明的文件输入，将副本中的输入改为相对路径，保留设备值、输出路径、页面和生产策略；不自动打包业务输出、数据库或任意插件代码。包含凭据的工程包按私有资料处理，不上传公共 Release。

操作员窗口在停止且 worker 已释放后可“更新工程”。更新要求项目 ID 相同，不同项目放入新目录；验证路径、哈希、算子版本、流程和页面后再替换配置/输入文件，业务输出和无关文件不删除、不覆盖。成功后按新工程的 `autoStart` 策略执行。

替换前的配置/输入保存在工程旁的 `.runtime-backup-<id>`。普通写入异常会恢复已替换文件，但这不是断电原子的整目录事务。更新中进程中断或断电后的恢复尚未验证；若工程加载失败，应退出程序，保留备份并离线修复。不能用整个旧目录覆盖现场历史输出。

## 验证边界

合成工程测试覆盖源码入口、工程移动/不同 CWD、2.3 保存、连续周期、页面结果、启停、工程包和停机更新。冻结验证包括隔离 Python 搜索路径、DLL/Qt/CPU ONNX、spawn、图像像素与工程包导入，不代表实际模型性能或设备验收。

D1-D3 历史验证见 [源码验证](testing/standalone-runtime-2026-10-06.md)，本次构建和工程交付证据见 [冻结与交付验证](testing/runtime-delivery-2026-10-06.md)。真实工程/模型、相机/PLC、磁盘耗尽、目标负载长测、干净 Windows 机器、云端 Action 实跑、现场安装和公开发布分别保留为未完成验收项。
