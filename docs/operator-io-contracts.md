# 算子输入输出契约 v1.2

本文定义 EmoMaster 算子之间传递图像分析与设备通讯结果时的稳定边界。目标是让 Blob、
RGB 统计、YOLO 推理和通讯算子可以多输入、多输出，并让下游无需猜测一段 JSON 到底
代表点、矩形、检测集合、PLC 数据还是 TCP 消息。

## 1. 边界原则

- `executeNode()` 的输入和输出仍是按端口名组织的 `dict`。
- 图像端口继续传运行时图像对象；几何和分析结果传 JSON-compatible `dict/list`。
- 语义 payload 顶层必须含 `type` 和 `schemaVersion`。
- Python 算子使用冻结 DTO 的 `toPayload()` / `fromPayload()` 构造和读取结果。
- DTO 实例不能直接跨算子边界；必须先调用 `toPayload()`。
- `attributes` 只允许 JSON 的 null、boolean、number、string、array、object，且数值必须有限。

典型端口声明：

```json
{
  "inputPorts": {
    "image": "image",
    "roi": {
      "type": "geometry2d",
      "required": false,
      "nullable": true,
      "schemaVersion": "1.x"
    }
  },
  "outputPorts": {
    "centroid": "point2d",
    "blobs": "blobCollection",
    "statistics": "colorStatistics",
    "detections": {
      "type": "detectionCollection",
      "required": true,
      "schemaVersion": "1.x"
    },
    "overlay": "image"
  }
}
```

端口既可使用旧字符串简写，也可使用 PortSpec 描述对象：

| PortSpec 字段 | 含义 |
| --- | --- |
| `type` | 必填，端口类型 |
| `required` | 输入缺少或输出缺少时是否报错 |
| `nullable` | 是否接受显式 JSON `null` |
| `schemaVersion` | 语义 payload 的精确版本或 `1.x` |

字符串输入保持旧路由行为；需要 Runner 强制检查缺失时应使用描述对象（描述对象输入的
`required` 默认值为 true）。字符串输出仍允许条件算子只返回命中分支。
`nullable` 只有显式声明时才覆盖旧类型匹配规则。Runtime 对旧 gRPC 客户端继续返回
字符串端口，同时通过独立 JSON 字段暴露完整 PortSpec。

一次执行可以返回其中多个端口：

```python
return {
    "status": "ok",
    "outputs": {
        "centroid": centroid.toPayload(),
        "blobs": blobs.toPayload(),
        "statistics": statistics.toPayload(),
        "detections": detections.toPayload(),
        "overlay": overlay,
    },
    "metrics": {},
    "diagnostics": {},
}
```

## 2. v1 端口类型

| 端口类型 | payload 含义 |
| --- | --- |
| `point2d` | 一个二维点 |
| `vector2d` | 一个无锚点的二维位移向量，分量为 `dx/dy` |
| `bbox2d` | 与坐标轴平行的二维包围盒 |
| `rotatedBox2d` | 带方向的二维包围盒 |
| `polygon2d` | 闭合二维多边形 |
| `line2d` | 两个不同端点定义的线段 |
| `circle2d` | 圆心和正半径定义的圆 |
| `geometry2d` | 上述六种具体几何类型的联合输入 |
| `blobCollection` | 连通域 / Blob 结果集合 |
| `detectionCollection` | 目标检测结果集合 |
| `colorStatistics` | 一个区域的逐通道颜色统计 |
| `contourCollection` | 带完整层级来源信息的轮廓集合 |
| `shapeMeasurementCollection` | 轮廓或 Blob 的形状测量集合 |
| `histogram` | GRAY/BGR 固定 bin 直方图 |
| `lineCollection` | 直线集合 |
| `circleCollection` | 圆集合 |
| `templateMatchCollection` | 模板匹配结果集合 |
| `plcValueCollection` | 一次三菱 SLMP 3E 读取产生的强类型 PLC 值集合 |
| `plcWriteReceipt` | 一次三菱 SLMP 3E 写入的结构化回执 |
| `tcpMessage` | 可安全跨 JSON 边界传递的文本或二进制 TCP 消息 |

`geometry2d` 是端口联合类型，不是 payload 的 `type` 值。连接到该端口的值仍必须带
`point2d`、`bbox2d`、`rotatedBox2d`、`polygon2d`、`line2d` 或 `circle2d`
中的具体标签。Detection 的主几何仍只允许 BBox、RotatedBox 或 Polygon；Line/Circle
不能作为 Detection 主几何。`vector2d` 没有锚点，不属于 `geometry2d`，也不能直接作为
ROI 或绘制几何。

兼容关系是有方向的：

- `bbox2d -> geometry2d` 合法，`geometry2d -> bbox2d` 不合法。
- `integer -> number` 是安全的数值拓宽，反方向不合法；运行时仍拒绝把 boolean
  当作 integer 或 number。
- 所有 v1 语义 payload 都可连接到通用 `json` 输入。
- `json` 不能直接连接到具体语义输入，因为编译时无法保证其中的结构。
- `object` 和 `any` 继续保留原有通配行为。
- `list<point2d>`、`list<geometry2d>` 等列表类型递归使用相同规则。
- 显式 `nullable: true` 的输出不能连接到显式 `nullable: false` 的输入；反向合法。

## 3. 坐标空间

所有几何 payload 都必须包含：

```json
{
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0],
    "imageWidth": 1920,
    "imageHeight": 1080
  }
}
```

v1 固定规则：

- 原点在左上角，x 向右，y 向下，单位为像素。
- 坐标可为浮点数，以保留亚像素结果。
- `reference` 标识当前坐标所在图像，`sourceId` 标识最终原始图像。
- `transformToSource=[a,b,c,d,e,f]` 是当前坐标到源图坐标的可逆仿射变换：
  `x'=a*x+c*y+e`，`y'=b*x+d*y+f`；默认是单位矩阵。
