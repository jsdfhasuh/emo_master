# S1、S2 设备链路与 S5 本机文件/导出实施记录

实施日期：2026-11-03。依据外部实施计划 v3.4；固定源码基线与当前未提交实现分支均由主记录说明。本文记录本分工实际执行的源码和原生 Qt 验证，不代表人工 UI、真实设备或部署验收。

## 修改

- S1：`devices/manager.py` 从本次 `DeviceOperationContext` 写入真实 Job、workflow、node、planRevision、slot；异常有 `outcome` 才传入失败 payload。`jsonl_writer.py` 的设备完成事件保留已紧凑 payload；其他完成事件仍剔除大 outputs，同时保留运行关联和 deviceBindings。
- S2：连接最终失败和普通驱动真实成功由 `DeviceLifecycle.connect()` 记录；已经连接直接返回不重复发布。自动连接调度不再重复记录。TCP 同步 bind/thread 启动失败保留本机通信记录，由 lifecycle 发布一次；真实线程启动成功、后台失败由通信管理器发布 `device.connect.completed/failed`，失败为 ERROR，连接 action 为空。原取消 INFO、故障动作隔离、摘要和恢复语义保留。
- S2：设备直接文件与聚合发布事件显式使用 `originApp=runtime`、纯映射 component 和原 scope 的 runtimeInstanceId；未补造 workflowRunId/nodeRunId。
- S5：纯 `core/log_export.py` 顺序消费迭代输入，返回实际条数；JSON 在 entries 写完后追加 entryCount，TXT 尾部实际计数，JSONL 每行仅一条记录。取消/失败清理临时文件，不替换原目的文件。Designer 原函数入口薄转发，保留 `os` 测试兼容入口。
- S5：文件历史开始时固定文件集合及各文件结束字节；独立于 tail/视图缓存，顺序分块读取和脱敏；只包含截止位置内完整行。文件读取失败、变短或无效记录明确报告失败。旧 JSONL 仅阅读时补应用/模块，实例为空，eventId 为空。
- S5：Runtime 文件列表只包含 runtime/runtime_app 两前缀；服务尚未产生日志时使用同次应用文件。表格增加应用/模块及实例详情。当前显示导出保持同步快照；完整所选文件/全部本机文件范围使用局部后台线程任务和取消 Event，不使用业务 busy。导出前固定显示数据或文件范围，之后追加/轮转不加入本次导出。
- S5：设备共享控件只发运行日志请求、缓存导出请求；拥有者分别打开选定设备日志和导出实际已返回缓存。Runtime 设备入口打开本机日志并设置 deviceId 筛选；设备过滤保留指定槽的冻结绑定语义。

## 实际验证命令与结果

Python：`D:\Conda\envs\emo_master\python.exe`，Python 3.10 / PySide2。全部使用临时目录、mock 驱动或 loopback 测试服务，未访问真实相机/PLC。

```powershell
& D:\Conda\envs\emo_master\python.exe -m pytest tests/runtime/test_jsonl_logging.py tests/runtime/test_device_operational_logs.py -q
```

首次 18 passed / 5 failed；当时共享分支 S2 正在修改，EventStore 已传新 metadata 而 SqliteStore 接口尚未落入，失败原因为 `unexpected keyword argument originApp`，连带 Runtime 全局事件缺失。S1 新增 projection/context/outcome 断言通过；未改弱断言。该阶段失败由后续完整重跑覆盖，另主记录 `s2-s4-initial.txt` 保留集成失败证据。

```powershell
& D:\Conda\envs\emo_master\python.exe -m pytest tests/runtime/test_device_operational_logs.py tests/runtime/test_device_connection_recovery.py tests/runtime/test_tcp_device_integration.py -q
```

29 passed；覆盖设备错误汇总/恢复、取消、有限重试，以及现有 TCP mock Job 集成。

```powershell
& D:\Conda\envs\emo_master\python.exe -m pytest tests/runtime/test_jsonl_logging.py tests/runtime/test_device_operational_logs.py tests/runtime/test_device_connection_recovery.py tests/runtime/test_tcp_device_integration.py tests/designer/test_log_export.py tests/runtime/test_operator_runtime_logs.py tests/ui/operator_view/test_runtime_log_view.py -q
```

- `devices-export-test-01.txt`：66 passed / 17 failed。主要是共享分支日志对话框功能正在落入，`_historySourceChanged` 当时尚未定义，导致 Designer 初始化和同 fixture TCP 后续链路失败；另 Runtime 旧测试仍取服务 writer 的启动文件。
- `devices-export-test-02.txt`：设备与 Designer 导出子集 25 passed。
- `devices-export-test-03.txt`：90 passed，61.46s。完整重跑通过；启动文件断言改为核对实际 `runtime_app-` 来源，没有删除失败证据。

```powershell
& D:\Conda\envs\emo_master\python.exe -m pytest tests/ui/operator_view/test_runtime_log_view.py tests/designer/test_log_export.py -q
```

`devices-export-ui-01.txt`：42 passed，22.83s。包括新增正常 Qt 控件操作：选定文件完整导出超过 tail 缓存（2300 条）、文件对话框打开后追加的记录不进入本次范围、取消按钮中止后台导出且原目标文件保留、缓存导出仅含已返回两条、设备历史请求与缓存导出为独立操作。

