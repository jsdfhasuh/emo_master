# 2026-10-23 网络摄像头算子验证

## 源码与范围

- 开始时 HEAD：`43d0dd0b856382cbd32230b0e656c7da3c0b6b23`。
- 最终检查时 HEAD：`f7367e252e350b1935096a5d3ebeb4f14311e5d2`，加本轮未提交修改及工作区原有修改。
  期间新增提交只修改 `core/project/test_delivery.py` 及 3 个已有运行时测试文件，未覆盖本轮实现。
- 实际 checkout：`D:/jsdfhasuh/documents/my_project/emo_master`。
- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，3.10.21；OpenCV 4.10.0，PySide2 5.15.2.1。
- 新增 `vision.io.rtsp_camera`、`communication.camera.hikvision_audio`，共用少量连接参数校验。
  接入既有参数表单和显式单节点调试，未增加后台服务、依赖、设备类型或全局配置写入。
- 复用了工作区中的已有文件遍历/视频修改，不回退或接管这些修改。算子数量在原有 72 个基础上增加为 74 个。

## 保留的首次结果

修改前运行插件注册和中文参数标题测试：**2 passed, 1 failed**。
失败为 `testAllBuiltinParameterTitlesSurviveRegistration` 的旧数量断言 `71`，实际已有 `72`。
新增两个算子后，严格注册和标题测试的明确数量断言均更新为 `74`，没有移除标题/注册检查。

新增插件测试首次运行：**60 passed, 1 failed, 2 errors**。
测试夹具存在超长字节参数自动生成测试 ID，以及模拟服务器发出认证挑战后才触发取消的时序问题。
分别改为简短明确的参数 ID、在发送挑战前设置取消；保留所有原断言与超大响应测试。
修正后的同组测试为 **62 passed**，之后继续增加播放认证阶段取消、查询参数通道标识测试。

第一轮周边回归：**294 passed, 1 skipped**。最终补充查询参数通道标识测试并核对播放请求
`Content-Type: application/json` 后重新执行：**295 passed, 1 skipped in 52.30s**，退出码 0。

## 最终执行

```powershell
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest -q -rs tests/plugins/test_rtsp_camera_operator.py tests/plugins/test_hikvision_audio_operator.py tests/runtime/test_network_camera_workflow.py tests/designer/test_network_camera_form.py tests/runtime/test_operator_registration.py tests/core/plugin tests/designer/test_param_form.py tests/designer/test_param_form_file_picker.py tests/designer/test_parameter_integrity.py tests/designer/test_parameter_input_integrity.py tests/designer/test_file_list_video_form.py tests/designer/test_video_loader_form.py tests/runtime/test_operator_debug_resources.py
```

唯一跳过：`tests/core/plugin/test_icon_resources.py:73`，原因
`symlinks unavailable in this environment`。该用例未记作通过。

本轮新增源码、测试及直接修改的既有 Python 文件通过 `ruff check`。
`git diff --check` 通过；Git 的 LF/CRLF 提示不是检查失败。

## 证据层次

- **插件源码与注册**：严格 manifest/实现一致性、全部 74 个内置注册、中文参数标题。
- **模拟 RTSP 输入**：正常输出、密码特殊字符编码和环境变量优先级、失败释放/重新执行、取消不发布旧结果。
  查询参数选择不同通道时的安全且独立坐标系标识。没有运行真实 RTSP 服务。
- **实际本地解码**：用 OpenCV FFmpeg 打开临时视频，验证实际安装的后端接受打开/读取超时选项。
  这不等同于 RTSP 传输验收。
- **实际本机 HTTP**：仅绑定 `127.0.0.1` 的模拟摄像头服务器，核对 Digest 摘要、空 PUT 请求体、JSON 请求头、
  正常回执、错误密码、模糊回执、HTTP 错误及取消认证。一次有效请求对应一次模拟播放，无自动重试。
- **原生运行时**：2.7 临时工程校验、保存重读、编译，WorkflowRunner 传图至模糊算子，按布尔条件触发发声、重复作业。
- **独立进程调试**：打开会话不访问设备；未确认被拒绝；显式确认后实际 worker 访问本机模拟服务器并返回成功。
- **原生 Qt offscreen**：Designer 实际打开入口、两个参数表单、密码遮蔽、音频模式切换保留参数、编辑保存重开。

没有执行全仓完整测试、真实桌面人工验收、冻结包、真实摄像头 RTSP 或扬声器测试。
没有访问现场摄像头、写入配置、上传音频、改动现场凭据、创建提交或推送。
功能使用说明见 [network-camera-operators.md](../network-camera-operators.md)。