- schema 1.2 可改用 row-major 的
  `homographyToSource=[h00,h01,h02,h10,h11,h12,h20,h21,h22]`。方向同样是当前图到
  源图：`w=h20*x+h21*y+h22`，`sourceX=(h00*x+h01*y+h02)/w`，
  `sourceY=(h10*x+h11*y+h12)/w`。
- `transformToSource` 与 `homographyToSource` 互斥。1.0/1.1 只允许仿射；1.2 必须显式
  提供且只能提供一种变换。Homography 必须有限、可逆；`abs(w)<=1e-12` 的点映射
  明确失败，不生成无穷坐标。
- Homography 允许任意非零整体缩放。等价比较先按 Frobenius 范数和首个非零元素符号
  规范化，再使用 `rel=1e-9/abs=1e-9` 容差；等价的仿射与 Homography 是同一空间，
  `reference` 不参与等价判断。
- 连续变换按 `H_output_to_source = H_input_to_source × H_output_to_input` 组合。两边都是
  仿射时继续保存 6 参数；任一边是 Homography 时保存 9 参数。
- 多图输入时使用稳定 ID，例如 `input:leftImage`；同一结果集合内必须保持一致。
- `imageWidth` 和 `imageHeight` 可同时省略；如果提供，必须同时存在且为正整数。
- 算子可以直接输出源图坐标，也可以保留模型输入坐标并准确填写
  `sourceId/transformToSource`；不再允许只靠文字约定猜测 resize、ROI 或 letterbox。
- v1 不接受 normalized、百分比或物理长度坐标。

### 3.1 图像 frame 伴随端口

`image` 仍传递裸运行时图像对象。会改变尺寸或有效内容区域的算子，额外使用
`frame: bbox2d` 伴随端口保存坐标信息：

- `frame.coordinateSpace.imageWidth/imageHeight` 是当前图像尺寸。
- `frame.coordinateSpace.transformToSource` 把当前图像坐标映射回最终源图。
- schema 1.2 时可由 `homographyToSource` 完成同一方向的透视回源。
- `frame` 自身的 BBox 是当前图像中的有效内容区域；Letterbox 的填充区不属于该区域，
  Crop 的显式 padding 也不属于该区域。
- Resize、Crop 会计算新的 `frame`；ColorConvert、Blur、Threshold、InRange、Morphology
  等不改变尺寸的算子原样透传它。
- 输入没有 `frame` 时，算子创建覆盖整图的单位映射 frame，因此旧工作流仍可运行。
- Blob、RGB Statistics、YOLO 接收可选 `frame`，并将其中的坐标空间用于语义输出。
- Rotate、Flip、Affine、Perspective 额外输出二维 `{0,255}` 的 `validMask`。`frame`
  继续表示有效像素的轴对齐边界；透视后的精确非矩形有效区由 `validMask` 表达。

图像和 frame 使用两条同方向的边连接。这保持了旧 `image` 端口的 ndarray 兼容性，
同时避免下游凭参数反推 Resize、Crop 或 Letterbox 坐标。

## 4. 坐标与具体几何 payload

### 4.1 Point2D

```json
{
  "type": "point2d",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "x": 123.5,
  "y": 48.25
}
```

### 4.1.1 Vector2D

```json
{
  "type": "vector2d",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "dx": 12.5,
  "dy": -3.0
}
```

向量表示同一坐标空间中两个点相减后的位移，不携带起点。它保留 coordinateSpace 是为了
防止不同图像、不同回源变换的坐标被静默混算，但不会被当成有绝对位置的几何对象。

### 4.2 BBox2D

```json
{
  "type": "bbox2d",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "x": 100.0,
  "y": 50.0,
  "width": 80.0,
  "height": 40.0
}
```

`x/y` 是左上角，`width/height` 必须大于或等于 0。区域按半开区间
`[x, x + width) × [y, y + height)` 理解。

### 4.3 RotatedBox2D

```json
{
  "type": "rotatedBox2d",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "centerX": 140.0,
  "centerY": 70.0,
  "width": 80.0,
  "height": 40.0,
  "angleDegrees": 25.0
}
```

角度范围是 `[-180, 180)`；从图像 x 正轴观察，在 y 向下的坐标系中正角为顺时针。
OpenCV 等库返回的角度必须先转换成该约定。

### 4.4 Polygon2D

```json
{
  "type": "polygon2d",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "points": [
    {"x": 10.0, "y": 20.0},
    {"x": 40.0, "y": 20.0},
    {"x": 40.0, "y": 60.0}
  ]
}
```

多边形至少三个点并隐式闭合，不重复首点。v1 校验点数和数值，不检查自相交。

### 4.5 Line2D 与 Circle2D

两种类型固定写 schema 1.2：

```json
{
  "type": "line2d",
  "schemaVersion": "1.2",
  "coordinateSpace": {"origin":"topLeft","xAxis":"right","yAxis":"down","unit":"pixel","reference":"sourceImage","sourceId":"sourceImage","transformToSource":[1,0,0,1,0,0]},
  "start": {"x": 10.0, "y": 20.0},
  "end": {"x": 80.0, "y": 50.0}
}
```

`line2d.start/end` 不能重合。`circle2d` 使用相同顶层字段，并以
`center:{x,y}` 与 `radius>0` 表达。Line 的包含判断按点到线段距离；Circle 按真实半径。
需要面积的节点若不支持 Line/Circle，必须返回 `E_INPUT_SHAPE`，不能把它们误当成旋转框。

## 5. 集合结果

### 5.1 BlobCollection

