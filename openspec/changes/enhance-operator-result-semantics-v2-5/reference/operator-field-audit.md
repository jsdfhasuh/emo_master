# 82 个内置算子逐字段审查

**计划与 P0 审查由 ChatGPT Web 于 2026-11-25 完成，共审查 82 个算子。** 表内源码和统计口径保留 P0 基线，当前实现证据见下文。

源码基线：`ed98a0212820efd0e6436029b69e82dc6e313fc7`，tree：`87bcb6c714d0986b49210eddebf835aaf1922401`。源工作区干净，已含 PR19、PR25。实现后若基线有新增提交，只复核受影响条目。

## 如何使用本记录

- 本表给出每个实际 operatorId 的版本、覆盖计数和最重要的源码结论；点击 operatorId 可打开固定版本的 Manifest。
- [operator-field-audit.json](operator-field-audit.json) 保存全部字段含义、真实参数来源、适用条件/单位、实际类型、敏感性、缺失项决定和源码证据。端口 contract 与 paramSchemaAtBaseline 保留当前类型、必需性、默认值、枚举与约束；schemaSource 指向需要与 Manifest 同步修改的真实定义。
- 运行身份、参数阶段、完成事件白名单、调试资源准备及安全副本，统一见 [runtime-field-audit.md](runtime-field-audit.md)，不在 82 条中重复通用生命周期测试。
- 没有专用 metrics/diagnostics 的空列表表示已核对当前实现，不等于漏审；已有输出能够说明结果时，明确不增加重复指标。动态变量/调试端口按其真实生成规则说明，不冒充静态 Manifest 端口。
- 审查路径中的 `[]` 表示数组元素、点号表示文档层级；它们是阅读记法。实现的 captureParams 使用 design.md 中的路径片段数组，不由此引入通用路径表达式语言。

## 覆盖汇总

| 项目 | 数量 |
|---|---:|
| 算子 | 82（数据/控制 28，视觉 31，I/O/设备 23） |
| 输入 / 输出端口 | 194 / 235 |
| 顶层配置参数 | 366 |
| 参数审查记录（含嵌套及数组元素） | 419 |
| 托管设备参数记录 | 163 |
| metrics / diagnostics 记录 | 131 / 221 |
| 复杂输出内部字段记录 | 313 |
| 处理决定 | 261 |

顶层参数中 301 项缺少 description；其余说明仍按实际行为核对，不能仅凭有文字就判为正确。所有计数按 operatorId 分别统计，共享字段会在相关算子中重复出现；不是新建业务指标的目标数量。

## P1 实施与静态验证

Codex（6.2 Sol）已落实 82 个内置算子的元数据，涵盖数据和控制 28 个、视觉 31 个、I/O 和设备 23 个。通过实际 PluginRegistry 注册后比对逐字段审查记录，覆盖 194 个输入、235 个输出、419 条参数记录、313 个嵌套输出字段、130 个现有指标和 221 个现有诊断字段。EmptyOperator 的固定零耗时已移除。字段说明覆盖检查也遍历了实际参数 schema 的嵌套结构。

- 数据和控制组已分别记录集合过滤生效条件、选择索引来源、自动排序键、坐标旋转中心和变量实际操作。FlowSwitch 在参数准备后保留缺失与显式空串的区别。
- 视觉组已说明裁剪与填充、数值结果的空值、模型实际输入形状、阈值和坐标来源。多数算子直接复用原有结果字段。
- I/O 和设备组已说明 ImageSaver 提交与保存时序、通信回执和 SQLite 提交状态。调试路径沿用资源准备链路，选定设备参数来自实际绑定槽及设备定义，列映射只记录来源类型的数量。

产品运行与待独立走查的范围见 [交付记录](../delivery.md)。

### SQLite 后台写入增量审查

main 提交 `5f337cb515af00468e508f6979323170fd990474` 为 `vision.io.sqlite_writer` 增加了写入方式，算子版本沿用 1.1.0。上表数字和逐算子索引保留 ChatGPT Web 在 `ed98a021` 的 P0 口径。Codex 在 SQLite 条目追加了 `writeMode`，参数审查记录从 419 条增至 420 条。字段依据和覆盖旧语义的决定见 [SQLite 增量记录所在的逐字段 JSON](operator-field-audit.json)。

