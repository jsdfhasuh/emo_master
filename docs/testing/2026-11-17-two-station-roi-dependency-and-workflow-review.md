# 双工位工程：ROI依赖写回、复验及流程压缩评估

## 本轮范围与当前结论

用户要求“写回这条依赖修正并复验”，并检查整体流程能否减少子流程；随后明确“页面可以先不管，我后面打算重写页面”。本轮完成主工程的一字段修正及复验；流程结构只作只读清点和内存候选预览，未写回压缩结构，未修改页面或平台代码。

- 日期：2026-11-17，Asia/Shanghai。
- 源码HEAD：`31afa5ecab8b7ddb53f0d6bdedc620f0a243e340`；开始工作区干净，复验时也无未提交源码修改。本报告在测试后新增，不能把最终含报告的工作区说成全目录干净。工程在独立目录，按自己的哈希记录。
- 与11月16日的HEAD `556acb10760a9463fe832aa4d8e7fc5c0a95a67e`核对后，工程模型/读写、工作流核心及图像插件没有变化；Designer调用链/标签导航等已有更新，故按本轮HEAD重新验证，不重复实现已有修改。
- 当前主工程仍为46流程、371节点；工程2.9/页面1.2/设备1.0，7个ONNX、9份坐标TXT、5设备、34项算子依赖。
- FastCtx不在可调用工具清单中，本轮用有界rg/PowerShell读取和JSON结构化解析。一次PowerShell对象模式解析遇到空属性名，改用`ConvertFrom-Json -AsHashtable`后成功；这不是工程损坏，未据该失败输出的空值作结论。

## 已写回的一字段修正

工程：`C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/two_station_screw_vision.emoproj`。

仅修改`dependencies.operators[28].version`，对应`vision.preprocess.roi`：**1.1.0 → 1.2.0**。

| 项目 | 证据 |
| --- | --- |
| 修改前SHA256 | `fd34f7f0bca88fef969cc347a58b06fb59df6ba1c59932ee67c17dc41fbb4f46` |
| 修改后SHA256 | `0d76952c11ae128dccfceb020e2cd5b0754e9c45b617746b6bbab3672d143df0` |
| 独立备份 | `C:/Users/dd109/my_work/emo_master/build/two_station_roi_dependency_20261117/before-roi-dependency.emoproj` |

先复制原字节到独立备份，生成仅一字段变化的暂存候选，通过正式模型、依赖、纯Runtime准备、编译和保存重读后，定点修改主文件。未覆盖原`.emoproj.bak`，没有重建工程或执行历史迁移/一次性应用脚本。ROI坐标、裁图方式和算法参数没有变化。

## 主工程复验结果

`test_applied.py`：**3 passed，26.50秒**，日志和JUnit保存在本轮证据目录。

1. 从实际主文件重读，完整JSON与备份仅在指定依赖版本处不同。ProjectDocument校验、34项精确依赖、5设备定义、46流程编译、页面绑定以及纯`prepareRuntimeProject`均通过。旧`E_OPERATOR_DEPENDENCY`缺口已实际关闭，不只是临时候选通过。
2. 标准`saveProject`保存临时副本、`loadProjectDocument`重读全文档相等，再编译/检查依赖通过。
3. 复用11月16日只读Designer验收函数，在本轮临时目录和隔离QSettings中运行实际`MainWindow.loadProjectDirectory`入口，切换46流程、打开相机/PLC/TCP绑定表单、临时保存重开并编译通过。查看流程截图正常。这是源码原生Qt离屏自动化，不是人工桌面或真实设备验收；页面操作发生在用户后来明确暂缓页面之前，不因此继续页面改造。

主工程模型/坐标文件及原`.bak`哈希保持。两工位commissioned仍false、8个pending-output调用保留，设备、标定、参考点、PLC和TCP规则不变。测试只有客户端替身，禁止发布/Job/设备命令及socket连接/绑定；未触发禁止入口。纯Runtime准备不等于创建或启动真实Runtime，未监听10000端口。

## 整体流程清点

从两个运行入口沿subflow和While body展开，46条中44条可达；最大6次调用跳转，即7层工作流。完整含调用节点的上游路径保存在`workflow-inventory.json`，不只列直接调用者。

当前主线为：

```text
station1-run / station2-run
  -> commissioned保护 -> While wait
  -> station*-cycle：取得本工位机器人TCP会话
  -> station*-frame：相机取图
       -> station*-sample：读D800 -> machine-code-check
       -> 拍次加1 -> 按本帧拍次分支
       -> station*-first：机型分支 -> 1匹/0.75匹首拍业务
            -> A、B图像测量 -> 坐标差值/参考点 -> 标定/PLC倍率
            -> pending-output -> station*-plc-write -> D6/D7
       -> station*-second：拍次归零 -> 机型分支 -> 1匹/0.75匹第二拍业务
            -> 孔测量 -> 对应机型参考点 -> 坐标差值/标定
            -> pending-output -> 报文格式化 -> TCP发送
       -> recognition-display：本帧识别内容/区域/检出结果
```

上图描述配置和调用关系，不是本轮实际运行的设备周期。机型代码2、3共用1匹测量，6走0.75匹；各自参考点读取规则保留。

