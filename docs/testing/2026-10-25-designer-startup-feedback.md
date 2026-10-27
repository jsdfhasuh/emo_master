# Designer 启动计时与提前反馈

日期：2026-10-25。基准 HEAD：`9265547dbe61bc57542d3cf0ea143fcf455600fb`，在已有未提交修改上局部实现；本记录不表示提交、打包或现场部署。

## 范围

本轮仅实施用户确认的两项：真实启动分段计时，以及耗时准备前显示轻量启动窗口。不迁移磁盘或 Conda 环境，不清空历史数据，不延迟创建或重构编辑器内部模块，不修改工程格式。

- `scripts/dev.py run-designer --local` 自动打印 `[designer:start]`，无需额外开关。启动器向子进程传递本机单调时钟起点；`entry total` 包含子进程启动及进入 main 模块前的包导入时间。
- `startup-visible` 表示启动窗口第一次原生绘制已完成。窗口包含当前阶段、已用时间、不定进度条和取消入口。
- Runtime 组件导入、数据库准备、算子扫描、预览索引和在线工程恢复在一个后台线程完成；只有 GUI 线程修改 QWidget。
- `editor-imports` 和 `editor-ui` 仍在 GUI 线程执行，创建前更新状态。未将 QWidget 构建移动到后台线程。
- `ready` 在项目入口第一次绘制时打印，后续等待用户选择工程不算入启动完成时间。
- 取消、Esc 或关闭启动窗口会请求退出。已经进行中的 Runtime 初始化不会被强杀；它返回后正常释放客户端和 Runtime，不继续创建编辑器或打开工程。取消期间显示“正在结束当前初始化并退出”。
- 保留目录锁冲突提示、退出码 2、内嵌 Runtime 与远程连接的所有权和显示服务先于客户端退出的顺序。普通初始化失败报告失败阶段和错误，不留下永久等待窗口。
- `--check` 仍只检查真实模块导入，不创建 QApplication 或 Runtime，不启动任务。

## 验证

环境：Python 3.10、PySide2 5.15.2.1，Windows。所有执行使用临时目录或只读备份后的隔离副本，不重启用户当前 Designer，不连接真实相机或 PLC。

```powershell
python -m pytest -q tests/designer/test_startup_progress.py tests/runtime/test_runtime_startup_progress.py tests/designer/test_designer_bootstrap.py tests/designer/test_startup_directory_lock.py tests/designer/test_startup_project_entry.py tests/designer/test_startup_load_feedback.py tests/designer/test_operator_icon_integration.py tests/test_dev_launcher.py tests/test_windows_package_entry.py tests/runtime/test_sqlite_idle_connection.py --timeout=45
```

结果：**82 passed in 42.06s**。包含原生 Qt 控件测试及独立子进程的真实启动入口验证：重模块导入前已有首次绘制、后台准备期间 GUI 能响应取消、准备失败可退出、目录占用不抢锁、项目入口首次绘制先于用户选择、退出后 Runtime 锁已释放、远程连接不创建内嵌服务。

首轮失败证据：新增 Designer / Runtime 测试曾同名 `test_startup_progress.py`，pytest 收集时报 `import file mismatch`，当次未执行测试。将 Runtime 测试更名为 `test_runtime_startup_progress.py` 后修复；随后第一轮针对性测试为 40 passed，扩展回归为上述 82 passed。未删除断言或跳过失败用例。

- Ruff：本轮启动代码和新增测试检查通过。
- mypy：`--follow-imports=silent` 检查 `main.py`、`startup.py`、`ui/startup_dialog.py`、`scripts/dev.py` 四个源码文件通过，不是全仓库 mypy 结论。
- `python scripts/dev.py run-designer --local --check` 通过。
- Windows 原生 Qt，100% / 150% 缩放：500×230 / 750×345 截图检查通过，文字与控件未重叠；后台人为等待约 1 秒时，GUI 计时持续变化，首次绘制发生在重模块导入之前。该等待仅验证界面响应，不用于宣称启动加速。

## 本机测量

此前空数据、热缓存基线约 2.5 秒，不能解释或否定用户报告的十几秒冷启动。当前源码与本地 Runtime 数据位于 D 盘机械硬盘，Python 环境位于 C 盘 NVMe；尚未捕获原始慢启动各段时间，不能将全部额外等待归因于磁盘。

Windows 原生 Qt、当前在线工程的隔离副本、未注入等待的启动结果：

| 标记 / 阶段 | 耗时 |
| --- | ---: |
| `startup-visible` | 启动起点后 0.936 秒 |
| `runtime-imports` | 0.560 秒 |
| `runtime-database` | 0.070 秒 |
| `runtime-plugins` | 0.374 秒 |
| `runtime-online-configuration` | 0.888 秒 |
| `editor-imports` | 0.110 秒 |
| `editor-ui` | 0.300 秒 |
| `ready` | 启动起点后 3.656 秒 |

副本恢复了 6 个工作流和 3 个资源，确认无设备定义；退出后锁已释放。原数据库以只读方式备份，资源上传文件复制到临时目录，副本中的原实例资源所有权记录被移除，避免对正在使用的原资源执行回收。副本保留在线工程内容，未执行工作流；这不是原目录冷启动或真实设备验收。

本轮改善是提前可见反馈和可定位的阶段耗时，不承诺所有环境下完整启动都缩短至一秒。没有执行全套测试、冻结 EXE 自测、打包、部署或真实设备验收。当前已运行的源码进程不会热更新本次启动逻辑，下次正常启动生效。
