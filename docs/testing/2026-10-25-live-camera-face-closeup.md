# 采集画面与人脸特写分离预览

## 范围与基线

- 源码 HEAD：`9265547dbe61bc57542d3cf0ea143fcf455600fb`，在已有未提交工作区上继续局部修改；未提交、推送或打包。
- 用户要求：左侧持续显示采集画面，右侧显示检测框裁剪的人脸特写，而不是另一张整图或标注图。
- 沿用 PySide2 / QWidget、托管 RTSP 驱动和现有 YOLO 裁剪输出。没有增加第二个相机连接，也没有修改模型算法。

## 实现

- Dispatch 增加可选 `queueFullPolicy=skip`。任一目标队列满时跳过整次扇出，返回 `SKIPPED`、`accepted=false`，不保留这次投递的图像/周期资源、不等待、不重放；消费者不可用仍报错。默认 `error` 和已有 `wait` 行为不变。编辑器和队列明细提供对应选项与累计跳过数量。
- 同页可绑定一个已声明的生产/消费入口。Runtime 分别冻结各自的采集计划，Designer 验证工程、Runtime、运行组及 Job 关系后复用两个只读 DisplaySession。图像需求、冻结租约及关闭仍交给原有会话处理；组合视图限制为两个图像来源，固定结果的总字节预算仍不超过 16 MiB。
- 两侧结果独立更新，不承诺来自同一帧。一侧连接失效只清除该侧；停止查看不停止执行。
- 图像行跨度获得伸展空间，避免人脸特写被数量/状态区域挤小。自定义空态仅用于可选输出缺失/分支跳过，执行失败和导出失败仍显示错误。

## 独立工程副本

`manual_test_workspace/rtsp-live-face-pages-20261025/engineering/RTSP Live Face Closeup.emoproj`

- 保留完整 2.7 结构、设备目录、资源及模型；未改写原在线工程。
- 左图绑定 `main/camera_loop/capture -> camera_acquire.live_preview.image`，显示校正后的完整采集图，预览尺寸限制在 960×540 内。
- 右图绑定 `camera_once/detect -> detect.face_preview.image`。YOLO 开启 `outputCrop=true`、`cropTargetIndex=1`、`onMissingCrop=skip`，裁剪预览尺寸限制在 480×480 内。
- 多个人脸时显示本次置信度最高的一张。无脸时清空特写并显示“本次未检出人脸”，不把历史人脸伪装为当前结果。
- 此副本启用忙时跳过检测，允许抽帧；必须逐帧处理的工程应继续使用等待模式。
- 生成器：`manual_test_workspace/design_live_face_pages.py`。通过 `ProjectDocument`、当前插件声明、`saveProject()` 保存后重读、绑定校验及工作流编译。
- 原在线文件 SHA256 前后均为 `e574c60b8c3fd0ba7a143c65bbd4402e86e3c420f0ab01f72a149d8d5df25c53`。

## 验证结果

证据目录：`manual_test_workspace/rtsp-live-face-pages-20261025/`。

- `focused-tests.xml`：104 通过，覆盖 Dispatch 等待/跳过/取消、资源释放、编辑器保存重开和流程包、作用域投影、来源身份、原有单 Job 查看、双 Job 查看和图像刷新。
- 双 Job 原生 Qt 测试分别从生产 Job 和消费 Job 接入。暂停消费取队列时，采集侧至少显示 6 个结果、消费完成数仍为 0；放行一次消费后，左侧再更新至少 5 次，右侧仍保持同一次检测身份。也验证单侧观察连接异常、有限租约及关闭观察后业务仍运行。
- `final-ui-tests.xml`：后续 39 通过，包含图像跨度布局、可选输出空态不吞掉失败、组合视图预算以及异常数据测试的单项复测；与上面存在重复，不合计成独立用例总数。
- `final/report.json`：通过实际 `MainWindow.loadProjectDirectory()` 打开入口及 `PreviewController.connect()` 查看真实运行结果。真实 ONNX 离线样例检出 1 张人脸，置信度约 0.76171，原始裁剪宽 92、高 121，逐像素等于原图检测区域，显示图与该裁剪的限定尺寸缩放一致。
- 无脸样例正常完成，数量为 0，裁剪分支 `BRANCH_SKIPPED`，右图清空并显示空态。
- 原生 Windows Qt 1280×800 / 1024×768 截图已检查，无水平或垂直滚动条，特写大于图像区域高度的 60%，统计区域小于 40%。

## 首次失败与未验证边界

- 开发中的第一次双 Job 集成测试暴露共享页没有登记消费采集计划；同时纠正了测试元数据对生产 `source_job_id` 的假设。修正 Runtime 的 capture 投影入口及对应身份条件后，生产/消费两个接入测试均通过。
- `attempt-1` 的辅助像素比对脚本使用浮点框直接切片失败；改为与裁剪算子一致的 floor/ceil 和图像边界裁切。
- `attempt-2` 中，GUI 辅助进程直接加载 ONNX 报 `E_BACKEND_UNAVAILABLE`；独立导入确认版本 1.23.2 可用，Runtime 业务 worker 已完成推理。后续参考推理移到独立进程，`attempt-3` 与 `final` 通过。未声称定位或修复 GUI 进程直接导入的根因。
- `capture-regression.xml`：扩展回归为 **60 通过、1 失败**。`testRealMalformedJsonInvalidatesWindow` 在批量运行中，连接已为 `INVALID_RESULT`、`displayed` 已空，但数量标签仍为旧值。随后同断言单项复测通过，未修改或弱化该测试。保留首次失败，原因未定位，不把扩展回归写成全绿。
- 当前已有用户工程进程运行；本轮没有停止它、没有发布在线配置、没有连接真实相机。上面的连续采集证据使用模拟设备，不是现场 RTSP 帧率验收，不能据此承诺 25/30 FPS。
- 组合预览本轮仅接入 Designer 的只读页面入口；未扩展独立 Operator Runtime 的生产页面。
- 未运行全仓库测试、EXE 冻结测试、重新打包或部署。已运行的旧进程不会自动加载这些源码变更。
