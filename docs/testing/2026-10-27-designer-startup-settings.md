# Designer 启动设置与多实例选择验证记录

## 基线与边界

- 日期：2026-10-27；应用 HEAD：`331c4d3586cb5cd5141871fbe3da353bca9a56b6`，加工作区未提交修改。本次没有提交或推送。
- 工作区开始时已有 Runtime 预览、日志和 Designer 刷新等修改；本次保留这些修改。执行期间也观察到其他模块的新修改，没有回退或纳入本轮修复。
- Python：本机 Conda `emo_master` 环境的 Python 3.10；PySide2 `5.15.2.1`；gRPC `1.78.0`。
- 核对冻结入口为 `apps/windows_entry.py`，默认复用 `runDesigner()`。中央打包配置核对于 `558823d5706e9aef5af62c0299c5e455fb8f7c51`，本次没有修改打包仓、构建 EXE 或运行安装/卸载。
- 不接触真实相机、PLC、现场工程、凭据或运行中的用户窗口。测试只使用临时目录、临时 INI、临时本机 gRPC 服务及已有模拟设备夹具。

## 本轮行为

- Designer 每次先显示启动设置；用户明确启动后才初始化 Runtime，并继续现有进度窗口与项目入口。选择阶段不创建运行数据。
- 本机模式支持编辑、最近目录、浏览和恢复系统默认目录。显式选目录后，数据库、任务目录、日志统一使用该目录下的 `emo_master.db`、`jobs/`、`logs/`。
- 未手动选目录时保留并显示旧的数据库/日志环境覆盖。环境用于预填，其次使用本机上次成功配置，最后使用原系统默认。`--local` 清理继承覆盖并预填仓库隔离目录，仍允许界面覆盖。
- 保留现有目录锁；占用、目录错误或连接失败返回保留输入的设置界面，允许重选。不会删除锁或数据库、抢占目录、停止其他进程或自动换目录重试。
- 外部模式复用只读候选检查，读取实例信息、已有在线工程快照和设备类型，每次 RPC 为 5 秒超时。取消在步骤边界生效；失败/取消关闭候选连接，不自动写配置、加载工程或开始任务。
- 多个 Designer 选不同本机目录时，运行数据独立；连接同一个外部 Runtime 时共享在线配置和任务。不承诺工程文件、业务输出和真实设备也已隔离。
- 成功完成初始化及编辑器创建、即将进入项目入口时，才保存 `QSettings("emo_master", "designer")` 的 `startup/mode`、对应目录/地址及各最多 10 条最近记录。失败和取消不更新；保存失败在项目入口显示非阻断通知。
- `EMO_DESIGNER_SETTINGS_PATH` 仅用于指定本机偏好的 INI 路径，启动设置和后续 Designer 共用它；冻结验收借此避免读写用户注册表偏好，不跳过启动界面。
- 现有连接状态显示实际本机目录或外部地址。长路径有工具提示，启动路径详情为有界只读滚动区，避免报错出现时压缩和遮挡文字。
- 没有工程 schema、RPC 协议、服务端监听或认证变化；没有迁移/复制历史数据，也未修改独立操作员的存储设置。

## 测试层次与结果

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 审查修复后核心回归 | 187 passed，92.12 秒 | 相同核心集合，补窗口包装对象/API 回归及真实 RPC 进行中取消的两种结局；无跳过项 |
| 初次实现最终核心回归 | 184 passed，107.03 秒 | 上述核心集合，另补同一最近目录显式选择、浏览/默认按钮、文件误填目录；无跳过项 |
| 核心回归 E | 181 passed，91.21 秒 | 配置、Qt 启动、真实源码双进程、外部连接、连接切换、项目入口、加载反馈、启动器、Windows 入口及冻结脚本单元测试 |
| 扩展回归 D | 189 passed，1 failed，125.33 秒 | 额外包括设备作业启动取消模块；失败保留如下，不记为全通过 |
| 扩展失败单独复跑 | 1 failed，3.92 秒 | 同一个预览统计断言独立可复现，未修订该断言或对应预览实现 |
| Ruff | PASS | 本轮相关源码、测试和脚本；不是全仓检查 |
| Mypy | PASS，5 个源码文件 | 启动选项、初始化、共同入口、设置界面、共享连接辅助函数；使用 `--follow-imports=silent` |
| 源码启动 `--local --check` | PASS | 正确源码、PySide2、gRPC 导入；不创建窗口、Runtime 或 Job，不当作 GUI 验收 |
| 原生 Windows Qt 截图 | PASS | 本机 640/480 宽度、外部模式及占用提示；隐藏的自有测试 QWidget，不是用户人工操作验收 |
| 冻结验收脚本 | 源码与单元测试通过；真实 EXE 未运行 | 适配选择、双开、占用后重选、独立关闭及关闭后目录重用，未用源码冒充冻结程序 |

