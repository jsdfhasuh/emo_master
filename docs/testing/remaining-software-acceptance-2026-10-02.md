# 现有软件功能补充验收（2026-10-02）

基线为 `51ca8f8878924792dfd7f3b6f5837c1f912ab05c`，其生产代码与 `425f04d` 相同。本轮针对已有功能补缺口，不增加算子、绑定协议或设备接口。下面的合成测试不能替代现场工程、模型精度和设备实测。

## 本轮结果

| 验证 | 结果 | 证据范围 |
| --- | --- | --- |
| Canny 和经典形状检测两模块 | 48 PASS，0.26 秒 | 新增 35 例；真实 OpenCV Canny/HoughCircle、几何/像素、ROI、空结果、输入与 frame 不变、异常输入 |
| 真实微型 ONNX 工作流 | 2 PASS，0.35 秒 | CPU ONNX→计数→叠加→JSON/PNG 落盘；分别有目标/阈值后为空；不替代真实模型准确率 |
| 平台状态来源准备校验 | 2 PASS，0.12 秒 | `job_state` / `connection_state` 在模型层合法，但正常采集不支持；提示明确且文档不被修改 |
| 同窗口两个正常 Job 的完整观察与收尾 | 1 PASS，3.67 秒 | 第二次明确选择当前任务；两轮显示、冻结/恢复、弹窗、断开、线程和运行所有权退休；恰好 2 Start、0 Stop |
| 首次候选完整 CI（e2e 所有者修正前） | FAIL：pytest SIGSEGV（-11） | proto/Ruff/mypy 通过；在旧正常观看用例的 Qt eventFilter 路径原生崩溃，无完整 pytest 汇总 |
| 崩溃用例与紧邻前序模块 | 7 PASS，15.87 秒 | `test_normal_run_path.py` + `test_normal_run_viewing.py`；小组合未复现，不覆盖整套失败 |
| 新增算子用例前缀 + 相同相邻模块 | 59 PASS，15.49 秒 | 新增用例前缀不是本次受控组合中足以触发崩溃的条件 |
| Qt 所有权回归 + 两个原 e2e 用例 | 9 PASS，3.03 秒 | 原业务断言/期限保留；新增 6 例验证 GUI 退休、正文失败收尾、关闭拒绝及 worker 仍在时不强删 |
| 最终候选完整 CI（e2e 所有者修正后） | PASS：2535 通过、4 跳过、27 子测试通过，386.70 秒 | Linux / Python 3.10；proto、Ruff、mypy 通过，`scripts/ci_check.py` 退出 0 |

新增用例见：

- `tests/plugins/test_canny_operator.py`
- `tests/plugins/test_classic_shape_detection_operators.py`
- `tests/runtime/test_real_onnx_result_pipeline.py`
- `tests/runtime/presentation/test_normal_capture_status_validation.py`
- `tests/ui/page_designer/test_two_job_observation.py`

唯一生产修改是 `normal_capture.py` 对既有拒绝分支提供准确原因：平台状态不是一次工作流调用的输出。页面原生实时 Job 标签显示执行状态，无绑定 `runtime_status` 组件显示连接状态。本轮没有让平台状态成为可冻结的算法结果。

另修复两个旧 Designer e2e 用例的测试所有权：3 个窗口原来仅 `close()`，没有退休原生窗口、scene 和 bubble。独立短进程对照显示仅关闭后 18 个被观察 QObject 仍原生存活，后台一次 GC 和 GUI 分发后也未消失；GUI 显式删除后原生对象全部退休。新 fixture 仅由这两个用例和所有权回归明确请求，确认控制器/图标/目录 worker 已结束才删除，关闭被拒或 owner 未结束时保留对象并使测试失败。它解决已证实的测试对象未退休，不证明首次 SIGSEGV 的唯一原因；没有修改生产 eventFilter 或掩盖失败。

## 49 个内置算子覆盖清单

注册表共 49 个清单；`tests/runtime/test_operator_registration.py` 验证全部激活、无拒绝项。全部已有直接软件测试；“有测试”不等于所有参数组合、全部业务场景或真实设备均已通过。下列未写目录的文件均在 `tests/plugins/`。

