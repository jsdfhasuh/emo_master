# PatchCore Inference V1.1

算子 ID：`vision.inference.patchcore`；分类：**推理**；版本：`1.1.0`。

用于在 Runtime 所在电脑上加载 Vision Workshop 导出的 **DINO ONNX 完整部署包**，
输出原始异常分数、部署包阈值、OK/NG，以及可选的彩色热图、叠加图和原始异常数值。
无需安装或导入训练平台，也不依赖 Torch、Anomalib 或训练平台的本机模型注册数据库。

## 选择模型

在参数窗口的“模型部署目录”中选择以下任一种目录，中文路径受支持：

```text
直接部署目录/
  deployment.json
  model.onnx
  model.onnx.data                  # 以图和清单实际引用为准
  threshold.json
  training_candidate_threshold.json  # v2 保留的训练候选阈值
  ...                             # 清单绑定的验证记录和支持文件

训练平台导出目录/
  transfer.json
  payload/
    deployment.json
    model.onnx
    ...
```

应完整保留原部署文件。不能只选择 `model.onnx`，不能删去 external-data 或校验记录。
首版仅接受正式 `dino_onnx_deployment_p6_v1` / `dino_onnx_deployment_p6_v2`，支持平台的
`dino_fp32_p5_v1` 和 `dino_fp32_features_fp64_distance_p5_v1` 距离 profile。
profile 名中的 `p5` 不代表接受 `dino_onnx_deployment_p5_v1` 实验包。

生产端完成状态须为 PASS，v2 校准须为 FROZEN。加载时检查任务与模型身份、固定预处理、
图及外部数据的路径/范围/摘要、阈值绑定、校准样本和方法绑定、验证记录及 transfer 清单。
只消费已冻结的阈值，不在 emo_master 内重新训练、重新计算校准阈值或改写部署文件。

**目录必须能由 Runtime 访问。** 远程 Runtime 不会读取 Designer 电脑上同名的目录，
文件夹选择也不意味着上传。单节点调试要求 Runtime 上的绝对路径；普通执行的相对路径
按 Runtime 的工作目录解析，不按 Designer 工程文件位置解析。建议统一填写 Runtime 绝对路径。

## 输入与参数

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| 输入 `image` | 必需 | `uint8` BGR 三通道或二维灰度图；BGR 转 RGB，灰度复制三通道 |
| 输入 `frame` | 可选 | 标准 bbox2d 坐标合同；裁剪图应连接上游裁剪节点的 frame |
| `modelPath` | 空 | 完整部署目录或 transfer 外层目录 |
| `drawHeatmap` | `true` | 输出彩色热图 |
| `drawOverlay` | `true` | 输出原图与热图的叠加图 |
| `overlayMode` | `heatmap` | “整图热图”或“疑似缺陷高亮”（`highlight`），仅在输出叠加图时生效 |
| `highlightThreshold` | `0.7` | 高亮相对阈值，范围 `(0, 1]`；仅在高亮模式下生效，不是模型判定阈值 |
| `outputAnomalyMap` | `false` | 按需输出完整 256×256 原始浮点数据，避免连续运行产生无用的大型 JSON |

不提供尺寸、归一化、距离算法或模型判定阈值覆盖参数，也没有设备下拉框。
模型固定使用 batch 1、CPUExecutionProvider、顺序执行、BASIC 优化、intra-op 2 / inter-op 1。
Runtime 依赖 `onnxruntime==1.23.2` 和 `onnx==1.16.0`，工程继续使用 Python 3.10 / PySide2。
原始图像上限沿用参考实现的 4,000 万像素限制。

## 输出语义

| 输出 | 类型 | 说明 |
| --- | --- | --- |
| `score` | number | 图直接输出的原始异常分数，可以大于 1，不是概率 |
| `threshold` | number | 部署包冻结的判定阈值 |
| `isAnomaly` | boolean | 用 binary64 标量比较 `score >= threshold`；相等也为 NG |
| `label` | string | `OK` 或 `NG` |
| `heatmap` | 可选 image | 输入尺寸的 `uint8` BGR 彩色热图 |
| `overlay` | 可选 image | 输入尺寸的 `uint8` BGR 叠加图，按 `overlayMode` 选择整图热图或疑似缺陷高亮 |
| `anomalyMap` | 可选 object | 原始 float32 数值及几何信息，不走图像端口 |
| `frame` | bbox2d | 保留输入坐标关系；未提供时按通用图像合同生成 |

`anomalyMap` 的结构如下。`values` 实际包含完整二维数组，不是省略值、热图 RGB 或归一化概率：

```text
{
  "shape": [256, 256],
  "dtype": "float32",
  "values": <256 行、每行 256 个原始浮点数>,
  "geometry": {
    "original_size": [113, 251],
    "resized_size": [115, 256],
    "padding": [0, 0, 70, 71]
  }
}
```