核心回归的模块集合为：

```text
tests/designer/test_startup_settings.py
tests/designer/test_startup_progress.py
tests/designer/test_startup_directory_lock.py
tests/designer/test_startup_runtime_connection.py
tests/designer/test_runtime_connection_ui.py
tests/designer/test_operator_icon_integration.py
tests/designer/test_startup_project_entry.py
tests/designer/test_startup_load_feedback.py
tests/designer/test_designer_bootstrap.py
tests/designer/test_runtime_validation_feedback.py
tests/runtime/test_runtime_startup_progress.py
tests/test_dev_launcher.py
tests/test_windows_package_entry.py
tests/test_frozen_runtime_validation.py
```

命令使用 `C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe -m pytest -q <上述模块>`，每轮使用独立临时 `--basetemp`。最终一轮目录为 `C:/Users/jsdfhasuh/AppData/Local/Temp/emo-startup-20261027-final`。扩展 D 另外运行完整的 `tests/runtime/test_startup_cancellation.py`。

重要正常路径和直接反例包括：中文/空格目录、环境预填和手动覆盖、最近记录去重与上限、选择同一个最近目录也取消旧数据库覆盖、浏览/默认按钮、文件误填为目录、选择取消、初始化取消、写偏好失败、失败保留输入且不保存、外部失败后明确切换本机、真实双进程独立关闭、锁释放后重用、真实本机 RPC 只读连接、拒绝、5 秒超时及步骤间取消。

## 保留的失败与修正证据

- 首轮 A：30 passed / 3 failed。旧外部启动夹具没有模拟新增只读检查；入口子进程夹具将 `QSettings` 类替换为函数后丢失 `NoError` 常量。修正夹具及对应构造调用后，真实入口测试通过。该次子进程还出现 `3221225477` 原生退出码；前置 Python 异常已定位，但未单独归因这次原生退出，不能用后续绿色覆盖原记录。
- 第二轮 B：53 passed / 1 failed。超时断言把错误代码当成了人类可读消息；改为核对 `RuntimeClientError.code` 中的 `DEADLINE_EXCEEDED`，仍验证真实 5 秒 RPC 超时和候选关闭。
- 初始核心回归 C：88 passed。之后扩大测试与补充边界，而不是据此声称全仓通过。
- 首轮 Mypy 指出 6 处新代码类型错误，涉及保存值的字典类型及用路径字典展开构造参数；通过明确类型和显式参数修正，复查 5 个文件无错误。
- 离屏 Qt 截图的字体未绘制，不能用于视觉验收。原生 `native-a` 自动几何检查虽通过，图像审阅发现报错状态长路径详情被挤压；改为只读滚动区域后，`native-b`、最终 `native-d` 图像复核正常。早期截图保留，没有删除失败证据。
- 扩展失败为 `tests/runtime/test_startup_cancellation.py::testUncancelledSpawnedDeviceActionFailureStaysFailed` 第 250 行。该测试直接构造 RuntimeService，不经过本轮启动选择；实际 `job.failed.previewSnapshots` 比旧精确字典断言多 `inspectionFailed: 0`，设备失败状态、错误代码和诊断均匹配。
- `inspectionFailed` 来自本轮开始时已有的 `runtime/preview/async_writer.py` 未提交修改。本轮没有修改该实现、设备作业测试或降低其断言。此项是扩展回归中保留的断言不一致，不是本轮启动设置已修复的问题，也不据此宣称设备路径已全面验收。

