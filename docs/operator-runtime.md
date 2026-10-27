# 独立操作员 Runtime

本页描述独立运行端；2026-10-25 增加起始页、本机下次启动目录设置及真实 JSONL 日志工具，2026-10-26 修正日志来源标记、启动诊断选区与日志横向阅读位置保持，保留 2.7 工程的显式重新加载、切换和停机更新。以下最新行为以源码为准，已有源码进程需重启、旧 EXE 需重新构建才能包含这些修改；本轮没有打包或发布，实际设备和生产模型验收仍需单独完成。

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

`EMO_MASTER_PYTHON` 可以指定已有解释器，启动脚本不会安装依赖。首次启动可选择 `.emoproj` 或 `project.json`。对于已建立在线配置的工程，不传路径启动会恢复上次提交的在线版本，不把磁盘草稿隐式写回 Runtime；显式传入工程路径、选择工程或点击“重新加载”才会应用所选文件。

`--check` 始终检查指定的当前磁盘文件，不使用旧在线快照代替；它不创建 Runtime、不连接设备、不写入在线配置，也不占用正在运行实例的数据目录。校验成功不等于工程已经应用或开始检测。

独立入口为 `scripts/run_operator.py`，无需导入 Designer；现有 Windows 产品入口增加 `--run-project` 分支。旧 `--runtime`、`--operator-view` 仍是受限测试宿主和测试查看器，没有改为生产模式。

冻结 EXE 同样以 `run_operator.py` 为入口，集中打包仓的目标为 `emo-master-runtime`。已在隔离构建环境生成并验证本地产物，未修改基础 Conda 环境。Actions 接入和验证命令见 [Runtime 构建与交付](operator-runtime-build.md)；源码启动器仍需要 Python，不能当作免安装 Runtime。

## 工程内配置

新工程必须使用完整的 2.7 格式，创建方式见[工程格式规范](project-json-spec.md)。旧 2.1/2.2 工程仍可按兼容路径加载，默认单次、不自动开始，不隐式改写原件。下面的脚本可修改现有工程的生产策略；对旧格式只执行它声明的生产字段迁移，不把脚本当作 2.7 新建模板：

```powershell
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" `
  .\scripts\configure_runtime_project.py "D:\现场工程\ProductA" `
  --mode continuous --auto-start --cycle-interval-ms 100
```

脚本校验设置后备份 `project.json.bak` 并原子保存。其他工程参数保留。之后可以使用 `--no-auto-start` 或 `--mode single` 修改策略。也可以直接编辑工程中的配置：

```json
{
  "schemaVersion": "2.7",
  "production": {
    "autoStart": true,
    "mode": "continuous",
    "cycleIntervalMs": 100,
    "inputs": {}
  }
}
```

上面只是生产字段示意，不是完整工程。2.7 还必须保留 `presentation`、`resources`、`devices.schemaVersion="1.0"` 等结构，无设备时使用空 `definitions`，不添加旧顶层 `communication`。本次不增加生产策略编辑面板。

`continuous` 表示反复执行一次入口工作流，串行运行，最小间隔按周期开始到下一周期开始计算。如果一次执行耗时超过间隔，下一次直接继续，没有并发补帧。等待可以停止。如果工程本身已有长期循环或触发流程，应选择 `single`，不再套第二层循环。采集/通信节点继续负责其触发方式。

## 资源和输出

- 流程、设备连接、模型、输出路径和页面来自完整工程目录，不要求另外的 SiteConfig。
- 声明资源通过 `resources.parameterBindings` 物化，按已有大小和哈希契约检查。`siteBindings` 不再要求外部覆盖，必填值应已经位于目标节点参数中。
- 文件参数使用已安装算子的 `xWidget=file`、`xFileMode=open/save` schema 元数据解析，不扫描所有字符串猜路径。相对路径基于工程根目录，显式绝对路径保留。
- 输入必须存在。文件输出不得覆盖工程配置、输入资源或 Runtime 数据。业务 SQLite 继续使用既有目标检查和明确初始化要求；普通文件写失败或 SQLite 故障不会自动改存其他目录。
- 检测参数在加载时准备、开始时快照。磁盘修改不热更新，停止后点击“重新加载”才能应用。缺资源、配置错误或运行错误不会显示为业务 OK/NG。
- 2.7 的重新加载和工程选择复用已有配置写入器，校验成功后提交在线版本，状态栏显示实际配置版本。同一路径的文件输入会重新准备，不因参数字符串未变继续使用旧图片或模型。相对路径始终以所选工程目录为基准。
- 正常校验失败不会替换原在线工程；原目录归属和开始入口仍保留，可以修正后重试。运行中换工程或更新仍需先正常停止，不自动停止或重放原任务。
- 默认 Runtime 数据在 `%LOCALAPPDATA%\EmoMaster\operator-runtime`，日志位于其 `logs`。`--data-root`、`EMO_RUNTIME_DATA_DIR` 和本机设置只改变内部数据位置，不改变业务输出路径。