```json
{
  "type": "blobCollection",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "items": [
    {
      "id": "blob-1",
      "label": 1,
      "area": 912.0,
      "centroid": {
        "type": "point2d",
        "schemaVersion": "1.1",
        "coordinateSpace": {
          "origin": "topLeft",
          "xAxis": "right",
          "yAxis": "down",
          "unit": "pixel",
          "reference": "sourceImage",
          "sourceId": "sourceImage",
          "transformToSource": [1, 0, 0, 1, 0, 0]
        },
        "x": 120.0,
        "y": 80.0
      },
      "bbox": {
        "type": "bbox2d",
        "schemaVersion": "1.1",
        "coordinateSpace": {
          "origin": "topLeft",
          "xAxis": "right",
          "yAxis": "down",
          "unit": "pixel",
          "reference": "sourceImage",
          "sourceId": "sourceImage",
          "transformToSource": [1, 0, 0, 1, 0, 0]
        },
        "x": 100.0,
        "y": 60.0,
        "width": 40.0,
        "height": 40.0
      },
      "attributes": {
        "circularity": 0.87
      }
    }
  ]
}
```

Blob 必填 `id/area/centroid/bbox`；`label/contour/attributes` 可选。`contour` 是完整
`polygon2d` payload。集合为空时仍保留 `coordinateSpace`，避免下游失去参考系。

### 5.2 DetectionCollection

```json
{
  "type": "detectionCollection",
  "schemaVersion": "1.1",
  "coordinateSpace": {
    "origin": "topLeft",
    "xAxis": "right",
    "yAxis": "down",
    "unit": "pixel",
    "reference": "sourceImage",
    "sourceId": "sourceImage",
    "transformToSource": [1, 0, 0, 1, 0, 0]
  },
  "items": [
    {
      "id": "det-1",
      "classId": 0,
      "label": "part",
      "confidence": 0.96,
      "bbox": {
        "type": "bbox2d",
        "schemaVersion": "1.1",
        "coordinateSpace": {
          "origin": "topLeft",
          "xAxis": "right",
          "yAxis": "down",
          "unit": "pixel",
          "reference": "sourceImage",
          "sourceId": "sourceImage",
          "transformToSource": [1, 0, 0, 1, 0, 0]
        },
        "x": 96.71586215429258,
        "y": 42.120580352613516,
        "width": 86.56827569141484,
        "height": 55.75883929477297
      },
      "geometry": {
        "type": "rotatedBox2d",
        "schemaVersion": "1.1",
        "coordinateSpace": {
          "origin": "topLeft",
          "xAxis": "right",
          "yAxis": "down",
          "unit": "pixel",
          "reference": "sourceImage",
          "sourceId": "sourceImage",
          "transformToSource": [1, 0, 0, 1, 0, 0]
        },
        "centerX": 140.0,
        "centerY": 70.0,
        "width": 80.0,
        "height": 40.0,
        "angleDegrees": 12.0
      },
      "attributes": {
        "model": "yolo"
      }
    }
  ]
}
```

v1.1 的主几何字段是 `geometry`，允许 `bbox2d`、`rotatedBox2d` 或
`polygon2d`。`bbox` 是主几何对应的轴对齐包围盒，DTO 会在需要时自动推导。
如果生产者同时提供 `geometry` 和 `bbox`，两者不一致会被拒绝。
`classId` 是非负整数，`confidence` 范围是 `[0, 1]`。读取器仍兼容 v1.0 的
`bbox` 加可选 `polygon` 结构，但 v1.1 写入器统一产生 `geometry`。

### 5.3 schema 1.2 经典视觉集合

以下新 DTO 固定写 `schemaVersion: "1.2"`，顶层都严格拒绝未知字段、非有限数值和
集合/子几何坐标空间不一致：

| 类型 | item / 固定字段 |
| --- | --- |
| `contourCollection` | 顶层 `coordinateSpace/items`；item 为 `id/polygon/parentId/firstChildId/previousSiblingId/nextSiblingId/depth/isHole`。ID 唯一；过滤/选择后层级引用可继续指向未入选的来源 item |
| `shapeMeasurementCollection` | item 为 `id/sourceId/sourceType/area/perimeter/centroid/bbox/minAreaRect/circularity`；`sourceType=contour|blob`，圆度为 `4πA/P²` 并限制在 `[0,1]`，长宽角只通过 `minAreaRect` 表达 |
| `lineCollection` | item 为 `id/line:line2d`，ID 唯一 |
| `circleCollection` | item 为 `id/circle:circle2d`，ID 唯一 |
| `templateMatchCollection` | 顶层另含 `method/label/templateWidth/templateHeight`；item 为 `id/bbox/rawScore/quality`，`quality` 固定在 `[0,1]` |

Contour Extraction 生产的关系引用在完整输出中都可解析；Filter/Select 只保留来源信息，
不改写 ID 或层级关系。Template Match 的集合级 method、label 和模板尺寸在 Filter、Sort、
Select、JSONL 与 Writer 中继续保留。

## 6. ColorStatistics 与 Histogram

```json
{
  "type": "colorStatistics",
  "schemaVersion": "1.1",
  "colorSpace": "RGB",
  "pixelCount": 912,
  "channels": {
    "r": {
      "minimum": 0.0,
      "maximum": 255.0,
      "mean": 121.4,
      "standardDeviation": 10.2
    },
    "g": {
      "minimum": 0.0,
      "maximum": 255.0,
      "mean": 110.1,
      "standardDeviation": 9.7
    },
    "b": {
      "minimum": 0.0,
      "maximum": 255.0,
      "mean": 99.8,
      "standardDeviation": 8.4
    }
  },
  "region": {
    "type": "bbox2d",
    "schemaVersion": "1.1",
    "coordinateSpace": {
      "origin": "topLeft",
      "xAxis": "right",
      "yAxis": "down",
      "unit": "pixel",
      "reference": "sourceImage",
      "sourceId": "sourceImage",
      "transformToSource": [1, 0, 0, 1, 0, 0]
    },
    "x": 100.0,
    "y": 50.0,
    "width": 80.0,
    "height": 40.0
  },
  "attributes": {}
}
```

`colorSpace/pixelCount/channels` 必填；`region/attributes` 可选。通道名由
`colorSpace` 定义，RGB 推荐使用小写 `r/g/b`。`standardDeviation` 非负，`mean`
必须位于 `minimum..maximum` 内。

