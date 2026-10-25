# PatchCore V1 验证记录

日期：2026-10-23，Asia/Shanghai。使用说明见 [PatchCore Inference](../patchcore-inference.md)。

## 源码与边界

- emo_master 实际 HEAD：`577450fc19c60ac86989475d76838e952bcc3e86`。
- 训练平台参考 HEAD：`aac0dfeac4cc217469cfff1a7031934da532f077`。
- 本次 PatchCore 实现为上述 emo_master HEAD 上的**未提交工作区修改**，不是该提交已有的功能。
- 两个仓库本来都有未提交修改。已有 YOLO、可选输出、Runtime 等修改保留；工作期间出现的
  README、开发指南、算子目录等并行文档修改也未回退或纳入本任务的实现归属。
- 训练平台代码只读，未加入 emo_master 运行时导入或路径依赖，未改其源码和本机注册数据库。
- 无构建 EXE、提交、推送、发布、修改现场工程、真实相机/PLC 访问。回归中的设备测试使用已有模拟器或回环地址。

环境：Python 3.10.21、PySide2 5.15.2.1、NumPy 1.26.4、OpenCV 4.10.0.84、
onnxruntime 1.23.2、onnx 1.16.0。使用现有 `emo_master` Conda 环境，安装了新增的 onnx 依赖。

## 验证结果

| 层次 | 结果 | 证据与限制 |
| --- | --- | --- |
| 实施前相关基线 | 28 passed | YOLO ONNX、算子生命周期、算子注册；保留原基线记录，不与下列回归相加 |
| 最终 PatchCore 专项 | 85 passed | [patchcore.xml](patchcore-2026-10-23/patchcore.xml)；包括算子、数值、工作流、调试子进程和 Designer 打开/保存/结果显示 |
| 源码专项及受影响回归 | 273 passed | [source.xml](patchcore-2026-10-23/source.xml)；真实 ORT 使用小型合成 ONNX，不是业务模型 |
| 插件、端口、图标合同 | 146 passed、1 skipped | [contracts.xml](patchcore-2026-10-23/contracts.xml) |
| Windows 原生 Qt 100% | 46 passed | [native-100.xml](patchcore-2026-10-23/native-100.xml)，不是人工点击验收 |
| Windows 原生 Qt 150% | 46 passed | [native-150.xml](patchcore-2026-10-23/native-150.xml)，不是冻结 EXE 验收 |
| 最终原生 Qt 截图复核 | 100% / 150% 各 5 passed | [100%](patchcore-2026-10-23/native-100-final.xml)、[150%](patchcore-2026-10-23/native-150-final.xml)；等待画布异步适应内容完成后，断言所有节点进入视口并截图 |
| 直接参考数值对照 | 14 组全部一致 | 7 种尺寸 × BGR/灰度；FP32 tensor/map 逐位相等，geometry 相等，输入不变 |
| Ruff | PASS | 新算子、集成适配与本轮测试文件 |
| Mypy | PASS | `--follow-imports=silent` 检查新算子 5 个 Python 文件，不代表全仓类型检查 |
| 真实 DINO 业务模型及图片对照 | **NOT_RUN** | 未提供正式业务部署包和图片，不把合成图推理或包内历史 PASS 记录记作通过 |
| 冻结 EXE、部署、真实设备、业务准确率 | **NOT_RUN** | 不在本轮实施范围内 |

合同组跳过项为 `tests.core.plugin.test_icon_resources::testSymlinkCannotEscapeRoot`，
原因 `symlinks unavailable in this environment`。这是已有图标测试的环境限制，不计为通过。
不同组有重复用例；上述数量不是可直接相加的唯一测试数。

## 覆盖内容

- 新算子可扫描、注册且元数据一致，内置算子数量断言从 76 更新为 77，未弱化原有校验。
- 直接部署目录、transfer 外层目录、中文路径、external-data、子图；包文件在加载/推理前后不变。
- 缺文件、摘要不匹配、模型身份/格式/profile 不符、未完成部署、v2 未冻结校准、阈值不一致、
  校准图/样本/方法绑定不一致、transfer 清单漏列必要文件，均拒绝加载且没有默认 OK 输出。
- 真实 CPU ORT 的输出类型、形状和有限值检查；直接读取 `pred_score` 与 `anomaly_map`。
- 分数大于 1、等于阈值及 binary64 相邻阈值的实际推理判定。
- 灰度/BGR 转 RGB、裁剪 frame 保留、热图/叠加开关、逐图相对 JET、0.6/0.4 叠加、输入不变。
- 原始 map 的完整 JSON 往返，包括有符号零、float32 次正规值及最大有限值的逐位恢复。
- 同节点循环复用、不逐帧哈希、换模型释放旧 Session、停止重启、推理后的取消清理。
- 真实调试子进程执行、模型切换、重置、加载失败后恢复、PNG/JSON 下载、关闭后进程/IPC/目录释放。
- 图片输入 → PatchCore → bool If / Result Writer / Image Saver；可选图像未输出时跳过下游保存，
  节点历史保留精确缺失说明，生成的图像可从历史记录读取。