## 起始页与本机目录设置

没有成功加载工程时显示起始页，可选择工程、查看最近成功工程/真实日志，并直接编辑目录设置。较窄窗口上下排列。加载工程后，顶部“Runtime 设置”“查看日志”仍可使用；进入工具页或返回不会重建业务页、关闭 DisplaySession 或改变正在运行的 Job，顶部停止入口始终保留。设置草稿、日志级别、选中记录和滚动位置在往返中保留。

独立操作员入口每次启动按以下顺序解析一次：

```text
--data-root / 显式 OperatorWindow(dataRoot=...)
  > EMO_RUNTIME_DATA_DIR
  > 本机保存的默认目录
  > 系统默认目录
```

系统默认在 Windows 为 `%LOCALAPPDATA%\EmoMaster\operator-runtime`；没有 `LOCALAPPDATA` 的平台使用 `~/.local/share/EmoMaster/operator-runtime`。本机默认使用 `QSettings("EmoMaster", "Runtime")` 的 `storage/defaultDataRoot`，与已有 `projectPath` 一起存于应用设置，不放进数据根，也不写入 `project.json`。本机输入要求绝对目录，支持中文、空格、Windows 盘符与 UNC；CLI 原有相对目录仍按启动 CWD 解析。

设置区分“当前有效目录/来源”和“下次启动默认目录”。展开可查看真实实例的 `runtime.sqlite3`、`logs`、`jobs`、`online-projects` 和 `display` 路径。`online-projects` 是托管工程根，不是当前打开的工程目录，也不是业务留档位置。没有权威的单一业务留档目录时，界面明确说明由工程配置，不用内部根或 `jobs` 冒充。

“保存下次设置”同步本机设置后检查写入结果；失败不会显示成功。CLI 或环境变量仍覆盖本机默认时，会说明“已保存但仍被覆盖”。“使用系统默认”先清空草稿，保存后移除本机覆盖键，不写死当前用户的系统路径。路径指向文件、相对路径或普通格式错误会在保存时反馈；尚未存在的目录由下次初始化创建，保存不会创建数据库。

保存不停止检测、不重新加载工程、不重建 Runtime、不切换 writer，也不修改环境变量或自动搬迁文件。下次关闭后重新启动才重新解析；换根后旧在线版本、数据库和历史日志留在旧根，不会自动带走，工程记忆和显式加载仍遵守原规则。

