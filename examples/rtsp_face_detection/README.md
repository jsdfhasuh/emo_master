# RTSP 人脸检测工程生成器

生成最新 2.7 格式的独立本地工程，不在仓库内保存现场密码、真实照片或模型权重。
使用原生 While、RTSP、Flip、YOLO、集合计数、数值比较和检测留档算子。

## 资源

先在交付目录准备以下文件，再运行生成器：

- `models/yolov8n-face-detect.onnx`：纯检测 ONNX，单类别 `face`，不带内置 NMS。
- `samples/astronaut.png`：有脸离线样例。
- `samples/empty.png`：无脸离线样例。

已验证模型来自 [deepghs/yolo-face](https://huggingface.co/deepghs/yolo-face)，
revision `e3662574830c534dfcc9c3b7ea4d89272f8aae4e`，路径 `yolov8n-face/model.onnx`。
SHA-256：`fd27189bfe5750a017648445700473459a6d02e7c3b0a3bfd8a54af77dd3b046`。
模型元数据标为 AGPL-3.0；镜像另有分发免责声明，上游仓库 LICENSE 为 GPL-3.0。
不要将其描述为无条件商用授权，分发、商用前另行核对适用许可。

上游 GitHub 1.0.0 release 当前同名文件是 `pose` + 内置 NMS 导出，
不是这里的检测模型。不要只凭文件名互换，更不能删除 metadata 伪装兼容。

## 使用

在可导入本仓库源码的 Python 3.10 环境中运行：

```powershell
python examples/rtsp_face_detection/build_project.py D:/private/face-demo --url rtsp://camera-address:554/Streaming/Channels/101 --store-password
python examples/rtsp_face_detection/run_project.py --project D:/private/face-demo/rtsp-face-detection.emoproj --workflow sample
python examples/rtsp_face_detection/run_project.py --project D:/private/face-demo/rtsp-face-detection.emoproj --workflow empty
# The following command explicitly reads the configured real camera.
python examples/rtsp_face_detection/run_project.py --project D:/private/face-demo/rtsp-face-detection.emoproj --workflow main
```

`--store-password` 通过隐藏输入收集密码并写入工程。不使用此选项时，工程引用
`EMO_RTSP_CAMERA_PASSWORD` 环境变量。含密码的工程及运行快照均按私有文件处理。

主流程默认通过 Flip 的 `both` 模式校正倒装摄像头，等价于 180 度旋转。
仅上下镜像用 `vertical`，左右镜像用 `horizontal`；普通正装摄像头可移除 Flip 节点并重接图像和 frame。
检测子流程共用唯一模型和阈值，离线样例不翻转。
摄像头检测和保存保留完整分辨率；两个独立 Resize 分支仅为结果页生成
最大 960×540 等比预览，减少连续运行时的导出负担，避免 1440p 原始像素超过展示通道预算
（每 Job 总计 8 MiB；单图 8 MiB，双图时各 4 MiB）。

`main` 是不限次数、无总超时的 While v2；循环体 `camera_tick` 调用 `camera_once`，
直到点击停止或命令行 Ctrl+C。`camera_once` / `sample` / `empty` 输出图像、人脸框、
`faceCount`、`hasFace` 和 `archive` 留档回执。结果页绑定到每轮 camera_once，运行期间即可刷新。
无脸不保存；有脸时由 `vision.io.detection_archive` 保存每个人脸 PNG 裁剪和完整标注 JPG。
保存目录分别是 `outputs/camera`、`outputs/sample`；无脸反例不会创建输出目录。
文件名包含 UTC 时间、会话标记、序号，不覆盖历史图片。默认每 2000ms 最多保存一组，
不是身份去重，同一人停留会周期留档；未实现自动清理，需关注磁盘空间。
`hasFace=false` 只表示未检测到人脸，不等同于没有人。

`verify_runtime.py --project ...` 验证离线 Runtime 子进程与结果图像发布；
仅显式指定 `--camera` 才运行真实摄像头流程，并验证多轮结果发布、停止和再次启动。
将 `launch_designer.py` 放在交付工程旁，可在本机源码环境直接打开该工程，
使用独立 `runtime-data`，不覆盖现有 Designer 的运行数据。

默认不自动启动、不发声、不修改相机配置。生产设置保持 single，由一个 Job 内的 While
负责持续运行，不再叠加 continuous 生产循环。有限次数实机验证用 `--workflow main --max-frames 3`；
省略 `--max-frames` 时持续运行，Ctrl+C 协作取消。命令行逐帧写入 `verification/*.jsonl`，
内存仅保留最近 100 条报告。正常运行错误会终止 Job 并报错，不无限忽略故障。
该 RTSP 算子每次连接读取一帧后释放，不是高帧率视频播放或录像。

## 切换在线工程

如果当前 Runtime 已有另一个工程，打开本地文件不会自动替换在线工程。
在“工程与 Runtime 配置”中先选择变更并“上传候选资源”（模型和两张样例图，共 3 项），
再“校验选中变更”，最后确认“写入 Runtime”。上传/校验不提交配置、不启动任务。
尚未发布的新工程，其 online-projects 目录可能还不存在，不需要手动建目录或删除 Runtime 数据。
写入会切换当前在线工程；确认目标正确后再执行，写入完成后才运行 main。