## 原生截图证据

```powershell
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' `
  tests/ui/designer/native_startup_settings_probe.py `
  --output build/startup-settings-20261027-native-d
```

输出为本机 `build/startup-settings-20261027-native-d/`，包括 `local-640.png`、`local-480.png`、`remote-640.png`、`error-640.png` 和 `result.json`。平台为 `windows`，字体为 `Microsoft YaHei UI`；每张标题有 842 个实际不透明深色文字像素。检查按钮边界、文字宽度及未创建 Runtime/目录、未保存偏好。图像已通过本机审阅。

`build/` 是本机证据输出，不是安装包或公开交付文件。此脚本重跑请使用新输出目录，以保留既有证据。

## 审查后修复：冻结窗口 API 与真实 RPC 取消

- 仍基于 `331c4d3586cb5cd5141871fbe3da353bca9a56b6` 加工作区未提交修改；本轮只修改冻结验收脚本、两份相关测试和本记录，未修改 Designer/Runtime 的业务行为。
- 确认原 `window_for()` 返回 `Desktop.windows()` 的窗口包装对象，但调用者使用仅由 `WindowSpecification` 提供的 `child_window()`。修复保留进程、标题及可见性筛选，再用匹配窗口的句柄调用 `desktop.window(handle=...)`，不改变双开/重选场景。
- 原宽松 `MagicMock` 未能识别接口错误。现在使用没有 `child_window` 的枚举结果和受 `spec_set` 约束的子控件替身，实际经过 `window_for()` 再操作设置；另验证未找到可见窗口时返回 `None`，不创建窗口查询对象。
- 修复前先运行增强后的回归：`1 failed / 33 passed`，15.76 秒，失败正是原脚本第 182 行的 `AttributeError: ... has no attribute 'child_window'`。这是本轮捕获的真实脚本接口缺陷，不以之前通过的宽松 mock 测试替代其证据。
- 修复后针对性回归：`34 passed`，15.35 秒。包含 `tests/test_frozen_runtime_validation.py` 与 `tests/designer/test_startup_runtime_connection.py`。
- 新增真实本机 gRPC 取消测试，经过 `runDesigner()` 和原初始化进度窗口，在 `DeviceNetworkCommand(info)` 尚未返回时点击取消，分别覆盖服务随后返回与保持等待直到原 5 秒截止时间。两者均验证无后续 RPC、无本机 Runtime/目录、无编辑器、无偏好覆盖，启动线程已结束，候选客户端关闭且真实 channel 拒绝后续调用；服务端和线程池由测试关闭。
- 完整核心集合复跑：`187 passed`，92.12 秒，无跳过项；临时目录为 `C:/Users/jsdfhasuh/AppData/Local/Temp/emo-startup-fix-20261027-core`。修复前/后针对性目录分别为同级的 `emo-startup-fix-20261027-red`、`emo-startup-fix-20261027-green`。三份改动的 Python 文件 Ruff 通过，相关 diff 空白检查通过。
- 另在临时 `emo-startup-uia-api-20261027` 目录安装 pywinauto `0.6.9` 及其依赖，使用真实 `Desktop.window()`、`WindowSpecification` 与嵌套 `child_window()` 做 API 契约校验，通过。仅枚举结果使用替身，没有枚举或操作原生窗口；不把此结果记为原生 UI 或 EXE 验收。未修改项目依赖或 Conda 环境安装内容。
- 本次没有重跑历史扩展失败 `testUncancelledSpawnedDeviceActionFailureStaysFailed`，其 `inspectionFailed: 0` 断言不一致及既有原因记录继续保留，不计入此次通过结果。

## 未执行

没有运行全仓 pytest、真实 EXE、安装/卸载、人工完整编辑流程、真实相机/PLC、现场部署或生产验收。已打开的源码进程不会自动加载修改，旧 EXE 需要之后重新构建；本轮不要求关闭或重启用户当前任务。
