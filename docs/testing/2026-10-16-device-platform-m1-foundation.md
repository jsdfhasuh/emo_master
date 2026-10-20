# 设备平台 M1 第一批实现验证

本文件保留第一批历史结果，不是当前全部实现状态。后续第三工作页、向导及模拟 Runtime 进展见 [UI/Runtime 验证](2026-10-16-device-platform-ui-runtime.md)。

- 日期：2026-10-16（Asia/Shanghai）。
- 基础 HEAD：`63070277133b89d61a5d27dde6d5f95494b4dce0`；本次实现为该 HEAD 上未提交的工作区修改，不是固定实现提交 SHA。
- 范围：M1.1 设备元数据/工程格式/参数来源，以及 M1.2 原子编辑与既有 Store/画布保存链路。M1 尚未整体完成。
- 契约说明：[Device Catalog Contract](../guides/device-catalog-contract-v1.md)。

## 已实现

1. 显式 schema 2.6、类型化设备定义与节点绑定、保留旧 bindings/未知插件数据。
2. 现有 Registry 的设备元数据扫描和算子可选 deviceRequirements，重复去重及冲突隔离，不导入设备驱动。
3. 继承/常量/动态来源互斥、父子路径冲突校验、关联模式校验、现有变量求值复用及请求前纯参数解析。
4. 同一 ProjectEditSession 下设备和节点原子提交、失败回滚、撤销/重做、引用删除保护和变量引用保护。
5. FlowGraph/WorkflowStore 保留字段，正常 Designer 离线打开 2.6、复制节点、页面切换、保存重开。
6. 在统一设备 Runtime 未完成前阻止新绑定落回旧直连；导出、快照、部署和单步调试明确拒绝尚未接通的设备路径。

## 首次失败及修复

第一轮三个新测试文件：`3 failed, 52 passed`，保留于 `build/device-management-m1/foundation-first.xml`。

- 两个失败来自新增节点未归一化 displayName，撤销/重做或回滚后 Store 自动补名导致草稿字节变化。修复：节点提交前按已有 Store 规则确定显示名，未放宽断言。
- 一个失败来自编译器已有结构检查先返回，设备执行拦截没有加入问题清单。修复：先汇总结构与设备执行问题，再统一拒绝，不依赖私有连接路径失败。
- 随后原 55 项全部通过；补充正常 Designer 打开、动态关联模式、单步调试/部署拦截、扫描根目录失败隔离后，新增测试最终为 `60 passed`。
- Ruff 首次发现测试内局部变量与 fixture 同名，已改名；Mypy 首次发现参数 schema 的 object 类型访问及迁移 devices 的 object 类型访问，已增加明确类型检查。最终重跑结果另列。

## 实际测试

解释器与环境沿用 [M0 基线](2026-10-16-device-platform-baseline.md)：Python 3.10.21 / PySide2 5.15.2.1，Qt offscreen。没有安装依赖。PLC 仅 loopback，未连接现场。

| 组 | 结果 | 报告 |
|---|---|---|
| 新增契约/注册/编辑/正常 Designer 打开测试 | 60 passed | `build/device-management-m1/foundation.xml` |
| 工程/插件/Store/图投影/工作流包/部署包回归 | 273 passed, 1 skipped | `build/device-management-m1/core-regression.xml` |
| 正常 Designer 保存加载/工作页切换/2.6 打开 | 12 passed | `build/device-management-m1/designer.xml` |
| 真实 spawn 单节点调试、旧通信工作流、双 PLC 模拟、资源生命周期、ASCII、受管 TCP 算子 | 64 passed | `build/device-management-m1/runtime.xml` |
| Mypy（20 个修改/新增源码文件，follow-imports=silent） | PASS | 本轮终端结果 |
| Ruff（全部修改/新增 Python 文件） | PASS | 本轮终端结果 |

这些组有重叠，不将数量相加为独立用例总数。GUI 组是正常 MainWindow 的 offscreen 自动化，不是用户桌面或现场验收。唯一已知环境跳过边界为符号链接不可用。

```powershell
$python = "$env:USERPROFILE\.conda\envs\emo_master\python.exe"
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'

& $python -m pytest -q tests/core/project/test_devices.py tests/core/plugin/test_device_registry.py tests/designer/test_device_catalog.py tests/designer/test_device_project_open.py --junitxml=build/device-management-m1/foundation.xml

& $python -m pytest -q tests/core/project tests/core/plugin tests/core/test_project_files.py tests/designer/test_workflow_store.py tests/designer/test_flow_graph_project_codec.py tests/designer/test_device_catalog.py tests/designer/test_workflow_package.py tests/core/test_global_variable_packages.py tests/core/test_runtime_package.py --junitxml=build/device-management-m1/core-regression.xml

& $python -m pytest -q tests/designer/test_device_project_open.py tests/designer/test_main_window_project_save_load.py tests/ui/page_designer/test_workspace_switch.py --junitxml=build/device-management-m1/designer.xml

& $python -m pytest -q tests/runtime/test_operator_debug_resources.py tests/runtime/test_builtin_communication_operator_workflows.py tests/runtime/test_dual_plc_field_project.py tests/runtime/test_runtime_resource_lifecycle.py tests/plugins/test_slmp_ascii.py tests/plugins/test_managed_tcp_operators.py --junitxml=build/device-management-m1/runtime.xml
```

## 未完成与边界

- 第三工作页、插件表单、可视化节点向导与拖入算子入口：NOT_IMPLEMENTED；本批只提供它们的共享编辑后端，不把 API 测试当可视交互验收。
- 示例只有设备声明，没有模拟采集驱动；真实 IMV/SLMP 设备声明和适配、UI 资源扩展、完整策略组合、旧节点迁移预览仍待后续 M1/M2/M4。
- DeviceExecutor、跨 Job FIFO/共享连接、相机 Host、FrameStore、在线配置、周期、Dispatch/Join：NOT_IMPLEMENTED。
- 设备化 siteBindings、跨工程设备依赖映射和冻结包资源：NOT_IMPLEMENTED；不支持的发布入口明确拒绝，不丢字段后继续。
- 原生桌面人工验收、真实 SDK/PLC/相机、Linux、冻结包、全仓组合测试：NOT_RUN。
- 未 commit/push、发布/部署、连接或控制现场设备；原始压缩包资料未修改。

验收台账只把有部分源码/测试证据的条目标为 PARTIAL，未把整个 A/B/G 类别或 M1 标为 PASS。下一工作包是补齐 M1 的设备声明与表单契约，然后接入第三工作页和节点向导；仍沿本批已验证的编辑边界推进。
