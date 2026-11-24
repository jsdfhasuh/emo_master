# 82 个内置算子逐字段审查清单

审查基线：b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd  
历史入口索引：openspec/changes/archive/2026-11-22-redesign-runtime-log-workbench/reference/operator-coverage.json  
当前状态：**待本 Change 的 P0 重新逐字段审查**。

下表只确保 82 个 operatorId 不遗漏。历史覆盖清单不代表本 Change 已完成审查。每个算子必须检查当前 Manifest、OperatorMeta、执行实现、共享 helper、继承类及托管设备路径。

状态：待审查、已审查、需要补充、已补齐、无需补充（必须填写理由）。

每个算子填写：输入端口、输出端口、配置参数、生效参数来源、metrics、diagnostics、敏感字段、缺失项决定和源码依据。Runtime 通用失败/跳过/取消生命周期只统一审查一次；仅在某算子有特殊处理时记录其专属行为。

| # | operatorId | 入口实现 | 输入/输出 | 参数 | metrics/diagnostics | 缺失项决定 | 状态 |
|---:|---|---|---|---|---|---|---|
| 1 | vision.image.absdiff | _arithmetic_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 2 | vision.image.add_weighted | _arithmetic_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 3 | vision.preprocess.affine | _transform_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 4 | vision.render.annotate | annotate/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 5 | vision.mask.apply | _arithmetic_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 6 | vision.analysis.blob | blob_analysis/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 7 | vision.preprocess.blur | blur/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 8 | vision.edge.canny | canny_edge/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 9 | vision.preprocess.clahe | _intensity_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 10 | vision.collection.count | collection_count/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 11 | vision.collection.filter | collection_filter/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 12 | vision.collection.select | collection_select/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 13 | vision.collection.sort | collection_sort/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 14 | vision.preprocess.color_convert | color_convert/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 15 | vision.analysis.contour | _shape_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 16 | vision.geometry.coordinate_calculator | _coordinate_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 17 | communication.coordinate_message_format | _managed_tcp.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 18 | vision.io.coordinate_reader | _coordinate_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 19 | vision.preprocess.crop | crop/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 20 | vision.io.detection_archive | detection_archive/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 21 | vision.geometry.detection_bbox | _geometry_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 22 | vision.state.elapsed_time | elapsed_time/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 23 | vision.demo.empty | empty_operator/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 24 | vision.preprocess.equalize | _intensity_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 25 | vision.io.file_list | file_list/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 26 | vision.preprocess.flip | _transform_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 27 | vision.flow.error | flow_error/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 28 | vision.flow.if | flow_if/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 29 | vision.flow.switch | flow_switch/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 30 | communication.gateway.ack_validate | _gateway_coordinates.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 31 | communication.gateway.coordinate_format | _gateway_coordinates.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 32 | vision.geometry.extract_points | _geometry_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 33 | vision.state.counter | global_counter/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 34 | communication.camera.hikvision_audio | hikvision_audio/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 35 | vision.analysis.histogram | histogram/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 36 | vision.analysis.hough_circle | _detection_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 37 | vision.analysis.hough_line | _detection_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 38 | vision.io.huaray_camera | huaray_camera/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 39 | vision.io.image_batch_loader | image_batch_loader/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 40 | vision.io.image_loader | image_loader/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 41 | vision.io.image_saver | image_saver/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 42 | vision.segment.in_range | in_range/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 43 | vision.math.integer_add | integer_add/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 44 | vision.geometry.line_from_points | _geometry_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 45 | vision.mask.logic | mask_logic/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 46 | vision.analysis.minimum_enclosing_circle | _geometry_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 47 | vision.preprocess.morphology | morphology/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 48 | vision.math.absolute | _numeric_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 49 | vision.compare.number | number_compare/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 50 | vision.math.exact_integer | _numeric_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 51 | vision.collection.number_pair | _numeric_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 52 | vision.math.truncate | _numeric_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 53 | vision.value.number | number_value/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 54 | vision.inference.patchcore | patchcore_inference/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 55 | vision.math.percentage | percentage/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 56 | vision.preprocess.perspective | _transform_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 57 | communication.plc.slmp_read | _communication_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 58 | communication.plc.slmp_write | _communication_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 59 | vision.geometry.point_components | _numeric_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 60 | vision.geometry.reframe_points | _geometry_bridges.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 61 | vision.preprocess.resize | resize/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 62 | vision.io.result_writer | result_writer/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 63 | vision.color.rgb_statistics | rgb_statistics/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 64 | vision.preprocess.roi | roi/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 65 | vision.preprocess.rotate | _transform_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 66 | vision.io.rtsp_camera | rtsp_camera/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 67 | vision.io.rtsp_capture | rtsp_capture/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 68 | vision.analysis.shape_measurement | _shape_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 69 | vision.io.simulated_camera | simulated_camera/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 70 | vision.io.sqlite_writer | sqlite_writer/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 71 | communication.tcp.client | _communication_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 72 | communication.tcp.receive_once | _communication_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 73 | communication.tcp.session | _managed_tcp.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 74 | communication.tcp.session_send | _managed_tcp.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 75 | vision.analysis.template_match | _detection_operators.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 76 | vision.preprocess.threshold | threshold/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 77 | vision.flow.select_value | value_select/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 78 | vision.state.variable_read | variable_read/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 79 | vision.state.variable_write | variable_write/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 80 | vision.io.video_loader | video_loader/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 81 | vision.inference.yolo_classification | yolo_classification/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |
| 82 | vision.inference.yolo | yolo_inference/operator.py | 待审查 | 待审查 | 待审查 | 待判定 | 待审查 |

## 每个算子的详细记录模板

- operatorId / 实际注册版本：
- Manifest / OperatorMeta：
- 执行入口及共享实现：
- 输入端口：key、type、required、中文名称、用途、现有摘要。
- 输出端口：key、type、中文名称、用途、单位、日志可见性。
- 配置参数：key、title、description、type、default、enum、constraints、sensitive。
- 生效参数：实际执行值来源、覆盖顺序、可否安全记录。
- metrics：key、实际赋值、统计口径、单位、重复来源、展示建议。
- diagnostics：key、实际含义、严重程度、敏感性、展示建议。
- 特殊执行结果（仅适用时）：算子专属失败、跳过或取消行为；通用生命周期行为引用 Runtime 统一审查结论。
- 缺失项：补充/不补/暂不可可靠取得；理由与证据。
- 最终说明覆盖与审查状态。

## 审查注意事项

- 不从 key 名称推断语义，必须追踪实际赋值和执行路径。
- metrics 不等于纯性能指标。
- 输出字段和 metrics 重复时记录两者来源关系。
- 不把通信 ACK、发送回执或相机请求确认解释成物理动作完成。
- 不把 PatchCore 相对热图解释成像素级缺陷判定。
- 不把 Annotate 的 drawnCount 解释成检测目标数。
- 不将默认参数当成实际生效参数，必须沿调用链确认。
- 共享模块中的多个 operatorId 分别审查。
- 无专用 metrics 的算子也必须完成审查并说明是否需要补充。