尺寸顺序为 `[高, 宽]`，补边顺序为 `[左, 右, 上, 下]`。原始 map 保留补边；
去补边再按参考 NumPy 双线性算法还原才得到输入尺寸的显示数据。
JSON 数字承载精确提升后的 float32 值，读取方按 `dtype` 转回 float32 可无损恢复。

## 显示与连线

预处理严格沿用 DINO 的 FP32 运算顺序、256 Letterbox、黑色补边与 ImageNet 归一化，
没有使用 YOLO 预处理或 OpenCV resize 近似替代。

热图使用 DINO 桌面实现的逐图 min/max 相对色标与 JET 配色；叠加权重为原图 0.6、热图 0.4。
**OK 图也可能显示红色。** 颜色只表示这张图内部的相对响应强弱，不能跨图直接比较，
不能把红色当作已判定的缺陷区域。显示不会改写原始 map、score 或 OK/NG。

### 突出疑似缺陷位置

在参数窗口打开“输出叠加图”，将“叠加显示”选为“疑似缺陷高亮”，查看或保存 `overlay`：

- 仅当模型判定为 **NG** 时，用半透明红色填充高响应位置，并绘制带黑色边缘的黄色轮廓。
- 未标记区域保留原图，轮廓边缘除外；**OK** 时输出原图的独立副本，不标记局部位置。
- 先去除 Letterbox 补边并还原到输入尺寸，再使用未量化浮点响应计算
  `(response - min) / (max - min) >= highlightThreshold`。不从 8 位彩色图反推位置。
- 默认 `0.7`；调高会缩小选中范围，调低会扩大。它是逐图相对的**显示阈值**，
  不改变部署包阈值、分数、OK/NG 或 `anomalyMap`，也不重新加载模型。
- 响应近似均匀、无法给出有意义的局部位置时，不强行圈出区域；诊断记录
  `localization.status=uniform_response`。此时判定仍可为 NG，不能用“有没有轮廓”替代 `isAnomaly`。
- 多个高响应位置会分别描边，不做小区域过滤；保留区域内部的低响应空洞。

这是**定位辅助显示，不是已校准的像素级分割或缺陷测量**。NG 图中的背景响应也可能被标出，
准确位置仍须用实际模型及业务图片确认；分辨率受模型原始 256×256 map 限制。
`heatmap` 始终保留原来的整图相对 JET 显示，只有 `overlay` 随模式切换。
不新增 mask、轮廓集合、坐标框或缺陷数量端口。

旧节点和省略新参数的调用仍使用整图热图，不改变已有显示。
已有四参数节点打开编辑器即可看到新模式；打开本身不会改写节点，应用/保存后才保存新设置。
隐藏高亮参数会保留草稿值，切回高亮模式时重新校验。

典型连接：

- 图片输入的 `image` → PatchCore `image`。
- PatchCore `isAnomaly` → If `value`，使用 bool 模式；true 为 NG，false 为 OK。
- PatchCore `score` → Result Writer `numberValue`，输入类型选“数值”。
- PatchCore `overlay` → Image Saver `image`。
- 使用裁剪图时，同时连接裁剪节点的 `frame` → PatchCore `frame`。

关闭某个图像输出时，该端口缺失而不是返回空图；节点结果会说明未生成原因。
连接到该可选图像的下游保存节点沿用现有可选输出语义跳过执行。
节点历史与单节点调试可以查看图像；较大的原始 `anomalyMap` 在调试结果中作为 JSON 资产查看和下载。

## 生命周期与失败

Session 属于节点执行会话：同一路径循环复用，切换模型释放旧 Session，停止/取消/结束调试时
由现有生命周期清理。只在加载时校验完整包，不逐帧哈希，没有全局模型缓存或后台文件监控。
同一路径的文件被替换后不会自动热更新；正常更新模型应停止后重新运行，或显式重置调试会话。

模型缺失、加载失败、非法输入、非有限分数或错误输出类型/形状都会报错，绝不返回默认 OK。
常见代码为 `E_MODEL_NOT_FOUND`、`E_MODEL_LOAD_FAILED`、`E_BACKEND_UNAVAILABLE`、
`E_INFERENCE_FAILED` 和 `E_RESULT_INVALID`。调试入口的目录预检查也可能报告
`E_PARAM_INVALID` / `E_INPUT_MISSING`，并注明目录属于 Runtime。

当前不支持 GPU、CNN/PT、裸 ONNX、P5 实验包、经校准的像素级分割/结构化缺陷区域输出、自动模型上传。
包内 PASS 记录是生产端历史记录，不是 emo_master 已完成真实模型验收的证明。
源码与界面验证及未执行边界见 [验证记录](testing/2026-10-23-patchcore-v1-validation.md)。
新增高亮模式的测试与合成显示示例见 [高亮验证记录](testing/2026-10-23-patchcore-highlight-validation.md)。