`EMO_RUNTIME_DB_PATH`、`EMO_MASTER_RUNTIME_DB_PATH`、`EMO_RUNTIME_LOG_DIR` 不分别覆盖该独立入口显式传入的数据库/日志路径；存在时会在目录诊断中标明“不用于当前独立入口”。这与 Designer 内嵌/外部底层服务的默认规则不同，见[部署指南](deployment-guide.md#3-数据目录备份与回退)。`--check` 使用相同四级规则计算受保护路径，但不创建 Runtime、数据根、日志目录或 Job，也不改本机设置。

初始化失败仍显示本次**目标**目录、来源、失败阶段/原因，设置、日志和关闭仍可用；没有成功实例时不会标成“正在使用”。本窗口保留最多 64 条启动诊断，未落盘的明确标记“未写入日志文件 / writer 未确认写入”。外部修正权限或文件/目录冲突后可点“重试本次初始化”，只重试同一启动目标；新保存目录仍须下次启动生效，没有静默回退或后台无限重试。

## 查看真实日志

“查看日志”读取现有 `RuntimeJsonlLogWriter` 的 `runtime-*.jsonl`，不是网页预览中的 `runtime.log`。默认跟随当前 writer 的活动文件；暂无本次文件时明确提示，可从文件列表选择历史文件。文件列表、日志状态和起始页摘要按真实 `activePath` 区分“本次活动文件”和“历史文件”；手动选定文件另外标明“手动固定，不跟随轮转”。手选当前文件不会把它误标成历史，历史文件也可能包含本次运行轮转前的记录，不能仅凭文件选择方式判定是否属于本次故障；应结合时间及 Job 等身份查看，不根据当前工程给其他运行的 ID 错配名称。

- 列表显示时间、级别、来源、消息，可筛选全部/错误/警告/信息/调试；空筛选可“恢复全部”。起始页“查看全部日志”会恢复全部级别。
- 选中记录可查看完整时间、真实错误码、原始消息、工程/流程/节点/Job 身份、payload；原始记录按需展开，所有文本均只读并脱敏。未知错误保留原始原因，不猜测成模型缺失，不触发下载、执行或重试。
- 初次只取文件尾最多 2 MiB / 2000 条，视图最多 10000 条；显示“最近记录”，不是完整历史。可见时约 1000 ms 刷新，初始页与日志页共用一个后台读取任务；业务页没有日志展示或窗口隐藏时停止新读取。
- 追加按文件/字节偏移读取，半行等完整换行后再解析；重复真实错误不会按消息去重。默认模式在轮转后跟随新活动文件；手动选定的文件保持固定，即使它原来是活动文件也不会自动切回。无效行有跳过计数/原因，读取失败可刷新重试。关闭等待已发出的有界读取收尾。
- 自动刷新保留选中记录及列表横向滚动位置；取消“跟随最新记录”后，还按可见记录身份保留纵向阅读位置。启动诊断内容不变或正常追加时，保留文本选区、光标及滚动位置，便于阅读和复制。更早的记录请查看原日志文件，界面不会删除日志或改变既有轮转/保留策略。
- “打开当前日志文件夹”始终使用本次 writer 的实际 `directory`，不是尚未生效的下次设置；初始化失败使用本次目标 logs 路径。通过系统本地 URL 接口请求打开，不执行 shell。目录不存在或系统拒绝会显示实际路径和失败原因，不编造文件。

独立端仅补充目录解析、Runtime 就绪/初始化失败、工程加载失败这几个允许的启动事件，直接交给原 writer，不伪造 Job 或写入 Job 生命周期表。writer 不可用时仍展示真实 `enabled` / `failureMessage` 及本窗口诊断；“已提交 writer”不等于已验证落盘，以日志文件为准。URL 用户信息和密码/token 等敏感字段在新视图和新增启动事件中脱敏；不把完整设备配置加入启动日志，也不改写原日志内容。

本轮测试与原生 Qt 证据见[独立起始页/设置/日志验证](testing/2026-10-25-runtime-startup-settings-and-logs.md)。源码和原生 QWidget 检查不代表旧 EXE 已升级，也不代表设备、生产模型或现场验收。

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

2.7 窗口更新会同时提交 Runtime 在线配置；在线校验或提交前失败时，沿用安装器备份恢复已替换的工程文件。已明确提交的配置不因后续发布错误回滚成旧磁盘版本，也不会自动重放写入。命令行离线安装只更新工程目录；之后显式传入该工程路径启动，或在窗口中点击“重新加载”，才会应用到已有在线实例。

替换前的配置/输入保存在工程旁的 `.runtime-backup-<id>`。普通写入异常会恢复已替换文件，但这不是断电原子的整目录事务。更新中进程中断或断电后的恢复尚未验证；若工程加载失败，应退出程序，保留备份并离线修复。不能用整个旧目录覆盖现场历史输出。

## 验证边界

合成工程测试覆盖源码入口、工程移动/不同 CWD、2.3 保存、连续周期、页面结果、启停、工程包和停机更新。冻结验证包括隔离 Python 搜索路径、DLL/Qt/CPU ONNX、spawn、图像像素与工程包导入，不代表实际模型性能或设备验收。

D1-D3 历史验证见 [源码验证](testing/standalone-runtime-2026-10-06.md)，本次构建和工程交付证据见 [冻结与交付验证](testing/runtime-delivery-2026-10-06.md)。真实工程/模型、相机/PLC、磁盘耗尽、目标负载长测、干净 Windows 机器、云端 Action 实跑、现场安装和公开发布分别保留为未完成验收项。
