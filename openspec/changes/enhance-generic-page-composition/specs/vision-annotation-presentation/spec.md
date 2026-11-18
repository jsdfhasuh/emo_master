# 通用视觉标注表示

## Purpose

增强现有 Annotate 的分类颜色、中文标签、标签底色和连线表示，使生产页与历史页直接复用同一张实际结果图。继续使用现有几何输入和位图输出，不建立前端交互覆盖层，不改变检测算法或业务判向。

本版采用一条当前绘制路径，中文和英文使用当前字体配置，不维护旧工程的英文绘制分支。新增表现用一张综合输入检查；字体资源检查针对当前执行环境的正式 Python wheel，不追加旧版本像素回归或外部 Windows 冻结发行前置验收。

## ADDED Requirements

### Requirement: Current Class Labels and Annotation Inputs

Annotate SHALL 继续使用 `vision.render.annotate` 的 image/frame、detections、lines、circles 等正式输入和 overlay/frame 输出，在输入图像副本上绘制。标注 SHALL 不修改输入数组、检测框、坐标系或判定值，也不根据项目特定标签自动选择、配对头尾对象。现有 `drawLabels`、`fontScale`、线宽及绘制开关继续可配置。

Annotate SHALL 支持 `classStyles`，以 `detection.label` 精确匹配可选的 `displayLabel` 和 `color`。未覆盖的名称和颜色 SHALL 使用原类别标签及当前 `detectionColor`。颜色 SHALL 沿用 `[B,G,R]` 三整数数组，每项 0 至 255；显示映射不改变类别、置信度或几何。`showConfidence` 当前默认 true，`labelBackground` 当前默认 false；文字和背景按实际文字尺寸绘制并保持可读对比。

#### Scenario: Configure a class style
- **WHEN** 对已知类别配置显示名称、置信度开关和颜色
- **THEN** 标注按配置展示，同时保持输入类别及检测参数

### Requirement: Bundle one current font with the execution environment

中文和英文 SHALL 统一使用已有 Pillow 与执行环境包提供的可分发简体中文字体。字体及许可 SHALL 进入当前正式 Python wheel，通过包资源路径读取，不依赖系统碰巧安装字体、在线下载或 QApplication。字体只由执行 Annotate 的环境提供，不要求每个 `.emoproj` 或 `.emoweb` 重复携带；浏览器直接显示标注位图。

#### Scenario: Render Readable Current Labels without Changing Inputs

- **WHEN** 使用当前 wheel 的包内字体，在 headless 环境为三个检测类别配置中文或英文显示名、独立颜色、标签底色并隐藏置信度
- **THEN** 综合结果图显示可读标签、相应类别颜色和底色，全部文字使用当前包内字体
- **AND** 输入图像、frame 坐标、原类别及置信度保持不变

### Requirement: Color Output and Line Styles

Annotate SHALL 支持 `colorMode='preserve'|'bgr'`，当前默认 `preserve`。需要彩色标注的灰度样例 SHALL 明确使用 `bgr`，仅将绘图副本转换为 BGR；Pillow 所需的 BGR/RGB 转换只作用于该副本，不改变原始图像与测量输入。

`lineStyle` SHALL 支持 `solid/dashed`，当前默认 `solid`；`endpointMarker` SHALL 支持 `none/dot`，当前默认 `none`。`lineColor` SHALL 使用可选 BGR 三整数数组，缺省使用当前 `detectionColor`；端点使用该线条颜色。这些参数控制同一实现路径，不按工程版本分支。

线段和端点 SHALL 直接来自现有 LineCollection，保持原坐标，不重新推算检测对象关系。页面图例 SHALL 与工程配置的分类名称、颜色一致。

#### Scenario: Draw Color Labels and a White Dashed Line on Grayscale

- **WHEN** 同一综合输入使用灰度底图、三类检测和明确线段，配置 `colorMode='bgr'`、`lineStyle='dashed'`、`lineColor=[255,255,255]`、`endpointMarker='dot'`
- **THEN** overlay 显示各分类彩色标注及白色虚线、端点，原灰度输入不变
- **AND** 连线位置与输入几何一致，算子不生成或修改业务判定

### Requirement: Editable Annotation Settings

参数 SHALL 同步进入当前算子 manifest、参数校验和既有参数编辑入口，并随工程保存重读。配置 SHALL 能由普通工程使用，不依赖临时脚本、项目专用绘图算子或手工修改页面 HTML。manifest 与执行实现 SHALL 对当前参数和默认值保持一致。

#### Scenario: Save and Reopen Annotation Settings

- **WHEN** 用户在正式编辑入口配置类别名称、颜色、标签底色和输出颜色模式，保存工程后重开
- **THEN** 配置保留，同一个 Annotate 算子按配置生成结果图
- **AND** `drawLabels` 和 `fontScale` 等普通参数仍可使用

### Requirement: Reuse One Result Image in Production and History

生产页、Web 页面和历史归档 SHALL 直接使用同一 overlay 输出。软件模拟生产 SHALL 明确标记模拟数据，并通过既有 Image Saver 和 SQLite Writer 保存结果图及对应记录；历史页显示该次实际归档的图，不重新绘制可能不同的几何，也不以手工效果图替代运行结果。

#### Scenario: Review the Saved Production Annotation

- **WHEN** 模拟生产将 Annotate 的 overlay 显示在生产页并实际保存，随后用户在历史页选中该次记录
- **THEN** 历史图与该次生产显示具有相同分类名称、颜色和连线，Qt 与 Web 使用同一张结果位图
- **AND** 页面上的模拟标记简洁可见，结果不被表述为真实模型精度或现场机械验证