| 算子组（数量） | 已有软件证据 | 边界或本轮补充 |
| --- | --- | --- |
| image_loader、image_saver（2） | `test_image_loader_operator.py`、`test_image_saver_operator.py`、`test_unicode_image_io.py`；e2e 文件链 | 临时文件 I/O；本轮新增真实 ONNX 叠加 PNG 读回比较 |
| result_writer（1） | `test_result_writer_operator.py`；编译流程中的 JSON/JSONL/CSV 与强类型结果 | 本轮真实 ONNX 检测结果 JSON 完整读回，含空集合 |
| demo.empty（1） | `test_empty_operator.py`、Runtime 架构用例 | 最小空操作行为 |
| color_convert、blur、crop、resize、roi、threshold（6） | 各自 `test_*_operator.py`；预处理/Blob 编译流程 | 真实 NumPy/OpenCV、尺寸和坐标；并非每种任意链路都有单独用例 |
| in_range、morphology、mask.logic（3） | `test_in_range_operator.py`、`test_morphology_operator.py`、`test_mask_logic_operator.py` | InRange/Morphology 有编译流程，MaskLogic 有直接测试 |
| canny（1） | `test_canny_operator.py` | 本轮补真实矩形四边、灰度/BGR、叠加、原图/frame 不变、非法输入 |
| rotate、flip、affine、perspective（4） | `test_classic_transform_operators.py`；Perspective 编译流程 | 地标、原图坐标、有效掩膜和变换组合 |
| absdiff、add_weighted、mask.apply、histogram、equalize、clahe（6） | `test_classic_intensity_arithmetic_operators.py` | 真实像素、饱和、掩膜、归一化、原图不变；不是所有组合均走完整 Runtime |
| contour、shape_measurement、template_match、hough_line、hough_circle（5） | `test_classic_shape_detection_operators.py`；前三项编译流程 | 本轮将 HoughCircle 补到真实算法，包含两圆、ROI 排除和空图 |
| blob（1） | `test_blob_analysis_operator.py`、Threshold/ROI/页面采集链 | 真实连通域和业务判定 |
| rgb_statistics（1） | `test_rgb_statistics_operator.py`、编译流程 | BGR 统计、ROI 与强类型结果 |
| collection.count/filter/sort/select（4） | `test_collection_operators.py`、多条编译业务链 | 排序、稳定元数据、空集合、越界策略 |
| annotate（1） | `test_annotate_operator.py`、强类型几何与编译链 | 不同坐标 frame 拒绝、原图不变；本轮与真实 ONNX 和保存组合 |
| value.number、compare.number（2） | `test_number_operators.py`、判定/页面采集链 | 有限数值、比较、输入优先级 |
| flow.if、flow.switch（2） | `test_flow_if_operator.py`、`test_flow_switch_operator.py`、Runtime DAG/子流程/循环用例 | 软件控制流 |
| coordinate_reader、coordinate_calculator（2） | `test_coordinate_operators.py`、`tests/runtime/test_builtin_coordinate_operator_workflow.py` | 临时坐标文件和编译计算；不代表机器人运动验证 |
| state.counter（1） | `tests/runtime/test_global_counter_operator.py`、并发/SQLite/正常采集用例 | 真实持久化；历史 Windows busy/终态等待失败仍需保留 |
| inference.yolo（1） | `test_yolo_inference_operator.py`、`test_yolo_onnx_backend.py` | 本轮补真实微型 ONNX 到 compiler/runner/输出链；不使用用户模型或图片 |
| io.huaray_camera（1） | `test_huaray_camera_operator.py`、`test_huaray_imv_adapter.py`、fake SDK 工作流 | 配置、重试、取消、清理为软件替身；真实 smoke 需显式 `HUARAY_CAMERA_SMOKE=1`，本轮不启用 |
| slmp_read、slmp_write、tcp.client、tcp.receive_once（4） | `test_communication_operators.py`、`tests/runtime/test_builtin_communication_operator_workflows.py` | 本机回环协议编码/分帧/期限；不是物理 PLC、机器人或现场网络实测 |

## 两任务观看与关闭的准确结论

`Preview._chooseJob` 在同项目只有一个任务时直接选择；两个及以上任务会打开 `QInputDialog`。本轮两任务测试确实走第二种分支，并明确提供用户选择。此前仅在第二次 Job 完成后结束的测试没有覆盖再次观看。

本轮没有改关闭生产逻辑，也没有证明某次外部 watchdog 超时一定由模态对话框造成。第二次选择完成后，合成路径可以正常退休，支持修正自动化脚本的交互处理。没有线程栈的历史超时仍为 INVALID，不能改记 PASS。

另据本机验收汇总，原 `425f04d` 的实际工程在明确处理第二次任务选择后，两轮正常推理、页面结果、冻结/恢复与全部观察/运行所有者关闭通过，进程正常退出；这是有限源码态实测。原始素材、截图和日志留在本机，没有纳入仓库。该结果不覆盖云端组合崩溃、模型总体准确率或冻结交付环境。

## 仍未完成的验收

- 基线 `51ca8f8` 普通 CI 结果混合：PR Windows 2491 PASS / 2 SKIP；push Windows 2489 PASS / 2 FAIL / 2 SKIP。两个失败均为原终态等待：`testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[presentation]`、`testLegacyStartAndProjectScopedReadonlyMetadata`。不得用另一轮通过覆盖失败
- 真实设备、现场网络、完整模型准确率、持续现场节拍和全部部署环境未被本轮合成测试证明
- 原 1080p / 5 Hz / 200 ms 与数据年龄回归 FAIL 不因功能测试通过而改变；已测通知优化收益也不等于全部性能达标
- 完整交付/冻结包仍受主计划 §5.3 的真实工程源码态验收门槛约束；本轮不扩大测试交付宿主权限

相关原计划：`docs/plans/2026-09-25-qt-runtime-pages-v1.md`。测试结果应按实际平台和候选提交分别记录，不用新增测试数量换算整个项目完成率。

本报告记录发布前的云端结果。发布后的精确提交还需查看普通 GitHub CI；既有路径规则也会自动触发 credit 诊断，其结果单列，不作为性能通过证明。本轮不改变这些触发规则，也不启动系统级采集。