### 可以直接去重的部分

| 项目 | 证据 | 减少数量 |
| --- | --- | ---: |
| `measure-centroid`、`measure-enclosing-circle` | 两个入口均不可达、无调用者，也无页面/入口/资源等外部引用；当前使用的合并轮廓圆测量另有流程 | 2 |
| 一工位1匹/0.75匹首拍B | 除显示名称、画布位置外，节点、参数、端口、连线及接口完全相同 | 1 |
| 一工位1匹/0.75匹第二拍孔测量 | 同样业务结构完全相同 | 1 |

这得到**42流程/345节点**的第一阶段候选。只在内存移出2个未使用流程、让两处调用复用已有同义测量流程；没有生成或安装压缩工程。42流程编译和页面绑定通过；对两个入口递归展开后的业务调用树逐项比较相等，忽略的仅是显示名称和画布位置。8个输出保护调用及其他顶层配置均保留。

代价：共用B路或孔测量后，未来修改共享链会同时影响两机型。两种机型的业务父流程、参考点和标定并未合并；需要独立调参时可以再拆分。未使用的旧方案只是建议从主流程清单移出，原完整工程备份保留，不删除历史证据。

### 页面暂缓后的进一步方案：40流程

用户明确页面以后重写后，可以再把两工位各自的`cycle`与`frame`合并，保留`station*-frame`作为While循环体，把原TCP会话节点移入。得到**40流程/339节点**的内存候选。

已完成的只是结构评估：

- ProjectDocument、精确依赖以及全部40流程编译通过。
- 原cycle递归展开的编译顺序，与新frame递归展开的算子声明逐项相等，包含参数、端口声明、设备绑定及覆写等。两工位静态序列均从TCP会话、相机、PLC读取开始；测量和状态节点参数不变。
- 这是当前同步调度器下的静态顺序对照，不是模拟周期或真实硬件的动态等价证明；不能据此声称已可投产或节拍变快。
- While循环体只输出循环状态`permit`。原frame的shot、overlay、found、回执等10项公开输出不能原封不动挂成While状态；这些值仍在原业务节点端口上，但页面/结果采集接口需要随后重做。
- 旧页面原有的`wait -> process`调用路径未迁移，内存候选页面绑定明确有8项错误。按用户本次范围只记录，不改页面，也不把这一候选当“完整可交付工程”。实际主工程46流程和现有页面仍保持有效。

第一版40流程预演试图保留frame全部公开输出，编译器正确拒绝：`While body output has no matching state input`。第一版脚本和错误保存在`preview_page_deferred_first_attempt.py`、`preview40-first-failure.txt`；随后候选按既有While状态契约只保留permit出口后通过。没有修改平台编译器、放松契约或删除失败证据。

### 不建议为数量继续强行合并的部分

- 一工位首拍A：1匹阈值80，0.75匹120，不能当相同链直接合并。
- 二工位首拍A：1匹多一层2500～6000面积过滤；两机型不是同一配置。
- 二工位首拍B：1匹YOLO孔测量，0.75匹传统真裁剪/阈值30/均值7/最长周长/bboxTopLeft，不是同一算法。
- 二工位第二拍：两机型模型分别为cal_hole3和1013，保留可见差异，不引入复杂参数开关只为少一条流程。
- `coordinate-difference-core`虽然只有两个算子，但调用接口的`leftChecked/rightChecked`要求左右单点校验先完成；不能删掉调用边界后留下无依赖的检查节点。
- `station*-sample`调用边界保证相机图像到达后才读取本帧D800，并在有效机型检查完成后才放出计数依赖；简单展开可能提前读PLC或让计数先走，不能只复制节点便称等价。
- 保留两运行入口、机型校验、PLC幅值/方向规则、参考点选择和输出保护。共享的小流程不必全部展开，否则多个位置维护相同规则更难，也不利于独立离线测量测试。

## 下一步与边界

压缩目标建议分两步落实：先42条的去重，再按页面暂缓范围推进40条的循环层合并。**本轮没有落实结构压缩**。落实40条前/同时，用隔离模拟设备对比会话/相机/PLC次序、合法与非法机型、两拍计数和取消行为；保留已确认的图像结果与保护，不用真实设备来试错。

依赖修正已经完成，流程压缩不是现有46条工程进入分项实机验收的新增前置。真实连接/Runtime加载/输出测试另按明确授权开展。没有重跑图像、训练模型、现场参数读回、PLC读写、TCP监听/发送、机器人动作、解除保护、发布、提交或推送。

## 证据

- 根目录：`C:/Users/dd109/my_work/emo_master/build/two_station_roi_dependency_20261117/`。
- `baseline.json`、独立备份、标准保存的依赖候选、`applied-verification.json`、`pytest.log/xml`、`native/`。
- `workflow-inventory.json`：所有流程调用与完整入口到叶子路径、外部引用、两机型对应链的逐字段比较。
- `simplification-preview.json`：42条内存候选、展开调用树一致性及编译结果。
- `page-deferred-preview.json`：40条内存候选、静态顺序对比和明确保留的页面错误。
- 当前工程说明：`C:/Users/dd109/my_work/emo-master-project/two_station_screw_vision/CURRENT_STATUS_2026-11-17.md`。
