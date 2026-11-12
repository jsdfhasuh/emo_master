# 二工位 0.75 匹第二拍：二值化和轮廓参数

## 范围和基线

- 平台源码 0.6.6，HEAD `51bff936ee2f2a0e4c3712441f8e622e33312c3c`，不是干净提交验收。
- 工作区原已有 ROI 编辑器、图像查看器、算子编辑器布局和插件注册相关未提交修改；本轮保留。
- 工程 `C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/two_station_screw_vision.emoproj`。
- 仅配置 `station2-075-holes`，没有将截图批量应用于其他路线，尤其未改二工位 0.75 匹第一拍 B 路的阈值 30 / 均值滤波 7 / 最大轮廓 / 矩形中心规则。

## 截图实际语义

对用户所用旧程序 `D:/GreeVisionMaster/main/main.exe` 内的 Python 3.11 模块进行只读静态反汇编，未启动旧程序或执行应用模块：

- `algorithm.threshold.ThresholdAlgorithm.Processing` 中，名为“自动阈值”的分支实际调用 `cv2.threshold(image, self.th, 255, BINARY)`；“固定阈值”分支反而调用 Otsu。
- 截图选择“自动阈值”、阈值 80、反二值，对应新平台 **fixed / 80 / invert=true**，不是 `otsu`。输出最大值 255；11 / C=2 保留为参数草稿，此模式不使用。
- `algorithm.findcontour` 使用 `len(contour) >= min_contour_points` 和闭区间面积筛选。列表 / 简单近似对应 `RETR_LIST / CHAIN_APPROX_SIMPLE`。
- 静态证据及旧 EXE SHA256 在 `C:/Users/dd109/my_work/emo_master/build/station2_075_threshold_contours_20261110/legacy-disassembly.txt`，生成脚本同目录 `inspect_legacy.py`。不以算法 JSON 的旧默认值代替实际代码证据。

## 最小实现和工程修改

- 现有 `vision.collection.filter` 升至 1.3.0，仅新增轮廓点数条件组与 `minContourPoints`，默认 0 不改变旧结果；沿用已有分组展示、校验、kept/rejected 集合和面积筛选。
- 二值化：`mode=fixed, threshold=80, invert=true, blockSize=11, constant=2`。
- 轮廓：`retrievalMode=list, approximation=simple`。
- 新增一个筛选节点：面积 50～10000，近似后的轮廓点数至少 10，再接原有最小外接圆测量。
- 工程依赖增加筛选算子 1.3.0；当前 45 工作流、357 节点、34 项算子依赖，格式保持 2.9 / 页面 1.2 / 设备 1.0。
- 原掩码、YOLO 模型和裁剪语义、测量子流程、原图坐标还原、标定、固定点、PLC/TCP、全局确认开关和输出保护均未改动。

## 验证

- 插件 / Runtime 定向回归：220 passed，包含点数 9/10/11、面积上下限、关闭条件、无效参数，以及二值化/轮廓结果与直接 OpenCV 计算对照。
- 原生 Qt 筛选界面回归：27 passed（offscreen 自动化，不是人工 UI 验收）。
- 实际工程从 Designer 的 `ProjectController.loadProjectDirectory` 打开并切换目标工作流，筛选参数表单有效、点数 10 可见：1 passed。使用非联网 Runtime 桩和临时 QSettings；未修改实际应用设置、未发布 Runtime。
- 45 个工作流编译通过、页面绑定检查通过、平台标准保存到临时文件并重读相等，再写回已备份的主工程，读回复核通过。保留历史 `.bak`，没有重跑历史迁移脚本。
- 本轮没有跑完整源码测试套件、冻结安装包、人工 UI 或现场设备验收。

## 备份和未完成项

- 本轮备份：`C:/Users/dd109/my_work/emo_master/build/station2_075_threshold_contours_20261110/before-threshold-contours.emoproj`，SHA256 `b4fa84ba19c22489ba8f6ec5faf621b46936e980c699241cb994d40b8e6daad3`。
- 修改后主工程 SHA256：`0bd8675187d28c6cbdd7aaed86c79feae1df91475d6518aebc4c4ac66912e561`。
- 配置、路径清单和 UI 自动化证据：同一 build 目录内 `verification.json`、`designer-open.json`。
- 已引用的 6 个 ONNX 和 9 份坐标文件均存在；文件存在不代表各路线实图验收完成。
- 尚缺真正 **0.75 匹第二拍**样图进行掩码 → YOLO → 二值化 → 轮廓筛选 → 圆心 → 坐标还原的完整实图验证。用户此前确认提供的是 1 匹原图，只作为掩码几何参考，不作为 0.75 匹通过或失败证据。
- 其他 10 条 YOLO 路线仍为原先固定阈值 127 / 外轮廓配置，待逐路确认，没有擅自套用本次参数。
- 现场完整两拍、PLC/机器人业务输出验收仍需单独进行，不据此宣称此前从未测试过相机或 PLC。
- 当前已打开的 Designer 进程若尚未载入新算子，需要正常关闭后重新启动；不强杀现场会话，不让旧窗口副本覆盖新工程。

## 后续状态更新

本文件以上为单条路线配置完成时的历史快照。用户随后确认相同参数也适用于其余 10 条 YOLO 路线，现已应用，最新工程哈希、样图诊断和剩余事项见 `C:/Users/dd109/my_work/emo_master/docs/testing/2026-11-10-all-yolo-common-image-recipe.md`。不再将“其余 10 条参数未确认”当作当前缺项。