### 6.1 Histogram

`histogram` 固定写 schema 1.2，字段为：

- `coordinateSpace`
- `colorSpace=GRAY|BGR`
- `normalization=counts|probability`
- `pixelCount`
- 严格递增的 `binEdges`
- `channels=[{name,values}]`

GRAY 恰好一个 `GRAY` 通道，BGR 固定按 `B/G/R` 顺序。每个通道的 values 长度必须是
`len(binEdges)-1`。counts 模式每通道总和等于 `pixelCount` 且值为整数；probability
模式在非空选区总和为 1，空选区全部为 0。

### 6.2 通讯 payload

通讯 DTO 位于 `emo_master.core.contracts.communication`，当前固定写
`schemaVersion: "1.0"`，同样严格拒绝未知字段、非有限数值和不一致的派生字段。

PLC 读取结果：

```json
{
  "type": "plcValueCollection",
  "schemaVersion": "1.0",
  "protocol": "mitsubishiSlmp3e",
  "device": "D",
  "startAddress": 798,
  "dataType": "uint16",
  "values": [2, 1935],
  "wordCount": 2
}
```

- `device` 只允许 `D/M`；地址范围为 24 位无符号整数。
- `dataType` 为 `uint16/int16/uint32/int32/float32/bit`。32 位数和 float32 使用两个连续
  D 寄存器，低字在前；`bit` 只允许 M 设备，并沿用 gateway 的 word-unit 读取语义，
  返回起始 M 设备对应响应 word 的最低位。
- `wordCount` 必须与值数量及数据类型严格一致，单次 SLMP 请求最多 960 words。
- `float32` 必须有限；PLC 返回 NaN/Infinity 时视为协议结果无效。

写入成功返回：

```json
{
  "type": "plcWriteReceipt",
  "schemaVersion": "1.0",
  "protocol": "mitsubishiSlmp3e",
  "device": "D",
  "startAddress": 798,
  "dataType": "uint16",
  "valueCount": 2,
  "wordCount": 2,
  "attempts": 1
}
```

`tcpMessage` 不把 Python `bytes` 直接塞进工作流边界，而是显式声明编码：

```json
{
  "type": "tcpMessage",
  "schemaVersion": "1.0",
  "data": "AP8Q",
  "encoding": "base64",
  "byteLength": 3,
  "peerHost": "127.0.0.1",
  "peerPort": 10001
}
```

`encoding` 支持 `utf-8/ascii/latin-1/hex/base64`；`byteLength` 必须等于解码后的实际字节
数。`peerHost/peerPort` 必须同时出现或同时省略。这样二进制报文仍是 JSON-compatible，
下游也无需猜测字符串究竟是普通文本、十六进制还是 Base64。

## 7. Python DTO 用法

DTO 位于 `emo_master.core.contracts.geometry2d`。

上游 YOLO 算子：

```python
from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    Detection2D,
    DetectionCollection,
)

space = CoordinateSpace2D(imageWidth=image.shape[1], imageHeight=image.shape[0])
detections = DetectionCollection(
    items=(
        Detection2D.fromGeometry(
            detectionId="det-1",
            classId=0,
            label="part",
            confidence=0.96,
            geometry=BBox2D(100, 50, 80, 40, space),
        ),
    ),
    coordinateSpace=space,
)

return {
    "status": "ok",
    "outputs": {"detections": detections.toPayload()},
    "metrics": {},
    "diagnostics": {},
}
```

下游过滤算子：

```python
from emo_master.core.contracts.geometry2d import DetectionCollection

detections = DetectionCollection.fromPayload(inputs["detections"])
kept = tuple(item for item in detections.items if item.confidence >= 0.5)
outputs = DetectionCollection(kept, detections.coordinateSpace).toPayload()
```

接收联合几何端口时：

```python
from emo_master.core.contracts.geometry2d import BBox2D, parseGeometry2D

geometry = parseGeometry2D(inputs["roi"])
if isinstance(geometry, BBox2D):
    left = geometry.x
```

## 8. 多输入、多输出与运行时校验

- 输入、输出都以端口名为键，所以原生支持多个输入和多个输出。
- Runner 在普通算子执行前校验实际收到的输入值类型。
- 缺少 required 输入时报 `E_INPUT_MISSING`；仅有 optional 输入的算子即使没有输入
  也会执行。未连接且使用 PortSpec 声明 required 输入的算子会明确报错；有入边但
  未被 If/Switch 命中、因而没有收到任何数据的分支节点仍按控制流语义标记 SKIPPED。
- 显式 `null` 只有在 `nullable: true` 时通过；`nullable: false` 会报 `E_INPUT_TYPE`。
- Runner 拒绝算子返回未在 `outputPorts` 声明的键，错误码为
  `E_OUTPUT_UNDECLARED`。
- Runner 校验每个实际返回值，类型不符时报 `E_OUTPUT_TYPE`。
- `required: true` 的输出缺少时报 `E_OUTPUT_MISSING`。字符串输出保持旧行为，If、
  Switch 等条件算子仍可只返回命中的分支。
- 成功结果中的 `outputs` 必须是对象；数组或标量 envelope 会报 `E_OUTPUT_TYPE`。
- 未知的旧式端口类型继续宽松放行；只有本页列出的 v1 语义类型启用严格结构校验。

集合处理算子为了保留静态类型，会同时声明七个可选入口：`blobs: blobCollection`、
`detections: detectionCollection`、`contours: contourCollection`、
`measurements: shapeMeasurementCollection`、`lines: lineCollection`、
`circles: circleCollection` 和 `matches: templateMatchCollection`。每次执行必须恰好收到
其中一个；全部缺失时报 `E_INPUT_MISSING`，同时收到多个时报 `E_INPUT_SHAPE`，payload
不符合对应 DTO 时报 `E_INPUT_TYPE`。Filter、Sort、Select 只返回与本次输入类型对应的
集合输出；其余集合输出端口不会出现在 `outputs` 中，因此这些端口在 manifest 中必须
声明为非 required。Count 以及其他固定计数端口仍始终返回。

