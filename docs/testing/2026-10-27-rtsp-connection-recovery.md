# RTSP 连接失败与断流自动重连

## 范围与基线

- 日期：2026-10-27。
- 本轮基线 SHA：`970107639a23a665c10e840cbbc1fd7f32e61c6d`。
- 相比上一轮 `1ca69d3697c54a300237a81eca55fe15cf05bdf6`，已核对新增提交仅重命名启动设置测试并增加交付记录，与连接逻辑无关。
- 保留上一轮未提交的连接测试反馈修复。本轮不提交、推送、部署或修改现场工程，不访问真实相机、PLC。
- 用户反馈：连接失败后不会自动重连。本轮覆盖 RTSP 的首次建连失败、连接测试和采集请求内的普通断流。

## 原因与修复

1. RTSP 未声明重试策略，也未提供既有连接恢复机制要求的 `connectionRetryCodes` / `resetConnection`，因此首次失败直接退出。
2. `E_CAMERA_READ` 被标记为 unhealthy，普通断流在没有恢复尝试的情况下进入永久隔离。新增有界取帧恢复：等待旧 reader 释放后重建连接，只发布恢复后的新帧。
3. 恢复期限耗尽原先一律转换为 unhealthy，即使上次失败只是已清理的建连错误。现在保留最后一次连接失败的健康判定；PLC 等本来 unhealthy 的错误仍保留隔离。
4. 失败时若驱动确认已不 ready 且不持有资源，DeviceManager 同步清除旧的 lifecycleConnected，避免后续请求沿用已断开的连接状态。

策略默认值：`connectionRetryCount=3`、`recoveryBackoffMs=100`、`recoveryTimeoutMs=5000`。
0 次重试保留原显式恢复模式；既有工程缺失这些字段时使用设备声明默认值，不批量改写工程。
首次失败后的恢复窗口包含旧 reader 的退出等待，不会每次重试刷新；当前请求期限同样有效。

边界：

- 重试只发生在正常连接、连接测试或当前 RTSP 取帧请求中，不新增无限后台重连。
- 已失败的 Job 不自动重新启动，已执行的检测、归档及其他业务节点不重放。
- 普通网络错误在资源已安全释放后不永久隔离，下一次正常请求可再次尝试。
- 取消、手动断开与暂停准入仍有效；不能安全释放、格式异常及内存超限不通过重试绕过隔离。
- 每个解码器的 open/read/release 仍在其所属 reader 线程执行，不重叠占用旧、新连接；共享设备执行器代次不因普通重连变化。

## 测试证据

环境：Windows、Python 3.10.21；Qt UI 使用 `QT_QPA_PLATFORM=windows`。
使用 FakeCapture、模拟设备及既有本机回环 PLC 夹具；不是现场硬件验收。

### 修复前及中间失败

- 新增反例首次执行：**5 failed, 1 passed**。建连失败与断流均只尝试一次；连接测试不重试；格式错误不重试的保护通过。
- 第一轮既有回归：**3 failed, 46 passed**。
- 其中两项原测试专门要求断流立即隔离/手动恢复；按新策略将这两项显式设置为 `connectionRetryCount=0`，保留其错误码、隔离、脱敏与恢复断言，没有删除失败证据或弱化断言。
- 原手动恢复测试因“应抛异常”的旧前提不成立，产生了未回收测试帧的 teardown 错误；修正该测试模式后恢复通过。
- `testStaleUnconsumedFrameIsNotReturned`（10 ms 帧龄）首次组合执行出现一次后续取帧失败；单独复核和后续两轮完整相关回归未再复现，未修改阈值或断言。根因未查明，不把后续通过写成此前失败不存在，也不据此声称性能验收。

### 已通过

- 共享 Runtime/设备回归：**129 passed**，无跳过。包含 RTSP 注册、建连重试、连续流断开后新帧恢复、次数与时限耗尽、下一次请求恢复、取消与手动断开、等待旧解码器释放、格式错误保留隔离、FrameStore、双 spawned Job 共用解码器、设备生命周期、SLMP 与通用恢复合同。
- Windows 原生 Qt：**30 passed**，无跳过。包含 RTSP 策略默认值和关闭重试、设备表单、迁移与工程打开，以及上一轮连接测试结果反馈。
- 将旧 reader 的退出等待也纳入首次失败后的恢复时限后，最终复跑 `test_rtsp_connection_recovery.py`、`test_managed_rtsp.py`、`test_device_connection_recovery.py`：**44 passed**，无跳过。
- Ruff 与 `git diff --check` 通过。

```powershell
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" -m pytest -q `
  tests/runtime/test_rtsp_connection_recovery.py tests/runtime/test_managed_rtsp.py `
  tests/runtime/test_device_connection_recovery.py tests/runtime/test_device_lifecycle.py `
  tests/runtime/test_device_manager.py tests/runtime/test_device_executor.py `
  tests/runtime/test_managed_slmp.py tests/runtime/test_device_contract_closure.py `
  tests/core/plugin/test_device_registry.py --tb=short --timeout=60

$env:QT_QPA_PLATFORM = "windows"
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" -m pytest -q `
  tests/designer/test_managed_rtsp_ui.py tests/designer/test_device_connection_feedback.py `
  --tb=short --timeout=60
```

## 未执行与生效

未执行全仓库回归、真实 RTSP 相机、现场断网恢复、EXE 打包或部署，没有重启用户当前程序。
本轮包含 Runtime 驱动修复，不是仅更新 Designer。源码运行需要新的 Runtime 进程加载修改；
打包程序需要重新构建、更新对应 Runtime。当前旧进程中已经隔离的设备不会因源码文件变化自动恢复。
