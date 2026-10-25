# 当前算子目录

本文件由 emo-master-operator-authoring skill 的 inspect_catalog.py 生成。开发流程和分类分析见 [算子与设备插件开发指南](operator-development-guide.md)。

扫描时间（UTC）：`2026-10-23T17:08:33.551146+00:00`。HEAD：`577450fc19c60ac86989475d76838e952bcc3e86`。
快照包含下列未提交工作区，不代表仅该提交或已发布 EXE 的状态。

范围：仓库 src/emo_master/plugins 下全部 manifest 和 device_types；不含 examples 或额外外部插件根。
AST 仅分析入口类及同文件基类，不完整追踪跨文件继承/helper；静态清单不是逐行安全审计、算法执行或硬件验收。

入口源码/manifest/设备声明文件哈希映射的 SHA-256：`5c8d0970252c64cdd35ca41b6c14b12ac8b93a4308b0905070e560fcceef42b3`。
该摘要不覆盖全部 helper、Runtime、测试或资源文件，不能代替完整测试源码快照。

## 数量

```json
{
  "operators": 77,
  "deviceTypes": 6,
  "entrySourceFiles": 54,
  "managedOperators": 8
}
```

## 扫描时工作区

```text
 M README.md
 M docs/development-guide.md
 M docs/operator-io-contracts.md
 M docs/plugin-registration-flow.md
 M docs/runtime-event-flow.md
 M pyproject.toml
 M requirements.txt
 M src/emo_master/apps/designer/controllers/layout_controller.py
 M src/emo_master/apps/designer/page_designer/chrome.py
 M src/emo_master/apps/designer/page_designer/tools.py
 M src/emo_master/apps/designer/page_designer/workspace.py
 M src/emo_master/apps/designer/ui/_lucide_data.py
 M src/emo_master/apps/designer/ui/designer_actions.py
 M src/emo_master/apps/designer/ui/floating_toolbox.py
 M src/emo_master/apps/designer/ui/main_window.py
 M src/emo_master/apps/designer/ui/node_context_menu.py
 M src/emo_master/apps/designer/ui/node_result_coordinator.py
 M src/emo_master/apps/designer/ui/workflow_navigation.py
 M src/emo_master/apps/runtime/devices/tcp.py
 M src/emo_master/apps/runtime/execution/operator_executor.py
 M src/emo_master/apps/runtime/jobs/worker_main.py
 M src/emo_master/apps/runtime/operator_debug/contracts.py
 M src/emo_master/apps/runtime/operator_debug/resources.py
 M src/emo_master/apps/runtime/workflow/runner.py
 M src/emo_master/core/workflow/compiler.py
 M src/emo_master/plugins/builtins/_communication_operators.py
 M src/emo_master/plugins/builtins/_operator_modes.py
 M src/emo_master/plugins/builtins/collection_select/operator.py
 M src/emo_master/plugins/builtins/detection_archive/operator.py
 M src/emo_master/plugins/builtins/yolo_inference/manifest.json
 M src/emo_master/plugins/builtins/yolo_inference/operator.py
 M tests/core/plugin/test_builtin_parameter_titles.py
 M tests/designer/test_designer_shortcuts.py
 M tests/designer/test_floating_toolbox.py
 M tests/designer/test_main_window_layout.py
 M tests/designer/test_node_image_viewer.py
 M tests/designer/test_visual_layout.py
 M tests/designer/test_yolo_parameter_presentation.py
 M tests/plugins/test_collection_select_dynamic_index.py
 M tests/plugins/test_communication_operators.py
 M tests/plugins/test_detection_archive_operator.py
 M tests/plugins/test_operator_modes.py
 M tests/plugins/test_yolo_inference_operator.py
 M tests/runtime/test_operator_executor_shared.py
 M tests/runtime/test_operator_registration.py
 M tests/runtime/test_runtime_workflow_architecture.py
 M tests/runtime/test_worker_capture_diagnostics.py
 M tests/runtime/test_yolo_inspection_outputs.py
 M tests/ui/page_designer/test_delivery_path.py
 M tests/ui/page_designer/test_page_navigation.py
 M tests/ui/page_designer/test_user_path.py
 M tests/ui/page_designer/test_visual_editor.py
 M tests/ui/page_designer/test_workspace_modes.py
?? docs/operator-catalog.md
?? docs/operator-development-guide.md
?? docs/patchcore-inference.md
?? docs/testing/2026-10-23-patchcore-highlight-validation.md
?? docs/testing/2026-10-23-patchcore-v1-validation.md
?? docs/testing/patchcore-2026-10-23/
?? docs/testing/patchcore-highlight-2026-10-23/
?? src/emo_master/apps/designer/page_designer/navigation.py
?? src/emo_master/apps/designer/ui/canvas_overlay.py
?? src/emo_master/apps/designer/ui/floating_run_panel.py
?? src/emo_master/plugins/builtins/patchcore_inference/
?? tests/core/plugin/test_operator_development_examples.py
?? tests/designer/test_floating_run_panel.py
?? tests/designer/test_patchcore_inference.py
?? tests/plugins/patchcore_fixture.py
?? tests/plugins/test_patchcore_highlight.py
?? tests/plugins/test_patchcore_inference_operator.py
?? tests/plugins/test_patchcore_numeric.py
?? tests/runtime/patchcore_fixture.py
?? tests/runtime/test_operator_error_propagation.py
?? tests/runtime/test_optional_output_execution.py
?? tests/runtime/test_patchcore_debug.py
?? tests/runtime/test_patchcore_workflow.py
?? tests/ui/page_designer/test_floating_workspace.py
```

