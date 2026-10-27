# 本地视频 YOLO 多目标裁剪（仅算子编排）

工程文件：

- `video-yolo-crop.emoproj`：保留原来的只传下游、不默认落盘版本。
- `video-yolo-crop-save.emoproj`：新增逐目标分文件保存版本，依赖 Image Saver 1.1.0 的 `fileName` 输入。

两个工程都只使用算子编排，不含自定义运行时脚本。
`test_example.py` 只用于验证工程，不参与实际处理，运行工程不需要执行这个脚本。

## 打开和配置

1. 在 Designer 中打开需要的版本；需要保存全部裁剪图时选择 `video-yolo-crop-save.emoproj`。
2. 切换到“取帧并检测”（`video_frame`）工作流。
3. 选择“本地视频取帧”（`video`），设置 `videoPath` 为本地视频路径，设置 `intervalSec` 为取帧间隔，例如 `2.0` 表示每 2 秒取一帧。保持 `colorMode=color`。
4. 选择“YOLO 检测全部目标”（`yolo`），设置 `modelPath` 为自己的 `.onnx` 模型路径，并按模型设置 `imageSize`、`confidence` 等参数。当前 YOLO 算子不能直接加载 `.pt`。
5. `classes=[]` 表示保留模型支持的所有类别。只裁剪人时，按模型实际类别编号填写；只有 COCO 类别模型的 `person` 才通常是 `[0]`，不要把其他模型也直接当作这一编号。
6. 保存配置，通过 Runtime 运行入口“主流程”（`main`）。不要以单个写变量算子的预览代替整图运行，它需要作业上下文。

视频和模型路径初始为空，因此未配置时编译/运行会提示缺少路径，这是待配置模板，不是随附模型的开箱检测演示。
本示例依赖当前工作区已有的 `vision.io.video_loader`，保存版还需要 `vision.io.image_saver` 1.1.0。
缺少对应算子版本的旧 EXE 不能直接运行；本次没有重打包或更新已运行的 EXE。

## 流程结构

```text
main: 首次条件 true -> While(video_frame) -> 视频结束

video_frame:
  本地视频取帧 -> YOLO（每帧一次）-> 统计目标数
                                  -> 每帧将 targetIndex 重置为 0
                                  -> targetIndex < count
                                  -> While(one_target)

one_target:
  按 targetIndex 选择一个检测目标 -> 提取 BBox -> 裁剪原图
      -> process_crop（裁剪后处理）-> targetIndex + 1 -> 判断还有无目标

process_crop:
  裁剪图及其 frame -> RGB 统计（下游示例）

process_crop（保存版另接）：
  源帧号 + 当前目标索引 -> 格式化文件名 -> Image Saver.fileName
  裁剪图 ----------------------------> Image Saver.image
```

- `targetIndex` 是作业级整数变量，不会在多个作业之间共用游标。
- 无目标帧不进入内层循环，不调用单对象 BBox 提取，也不会因空集合报错。
- 最后一张抽样图即使带着 `hasNext=false`，仍先处理全部目标，再结束外层循环。
- 裁剪使用视频原始像素，不裁剪 YOLO 叠加图；默认不填充、不缩放。
- 默认外层循环上限为 100000，内层为 1000；YOLO 的 `maxDetections=300`。若自行提高检测数量或处理超长视频，循环上限要留出最后一次终止判断。
- 这是逐张处理链，不会把所有裁剪图汇总成一个图片列表。基础版只送下游；保存版会保存每张目标裁剪图。

## 接入自己的下游

编辑“裁剪后处理”（`process_crop`）子流程即可，不必改前面的取帧和遍历逻辑。

该子流程每次收到一个目标：

| 输入 | 类型 | 内容 |
| --- | --- | --- |
| `image` | `image` | 当前目标的裁剪图 |
| `frame` | `bbox2d` | 裁剪图坐标系，保留回到视频原帧的变换 |
| `detections` | `detectionCollection` | 仅含当前目标的类别、置信度和原帧检测框 |
| `frameIndex` | `integer` | 视频源帧号，从 0 开始 |
| `timestampSec` | `number` | 当前抽样帧在视频中的时间 |

