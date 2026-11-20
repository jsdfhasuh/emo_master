# 双工位工程30流程压缩验收

## 范围与版本

用户批准进一步压缩流程，页面暂不重写。本轮从实际46流程工程做局部结构变换，不从模板生成，不重跑历史迁移脚本，不改平台源码、算法、标定、PLC/TCP或保护。

- 源码0.6.6，HEAD `31afa5ecab8b7ddb53f0d6bdedc620f0a243e340`；Python 3.10环境，PySide2/Qt5。
- 开始时工作区有未跟踪报告`docs/testing/2026-11-17-two-station-roi-dependency-and-workflow-review.md`，本轮新增本报告，平台源码无改动。外部工程和build证据不属于该提交的干净交付包。
- 主工程：[two_station_screw_vision.emoproj](C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/two_station_screw_vision.emoproj)。
- 压缩前SHA256：`0d76952c11ae128dccfceb020e2cd5b0754e9c45b617746b6bbab3672d143df0`。
- 安装后SHA256：`07d2d959e1ea3fa6acade14e0226702b0453c416be7de0003e9265413ad6279e`。
- 工程2.9/页面1.2/设备1.0不变；ROI精确依赖1.2.0保留。7个ONNX、9份TXT和原`.bak`哈希未变。

## 已实施

46流程/371节点变成30流程/321节点：内联12条测量链至8条原业务父流程；合并两对cycle/frame；删除2条无调用测量流程。保留机型/拍次分支、PLC采样边界、两级单点校验相减、参考点读取、合并轮廓外接圆和8处输出保护。

内联节点使用`a__`/`b__`前缀，参数、端口、设备/变量绑定和测量连线逐项对照。While循环体改为`stationN-frame`，仅公开`permit:boolean`状态，原报告结果仍由相应节点/子流程输出。两个会话节点从cycle移入frame，顺序仍为会话、相机、D800、合法机型后计数。

没有重写页面内容和布局，只修正2个结果范围和6个数据源。阶段绑定至frame内`isLocation/result`；图像/检出结果绑定至`recognition-display`的工作流输出，路径为`wait(loop_body) -> display(subflow)`。没有新增平台功能。

## 验证结果

候选专项：**46 passed，88.61秒**。安装后实际路径复验：**3 passed，19.11秒**。不与历史测试数量相加，不代表全仓库CI、现场硬件或生产精度验收。

| 验证层次 | 本轮实际执行 |
| --- | --- |
| 结构/格式 | ProjectDocument、精确依赖、全部30流程编译、页面绑定、纯prepareRuntimeProject、标准保存重读相等 |
| 参数/资源 | 12组算子及连线逐项一致；未受影响流程相等；顶层设备/变量/标定/资源/生产/运行配置相等；17份模型/TXT/旧bak哈希保持 |
| 真实样图 | 12条测量路线前后输出对照；197/282仍零点、220恢复和空白图漏检反例一致；只规范化已记录的裁图坐标系节点名称，全部坐标、变换、源ID和数值保持严格对照 |
| 模拟周期 | 工位1/2 × D800=2/3/6，4帧拍次1/2/1/2、结束计数1/0/1/0；PLC数值、TCP文本、会话来源、设备路由、分支及顺序前后一致；二工位code6的显示步骤仅在测试替换，见下文 |
| 保护/异常 | 2个commissioned保护不访问设备；8处pending-output不写PLC/发TCP；非法机型不计数；普通会话/PLC失败和等待采图取消均不继续输出；零/多点仍被单点校验拒绝 |
| 原生Qt | 隔离QSettings/禁止Runtime命令的客户端；实际loadProjectDirectory，30流程切换、3设备表单、2页面、保存重开；安装后另从实际主文件路径打开并另存临时文件复验 |
| 离线入口 | 安装独立脚本和12条路线映射；从实际工程提取图片测量组，禁止设备和网络，真实图片冒烟通过 |

周期验证使用真实WorkflowRunner和真实图像/纯计算算子，但设备节点被测试替身拦截；全局变量采用内存测试访问器。为比较后续输出，只在深拷贝内存工程中临时放行commissioned和pending-output。正式文件仍两个false、8个保护调用。

## 仍存在的旧显示故障

**二工位0.75匹第一拍的原始显示链仍会中断完整周期。** 传统B路返回的`measurements`处于ROI裁图坐标系，测量点经reframe回原图，但整个测量集合并未回原图。`recognition-display/firstB`按原图frame叠加该集合时返回：

```text
E_INPUT_SHAPE
Measurement collection and image frame coordinate spaces do not match
```

样图：`D:/2026_8_5_0.7匹/images/B/2026-08-05_15-31-52-436.jpg`。

未压缩46条和压缩后30条均原样复现；放行保护的模拟副本在PLC写入替身已执行后才于显示阶段失败，拍次保留1。实际保护未解除，没有现场写入。不能把“页面暂不重写”理解为这个故障不影响运行，也不能自动重放已有输出的失败帧。

本轮未擅自改变确认算法或扩成平台显示改造。5个工位/机型组合包含原显示链的4帧对照通过；工位2/code6使用仅测试的显示替身验证业务4帧，另有专门测试确认旧显示故障压缩前后相同。**46 passed中的该项是故障保持可追溯的断言，不是故障已修复。完整0.75匹二工位连续实机运行前仍需处理显示坐标转换或隔离。**

## 首轮失败记录

全部保留在证据目录，不删除失败、不跳过或弱化数值断言：

- staging首次4个页面源无效：把display子流程当作operator的node_output；按平台已有工作流输出契约修正路径，不改平台。
- pytest-first：离线切片的节点端口误用了工作流描述对象，改为节点要求的字符串类型。
- pytest-second：测试context误把文件路径当资源目录，修正为实际工程目录；无模型路径改写。
- pytest-third：ROI内部reference含节点ID，按明确old/new映射规范化该标识；坐标与变换不放宽。
- pytest-fourth：相机替身缺少当前必需的时间戳/曝光输出，补齐类型契约。
- pytest-fifth：真实发现上述旧显示故障，保留整链复现和独立业务周期验证，不修改工程掩盖。
- pytest-sixth：29 passed（16个图像项及Qt项未在该次选择执行，不计入通过）；最终完整候选46项全部执行。
- 安装后首次回读3 passed（17.17秒）；补齐离屏测试字体后复跑3 passed（19.11秒），截图已查看。

## 文件与边界

- [证据目录](C:/Users/dd109/my_work/emo_master/build/two_station_compact30_20261117)：before、baseline、candidate、mapping、各轮日志/XML、verification-details、installed、applied-verification、native截图。
- [工程最新状态](C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/CURRENT_STATUS_2026-11-17.md)
- [新配置入口与离线脚本用法](C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/WORKFLOW_COMPACTION_2026-11-17.md)

未连接现场相机/PLC/机器人，未实例化真实Runtime服务，未监听TCP、发送报文或释放输出保护；未人工桌面验收、冻结产物测试、整批历史样本重跑、发布、提交或推送。以前已接受的模型待训练项不因本轮压缩重新列为待决策。
