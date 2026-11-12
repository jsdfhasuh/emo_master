# 二工位 1 匹：合并轮廓点集后求一个圆

## 需求与实施边界

用户确认孔被亮线分成的三个轮廓应作为同一个孔测量，要求合并后再求圆。此前已说明现有最小外接圆算子逐轮廓输出，需增加显式合并选项；用户随后明确要求实现。

- 平台源码 0.6.6，HEAD `51bff936ee2f2a0e4c3712441f8e622e33312c3c`。验证工作区包含既有 ROI 编辑器、图像查看器、布局、注册及前轮轮廓筛选等未提交修改，不是该提交干净版本或冻结安装包验收。
- 工程：`C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/two_station_screw_vision.emoproj`。
- 仅调整二工位 1 匹第一拍 B 路与第二拍孔测量。二拍从原质心选择切换为本次明确要求的合并圆心；其他路线的测量方式不变。
- 未重建工程、未重跑历史迁移脚本；未访问相机、PLC、机器人，未发布 Runtime 或解除输出保护。

## 算子实现

`vision.analysis.minimum_enclosing_circle` 升至 1.1.0，复用原 `cv2.minEnclosingCircle`：

- `scope=perContour` 为默认，每条轮廓分别求圆，保留旧行为、圆 ID 和坐标空间。
- `scope=allContours` 合并当前输入集合的所有轮廓顶点，一次求出一个最小外接圆；不是平均多个圆心，不构造自交多边形，不改变原二值图，不使用霍夫圆或最小二乘拟合。
- 输入经过已有筛选，当前集合由同一个 YOLO 目标裁剪得到；不跨拍、不跨图像合并，也不自动判断多轮廓是否属于同一物理目标。
- 不硬编码必须三个轮廓。一个轮廓仍求一个圆；空集合输出零个圆，不伪造圆心。
- 保留全部来源轮廓 ID，沿用原裁剪坐标空间，再由原 `reframe` 节点只还原一次到原图。

源码：`C:/Users/dd109/my_work/emo_master/src/emo_master/plugins/builtins/_geometry_bridges.py`，对应 manifest 与算子目录/契约文档同步更新。

## 工程配置

- `station2-1hp-b/measure` 启用 `scope=allContours`；后续圆心提取与原图坐标还原不变。
- 新增普通测量子流程 `measure-merged-enclosing-circle`，由合并最小外接圆与圆心提取组成；`station2-1hp-holes/points` 指向该子流程。
- 原 `measure-centroid` 与 `measure-enclosing-circle` 子流程保留，未全局修改共享模板；仍可在 Designer 选择测量子流程。旧节点已保存的空参数快照保留，不为新增选项批量改写其他节点。
- 沿用前轮全部阈值与轮廓筛选参数。模型、ROI、坐标文件、标定、PLC/TCP、运行入口及保护变量不变。
- 工程保持 schema 2.9 / 页面 1.2 / 设备 1.0；现在 46 个普通工作流、371 个节点，仍只有两个工位运行入口。

## 验证

- 插件和 Runtime 定向回归：191 passed，涵盖默认兼容、合并全部顶点而非选第一个/平均圆心、一个/空集合、无效参数、来源不变与裁剪坐标还原；未运行完整源码套件。
- 本轮改动源码与测试的 Ruff 检查、仓库默认 mypy 检查通过。额外启用 `--check-untyped-defs` 后，未改动的 `GeometryPointsOperator` 有 4 条类型诊断；对修改前源码备份设置 `MYPYPATH=src` 后重跑得到相同 4 条，属于既有问题，严格检查未通过，本轮未扩大修改范围。
- 原生 Qt 自动化：1 passed。使用 Designer 实际草稿打开入口检查两个合并选项、二拍测量子流程，以及新节点默认逐轮廓的表单。Runtime 为非联网桩，QSettings 使用临时文件；不是人工 UI 或冻结安装包验收。
- 当前插件扫描通过；46 个工作流编译、页面绑定校验、平台标准保存到临时文件再重读/重编译相等。恢复执行时仅只读核对，没有重复应用工程修改或覆盖历史备份。
- 精确比较备份：仅两个已有工作流变化，另有一个新增普通测量子流程。其余 43 个已有工作流不变；全部阈值、筛选、模型、掩码、坐标还原、设备、参考点及保护变量保持。

### 对应 1 匹实图的离线结果

| 路线 | 参考图 | 筛后轮廓 | 圆/圆心数量 | 原图圆心（像素） |
| --- | --- | ---: | ---: | --- |
| 二工位第一拍 B | `D:/2026_11_8_data/B/2026-11-06_17-29-20-199.jpg` | 3 | 1 | (1774.4175, 712.9539) |
| 二工位第二拍孔 | `D:/2026_11_8_data/B/2026-11-06_17-29-21-190.jpg` | 3 | 1 | (1777.8160, 716.6680) |

两圆分别与直接汇总所有顶点的 OpenCV 结果一致，并包含所有保留顶点。第一拍 A/B 单点减法通过；第二拍使用工程中 D800=2 的已确认参考点 (791,549)，原单点减法也通过。没有放宽单点契约或执行任何设备写入。以上不代表标定精度、机器人最终位置或现场两拍验收通过。

验证脚本最初暴露的是辅助代码问题：手工解析坐标文本未处理注释，现已复用平台 Coordinate Reader；旧空 schema 快照没有新字段、`SchemaParamForm` 构造器无 parent 参数，现按实际契约检查原快照保留和新表单默认。未通过修改平台行为或削弱单点检查绕过这些问题；修正后图像与原生 Qt 验证均重新通过。

## 备份与证据

目录：`C:/Users/dd109/my_work/emo_master/build/merged_contour_circle_20261110/`。

- `before-merged-circle.emoproj`：修改前 SHA256 `319e27af871ccadb3bbbb3e2fca2e95fafadbc5c3c959e07a50b45e6e4f74398`。
- 修改后工程 SHA256 `5e3dcd3f501274bded32386160b55cb147f9b561ec15b313bd205718e0e81272`。
- 修改前源码和 manifest 已备份为 `before_geometry_bridges.py.txt`、`before_minimum_circle_manifest.json`。
- `verification.json`、`final-verification.json`：固定 HEAD、工作区、精确修改范围、编译与保存重读记录。
- `image-verification.json`、`verify_images.py`：两张样图及单点减法验证。
- `designer-open.json`、`test_designer_open.py`：原生 Qt 草稿打开和参数检查。
- `merged-circle-comparison.png`：左为第一拍 B，右为第二拍；绿线为三个保留轮廓，红圈/十字为共同最小外接圆与圆心。

下一步由用户核对红圈是否覆盖所需整体孔。0.75 匹对应样图、现场完整两拍和真实输出验收仍单独待办。已打开的 Designer/Runtime 进程需正常重启加载新源码算子；本轮不强杀会话，不更新冻结安装包，不让旧窗口副本覆盖新工程。