```powershell
& D:\Conda\envs\emo_master\python.exe -m ruff check src/emo_master/apps/runtime/devices/manager.py src/emo_master/apps/runtime/devices/lifecycle.py src/emo_master/apps/runtime/devices/diagnostics.py src/emo_master/apps/runtime/communication/manager.py src/emo_master/core/log_export.py src/emo_master/apps/designer/services/log_export.py src/emo_master/apps/operator_runtime/log_reader.py src/emo_master/apps/operator_runtime/log_view.py src/emo_master/ui/communication_panel.py src/emo_master/ui/device_management.py tests/runtime/test_device_operational_logs.py tests/runtime/test_jsonl_logging.py tests/designer/test_log_export.py tests/ui/operator_view/test_runtime_log_view.py
```

All checks passed。`git diff --check` 无空白错误（Git 正常给出已有 CRLF 转换提示）。

```powershell
& D:\Conda\envs\emo_master\python.exe -m pytest tests/runtime/test_device_operational_logs.py tests/runtime/test_device_feedback_closure.py tests/ui/operator_view/test_runtime_log_view.py -q
```

最后显式传递设备聚合 metadata 后，`devices-export-final.txt`：57 passed，46.96s；覆盖真实上下文、outcome、连接发布、现有冻结归属/回执/错误反馈及全部本机 Qt 日志视图。

```powershell
& D:\Conda\envs\emo_master\python.exe -m pytest tests/runtime/test_device_lifecycle.py tests/runtime/test_device_executor.py tests/runtime/test_cancellable_device_wait.py -q
```

`devices-lifecycle-regression.txt`：30 passed，14.38s；直接受连接发布改动影响的生命周期、FIFO 执行器、正常取消出口回归通过。

## 验证边界

以上 UI 层是实际创建 PySide2 QWidget、处理 Qt 事件并使用 QTest 点击的原生 Qt 自动化；未将其称作人工 UI 走查。设备集成为模拟设备和 loopback 通信，不包含真实硬件验收。没有修改现场工程、凭据或网络地址，也没有提交、推送、合并或发布。

## 补充：独立 Web SDK 对实际源码 Runtime 的日志查询

在既有 `web_panel/tests/test_product_runtime.py` 两个 opt-in `actual_runtime` 正常场景中追加直接调用 `GrpcRuntimeClient.query_runtime_logs()` 的断言，保留原先历史图片、启动关联、停止和不重放等正确断言。第一个场景在没有 Job 时查询跨 Job 日志，核对回复实例、十进制游标且查询未产生 Job；第二个场景实际启动既有无设备 2.9 临时工程，产生 capture 后按 Job/component 查询 execution 日志，核对 global eventId 排序及上界、独立 sequence、源时间、Runtime/工程/工作流身份及应用/模块 metadata，再验证未选 Job 查询同样包含该任务日志。

```powershell
$env:EMO_WEB_PRODUCT_RUNTIME='1'
$env:EMO_WEB_PRODUCT_PYTHON='D:\Conda\envs\emo_master\python.exe'
$env:EMO_WEB_PRODUCT_EVIDENCE_DIR=(Join-Path (Get-Location) 'build\logging-owning-runtime-evidence')
$env:PYTHONPATH='web_panel/backend'
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_product_runtime.py -q
```

结果：`web-owning-runtime.txt`，**4 passed，6.87s**，没有 skip。Web 执行器来自独立 `build/logging-web-venv`、安装 SDK 1.4.0；实际 Runtime 为独立 Python 3.10 子进程并自行设置 `src`。合成数据的 Runtime 应用/服务 JSONL、stdout 和 Web 查询 observation JSON 保存在 `build/logging-owning-runtime-evidence/<test_actual_runtime...>/`。该验证实际运行软件 Runtime 与 gRPC，未连接设备；属于源码 Runtime/独立 SDK 集成，未记作浏览器或人工 UI 验收。新增测试文件 Ruff 检查通过。

环境复核发现上述第一轮 SDK 使用 editable 安装，其 `.pth` 暴露整个 `src`。因此将该测试 venv 中的 SDK 改装本轮合同产物的正式 wheel，复跑并保留第一轮结果：

```powershell
& build/logging-web-venv/Scripts/python.exe -m pip install --no-deps --force-reinstall build/logging-consolidation-panel-contract-sdk-1.4.0/python/emo_panel_protocol-1.4.0-py3-none-any.whl
# 保持上述 Runtime、PYTHONPATH、证据目录设置
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_product_runtime.py -q
& build/logging-web-venv/Scripts/python.exe -m unittest discover -s web_panel/tests -p test_product_runtime.py -v
```

- `web-owning-runtime-wheel.txt`：4 passed，6.83s。
- `web-owning-runtime-wheel-unittest.txt`：Ran 4 tests，6.815s，OK，没有 skip。直接 unittest 避免仓库 pytest pythonpath 配置影响独立安装验证；最终 observation 中记录 `protocolPath=build/logging-web-venv/lib/site-packages/emo_panel_protocol/__init__.py`，版本 1.4.0。
- 独立执行器探测 `find_spec('PySide2')` 与 `find_spec('emo_master')` 均为 False；gateway 通过正式 SDK/gRPC 连接独立 owning Runtime 子进程。