默认下游是已有的 `vision.color.rgb_statistics`，用于证明裁剪图确实进入后续处理，图像像素保持不变。
可以在此连接第二个 YOLO、分类或其他现有图像算子；保留子流程的 `image`、`frame` 输出供调用方完成依赖即可。
`detections` 的检测框仍属于原帧坐标系，不应把它直接当成裁剪图的局部 ROI。

## 关于保存

Image Saver 1.1.0 增加了可选的 `fileName` 字符串输入：

- 不连接时沿用旧行为，直接保存到 `outputPath`。
- 连接时仅替换文件名，目录仍取 `outputPath` 的所在目录；例如 `outputPath=outputs/crops/default.png`、`fileName=frame_001.png`，最终保存为 `outputs/crops/frame_001.png`。
- 文件名没有扩展名时沿用 `outputPath` 的扩展名。
- 空文件名、完整路径或带目录的名字会报错，不会静默回退到默认文件名。
- 为保留输入和 Runtime 数据保护，动态命名应使用独立输出目录，不能与工程输入或 Runtime 数据重叠。

保存版已经连接好全部节点，默认保存到工程目录下的 `outputs/crops/`，名称为 `源帧号,目标索引.png`，例如 `0,0.png`、`0,1.png`、`10,0.png`；两种编号都从 0 开始。
它复用已有的坐标报文格式化算子，参数为 `decimals=0`、`numberFormat=fixed`、`quote=none`、`suffix=.png`，这里只做数字转文本，不连接任何 TCP、PLC 或设备。
文件名唯一性来自上游提供的帧号和目标索引，不是 Image Saver 自动编号。

保存节点默认 `overwrite=false`，已有同名文件会明确报错。重复处理同一视频或换另一个视频时，可将 `outputPath` 改到新的输出子目录；确实需要替换旧图时再开启覆盖。
也可以换成自己的上游字符串来源，连接它的输出到 `Image Saver.fileName`。

如果只接图片、不接文件名并开启覆盖，循环仍只保留固定路径的最后一张图，不会自动生成多个文件。
本工程没有照搬 Helmet_Crops 包中的头肩区域估算；当前按 YOLO 检测框裁剪完整目标区域。

## 已执行的验证

2026-10-21，源码基线 `4550d46e34e508cbb2774f7bcda8863d293541e6`，加上工作区此前已有的视频取帧算子及本次 Image Saver 1.1.0 源码修改。
最终相关回归在 `cdd690065bd629d0294151cb0310965aa90b9c71` 上通过；期间进入的独立 TCP 提交不与本次实现文件重叠。
测试只使用临时视频、仓库已有微型 ONNX 夹具、隔离数据库和临时作业目录，没有读取用户实际视频、模型或现场工程。

```powershell
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest examples/video_yolo_crop_operators/test_example.py -q
```

结果：12 passed。

- 工程编译、Designer 数据模型连线端口和变量绑定。
- 真实 OpenCV 视频解码、真实 ONNX Runtime 单目标/无目标推理、原始像素裁剪和原生 RGB 统计。
- 多目标夹具覆盖 5 个抽样帧的 8 个目标，其中含无目标中间帧；每帧推理一次，所有目标处理一次；新作业完整重跑。
- 逐像素核对裁剪内容，核对回原帧坐标的映射及传给下游的源帧号、时间和检测身份。
- 验证可选固定路径保存确实只保留最后一张，而不是批量图片。
- 保存版用相同多目标夹具生成 8 个独立 PNG，逐张核对原始裁剪像素一致。
- 两个版本均通过 RuntimeService 加载临时配置工程并启动原生子进程作业，真实 ONNX 完成 5 帧、5 次下游处理；保存版同时生成 5 个独立图片文件。
- 正常取消后不继续处理其他目标，视频句柄被释放。

这些是源码/夹具和 Runtime 作业证据，不是用户真实模型识别效果、已安装 EXE、原生桌面人工操作或现场验收。