这意味着坐标不是“随便塞进一段 JSON 再由每个下游自行猜”。它在传输层确实是 JSON，
但端口声明、`type/schemaVersion`、Runner 校验和 DTO 共同组成了可执行契约。

## 9. 版本策略

- 当前读取器明确支持 `1.0`、`1.1` 和 `1.2`；旧 Detection 与旧 coordinateSpace 会在
  读取时归一化为新 DTO。
- 基础几何、Vector2D 及 Blob/Detection/ColorStatistics 在仿射坐标空间继续写 `1.1`；
  一旦坐标空间使用 Homography，就写 `1.2`。Line、Circle、Contour、ShapeMeasurement、Histogram、
  LineCollection、CircleCollection 和 TemplateMatchCollection 固定写 `1.2`。
- PortSpec 可以声明精确版本 `1.0` / `1.1` / `1.2`，或声明同主版本 `1.x`。可传播
  Affine/Homography 的端口应声明 `1.x`，不要错误收窄为 `1.1`。
- 编译器同时检查端口类型和版本方向：`1.2 -> 1.x` 合法，`1.x -> 1.2` 不保证安全，
  `1.1 -> 1.2` 不合法。
- 格式错误、未实现的新 minor（例如 `1.3`）和未知 major（例如 `2.0`）都会明确拒绝，
  不会静默猜测。
- 新增 minor 必须先更新读取器和测试；破坏字段语义时升级 major。
- 每个已支持版本仍拒绝未知字段，以便尽早发现拼写错误和生产者/消费者漂移。
- 通讯 payload 使用独立的 `1.0` 读取/写入版本；它与视觉 schema 的 minor 版本不互相
  推导，但同样可在 PortSpec 中声明精确 `1.0` 或 `1.x`。

## 10. Builtin 参考算子

| operatorId | 输入 | 输出 | 说明 |
| --- | --- | --- | --- |
| `vision.preprocess.resize` | `image`、可选 `frame` | `image`、`frame` | Stretch、Fit 或 Letterbox，保留回源仿射 |
| `vision.preprocess.crop` | `image`、可选 `roi/frame` | `image`、`frame` | BBox 或参数裁剪及四边 padding |
| `vision.preprocess.roi` | `image`、可选 `frame` | `croppedImage/croppedMask/croppedFrame`、`maskedImage/fullMask/maskedFrame`、`roi` | BBox、旋转框或多边形 ROI 的紧致与原尺寸两套输出 |
| `vision.preprocess.color_convert` | `image`、可选 `frame` | `image`、`frame` | BGR/RGB/GRAY/HSV/LAB 颜色空间转换 |
| `vision.preprocess.blur` | `image`、可选 `frame` | `image`、`frame` | Gaussian、Median 或 Bilateral 去噪 |
| `vision.preprocess.threshold` | `image`、可选 `frame` | `mask`、`threshold`、`frame` | 固定、Otsu、Triangle 或自适应二值化 |
| `vision.segment.in_range` | `image`、可选 `frame` | `mask`、`frame` | 按颜色通道范围生成二值掩码 |
| `vision.preprocess.morphology` | `mask`、可选 `frame` | `mask`、`frame` | 腐蚀、膨胀、开闭、梯度、顶帽、黑帽 |
| `vision.mask.logic` | `maskA`、可选 `maskB/frameA/frameB` | `mask`、`frame` | AND、OR、XOR、NOT 或差集 |
| `vision.analysis.blob` | `image`、可选 `mask/frame` | `blobs`、`mask`、`frame`、可选 `overlay` | 连通域过滤、轮廓和质心 |
| `vision.color.rgb_statistics` | `image`、可选 `roi/frame` | `statistics`、`frame`、可选 `mask` | 按 BGR 图像输入计算标准 RGB 通道统计 |
| `vision.inference.yolo` | `image`、可选 `frame` | `detections`、`frame`、可选 `overlay` | 本地 Ultralytics 模型推理，输出源图坐标 |
| `vision.analysis.contour` | `mask`、可选 `frame` | `polygons`、`contours`、`frame` | 提取轮廓并保留完整层级来源关系 |
| `vision.analysis.shape_measurement` | `contours` 或 `blobs` | `measurements` | 面积、周长、质心、圆度、BBox 和最小外接矩形 |
| `vision.preprocess.rotate` | `image`、可选 `frame/validMask` | `image`、`frame`、`validMask` | 围绕图像中心旋转，可扩展画布并保留回源变换 |
| `vision.preprocess.flip` | `image`、可选 `frame/validMask` | `image`、`frame`、`validMask` | 水平、垂直或双向翻转 |
| `vision.preprocess.affine` | `image`、可选 `frame/validMask` | `image`、`frame`、`validMask` | 三点仿射变换，保存逆向回源矩阵 |
| `vision.preprocess.perspective` | `image`、可选 `frame/validMask` | `image`、`frame`、`validMask` | 四点透视矫正，输出 schema 1.2 Homography |
| `vision.analysis.histogram` | `image`、可选 `frame/roi/mask/maskFrame` | `histogram`、`frame` | 对 frame、ROI 与 mask 交集计算 GRAY/BGR 直方图 |
| `vision.preprocess.equalize` | `image`、可选 `frame` | `image`、`frame` | 灰度均衡；BGR 只处理 LAB 的 L 通道 |
| `vision.preprocess.clahe` | `image`、可选 `frame` | `image`、`frame` | 限制对比度的自适应直方图均衡 |
| `vision.analysis.template_match` | `image/template`、可选 `frame/roi` | `matches`、`frame` | 六种 OpenCV 方法、峰值提取与 IoU NMS |
| `vision.analysis.hough_line` | `edge`、可选 `frame` | `lines`、`frame` | 使用 HoughLinesP 检测线段 |
| `vision.analysis.hough_circle` | `image`、可选 `frame` | `circles`、`frame` | 使用 HOUGH_GRADIENT 检测圆 |
| `vision.image.absdiff` | `imageA/imageB`、可选 `frameA/frameB` | `image`、`frame` | 对同空间同结构图像做绝对差分 |
| `vision.image.add_weighted` | `imageA/imageB`、可选 `frameA/frameB` | `image`、`frame` | uint8 饱和加权融合 |
| `vision.mask.apply` | `image/mask`、可选 `frame/maskFrame` | `image`、`frame` | 按二值 mask 保留像素，其余填固定值 |
| `vision.collection.filter` | 七类集合中的一个、可选 `roi` | 对应类型的 kept/rejected 集合、`keptCount/rejectedCount` | 按属性和空间条件进行 AND 过滤 |
| `vision.collection.count` | 七类集合中的一个 | `count` | 空集合返回 0，保留强类型输入 |
| `vision.collection.sort` | 七类集合中的一个 | 对应类型的 sorted 集合 | 按类型白名单字段稳定排序，缺失字段始终置后 |
| `vision.collection.select` | 七类集合中的一个 | 对应类型的 selected 集合、`selectedCount` | First、Last、Index 或 Top-K，单项仍使用集合 |
| `vision.value.number` | 无 | `value` | 产生有限 number 常量 |
| `vision.compare.number` | `left`、可选 `right` | `result` | 数值比较；未连接 `right` 时使用 `rightValue` |
| `vision.render.annotate` | `image`、可选 `frame/roi` 及七类集合 | `overlay`、`frame`、`drawnCount` | 在图像副本上绘制多类语义结果 |
| `vision.io.result_writer` | 十三种强类型或基础值入口中的一个 | `result` | 在 Job 工作区写 JSON、JSONL 或 CSV，并返回 receipt |
| `vision.io.coordinate_reader` | 可选 `frame:bbox2d` | `points:list<point2d>`、`pointCount`、可选 `polygon` | 从本地 TXT/CSV 严格读取坐标，支持列、分隔符、编码和无效行策略 |
| `vision.geometry.coordinate_calculator` | `points:list<point2d>`、按模式可选 `otherPoints/scalar` | 按模式输出点、向量、点积或测量值；始终输出 `resultCount` | 一个多模式坐标节点完成测量、变换、相减、点积和标量乘，避免拆成大量原子节点 |
| `communication.plc.slmp_read` | 无 | `values:plcValueCollection`、按类型可选 `numberValue/booleanValue` | 三菱 SLMP/MC 3E 二进制批量读取，并按 `outputIndex` 暴露一个强类型标量 |
| `communication.plc.slmp_write` | `data`、`value`、`values` 或参数值中的一个 | `receipt:plcWriteReceipt` | 三菱 SLMP/MC 3E 字设备写入，传输失败可有界重试 |
| `communication.tcp.client` | `message`、`text` 或参数文本中的一个 | `sentBytes`、可选 `response:tcpMessage/responseText` | 一次有界 TCP send/exchange 事务，可独立解码文本响应 |
| `communication.tcp.receive_once` | 无 | `message:tcpMessage`、`peerHost/peerPort`、可选 `text` | 在超时内监听并接收一条消息，可独立解码文本后关闭 |