## 算子索引

目录相对 src/emo_master/plugins/builtins；参数、端口和版本来自当前 manifest。
summary 是声明用途，不表示现场功能已验收。JSON 输出模式另含完整 PortSpec、schema、import、context 线索和文件哈希。

| 目录 | Operator / 版本 | 声明用途 | Entry | 输入 -> 输出 | 参数 | 设备槽 | 生命周期 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| absdiff | vision.image.absdiff @ 1.0.0 | 计算两张同尺寸、同通道图像的绝对差。 | emo_master.plugins.builtins._arithmetic_operators:AbsDiffOperator | imageA, imageB, frameA, frameB -> image, frame | (none) | (none) | (none declared/local) |
| add_weighted | vision.image.add_weighted @ 1.0.0 | 使用 alpha、beta 和 gamma 对两张图像执行饱和加权融合。 | emo_master.plugins.builtins._arithmetic_operators:AddWeightedOperator | imageA, imageB, frameA, frameB -> image, frame | alpha, beta, gamma | (none) | (none declared/local) |
| affine | vision.preprocess.affine @ 1.0.0 | 使用三组源点和目标点执行仿射变换并传播回源坐标。 | emo_master.plugins.builtins._transform_operators:AffineOperator | image, frame, validMask -> image, frame, validMask | srcPoints, dstPoints, outputWidth, outputHeight, interpolation, padValue | (none) | (none declared/local) |
| annotate | vision.render.annotate @ 1.1.0 | 在图像副本上统一绘制 Blob、Detection、轮廓、测量、直线、圆、模板匹配和 ROI。 | emo_master.plugins.builtins.annotate.operator:AnnotateOperator | image, frame, blobs, detections, contours, measurements, lines, circles, matches, roi -> overlay, frame, drawnCount | drawBBoxes, drawContours, drawCentroids, drawLabels, drawRoi, thickness, fontScale, blobColor, detectionColor, roiColor | (none) | (none declared/local) |
| apply_mask | vision.mask.apply @ 1.0.0 | 使用二值 Mask 保留图像区域，并以固定像素值填充区域外。 | emo_master.plugins.builtins._arithmetic_operators:ApplyMaskOperator | image, mask, frame, maskFrame -> image, frame | invert, fillValue | (none) | (none declared/local) |
| blob_analysis | vision.analysis.blob @ 1.2.0 | 从图像或外部二值掩码提取连通域，并输出 Blob 集合、过滤后掩码和可选叠加图。 | emo_master.plugins.builtins.blob_analysis.operator:BlobAnalysisOperator | image, mask, frame -> blobs, mask, overlay, frame | threshold, invert, useOtsu, connectivity, minArea, maxArea, includeContour, drawOverlay | (none) | (none declared/local) |
| blur | vision.preprocess.blur @ 1.1.0 | 执行 Gaussian、Median、Bilateral 或 Mean 平滑去噪。 | emo_master.plugins.builtins.blur.operator:BlurOperator | image, frame -> image, frame | mode, kernelSize, sigmaX, sigmaY, diameter, sigmaColor, sigmaSpace | (none) | (none declared/local) |
| canny_edge | vision.edge.canny @ 1.1.1 | 提取图像边缘并输出边缘图与叠加图。 | emo_master.plugins.builtins.canny_edge.operator:CannyEdgeOperator | image, frame -> edges, overlay, frame | thresholdLow, thresholdHigh, apertureSize, l2Gradient | (none) | (none declared/local) |
| clahe | vision.preprocess.clahe @ 1.0.0 | 对灰度图或 BGR 图像 LAB 色彩空间的 L 通道执行 CLAHE。 | emo_master.plugins.builtins._intensity_operators:ClaheOperator | image, frame -> image, frame | clipLimit, tileGridWidth, tileGridHeight | (none) | (none declared/local) |
| collection_count | vision.collection.count @ 1.1.0 | 统计 Blob 或 Detection 集合中的目标数量。 | emo_master.plugins.builtins.collection_count.operator:CollectionCountOperator | blobs, detections, contours, measurements, lines, circles, matches -> count | collectionType | (none) | (none declared/local) |
| collection_filter | vision.collection.filter @ 1.2.0 | 按类型字段及空间关系过滤强类型视觉集合。 | emo_master.plugins.builtins.collection_filter.operator:CollectionFilterOperator | blobs, detections, contours, measurements, lines, circles, matches, roi -> keptCount, rejectedCount, keptBlobs, rejectedBlobs, keptDetections, rejectedDetections, keptContours, rejectedContours, keptMeasurements, rejectedMeasurements, keptLines, rejectedLines, keptCircles, rejectedCircles, keptMatches, rejectedMatches | collectionType, areaEnabled, minArea, maxArea, perimeterEnabled, minPerimeter, maxPerimeter, circularityEnabled, minCircularity, maxCircularity, blobLabelsEnabled, blobLabels, contourEnabled, contourMode, confidenceEnabled, minConfidence, maxConfidence, classIdsEnabled, classIds, detectionLabelsEnabled, detectionLabels, geometryTypesEnabled, geometryTypes, holeEnabled, holeMode, sourceTypesEnabled, sourceTypes, lengthEnabled, minLength, maxLength, radiusEnabled, minRadius, maxRadius, qualityEnabled, minQuality, maxQuality, templateMethodsEnabled, templateMethods, spatialEnabled, spatialMode | (none) | (none declared/local) |
| collection_select | vision.collection.select @ 1.1.0 | 从强类型视觉集合中选择首项、末项、指定索引或 Top-K；按索引选择时可连接动态 index 输入。 | emo_master.plugins.builtins.collection_select.operator:CollectionSelectOperator | blobs, detections, contours, measurements, lines, circles, matches, index -> selectedBlobs, selectedDetections, selectedContours, selectedMeasurements, selectedLines, selectedCircles, selectedMatches, selectedCount | collectionType, mode, index, count, onOutOfRange | (none) | (none declared/local) |
| collection_sort | vision.collection.sort @ 1.1.0 | 按类型白名单字段稳定排序强类型视觉集合。 | emo_master.plugins.builtins.collection_sort.operator:CollectionSortOperator | blobs, detections, contours, measurements, lines, circles, matches -> sortedBlobs, sortedDetections, sortedContours, sortedMeasurements, sortedLines, sortedCircles, sortedMatches | collectionType, key, direction | (none) | (none declared/local) |
| color_convert | vision.preprocess.color_convert @ 1.0.0 | 在 BGR、RGB、GRAY、HSV 和 OpenCV uint8 LAB 之间转换颜色空间。 | emo_master.plugins.builtins.color_convert.operator:ColorConvertOperator | image, frame -> image, frame | sourceSpace, targetSpace | (none) | (none declared/local) |
| contour | vision.analysis.contour @ 1.0.0 | 从二值 Mask 提取多边形轮廓与完整层级集合。 | emo_master.plugins.builtins._shape_operators:ContourExtractionOperator | mask, frame -> polygons, contours, frame | retrievalMode, approximation | (none) | (none declared/local) |
| coordinate_calculator | vision.geometry.coordinate_calculator @ 1.2.0 | Measure, transform, add, subtract, dot, or scalar-multiply strongly typed point2d coordinates. | emo_master.plugins.builtins._coordinate_operators:CoordinateCalculatorOperator | points, otherPoints, scalar -> points, centroid, bounds, segmentLengths, pathLength, perimeter, signedArea, area, firstToLastDistance, firstToLastAngleDegrees, polygon, vectors, magnitudes, values, dotSum, resultCount | mode, pairing, scalarValue, closed, offsetX, offsetY, scaleX, scaleY, rotationDegrees, pivot, pivotX, pivotY | (none) | (none declared/local) |
| coordinate_message_format | communication.coordinate_message_format @ 1.0.0 | 坐标 round 后转文本，保留引号与后缀；默认兼容 LN5 正常 EY 报文。 | emo_master.plugins.builtins._managed_tcp:CoordinateMessageFormatOperator | x, y -> data | decimals, numberFormat, quote, suffix | (none) | (none declared/local) |
| coordinate_reader | vision.io.coordinate_reader @ 1.1.0 | Read TXT or CSV rows as strongly typed point2d coordinates. | emo_master.plugins.builtins._coordinate_operators:CoordinateReaderOperator | frame -> points, pointCount, contentHash, polygon | readMode, filePath, fileFormat, encoding, delimiter, headerMode, xColumn, yColumn, commentPrefix, onInvalidRow, maxPoints, maxFileBytes, asPolygon, sourceId, imageWidth, imageHeight, transformToSource | (none) | (none declared/local) |
| crop | vision.preprocess.crop @ 1.0.0 | 按参数或 BBox ROI 裁剪图像、添加常量边框，并保留回源坐标。 | emo_master.plugins.builtins.crop.operator:CropOperator | image, roi, frame -> image, frame | x, y, width, height, clip, paddingLeft, paddingTop, paddingRight, paddingBottom, padValue | (none) | (none declared/local) |
| detection_archive | vision.io.detection_archive @ 1.0.0 | 有检测目标时保存逐个裁剪图和整帧标注图，支持保存间隔。 | emo_master.plugins.builtins.detection_archive.operator:DetectionArchiveOperator | image, frame, detections, overlay -> result, saved, cropCount | outputPath, minIntervalMs | (none) | initOperator |
| detection_bbox | vision.geometry.detection_bbox @ 1.0.0 | 从明确选中的单个检测对象提取强类型轴对齐包围框，供测量区域裁剪。 | emo_master.plugins.builtins._geometry_bridges:DetectionBBoxOperator | detections -> bbox | (none) | (none) | (none declared/local) |
| empty_operator | vision.demo.empty @ 0.1.0 | 示例算子，输出基础结果用于流程联调。 | emo_master.plugins.builtins.empty_operator.operator:EmptyOperator | image -> result | enabled | (none) | disposeOperator, initOperator |
| equalize | vision.preprocess.equalize @ 1.0.0 | 均衡灰度图或 BGR 图像 LAB 色彩空间的 L 通道。 | emo_master.plugins.builtins._intensity_operators:EqualizeOperator | image, frame -> image, frame | (none) | (none) | (none declared/local) |
| file_list | vision.io.file_list @ 1.0.0 | 按文件名自然顺序列出 Runtime 文件夹中的文件路径，连接 ForEach 逐文件处理。 | emo_master.plugins.builtins.file_list.operator:FileListOperator | (none) -> paths, count | folderPath, patterns, recursive | (none) | (none declared/local) |
| flip | vision.preprocess.flip @ 1.0.0 | 水平、垂直或双向翻转图像并传播有效区域与回源坐标。 | emo_master.plugins.builtins._transform_operators:FlipOperator | image, frame, validMask -> image, frame, validMask | mode | (none) | (none declared/local) |
| flow_error | vision.flow.error @ 1.0.0 | 仅在依赖分支被选中时停止并报告技术错误，不实现业务 NG。 | emo_master.plugins.builtins.flow_error.operator:FlowErrorOperator | after -> (none) | code, message | (none) | (none declared/local) |
| flow_if | vision.flow.if @ 1.0.0 | 根据条件将输入分流到 true 或 false 分支。 | emo_master.plugins.builtins.flow_if.operator:FlowIfOperator | value -> true, false | mode, compareValue | (none) | (none declared/local) |
| flow_switch | vision.flow.switch @ 1.0.0 | 根据输入值命中 case 分支，未命中时走 default。 | emo_master.plugins.builtins.flow_switch.operator:FlowSwitchOperator | value -> case0, case1, case2, case3, default | case0Value, case1Value, case2Value, case3Value | (none) | (none declared/local) |
| gateway_ack_validate | communication.gateway.ack_validate @ 1.0.0 | 按本次请求校验正常网关 ACK，不仅匹配 OK 前缀；不确认设备动作完成。 | emo_master.plugins.builtins._gateway_coordinates:GatewayAckValidateOperator | request, response -> acknowledged | channel | (none) | (none declared/local) |
| gateway_coordinate_format | communication.gateway.coordinate_format @ 1.0.0 | 生成正常坐标双引号报文，不加换行或 EY；一个事务一个坐标对。 | emo_master.plugins.builtins._gateway_coordinates:GatewayCoordinateFormatOperator | points -> message, text, expectedAck | channel, plcRounding | (none) | (none declared/local) |
| geometry_points | vision.geometry.extract_points @ 1.0.0 | 提取圆心、指定矩形特征或明确的质心；不推测 Blob 内心。 | emo_master.plugins.builtins._geometry_bridges:GeometryPointsOperator | circles, measurements, bbox -> points, pointCount | inputType, feature | (none) | (none declared/local) |
| global_counter | vision.state.counter @ 1.0.0 | 按项目和名称持久化共享计数，可由任意布尔信号递增或复位。 | emo_master.plugins.builtins.global_counter.operator:GlobalCounterOperator | increment, reset -> count | name | (none) | (none declared/local) |
| hikvision_audio | communication.camera.hikvision_audio @ 1.0.0 | 通过海康 H8 ISAPI 播放内置或已上传音频；不修改全局音量和播放次数，不自动重试。 | emo_master.plugins.builtins.hikvision_audio.operator:HikvisionAudioOperator | trigger -> triggered, audioId, volume, alarmTimes | cameraUrl, username, password, passwordEnv, audioSource, audioId, customAudioId, maxVolume, maxAlarmTimes, timeoutMs | (none) | (none declared/local) |
| histogram | vision.analysis.histogram @ 1.1.0 | 统计灰度或 BGR 图像在 Frame、ROI 与 Mask 交集中的等宽直方图。 | emo_master.plugins.builtins.histogram.operator:HistogramOperator | image, frame, roi, mask, maskFrame -> histogram, frame | bins, normalization | (none) | (none declared/local) |
| hough_circle | vision.analysis.hough_circle @ 1.0.0 | 在灰度或 BGR 图像上使用 Hough Gradient 检测圆。 | emo_master.plugins.builtins._detection_operators:HoughCircleOperator | image, frame -> circles, frame | dp, minDist, param1, param2, minRadius, maxRadius | (none) | (none declared/local) |
| hough_line | vision.analysis.hough_line @ 1.0.0 | 在单通道边缘图上使用 HoughLinesP 检测有限线段。 | emo_master.plugins.builtins._detection_operators:HoughLineOperator | edge, frame -> lines, frame | rho, thetaDegrees, threshold, minLineLength, maxLineGap | (none) | (none declared/local) |
| huaray_camera | vision.io.huaray_camera @ 1.2.0 | 通过华睿 IMV SDK 直连相机，每次执行采集一帧并保留作业内连接。 | emo_master.plugins.builtins.huaray_camera.operator:HuarayCameraOperator | (none) -> image, frame, blockId, deviceTimestamp, actualExposureUs | selectionMode, ipAddress, cameraKey, userId, deviceIndex, triggerMode, triggerSource, triggerActivation, captureTimeoutMs, waitMode, sequencePolicy, retryCount, retryDelayMs, outputColor, demosaic, exposureMode, exposureUs, gainMode, gainRaw, frameRateMode, frameRate, roiMode, width, height, offsetX, offsetY, pixelFormat | camera: device.camera.huaray_imv (capture) | disposeOperator, initOperator |
| image_batch_loader | vision.io.image_batch_loader @ 1.0.0 | 每次执行读取文件夹中的下一张图片，输出 hasNext 用于 While 循环退出。 | emo_master.plugins.builtins.image_batch_loader.operator:ImageBatchLoaderOperator | reset -> image, frame, imagePath, index, total, hasNext | folderPath, colorMode, recursive | (none) | disposeOperator |
| image_loader | vision.io.image_loader @ 1.1.0 | 从本地路径读取图片，并输出 image 与带源路径标识的 frame。 | emo_master.plugins.builtins.image_loader.operator:ImageLoaderOperator | (none) -> image, frame | imagePath, colorMode | (none) | (none declared/local) |
| image_saver | vision.io.image_saver @ 1.1.0 | 将输入图像写出到本地路径，可通过 fileName 输入逐张指定保存文件名。 | emo_master.plugins.builtins.image_saver.operator:ImageSaverOperator | image, fileName -> result | outputPath, overwrite | (none) | (none declared/local) |
| in_range | vision.segment.in_range @ 1.0.0 | 按 GRAY、BGR、RGB、HSV 或 OpenCV uint8 LAB 通道范围生成 0/255 掩码。 | emo_master.plugins.builtins.in_range.operator:InRangeOperator | image, frame -> mask, frame | colorSpace, lower, upper | (none) | (none declared/local) |
| integer_add | vision.math.integer_add @ 1.0.0 | 整数相加；输入优先于默认值，可用于 While 局部索引，不读写全局变量或数据库。 | emo_master.plugins.builtins.integer_add.operator:IntegerAddOperator | left, right -> value | leftValue, rightValue | (none) | (none declared/local) |
| mask_logic | vision.mask.logic @ 1.0.0 | 对一个或两个同尺寸二值掩码执行 AND、OR、XOR、NOT 或差集。 | emo_master.plugins.builtins.mask_logic.operator:MaskLogicOperator | maskA, maskB, frameA, frameB -> mask, frame | operation | (none) | (none declared/local) |
| minimum_enclosing_circle | vision.analysis.minimum_enclosing_circle @ 1.0.0 | 逐轮廓计算最小外接圆，保持来源和坐标空间；不是 Hough 圆或质心。 | emo_master.plugins.builtins._geometry_bridges:MinimumEnclosingCircleOperator | contours -> circles, circleCount | (none) | (none) | (none declared/local) |
| morphology | vision.preprocess.morphology @ 1.0.0 | 对二值掩码执行腐蚀、膨胀、开闭、梯度、顶帽或黑帽运算。 | emo_master.plugins.builtins.morphology.operator:MorphologyOperator | mask, frame -> mask, frame | operation, kernelShape, kernelSize, iterations | (none) | (none declared/local) |
| number_absolute | vision.math.absolute @ 1.0.0 | 输出有限数值的绝对值，不改变倍率或取整。 | emo_master.plugins.builtins._numeric_bridges:NumberAbsoluteOperator | value -> value | (none) | (none) | (none declared/local) |
| number_compare | vision.compare.number @ 1.0.0 | 比较两个有限数值并输出布尔结果；未连接 right 时使用参数阈值。 | emo_master.plugins.builtins.number_compare.operator:NumberCompareOperator | left, right -> result | operator, rightValue | (none) | (none declared/local) |
| number_exact_integer | vision.math.exact_integer @ 1.0.0 | 只转换本身为整数的有限数值；含小数报错，不截断或四舍五入。 | emo_master.plugins.builtins._numeric_bridges:NumberExactIntegerOperator | value -> value | (none) | (none) | (none declared/local) |
| number_pair | vision.collection.number_pair @ 1.0.0 | 按 first、second 顺序组装数值列表，保持数值和整数类型，不自动转换。 | emo_master.plugins.builtins._numeric_bridges:NumberPairOperator | first, second -> values | (none) | (none) | (none declared/local) |
| number_truncate | vision.math.truncate @ 1.0.0 | 将有限数值向零截断为整数；不四舍五入，拒绝布尔值、文本和非有限值。 | emo_master.plugins.builtins._numeric_bridges:NumberTruncateOperator | value -> value | (none) | (none) | (none declared/local) |
| number_value | vision.value.number @ 1.0.0 | 输出一个有限数值，可作为比较阈值或其他数值参数。 | emo_master.plugins.builtins.number_value.operator:NumberValueOperator | (none) -> value | value | (none) | (none declared/local) |
| patchcore_inference | vision.inference.patchcore @ 1.1.0 | 使用 CPU 加载 Vision Workshop DINO ONNX 完整部署包，输出原始分数、OK/NG、相对热图及可选疑似缺陷高亮。 | emo_master.plugins.builtins.patchcore_inference.operator:PatchCoreInferenceOperator | image, frame -> score, threshold, isAnomaly, label, heatmap, overlay, anomalyMap, frame | modelPath, drawHeatmap, drawOverlay, overlayMode, highlightThreshold, outputAnomalyMap | (none) | disposeOperator, initOperator |
| perspective | vision.preprocess.perspective @ 1.0.0 | 把源四边形校正为指定尺寸矩形并输出精确 Homography 回源坐标。 | emo_master.plugins.builtins._transform_operators:PerspectiveOperator | image, frame, validMask -> image, frame, validMask | srcPoints, outputWidth, outputHeight, interpolation, padValue | (none) | (none declared/local) |
| plc_slmp_read | communication.plc.slmp_read @ 1.1.0 | 通过三菱 SLMP/MC 3E Binary 或 ASCII 报文有界读取 D/M 设备数据。 | emo_master.plugins.builtins._communication_operators:PlcSlmpReadOperator | (none) -> values, numberValue, booleanValue | frameEncoding, host, port, device, startAddress, connectTimeoutMs, responseTimeoutMs, retryCount, retryDelayMs, networkNo, pcNo, moduleIoNo, moduleStationNo, monitoringTimer, dataType, count, outputIndex | plc: device.plc.mitsubishi_slmp (read) | (none declared/local) |
| plc_slmp_write | communication.plc.slmp_write @ 1.0.0 | 通过三菱 SLMP/MC 3E Binary 或 ASCII 报文写入 D/M 字设备数据。 | emo_master.plugins.builtins._communication_operators:PlcSlmpWriteOperator | data, value, values -> receipt | frameEncoding, host, port, device, startAddress, connectTimeoutMs, responseTimeoutMs, retryCount, retryDelayMs, networkNo, pcNo, moduleIoNo, moduleStationNo, monitoringTimer, dataType, values | plc: device.plc.mitsubishi_slmp (write) | (none declared/local) |
| point_components | vision.geometry.point_components @ 1.0.0 | 拆分单个点的 X/Y 原始数值；不改变坐标系或单位，空点和多点报错。 | emo_master.plugins.builtins._numeric_bridges:PointComponentsOperator | points -> x, y | (none) | (none) | (none declared/local) |
| points_reframe | vision.geometry.reframe_points @ 1.0.0 | 按同一来源的变换映射点到目标图像空间，不进行单位转换。 | emo_master.plugins.builtins._geometry_bridges:PointsReframeOperator | points, frame -> points | (none) | (none) | (none declared/local) |
| resize | vision.preprocess.resize @ 1.0.0 | 拉伸、等比缩放或 Letterbox 图像，并输出可回源的有效内容 frame。 | emo_master.plugins.builtins.resize.operator:ResizeOperator | image, frame -> image, frame | width, height, mode, interpolation, padValue | (none) | (none declared/local) |
| result_writer | vision.io.result_writer @ 1.1.0 | 将强类型视觉结果或基础值安全写入 Job 工作区的 JSON、JSONL 或 CSV 文件。 | emo_master.plugins.builtins.result_writer.operator:ResultWriterOperator | blobs, detections, contours, measurements, histogram, lines, circles, matches, statistics, geometry, numberValue, booleanValue, stringValue -> result | inputType, format, relativePath, overwrite | (none) | (none declared/local) |
| rgb_statistics | vision.color.rgb_statistics @ 1.1.0 | 计算整图或几何 ROI 内的 RGB 最小值、最大值、均值和标准差。 | emo_master.plugins.builtins.rgb_statistics.operator:RgbStatisticsOperator | image, roi, frame -> statistics, mask, frame | ddof, clipRoi, emitMask | (none) | (none declared/local) |
| roi | vision.preprocess.roi @ 1.1.0 | 按 BBox、旋转框或多边形提取紧致区域与原尺寸遮罩，并保留回源坐标。 | emo_master.plugins.builtins.roi.operator:RoiOperator | image, frame -> croppedImage, croppedMask, croppedFrame, maskedImage, fullMask, maskedFrame, roi | roiType, x, y, width, height, centerX, centerY, angleDegrees, points, padValue, interpolation | (none) | (none declared/local) |
| rotate | vision.preprocess.rotate @ 1.0.0 | 按图像坐标系顺时针旋转图像并传播有效区域与回源坐标。 | emo_master.plugins.builtins._transform_operators:RotateOperator | image, frame, validMask -> image, frame, validMask | angleDegrees, expand, interpolation, padValue | (none) | (none declared/local) |
| rtsp_camera | vision.io.rtsp_camera @ 1.0.0 | 每次连接 RTSP 流读取一张当前画面，完成或取消后释放连接。 | emo_master.plugins.builtins.rtsp_camera.operator:RtspCameraOperator | (none) -> image, frame, capturedAt | rtspUrl, username, password, passwordEnv, openTimeoutMs, readTimeoutMs, colorMode | (none) | (none declared/local) |
| shape_measurement | vision.analysis.shape_measurement @ 1.0.0 | 测量轮廓或 Blob 的面积、周长、质心、圆度及最小外接矩形。 | emo_master.plugins.builtins._shape_operators:ShapeMeasurementOperator | contours, blobs -> measurements | inputType | (none) | (none declared/local) |
| simulated_camera | vision.io.simulated_camera @ 1.0.0 | 通过 Runtime 设备队列生成模拟图像，不访问真实相机。 | emo_master.plugins.builtins.simulated_camera.operator:SimulatedCameraOperator | (none) -> image, frame | (none) | camera: device.simulated.camera (capture) | (none declared/local) |
| sqlite_writer | vision.io.sqlite_writer @ 1.0.0 | 通过字段映射将同次流程输出写入明确选择的本地 SQLite 业务表。 | emo_master.plugins.builtins.sqlite_writer.operator:SqliteWriterOperator | enabled -> receipt | configVersion, databasePath, debugDatabasePath, table, mappings, failurePolicy | (none) | (none declared/local) |
| tcp_client | communication.tcp.client @ 1.2.0 | 执行一次有界 TCP 发送或请求响应事务，支持显式消息分帧。 | emo_master.plugins.builtins._communication_operators:TcpClientOperator | message, text -> response, sentBytes, responseText | operation, text | tcp: device.tcp.client_once (send, exchange) | (none declared/local) |
| tcp_receive_once | communication.tcp.receive_once @ 1.1.0 | 在有界超时内监听并接收一个 TCP 客户端的一条完整消息。 | emo_master.plugins.builtins._communication_operators:TcpReceiveOnceOperator | (none) -> message, peerHost, peerPort, text | (none) | tcp: device.tcp.receive_once (receive) | (none declared/local) |
| tcp_session | communication.tcp.session @ 1.0.0 | 取得当前轮次的通道会话；请放在图像采集或结果计算之前，不创建监听。 | emo_master.plugins.builtins._managed_tcp:TcpSessionOperator | (none) -> session | (none) | tcp: device.tcp.server (acquire_session) | (none declared/local) |
| tcp_session_send | communication.tcp.session_send @ 1.0.0 | 仅向输入会话的原连接发送；无自动重试，回执不表示设备执行完成。 | emo_master.plugins.builtins._managed_tcp:TcpSessionSendOperator | session, data -> receipt | (none) | tcp: device.tcp.server (send) | (none declared/local) |
| template_match | vision.analysis.template_match @ 1.0.0 | 对第二个图像端口提供的模板执行单尺度匹配、局部峰值提取与 NMS。 | emo_master.plugins.builtins._detection_operators:TemplateMatchingOperator | image, template, frame, roi -> matches, frame | method, colorMode, thresholdMode, threshold, peakKernelSize, nmsIouThreshold, maxMatches, label | (none) | (none declared/local) |
| threshold | vision.preprocess.threshold @ 1.1.0 | 将 uint8 灰度或 BGR 图像转换为可复用的 0/255 二值掩码。 | emo_master.plugins.builtins.threshold.operator:ThresholdOperator | image, frame -> mask, threshold, frame | mode, threshold, invert, blockSize, constant | (none) | (none declared/local) |
| value_select | vision.flow.select_value @ 1.0.0 | 根据布尔条件输出 trueValue 或 falseValue；选中的输入缺失时报错，不回退到另一分支。只选择数据，不控制上游执行。 | emo_master.plugins.builtins.value_select.operator:ValueSelectOperator | condition, trueValue, falseValue -> value | (none) | (none) | (none declared/local) |
| variable_read | vision.state.variable_read @ 1.0.0 | 读取项目常量或当前任务可见的全局变量。 | emo_master.plugins.builtins.variable_read.operator:ReadVariableOperator | (none) -> value | variableId | (none) | (none declared/local) |
| variable_write | vision.state.variable_write @ 1.1.0 | 对全局变量赋值、原子自增或恢复初始值；自增和复位必须连接 after 执行依赖。 | emo_master.plugins.builtins.variable_write.operator:WriteVariableOperator | value, after -> value | variableId, operation, delta | (none) | (none declared/local) |
| video_loader | vision.io.video_loader @ 1.0.0 | 按视频时间每隔 X 秒取一帧，每次执行输出一张图片，用 hasNext 结束 While 循环。 | emo_master.plugins.builtins.video_loader.operator:VideoLoaderOperator | videoPath, reset -> image, frame, videoPath, index, frameIndex, timestampSec, hasNext | videoPath, intervalSec, colorMode | (none) | disposeOperator |
| yolo_inference | vision.inference.yolo @ 1.4.0 | 使用 ONNX Runtime CPU 执行 YOLOv8/YOLO11 目标检测，按置信度降序输出检测集合，可选择目标输出检测框裁剪图。 | emo_master.plugins.builtins.yolo_inference.operator:YoloInferenceOperator | image, frame -> detections, overlay, croppedImage, croppedFrame, frame | modelPath, confidence, iou, imageSize, maxDetections, device, classes, agnosticNms, drawOverlay, outputCrop, cropTargetIndex, onMissingCrop | (none) | (none declared/local) |

