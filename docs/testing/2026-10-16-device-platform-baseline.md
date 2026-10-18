# 设备平台 R2.1 实施前定向基线

日期：2026-10-16（Asia/Shanghai）。源码 HEAD：`63070277133b89d61a5d27dde6d5f95494b4dce0`。

本记录对应 [实施启动记录](../plans/2026-10-16-device-management-implementation-start.md)。只验证改造前选定路径，不代表设备管理新功能已实现。88 项新增方案验收继续全部为 [NOT_RUN](2026-10-16-device-platform-acceptance.md)。

## 环境与隔离

- Windows / PowerShell，仓库 `C:/Users/jsdfhasuh/my_scripts/emo_master`。
- 使用已有 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，未安装或更新依赖。
- Python 3.10.21、PySide2 5.15.2.1、pytest 9.1.1、pydantic 2.13.5。
- Qt 使用 `QT_QPA_PLATFORM=offscreen`。相机组显式设置 `HUARAY_CAMERA_SMOKE=0`，未选中真实相机 smoke 测试。
- `tests/conftest.py` 给每个测试隔离 Runtime 数据目录并清除真实 DB 路径覆盖。
- 双 PLC 测试源码已核对：执行前把全部 PLC 节点改为 `127.0.0.1` 的 PlcDebugServer 端口；现场地址仅静态断言，不连接现场 PLC。
- 测试产生的 JUnit XML 位于被 Git 忽略的 `build/device-management-m0/`，不把测试临时数据混入工程。

## 首次执行结果

| 组 | 实际结果 | 耗时 | 本地原始报告 |
|---|---|---|---|
| 工程/插件/Store/图投影 | 190 passed, 1 skipped | 16.52 s | `build/device-management-m0/core.xml` |
| PLC 协议/调试/Runtime 生命周期 | 559 passed | 45.00 s | `build/device-management-m0/runtime.xml` |
| 相机适配/配置/取消 | 91 passed | 6.18 s | `build/device-management-m0/camera.xml` |
| Designer 工作页/保存加载/PLC UI | 184 passed | 56.46 s | `build/device-management-m0/designer.xml` |

四组都是本轮首次执行结果，没有先失败再选择性重跑，没有修改测试或实现以取得这些结果。唯一跳过项为 `tests.core.plugin.test_icon_resources.testSymlinkCannotEscapeRoot`，原因：`symlinks unavailable in this environment`。该边界未验证，不能算 PASS。

## 可复现命令

在仓库根目录执行，使用现有环境；每组独立进程。

```powershell
$python = "$env:USERPROFILE\.conda\envs\emo_master\python.exe"
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'

& $python -m pytest -q tests/core/project tests/core/plugin tests/core/test_project_files.py tests/designer/test_workflow_store.py tests/designer/test_flow_graph_project_codec.py --junitxml=build/device-management-m0/core.xml

& $python -m pytest -q tests/plugins/test_slmp_ascii.py tests/plugins/test_slmp_debug_transport.py tests/plugins/test_communication_operators.py tests/runtime/test_plc_debug.py tests/runtime/test_dual_plc_field_project.py tests/runtime/test_runtime_resource_lifecycle.py tests/runtime/test_runtime_project_state_lock.py --junitxml=build/device-management-m0/runtime.xml

& $python -m pytest -q tests/plugins/test_huaray_imv_adapter.py tests/plugins/test_huaray_camera_operator.py tests/plugins/test_camera_hardware_wait.py tests/plugins/test_camera_configuration.py tests/runtime/test_camera_configuration_session.py tests/runtime/test_camera_configuration_rpc.py --junitxml=build/device-management-m0/camera.xml

& $python -m pytest -q tests/ui/page_designer/test_workspace_switch.py tests/designer/test_main_window_project_save_load.py tests/designer/test_plc_editor.py tests/designer/test_plc_presentation.py tests/designer/test_dual_plc_project_open.py --junitxml=build/device-management-m0/designer.xml
```

## 验证边界

| 层次 | 状态 |
|---|---|
| 当前源码选定回归 | PASS，以上述各组范围为限，另有一个环境跳过项 |
| 现有协议/相机模拟 | 上述选定测试 PASS，不是新设备平台测试 |
| offscreen Qt | 上述 Designer 组 PASS，不是现场人工界面验收 |
| R2.1 新功能 A01-G08 | 88 项 NOT_RUN |
| 全仓组合测试/长期交互 | NOT_RUN；独立进程分组通过不证明组合运行稳定 |
| 真实桌面操作/截图验收 | NOT_RUN；随包图片仅设计参考 |
| 华睿 SDK/真实相机/真实 PLC | NOT_RUN |
| Windows 冻结包/Linux 软件路径/云端 CI | NOT_RUN |
| 发布/部署/现场节拍 | NOT_RUN；节拍和性能专项不在本计划范围 |

本轮没有改动业务源码、proto、已有测试或真实设备配置；没有 commit/push、启动现场工程、修改现场网络或控制生产任务。