### 10.1 ROI 输出契约

`vision.preprocess.roi` 的 `image` 必填，`frame` 可选；缺少 frame 时按整图建立单位
映射。`roiType` 固定为 `bbox`、`rotatedBox` 或 `polygon`。旋转框正角沿图像坐标顺时针，
多边形允许凹形，但参数层拒绝少于三点、零面积和自相交。

节点固定返回全部七个端口：

- `croppedImage/croppedMask/croppedFrame` 是 ROI 的紧致输出。BBox 和 Polygon 使用其
  请求外接框尺寸，RotatedBox 旋正为 `ceil(width) x ceil(height)`；`croppedFrame`
  保存该紧致图到输入 frame、再到源图的精确仿射。
- `maskedImage/fullMask/maskedFrame` 保持输入图像尺寸；`maskedImage` 是输入图像副本，
  ROI 外填 `padValue`，`maskedFrame` 等于输入 frame。
- `roi` 保留在输入图像坐标空间，并以具体的 `bbox2d`、`rotatedBox2d` 或
  `polygon2d` payload 返回。

两套 Mask 都是二维 `uint8`，值域固定为 `{0, 255}`，并与输入 frame 的有效内容区域
相交，因此 Letterbox 和显式 padding 不会被当作有效 ROI。部分越界时紧致输出继续
保持请求尺寸，图像越界部分填 `padValue`、Mask 填 0；完全不与有效 frame 相交时报
`E_INPUT_SHAPE`。节点不修改输入图像。

### 10.2 集合处理契约

Filter、Count、Sort、Select 都使用第 8 节的 exactly-one 七入口规则，并保留 item ID、
输入顺序、坐标空间和集合级元数据。读取接受 `schemaVersion: 1.x`；Blob/Detection 按
第 9 节选择 `1.1` 或 `1.2`，五类新增集合固定写 `1.2`。空集合仍带原坐标空间。

- Filter 对所有启用条件做 AND，数值范围包含边界。空间模式为 `centerInside`、
  `bboxIntersects` 或 `bboxContained` 时必须连接 `roi` 且坐标空间等价；前者按真实
  ROI 几何判断中心点，后两者明确使用目标与 ROI 的轴对齐包围盒。输出 kept、rejected
  及双方计数。
- Sort 只接受集合类型对应的字段白名单并使用稳定排序；缺少 circularity、label 等
  可选字段的项无论升序或降序都排在末尾。
- Select 支持 `first/last/index/topK`，越界按 `onOutOfRange=empty|error` 处理；即使只
  选择一项，输出仍为对应的集合类型。

### 10.3 数值与渲染契约