## 设备类型

| Type / 版本 | 声明文件 | Driver | Scheduling / Lifetime | Actions |
| --- | --- | --- | --- | --- |
| device.camera.huaray_imv @ 1.0.0 | src/emo_master/plugins/builtins/huaray_camera/device_types/imv.json | emo_master.apps.runtime.devices.imv_host:ManagedImv | serial / persistent | capture |
| device.plc.mitsubishi_slmp @ 1.0.0 | src/emo_master/plugins/builtins/plc_slmp_read/device_types/slmp.json | emo_master.apps.runtime.devices.slmp:ManagedSlmp | serial / persistent | read, write |
| device.simulated.camera @ 1.0.0 | src/emo_master/plugins/builtins/simulated_camera/device_types/camera.json | emo_master.apps.runtime.devices.simulated:SimulatedCamera | serial / persistent | capture |
| device.tcp.client_once @ 1.0.0 | src/emo_master/plugins/builtins/tcp_client/device_types/client_once.json | emo_master.apps.runtime.devices.tcp:ManagedTcpClientOnce | serial / per_operation | send, exchange |
| device.tcp.receive_once @ 1.0.0 | src/emo_master/plugins/builtins/tcp_receive_once/device_types/receive_once.json | emo_master.apps.runtime.devices.tcp:ManagedTcpReceiveOnce | serial / per_operation | receive |
| device.tcp.server @ 1.0.0 | src/emo_master/plugins/builtins/tcp_session/device_types/server.json | emo_master.apps.runtime.devices.tcp:ManagedTcpServer | managed_service / persistent | acquire_session, send |

## 注册检查

```json
{
  "coreVersion": "0.6.1",
  "activeOperatorCount": 77,
  "deviceTypeCount": 6,
  "rejectedOperators": {},
  "rejectedDeviceTypes": {},
  "assetIssues": {},
  "limits": "Imports registered operator modules; does not instantiate operators or execute devices. It does not validate driver methods or prove hardware support."
}
```