- 临时工程从 `WorkflowStore().toPayload()` 默认链创建，经过 ProjectDocument 校验、保存重读、编译，
  实际调用 Designer `loadProjectDirectory()` 打开，再修改参数、保存和重开。保留 2.7 的页面、资源、
  生产设置及空设备 definitions，没有复制旧 2.1 夹具假装新建工程。
- 原生 Qt 自动化检查 460/620 宽度参数窗口、目录选择、真实调试结果/图像/原始 map/历史分数。

## 数值参考

只在开发对照命令中通过 importlib 加载：
`D:\jsdfhasuh\documents\my_project\training_platform\anomaly_func\dinov2_onnx_numeric.py`。

文件 SHA256：`8deb7e7ef96d4f0e73440a36c4feb6ad2c99210a017165d7a4e6f54d0c08ad92`。
对照尺寸为 `140×360`、`360×140`、`113×251`、`1×20`、`19×1`、`511×717`、`256×256`。
输入用 NumPy 随机种子 7，原始 map 用随机种子 3；比较实际字节、geometry 和输入不变性。
7 组参考摘要已固化在 `tests/plugins/test_patchcore_numeric.py`，回归时不依赖训练平台安装或路径。

此项验证纯 NumPy 预处理/去补边还原一致性，**没有验证真实 DINO 权重、真实图片的分数对照或业务准确率**。

## 保留的限制

最初的合成“常量 If + 子图外部 initializer”在 ORT 1.23.2 BASIC 加载时抛出 RuntimeException，
底层信息涉及 `file_size: "weights.bin"`；同一图用 DISABLE 可运行。这不是中文路径问题。
该反例保留为 `testOrtNestedExternalInitializerLimitationDoesNotChangeOptimization`，验证失败时明确报错，
没有静默改优化等级或切换进程工作目录。

正常正例使用依赖图像输入的 If 子图，并通过 BASIC 实际推理。以上不能证明正式 DINO 导出图有同一问题，
也不能证明所有正式模型已兼容；真实部署包验收仍为 NOT_RUN。此项作为有复现证据的合成图兼容性限制记录，
不是业务模型缺陷结论，不引入模型改写/重导出或其他回退机制。

## 复现入口

在仓库根目录及声明的 Python 环境中运行：

```powershell
python -m pytest tests/plugins/test_patchcore_inference_operator.py tests/plugins/test_patchcore_numeric.py tests/runtime/test_patchcore_workflow.py tests/runtime/test_patchcore_debug.py tests/designer/test_patchcore_inference.py -q
```

源码 273 项组包含上述非 Designer 用例，以及：

```text
tests/plugins/test_yolo_onnx_backend.py
tests/plugins/test_yolo_inference_operator.py
tests/runtime/test_operator_lifecycle.py
tests/runtime/test_operator_registration.py
tests/core/plugin/test_builtin_parameter_titles.py
tests/runtime/test_optional_output_execution.py
tests/runtime/test_yolo_inspection_outputs.py
tests/runtime/test_worker_capture_diagnostics.py
tests/runtime/test_runtime_workflow_architecture.py
tests/runtime/test_builtin_vision_operator_workflows.py
tests/runtime/test_operator_debug_resources.py
tests/runtime/test_operator_debug_rpc.py
tests/runtime/test_operator_debug_sessions.py
```

合同组：`tests/core/plugin`、`tests/runtime/test_operator_output_contracts.py`、
`tests/runtime/test_operator_port_specs.py`、`tests/runtime/test_operator_icon_assets.py`。

原生 Qt 两组均设置 `QT_QPA_PLATFORM=windows`，分别设置 `QT_SCALE_FACTOR=1` / `1.5`，运行：

```text
tests/designer/test_patchcore_inference.py
tests/designer/test_yolo_parameter_presentation.py
tests/designer/test_node_result_panel.py
tests/designer/test_node_image_viewer.py
tests/designer/test_run_result_history.py
tests/designer/test_operator_icon_integration.py
```

设置 `EMO_PATCHCORE_SCREEN_DIR` 可保留当前测试的参数窗口、工程打开、调试热图和原始 map 截图。
本轮截图分别位于 [100%](patchcore-2026-10-23/native-100/) 和 [150%](patchcore-2026-10-23/native-150/)。
截图中的随机图像与响应仅用于机制验证，不用于缺陷效果宣传。

已查看代表性参数窗口（两种缩放的 460 宽）、完整流程画布、叠加图和原始 map 结果页截图，
没有文字遮挡、按钮不可达或空白图像。文件路径输入框使用普通文本框水平滚动，长绝对路径不挤占文件夹按钮。
