# RTSP 设备管理接入验证

## 范围与基线

- 日期：2026-10-25。
- 分支：`agent/runtime-workflow-architecture-v1`。
- 开始及结束 HEAD：`9265547dbe61bc57542d3cf0ea143fcf455600fb`。
- 验证对象是该 SHA 上的当前未提交工作区，不是已打包版本。
- 本轮新增 RTSP 托管驱动、设备声明、采集算子和显式迁移入口，保留旧单次算子。
- 工作区已有的日志窗口、调度队列、资源诊断等改动保留，未回退、提交、推送或部署。

使用方式和行为边界见 [RTSP 托管相机](../guides/rtsp-managed-camera.md)。

## 实现核对

- 注册：78 个算子、55 个入口模块、9 个托管设备算子、7 种设备类型；
  `rejectedOperators` 和 `rejectedDeviceTypes` 均为 0，新算子无图标诊断。
- `DeviceCaptureResult` 使 io_thread 驱动沿既有 FrameStore 发布接收时间和序号，
  不改变既有 ndarray 驱动和 IMV image/metadata 返回路径。
- 单个 reader 线程拥有 open/read/release；首次采集前不调用 read。
  连接测试包含 FFmpeg 建连及流信息获取，但不启动持续解码或发布图像。
- 持续解码只保存最新帧，按序号和本地帧龄交付；断流清缓存，不重放业务动作。
- 真实资源未退出时保留 `ownsResources` 和释放待完成错误；恢复无需重启 Runtime。
- 旧节点通过迁移预览显式转换，保留端口、连线、页面引用与颜色模式，原子更新依赖。
- 设备状态列表和详情读取驱动的实际连接状态，能显示后台断流；迁移信息隐藏密码和查询令牌。
- 原生截图发现切换来源后旧提示等待 DeferredDelete 时短暂残留，已通过先隐藏再删除修复。

## 源码回归

环境为仓库 `emo_master` Python 3.10、`-X utf8`、`PYTHONPATH=src;.`、
`QT_QPA_PLATFORM=offscreen`。最终一次执行耗时 58.51 秒：

```text
180 passed, 1 skipped
```

执行文件列表如下，参数为 `-q -rs --tb=short`：

```text
tests/runtime/test_managed_rtsp.py
tests/designer/test_managed_rtsp_ui.py
tests/plugins/test_rtsp_camera_operator.py
tests/runtime/test_network_camera_workflow.py
tests/runtime/test_rtsp_face_project.py
tests/designer/test_rtsp_face_project_ui.py
tests/runtime/test_device_manager.py
tests/runtime/test_device_lifecycle.py
tests/runtime/test_device_connection_recovery.py
tests/runtime/test_device_runtime.py
tests/runtime/test_operator_registration.py
tests/core/plugin/test_device_registry.py
tests/core/plugin/test_operator_development_examples.py
tests/designer/test_managed_camera_ui.py
tests/designer/test_device_catalog.py
tests/designer/test_device_usability_drafts.py
tests/designer/test_device_identity_reuse.py
tests/designer/test_network_camera_form.py
```

跳过的是 `tests/designer/test_rtsp_face_project_ui.py:37` 的显式现场工程验收，
原因是没有设置 `EMO_FACE_ACCEPTANCE_PROJECT`，不计为通过。

新测试实际覆盖：注册和构造不联网、连接测试不读帧、最新帧与接收时间、重复/过期帧拒绝、
灰度、断流清缓存、脱敏、环境变量凭据、超预算帧、连接及取帧取消、资源退出与共享 owner、
空闲释放和重连、恢复、更换代次、FrameStore 元数据、两个 spawned Job 共用 Runtime 解码器。
另以 spawned While 连续执行超过 40 次采集，验证没有耗尽 32 帧容量且仅打开一次连接，
停止后原图、预约和解码器均释放。取消终态按既有 Runtime 取消合同检查，不将未知回执伪装为完成。

原生 OpenCV/FFmpeg 使用本地临时 AVI 验证解码及同线程释放，不是 RTSP 真实相机协议验收。
Designer 测试通过实际工程打开入口完成迁移、保存与重开，并检查完整人脸 While 工程的
页面定义、子流程关系和连线保持不变；模型和图像仅为临时夹具。

## 原生 Qt

`QT_QPA_PLATFORM=windows`，运行 `tests/designer/test_managed_rtsp_ui.py -q -s --tb=short`：

| 缩放 | 最终结果 | 证据 |
| --- | --- | --- |
| `QT_SCALE_FACTOR=1` | 8 passed，10.15 秒 | 设备表单和迁移绑定窗口的受控截图 |
| `QT_SCALE_FACTOR=1.5` | 8 passed，11.68 秒 | 同上，长设备 ID 和中文字段无重叠或截断 |

使用 `Qt.WA_DontShowOnScreen` 受控窗口。逐张查看了截图，检查密码遮罩、设备选择、
参数来源、继承值、按钮和提示位置。测试客户端没有在线对比接口，截图中的该接口提示
属于夹具，不是实际 Runtime 版本检测结果。截图路径由测试打印，未加入现场凭据。

这不等于人工桌面走查、冻结 EXE 或真实相机验收。

## 保留的失败记录

1. 首轮 `40 passed, 2 failed`：新夹具编译漏传 `deviceTypes`；过期帧用例的后续调用
   恢复了较大的默认允许帧龄。分别补充设备类型、保持相同帧龄约束后通过，未弱化断言。
2. 一轮 `70 passed, 3 failed`：新 UI 夹具把 Pydantic `DeviceRequirement` 直接传给
   期待 RPC 字典的画布。按真实 RuntimeClient 返回格式序列化后通过。
3. 扩大回归 `93 passed, 1 failed`，随后单独重跑同一用例仍失败：
   `tests/designer/test_device_workspace.py::testDeviceMenuOpensDialogWithoutChangingWorkspace`
   第 77 行假设主窗口下只有一个 `QDialog`。实际是一个 `DeviceWorkspace` 加当前工作区
   已有的 `runtimeLogDialog`，并非重复创建设备窗口。`main_window.py` 的既有改动将日志
   从 dock 改为对话框；本轮未修改该主窗口路径，也未改写此断言使其通过。
   此项是其他改动与旧断言的适配问题，保留为本轮非阻断记录。

`git diff --check` 检查通过。以上不是全仓库回归全通过。

## 未执行

- 未访问真实摄像头、发声、发现或修改设备网络。
- 未修改用户的私有人脸检测工程、模型、摄像头凭据或现场 Runtime 配置。
- 未构建/替换 EXE、重启用户程序或执行在线写入。
- 未验证端到端网络延迟、真实编码兼容性或异常 native 调用的强制终止。
  帧龄仅约束本机解码后的缓存；底层调用退出前不会声称已释放。

后续人工验收需使用包含本次代码的 Designer/Runtime，从设备管理器显式迁移旧节点，
保存并写入 Runtime，再检查真实画面、页面显示和工程现有归档行为。