`vision.value.number` 只产生有限 number。`vision.compare.number` 的 `left` 必填，
`right` 可选；连接 `right` 时覆盖参数 `rightValue`，比较符固定为
`eq/ne/lt/lte/gt/gte`，输出 boolean，可直接接入 `vision.flow.if`。Count 的 integer
可通过第 2 节的单向拓宽直接连接 Compare 的 number 输入。

`vision.render.annotate` 可同时接收 ROI 与 Blob、Detection、Contour、Measurement、Line、
Circle、TemplateMatch 七类集合；所有语义输入必须与图像 frame 的坐标空间等价，空集合
合法。它始终复制图像后绘制真实几何、Blob 中心/轮廓、测量值、label、confidence 和
quality，输出 overlay、原 frame 以及实际绘制对象数。

### 10.4 Result Writer 契约

`vision.io.result_writer` 每次必须恰好连接 `blobs`、`detections`、`statistics`、
`geometry`、`numberValue`、`booleanValue`、`stringValue`、`contours`、`measurements`、
`histogram`、`lines`、`circles`、`matches` 中的一个。它只在
`runtimeContext.workspacePath` 指向的 Job 工作区内写文件；`relativePath` 禁止绝对
路径和 `..` 越界。未写扩展名时按 format 自动补 `.json/.jsonl/.csv`，已有冲突扩展名
报 `E_PARAM_INVALID`。

JSON 保存完整 payload；JSONL 的每个集合 item 都附带集合类型、schemaVersion、
coordinateSpace 和集合级元数据；CSV 对各类型使用固定表头，动态 attributes 作为 JSON
字符串列，Histogram 按 channel/bin 展开。
写入使用同目录临时文件、flush/fsync 和原子替换，异常时清理临时文件。目标已存在且
`overwrite=false` 时返回 `E_OUTPUT_EXISTS`，写入失败返回 `E_OUTPUT_WRITE_FAILED`。
成功输出的 `result: json` 固定包含 `saved/path/format/recordCount`；其中 `path` 会被
Runner 的 artifact 注册机制识别并发布 `artifact.created`。

### 10.5 坐标文件与坐标计算契约

`vision.io.coordinate_reader` 只读取 `.txt` 或 `.csv`，文件路径可以是绝对路径；相对路径
以 `runtimeContext.workspacePath` 为基准。节点支持 UTF-8、UTF-8 BOM、GB18030、ASCII，
以及逗号、制表符、空白和分号分隔；`xColumn/yColumn` 使用从 0 开始的列索引。
`headerMode=auto|present|absent` 控制表头，空行和注释行会忽略，其他错误行由
`onInvalidRow=error|skip` 决定。文件大小、点数都有显式上限，所有坐标必须为有限数值。

输出 `points` 是强类型 `list<point2d>`，不是需要下游自行解析的 JSON。连接 `frame` 时，
所有点直接沿用该 frame 的 coordinateSpace；未连接时由 `sourceId`、`imageWidth`、
`imageHeight` 和 `transformToSource` 建立坐标空间。`asPolygon=true` 时额外输出去除重复闭合点后的非零面积
`polygon2d`，少于三点或退化多边形返回 `E_INPUT_SHAPE`。

`vision.geometry.coordinate_calculator` 是一个多模式节点，默认 `mode=transform`，因此旧工作流
仍保持原来的变换加测量行为：

- `measure`：不改变 `points`，输出中心、BBox、逐段长度、路径长度、首尾距离/角度、面积和
  周长等测量结果。
- `transform`：先围绕 `pivot=origin|centroid|first|custom` 做 X/Y 缩放，再按图像坐标系
  正角顺时针旋转，最后应用 `offsetX/offsetY`，并输出变换后的点及测量结果。
- `subtract`：要求 `otherPoints`，逐项计算 `points - otherPoints`，输出
  `vectors:list<vector2d>`、`magnitudes:list<number>`。相减结果是无锚点向量，不伪装成点。
- `dot`：要求 `otherPoints`，对当前坐标空间中的 `(x,y)` 分量逐项点积，输出
  `values:list<number>` 和汇总 `dotSum:number`。
- `scalarMultiply`：使用可选 `scalar:number` 输入；未连接时使用 `scalarValue` 参数。输入端口
  优先于参数，结果继续输出为 `list<point2d>` 及对应测量值。

二元模式默认 `pairing=strict`，两组点数必须相等；`pairing=broadcast` 允许其中一组恰好只有
一个点并广播到另一组。每组内部以及每对操作数都必须使用等价 coordinateSpace，否则返回
`E_INPUT_SHAPE`。`otherPoints` 或 `scalar` 连接到不使用它的模式同样会明确报错，避免悄悄
忽略接线。所有模式始终返回 `resultCount:integer`。`closed=true` 用于 measure、transform、
scalarMultiply：去除重复闭合点并计算 signedArea、绝对 area、闭合 perimeter 和 polygon；
退化闭合路径会明确报错。

### 10.6 PLC 与 TCP 通讯契约

`communication.plc.slmp_read/write` 实现的是 `F:\gateway` 现场使用的三菱 SLMP/MC 3E
二进制协议，不代表通用 Modbus、S7、OPC UA 或 EtherNet/IP。直连默认路由参数为
`networkNo=0`、`pcNo=255`、`moduleIoNo=1023`、`moduleStationNo=0`。请求和响应均有连接、
读取超时，响应长度上限为结束码加 960 words；畸形子头、长度和提前 EOF 不会被当作
成功值。

Read 的 `count` 表示逻辑值数量；32 位/float32 会换算为两倍 word 数后再执行 960 words
上限检查。Write 每次必须恰好使用 `data`、`value`、`values` 或参数 `values` 中的一种。
M 设备以 word-unit 访问时每个 word 覆盖 16 个连续 M 点，因此地址上限按
`startAddress + wordCount * 16 - 1` 检查；D 设备仍按每 word 一个地址检查。
连接 `data` 时沿用其 device/startAddress/dataType；其他输入使用属性面板的目标配置。
Read 对连接/协议传输失败默认重试一次。Write 即使写入相同值通常具有状态幂等性，也可能
在响应丢失时已经被 PLC 执行，因此默认不自动重试；只有确认现场写入可安全重复时才显式
提高 `retryCount`。PLC 返回非零 end code 始终不重试并返回 `E_PLC_RESPONSE`。