同步模式默认启用。后台模式的节点输出是 `QUEUED`，行数和主键均待定。正式运行以同一节点执行和 `writeId` 的 `sqlite.write.finished` 核对最终入库结果。单算子调试可同时查看入队输出和隔离库中的最终诊断回执。已确认取消且未提交时行数为 0，提交情况未知时行数为 null。入队耗时和包含排队的最终耗时分别随对应回执记录。工作进程退出后补记的 `UNKNOWN` 沿用开始事件的耗时占位。[后台写入实现](https://github.com/jsdfhasuh/emo_master/blob/5f337cb515af00468e508f6979323170fd990474/src/emo_master/plugins/builtins/sqlite_writer/operator.py#L163-L178) · [最终回执归集](https://github.com/jsdfhasuh/emo_master/blob/5f337cb515af00468e508f6979323170fd990474/src/emo_master/apps/runtime/business_sqlite/background_receipts.py#L43-L82) · [工作进程退出补记](https://github.com/jsdfhasuh/emo_master/blob/5f337cb515af00468e508f6979323170fd990474/src/emo_master/apps/runtime/business_sqlite/outcomes.py#L23-L41)

在仓库根目录使用项目 Python 环境复查审查记录与注册结果。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python openspec/changes/enhance-operator-result-semantics-v2-5/reference/check_operator_semantics.py
```

## 逐算子索引

所有条目状态均为**已审查**。表中“参数”是顶层配置参数数，“M/D”是现有 metrics/diagnostics 的字段审查记录数；嵌套配置、设备参数和复杂输出详见 JSON。

| # | operatorId | 版本 | 输入/输出 | 参数 | M/D | 源码结论与 P1 注意点 |
|---:|---|---|---:|---:|---:|---|
| 1 | [vision.image.absdiff](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/absdiff/manifest.json) | 1.0.0 | 4/2 | 0 | 2/1 | 补同尺寸与坐标空间要求；复用现有内部耗时，operation 只是操作名称而非性能指标。 |
| 2 | [vision.image.add_weighted](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/add_weighted/manifest.json) | 1.0.0 | 4/2 | 3 | 2/1 | 需明确加权公式及权重和不必为一；输入尺寸与坐标须匹配，现有操作名不算性能值。 |
| 3 | [vision.preprocess.affine](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/affine/manifest.json) | 1.0.0 | 3/3 | 6 | 2/1 | 补源点和目标点对象的横纵坐标结构；三对点决定仿射变换，画布及有效掩膜需单独说明。 |
| 4 | [vision.render.annotate](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/annotate/manifest.json) | 1.2.0 | 10/3 | 17 | 2/1 | drawnCount 是遍历对象数，不是绘制图元数；说明部分开关仅作用于连通域及动态类别样式。 |
| 5 | [vision.mask.apply](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/apply_mask/manifest.json) | 1.0.0 | 4/2 | 2 | 2/1 | 非零掩膜先归一化，maskFrame 只校验空间；说明反选与填充值，不新增覆盖率计数。 |
| 6 | [vision.analysis.blob](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/blob_analysis/manifest.json) | 1.2.0 | 3/4 | 8 | 5/1 | 外部掩膜覆盖图像阈值分割，面积为像素数且轮廓未必存在；复用来源、数量与实际阈值。 |
| 7 | [vision.preprocess.blur](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/blur/manifest.json) | 1.1.0 | 2/2 | 7 | 2/1 | 复用已有模式条件和参数说明，生效摘要只列当前滤波模式字段；不补推测的平滑质量。 |
| 8 | [vision.edge.canny](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/canny_edge/manifest.json) | 1.1.1 | 2/3 | 4 | 1/1 | 补双阈值大小关系和梯度开关用途；保留现有内部耗时，不新增无依据的边缘质量指标。 |
| 9 | [vision.preprocess.clahe](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/clahe/manifest.json) | 1.0.0 | 2/2 | 3 | 1/1 | 网格宽高表示分块数量而非像素；复用已完整的参数说明，明确彩色图只处理亮度通道。 |
| 10 | [vision.collection.count](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/collection_count/manifest.json) | 1.1.0 | 7/1 | 1 | 3/1 | 七类集合都按实际项数计数，空集合为零；复用既有输出与统计，避免重复计数。 |
| 11 | [vision.collection.filter](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/collection_filter/manifest.json) | 1.3.0 | 8/16 | 42 | 4/1 | 生效摘要需在筛选条件归一化后取值；关闭或不适用的条件不能按保存草稿显示。 |
| 12 | [vision.collection.select](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/collection_select/manifest.json) | 1.1.0 | 8/8 | 5 | 3/1 | 索引从零开始且输入优先；topK 只取当前顺序前项，不会先排序，复用选择数量。 |
| 13 | [vision.collection.sort](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/collection_sort/manifest.json) | 1.1.0 | 7/7 | 3 | 4/1 | 空排序键按实际集合类型解析默认字段；同值保持原顺序，缺失值始终放在末尾。 |
| 14 | [vision.preprocess.color_convert](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/color_convert/manifest.json) | 1.0.0 | 2/2 | 2 | 3/1 | 输入颜色空间是用户声明而非自动探测；说明灰度与三通道约束，复用实际转换模式。 |
| 15 | [vision.analysis.contour](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/contour/manifest.json) | 1.0.0 | 2/3 | 2 | 3/1 | 说明退化轮廓剔除及层级字段，引用真实序列化 id；复用已有保留数量与丢弃数量。 |
| 16 | [vision.geometry.coordinate_calculator](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/coordinate_calculator/manifest.json) | 1.2.0 | 3/16 | 12 | 3/1 | 质心为点坐标均值，面积仅闭合模式有意义；标明标量输入覆盖与实际变换中心。 |
| 17 | [communication.coordinate_message_format](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/coordinate_message_format/manifest.json) | 1.0.0 | 2/1 | 4 | 0/0 | 仅格式化逗号分隔的坐标文本，后缀和引号按配置追加；不自动换行，也不执行发送。 |
| 18 | [vision.io.coordinate_reader](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/coordinate_reader/manifest.json) | 1.1.0 | 1/4 | 17 | 4/3 | 会话读取使用冻结字节及内容哈希，文件改变不立即更新；解释 frame 覆盖与闭合末点处理。 |
| 19 | [vision.preprocess.crop](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/crop/manifest.json) | 1.0.0 | 3/2 | 10 | 5/1 | ROI 输入覆盖矩形参数，需解释取整、裁边和填充区别；实际裁剪框已有统计可复用。 |
| 20 | [vision.io.detection_archive](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/detection_archive/manifest.json) | 1.0.0 | 4/3 | 2 | 0/6 | 无检测和冷却跳过是正常结果，saved=false 不代表失败；中断可能已保存部分文件，保留进度。 |
| 21 | [vision.geometry.detection_bbox](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/detection_bbox/manifest.json) | 1.0.0 | 1/1 | 0 | 0/1 | 只接受恰好一个检测对象并透传已有框；空集合或多对象需先选择，不新增重复指标。 |
| 22 | [vision.state.elapsed_time](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/elapsed_time/manifest.json) | 1.0.0 | 1/2 | 0 | 0/0 | 累计当前算子实例自首次执行起的单调时钟秒差；它是业务输出，不是节点运行耗时。 |
| 23 | [vision.demo.empty](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/empty_operator/manifest.json) | 0.1.0 | 1/1 | 1 | 1/1 | enabled 当前只校验不控制执行；硬编码零耗时不能视为实测，优先复用框架耗时。 |
| 24 | [vision.preprocess.equalize](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/equalize/manifest.json) | 1.0.0 | 2/2 | 0 | 1/1 | 灰度直接均衡，彩色仅处理 LAB 亮度通道；复用既有内部耗时，不补泛化性能指标。 |
| 25 | [vision.io.file_list](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/file_list/manifest.json) | 1.0.0 | 0/2 | 3 | 1/1 | 结果按相对路径自然排序，匹配忽略大小写；无匹配是成功空列表，复用已有文件数量输出。 |
| 26 | [vision.preprocess.flip](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/flip/manifest.json) | 1.0.0 | 3/3 | 1 | 2/1 | 翻转应同时解释图像、有效掩膜与回源坐标；模式是配置分类，复用已有内部耗时。 |
| 27 | [vision.flow.error](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/flow_error/manifest.json) | 1.0.0 | 1/0 | 2 | 0/0 | 有效执行就是主动技术停止；沿用真实错误码和说明，不展示为通过或新增业务判定。 |
| 28 | [vision.flow.if](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/flow_if/manifest.json) | 1.0.0 | 1/2 | 2 | 0/0 | 仅命中分支透传原值，另一端口缺省；相等模式按字符串精确比较，非数值阈值判断。 |
| 29 | [vision.flow.switch](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/flow_switch/manifest.json) | 1.0.0 | 1/5 | 4 | 0/0 | 配置缺失与显式空串必须区分；按分支顺序取首个字符串匹配，不无条件补空默认。 |
| 30 | [communication.gateway.ack_validate](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/gateway_ack_validate/manifest.json) | 1.0.0 | 2/1 | 1 | 0/4 | 只有与本次请求精确关联才输出确认；不匹配走错误且不重发，确认不等于物理动作完成。 |
| 31 | [communication.gateway.coordinate_format](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/gateway_coordinate_format/manifest.json) | 1.0.0 | 1/3 | 2 | 0/4 | 机器人与 PLC 的舍入及应答格式不同；expectedAck 是预期值，不是确认结果，也不换算单位。 |
| 32 | [vision.geometry.extract_points](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/geometry_points/manifest.json) | 1.0.0 | 3/2 | 2 | 0/0 | 按所选几何特征提取已有坐标并保留顺序；空集合计数为零，复用现有点列表输出。 |
| 33 | [vision.state.counter](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/global_counter/manifest.json) | 1.0.0 | 2/1 | 1 | 1/1 | 计数器按项目和名称共享持久化，输出原子操作后的实际值；不能用节点执行次数替代。 |
| 34 | [communication.camera.hikvision_audio](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/hikvision_audio/manifest.json) | 1.0.0 | 1/4 | 10 | 1/3 | 音量和播放次数参数是验收上限，实际值来自读取；触发被接受不代表声音播放完成。 |
| 35 | [vision.analysis.histogram](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/histogram/manifest.json) | 1.1.0 | 5/2 | 2 | 3/1 | 解释交集选区、通道顺序与空选区概率；复用像素数和分箱数，版本须取实际入口子类。 |
| 36 | [vision.analysis.hough_circle](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/hough_circle/manifest.json) | 1.0.0 | 2/2 | 6 | 2/1 | 补圆检测参数用途、像素单位与零值条件；已有圆数量只表示输出对象数，不推断合格状态。 |
| 37 | [vision.analysis.hough_line](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/hough_line/manifest.json) | 1.0.0 | 2/2 | 5 | 2/1 | 阈值是霍夫投票阈值，角度配置由度转弧度；复用现有线段数，勿称业务合格数。 |
| 38 | [vision.io.huaray_camera](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/huaray_camera/manifest.json) | 1.2.0 | 0/5 | 27 | 2/37 | 曝光值需按证据来源说明，未知保留空；SDK 原始时间戳单位未确认，不能一律称逐帧硬件测量。 |
| 39 | [vision.io.image_batch_loader](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/image_batch_loader/manifest.json) | 1.0.0 | 1/6 | 3 | 1/1 | 文件索引从零开始、日志进度从一开始；末帧仍有有效图像，下一次耗尽才报错，复用现有进度。 |
| 40 | [vision.io.image_loader](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/image_loader/manifest.json) | 1.1.0 | 0/2 | 2 | 1/1 | 记录资源处理后实际读取路径及来源，不能拿旧草稿路径代替；解释 BGR、灰度与回源框。 |
| 41 | [vision.io.image_saver](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/image_saver/manifest.json) | 1.2.0 | 2/1 | 4 | 1/1 | 后台接受提交不等于保存完成，提交路径尚未发布；复用已有任务和文件证据区分执行阶段。 |
| 42 | [vision.segment.in_range](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/in_range/manifest.json) | 1.0.0 | 2/2 | 3 | 3/1 | 颜色空间用于逐通道范围校验，不执行颜色转换；复用已有边界说明及真实前景像素数。 |
| 43 | [vision.math.integer_add](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/integer_add/manifest.json) | 1.0.0 | 2/1 | 2 | 0/0 | 两侧输入覆盖备用整数参数；结果受有符号六十四位范围限制，复用实际操作数来源。 |
| 44 | [vision.geometry.line_from_points](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/line_from_points/manifest.json) | 1.0.0 | 3/2 | 0 | 0/0 | 仅双端点均存在时生成一条线；任一端为空输出空集合，数量只可能为零或一。 |
| 45 | [vision.mask.logic](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/mask_logic/manifest.json) | 1.0.0 | 4/2 | 1 | 3/1 | 非反相模式必须提供第二掩膜；像素逻辑运算与 frame 交集要分开解释，复用已有统计。 |
| 46 | [vision.analysis.minimum_enclosing_circle](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/minimum_enclosing_circle/manifest.json) | 1.1.0 | 1/2 | 1 | 0/3 | 逐轮廓或合并顶点求最小外接圆；复用已有来源轮廓诊断，不复制圆心半径为指标。 |
| 47 | [vision.preprocess.morphology](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/morphology/manifest.json) | 1.0.0 | 2/2 | 4 | 3/1 | 实际核尺寸必须为奇数，不能只按 schema 最小值说明；复用既有操作名与前景计数。 |
| 48 | [vision.math.absolute](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/number_absolute/manifest.json) | 1.0.0 | 1/1 | 0 | 0/0 | 绝对值保留整数或浮点类型，不取整也不改单位；复用既有结果与框架耗时即可。 |
| 49 | [vision.compare.number](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/number_compare/manifest.json) | 1.0.0 | 2/1 | 2 | 3/1 | 右侧输入覆盖备用阈值，比较无容差；false 是合法结果，实际右值已有统计可复用。 |
| 50 | [vision.math.exact_integer](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/number_exact_integer/manifest.json) | 1.0.0 | 1/1 | 0 | 0/0 | 仅原数值没有小数时转为整数，既不截断也不容差吸附；失败不能伪造零结果。 |
| 51 | [vision.collection.number_pair](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/number_pair/manifest.json) | 1.0.0 | 2/1 | 0 | 0/0 | 严格按两个输入的先后返回列表，保留原数值类型；不排序、不拼为坐标，不补重复指标。 |
| 52 | [vision.math.truncate](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/number_truncate/manifest.json) | 1.0.0 | 1/1 | 0 | 0/0 | 按向零方向截去小数，负小数可变零；应与向下取整、四舍五入明确区分。 |
| 53 | [vision.value.number](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/number_value/manifest.json) | 1.0.0 | 0/1 | 1 | 1/1 | 输出是本次数值配置转成浮点后的常数；复用既有结果与诊断，不新增同义指标。 |
| 54 | [vision.inference.patchcore](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/patchcore_inference/manifest.json) | 1.1.0 | 2/8 | 6 | 1/18 | 区分部署判定阈值与高亮显示阈值；复用模型身份和定位状态，不把相对响应称像素缺陷。 |
| 55 | [vision.math.percentage](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/percentage/manifest.json) | 1.0.0 | 2/2 | 0 | 0/0 | 分母为零仍执行成功，数值为空且 defined=false；不可展示为零百分比或节点失败。 |
| 56 | [vision.preprocess.perspective](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/perspective/manifest.json) | 1.0.0 | 3/3 | 5 | 2/1 | 补四个源点的横纵坐标结构、顺序与非退化要求；输出画布与精确有效掩膜须区分。 |
| 57 | [communication.plc.slmp_read](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/plc_slmp_read/manifest.json) | 1.1.0 | 0/3 | 17 | 5/4 | 区分动作参数与绑定设备连接配置，并说明逻辑值数和字数；不能把旧节点默认值当生效值。 |
| 58 | [communication.plc.slmp_write](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/plc_slmp_write/manifest.json) | 1.0.0 | 3/1 | 16 | 4/4 | 协议写入回执不代表物理动作完成；说明实际设备参数来源，托管恢复不等于自动重放写入。 |
| 59 | [vision.geometry.point_components](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/point_components/manifest.json) | 1.0.0 | 1/2 | 0 | 0/0 | 读取原坐标空间中的单点横纵坐标；需要解释坐标来源，不再复制坐标为指标。 |
| 60 | [vision.geometry.reframe_points](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/points_reframe/manifest.json) | 1.0.0 | 2/1 | 0 | 0/0 | 点先回映至来源空间，再通过目标逆矩阵映入目标空间；补坐标语义，不另造计数。 |
| 61 | [vision.preprocess.resize](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/resize/manifest.json) | 1.0.0 | 2/2 | 5 | 9/1 | fit 实际尺寸未必等于配置，填充值仅用于 letterbox；复用已有尺寸、缩放与填充数据。 |
| 62 | [vision.io.result_writer](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/result_writer/manifest.json) | 1.1.0 | 13/1 | 4 | 3/1 | 记录数随格式和输入类型变化，不能一律称检测数；复用原子写入回执并同步动态输入类型源。 |
| 63 | [vision.color.rgb_statistics](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/rgb_statistics/manifest.json) | 1.1.0 | 3/3 | 3 | 2/1 | 输出保留输入像素量纲，允许非 uint8 实数；解释标准差自由度和样本数，不复制通道统计。 |
| 64 | [vision.preprocess.roi](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/roi/manifest.json) | 1.2.0 | 2/7 | 12 | 5/1 | 多区域 roi 只表达裁剪几何或包络，孔洞由掩膜表达；补嵌套条件并同步动态 schema 源。 |
| 65 | [vision.preprocess.rotate](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/rotate/manifest.json) | 1.0.0 | 3/3 | 4 | 2/1 | 区分扩展画布、有效掩膜与回源框；现有耗时仅覆盖变换段，不能称整节点总耗时。 |
| 66 | [vision.io.rtsp_camera](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/rtsp_camera/manifest.json) | 1.0.0 | 0/3 | 7 | 1/1 | 每次调用新建连接读取有效帧；采集时间是读帧后的 Unix 秒，非硬件曝光时间，连接凭据需隐藏。 |
| 67 | [vision.io.rtsp_capture](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/rtsp_capture/manifest.json) | 1.0.0 | 0/3 | 0 | 0/13 | 节点虽无自有参数，仍使用设备新鲜度和超时配置；返回未交付的最新有效帧，复用帧龄诊断。 |
| 68 | [vision.analysis.shape_measurement](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/shape_measurement/manifest.json) | 1.0.0 | 2/1 | 1 | 2/1 | 连通域输入必须有轮廓；面积是几何面积而非像素数，圆度已限幅，不复制测量值为指标。 |
| 69 | [vision.io.simulated_camera](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/simulated_camera/manifest.json) | 1.0.0 | 0/2 | 0 | 0/9 | 图像模式和等待均为确定性模拟，不代表真实硬件验证；复用帧身份与尺寸，不新增性能指标。 |
| 70 | [vision.io.sqlite_writer](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/sqlite_writer/manifest.json) | 1.1.0 | 1/1 | 7 | 0/23 | 节点成功不等于已提交，未知行数保持空；已提交后的清理异常不应触发重复写入，复用真实回执。 |
| 71 | [communication.tcp.client](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/tcp_client/manifest.json) | 1.2.0 | 2/3 | 2 | 0/8 | 连接、分帧、编码和超时来自绑定设备；区分发送与交换的条件输出，复用事务诊断。 |
| 72 | [communication.tcp.receive_once](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/tcp_receive_once/manifest.json) | 1.1.0 | 0/4 | 0 | 0/8 | 输出对端端口是实际客户端临时端口；分帧与解码配置来自设备，一次事务结束关闭连接。 |
| 73 | [communication.tcp.session](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/tcp_session/manifest.json) | 1.0.0 | 0/1 | 0 | 0/0 | 仅获取已有托管监听的会话引用，节点不启动监听；解释本轮作用域，普通摘要不展开能力令牌。 |
| 74 | [communication.tcp.session_send](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/tcp_session_send/manifest.json) | 1.0.0 | 2/1 | 0 | 0/14 | 发送回执只确认交给传输层，未确认设备动作；旧会话不能改投重连的新连接，无自动重试。 |
| 75 | [vision.analysis.template_match](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/template_match/manifest.json) | 1.0.0 | 4/2 | 8 | 3/1 | quality 随匹配方法变换，并非概率；保留原始分数、阈值方向与模板须完整落入选区的条件。 |
| 76 | [vision.preprocess.threshold](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/threshold/manifest.json) | 1.1.0 | 2/3 | 5 | 5/1 | 自适应模式没有单个阈值，输出及统计为 null；复用既有前景统计并保留空值语义。 |
| 77 | [vision.flow.select_value](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/value_select/manifest.json) | 1.0.0 | 3/1 | 0 | 0/0 | 只返回当前条件选中的原值并保留类型；不回退另一侧或上次结果，复用已有输出。 |
| 78 | [vision.state.variable_read](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/variable_read/manifest.json) | 1.0.0 | 0/1 | 1 | 0/0 | 实际变量定义决定类型与常量、项目或任务作用域；静态 any 端口不承诺任意对象。 |
| 79 | [vision.state.variable_write](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/variable_write/manifest.json) | 1.1.0 | 2/1 | 3 | 0/0 | 记录真实操作及自增时的增量；reset 恢复定义初始值而非归零，复用状态服务返回值。 |
| 80 | [vision.io.video_loader](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/video_loader/manifest.json) | 1.0.0 | 2/7 | 3 | 1/1 | 区分采样序号、原始视频帧号和视频时间戳；输入路径覆盖配置，时间戳不是墙钟耗时。 |
| 81 | [vision.inference.yolo_classification](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/yolo_classification/manifest.json) | 1.0.0 | 2/8 | 3 | 1/6 | 低置信度仍成功并保留 Top-1，仅接受标志和标签改变；复用真实预处理协议与模型输入形状。 |
| 82 | [vision.inference.yolo](https://github.com/jsdfhasuh/emo_master/blob/ed98a0212820efd0e6436029b69e82dc6e313fc7/src/emo_master/plugins/builtins/yolo_inference/manifest.json) | 1.4.0 | 2/5 | 12 | 2/9 | 固定模型维度优先于 imageSize，复用真实输入形状；缺裁剪目标的跳过原因已有诊断。 |

## 已收敛的实施规则

1. 参数说明补在现有 paramSchema，并同步 schemaSource 的定义；端口说明按 key 放入可选 resultSchema，不改端口连接合同。
2. 区分 call/effective、输入覆盖和托管设备来源；优先使用已有输出、metrics、diagnostics 证明实际值，来源无法区分就明确记录。
3. 同一数据在概览中可以合并，但保留原始来源；0、false、null、空字符串、空集合和缺失端口不得混同。
4. 只隐藏已确认的凭据与认证信息。普通模型/文件路径保留排障价值；业务 payload 按现有有界摘要处理，不一律标为秘密或整份复制。
5. ImageSaver、TCP/网关/PLC、SQLite 的回执均只说明实际证据范围；不把节点正常完成推为产品合格或物理动作完成。
6. EmptyOperator 固定零耗时应移除并复用框架耗时；无必要专用 metrics 的数值/几何算子保持现有结果表达。

本次完成的是静态审查与计划更新。没有执行 82 个算子、加载真实模型、连接硬件或进行 Qt 验收；运行验证仍按 tasks.md 的少量代表与共用机制安排执行。
