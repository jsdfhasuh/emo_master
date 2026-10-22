# Image Saver：传入保存文件名

算子：`vision.io.image_saver`，版本：`1.1.0`。

## 输入与配置

| 项目 | 作用 |
| --- | --- |
| 输入 `image` | 必需，待保存图像 |
| 输入 `fileName` | 可选字符串，每张图的保存文件名 |
| 参数 `outputPath` | 原有固定完整路径；动态命名时提供保存目录和默认扩展名 |
| 参数 `overwrite` | 是否覆盖已有同名文件，默认保持原来的 `true` |
| 输出 `result` | 原有 JSON，包含 `saved` 和实际保存的 `path` |

不连接 `fileName` 时，旧工程仍按 `outputPath` 保存，不需要迁移参数。
连接 `fileName` 后，只替换 `outputPath` 的最后一段，不改变保存目录。

```text
配置 outputPath = outputs/crops/default.png

输入 fileName = frame_001_person_02.png
保存路径      = outputs/crops/frame_001_person_02.png

输入 fileName = frame_002_person_01
保存路径      = outputs/crops/frame_002_person_01.png
```

上游需要输出 `string` 类型并连接到 `fileName`，不是把占位符填进 `outputPath`。
文件参数仍由原有工程路径处理流程管理，不新增全局变量文件路径绑定。

## 保存行为

- 支持中文文件名和中文目录。
- 输入自带扩展名时按输入扩展名编码；不带扩展名时沿用 `outputPath` 的扩展名。
- 文件名只能是名字，不接受绝对路径、子目录或 `../`；输出目录仍由配置控制。
- 明确传入空白或非字符串会报错，不会偷偷使用默认文件名覆盖其他图。
- 没有自动序号、自动改名或失败重试。逐张保存不同图片，需要上游逐张提供不同名字。
- `overwrite=false` 时，同名文件存在会返回 `E_OUTPUT_EXISTS`，包括普通并发作业争用同名文件的情况。
- `overwrite=true` 时，同名文件仍会被替换；不同源视频建议使用不同输出子目录或不同命名。
- 编码或写入失败返回 `E_OUTPUT_WRITE_FAILED`，`result.path` 在成功时返回实际目标，而不是配置中的默认文件名。

Runtime 会在 `fileName` 连线存在时把保存目录纳入已有的输出重叠校验。请使用 `outputs/crops/` 等独立目录，不要将动态输出目录设在包含输入图、模型、工程文件或 Runtime 数据的位置。
固定文件保存仍沿用原来的单文件校验，不扩大旧工程的限制。

## 视频裁剪保存工程

参见 `examples/video_yolo_crop_operators/video-yolo-crop-save.emoproj`。

```text
视频取帧 -> YOLO -> 遍历目标 -> Crop.image -> Image Saver.image
源帧号 + 当前目标索引 -> 已有文本格式化 -> Image Saver.fileName
```

工程默认生成 `源帧号,目标索引.png`，例如 `0,0.png`、`0,1.png`。格式化节点只做本地数字转文本，不连接设备；保存后仍可继续在“裁剪后处理”子流程中接其他算子。

## 使用版本

本次是源码功能修改，没有构建、发布或更新已安装 EXE。
使用包含该版本的 Runtime，并在 Designer 刷新算子目录后添加 Image Saver；旧节点如果使用缓存端口信息，可重新添加该保存节点并连接 `fileName`。
单算子调试沿用显式写入确认，不会自动预览落盘，取消也不会撤销已经写入的文件。