Read 始终保留完整 `values:plcValueCollection`，并由 `outputIndex`（默认 0，必须小于
`count`）选择一个业务标量：非 bit 数据只额外返回 `numberValue:number`，bit 数据只额外
返回 `booleanValue:boolean`，不会同时返回两个标量端口。设备名在执行前统一规范化为大写；
float32 值必须可表示为有限 IEEE-754 单精度数。

`communication.tcp.client` 支持只发送或发送后接收。通用 TCP 发送默认不重试，避免对
机器人、执行器或业务服务造成重复动作。`communication.tcp.receive_once` 是适配当前同步
Runner 的有界单次监听节点：默认只绑定 `127.0.0.1`，accept/read 都必须设置有限超时，
接收一条消息后关闭 listener；它不是后台常驻 TCP 服务。

Client 的最终发送字节数（包括可选换行）不得超过 `maxRequestBytes`；Receive Once 的 ACK
（同样包括可选换行）不得超过 `maxAckBytes`，二者默认 1 MiB、最大 16 MiB。idle 分帧使用
读取总 deadline，但发送 ACK 前会恢复完整的 I/O timeout，避免继承接收阶段的剩余超时。

TCP Client 始终保留 `response:tcpMessage`，当 `responseTextEncoding` 不是 `none` 时，
还会直接从原始响应字节生成 `responseText:string`；该配置只允许用于 `operation=exchange`。
Receive Once 同理通过 `messageTextEncoding` 可选输出 `text:string`。文本解码与
`responseEncoding/messageEncoding` 的 DTO 存储形式完全独立，因此 DTO 使用 hex/base64
时仍可按 UTF-8、ASCII 或 Latin-1 解码。非法文本字节返回 `E_RESULT_INVALID`。

TCP 是字节流，没有天然消息边界。两个节点都要求显式选择 `newline`、`quoted`、`idle`
或 `fixedLength`：newline 去除末尾 CRLF，quoted 保留完整外层引号，idle 在收到至少一个
字节后以读超时作为边界，fixedLength 必须给出 `expectedBytes`。所有模式都强制最大消息
字节数；正式协议优先使用 newline、quoted 或 fixedLength，idle 仅适合无法修改发送端的
兼容场景。通讯错误统一使用 `E_COMM_CONNECT_FAILED/E_COMM_TIMEOUT/E_COMM_IO/`
`E_COMM_PROTOCOL/E_PLC_RESPONSE`。

### 10.7 华睿 IMV 相机契约

`vision.io.huaray_camera` 只支持华睿 IMV 直连设备，不支持 IMVFG 采集卡。节点无输入，
每次执行固定输出一帧 `image:image`、完整 `frame:bbox2d`、SDK 原始 `blockId`、
`deviceTimestamp` 和曝光读回 `actualExposureUs`。相机 ROI 的实际 `OffsetX/OffsetY` 写入
`frame.coordinateSpace.transformToSource` 平移项；`sourceId` 优先使用
`DeviceSerialNumber`，读取失败时回退到节点选择器。

设备可按 IPv4、cameraKey、userId 或零基枚举 index 选择。硬件和软件触发使用 sequential
取帧，自由运行使用 latest-image；软件触发在每次节点执行时发送一次
`TriggerSoftware`。同步 `GetFrame` 最多阻塞 100ms 就检查一次取消，总等待时间仍由
`captureTimeoutMs` 控制。最终超时返回 `E_CAMERA_TIMEOUT` 且不重连；连接、触发和传输错误
按 `retryCount` 有界关闭、重连、重新配置。所有成功取得的 SDK Frame 无论转换成功与否都
必须调用 `IMV_ReleaseFrame`。

SDK 在首次执行节点时延迟加载，因此无 SDK 的机器仍能完成插件扫描。运行库从
`HUARAY_MV_VIEWER_ROOT` 或标准 MV Viewer 安装目录下的 `Runtime/x64|Win32/MVSDKmd.dll`
解析，项目不复制厂商 DLL。只有显式启用的曝光、GainRaw、帧率、ROI 和 PixelFormat 才写入
设备；不支持、超范围、步长不匹配或读回不一致均返回 `E_CAMERA_CONFIG_FAILED`。同一 Job
内按节点缓存会话并在最外层工作流结束时释放，不能跨 Job 共享。

RGB 算子的 `roi` 接受点、BBox、旋转框、多边形、Line 和 Circle；点按其所在单个像素
计算，Line 按 1 像素线宽栅格化，Circle 填充圆内区域。Blob 的
可选输入 `mask` 必须是与原图同尺寸的单通道 `uint8` 图像；输入后会跳过 Blob 内部
阈值化，所有非零值按前景处理。Blob 输出的 `mask` 只保留通过面积过滤的连通域。
Threshold 输出固定为仅含 0/255 的单通道 `uint8` 掩码；固定、Otsu、Triangle 模式
同时返回实际阈值，自适应模式的 `threshold` 为 `null`。HSV 使用 OpenCV uint8
范围（H 为 0..179，S/V 为 0..255）；LAB 使用 OpenCV 的 uint8 编码。YOLO 后端按需加载并按模型路径与 device 缓存，
因此算子注册不依赖 Ultralytics；实际推理机器可使用 `pip install -e .[yolo]` 安装
可选后端。模型路径可以是绝对路径，也可以相对作业工作区。用于闭源、内部或商业
部署前，应按 [Ultralytics 官方授权说明](https://www.ultralytics.com/license) 确认
AGPL-3.0 或 Enterprise License 的适用方式。
