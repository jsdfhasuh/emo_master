# 双工位硬件触发正常链路能力夹具

本目录用于验证 **现有工作流机制和实际算子能否串起正常两拍链路**，不是原工程四条算法的等价复制，也不是可直接连接现场设备的生产工程。

该项目包含两个普通工作流入口 `station1-run`、`station2-run`，各有一个 While；单帧链路仍是 `station1`、`station2`。2026-10-09 已撤回 M5 的平台级工位声明，不再使用 `production.stations` 或工位配置表单。源码和模拟边界测试不等于原算法或现场设备验收。

## 1 两拍控制没有专用阶段节点

~~~text
硬件触发取到本次图像 / frame
    → 通过必填 image / frame 的子工作流读取本帧 PLC
    → 全局变量原子 increment，输出本帧 n
    → Switch
        n=1：两路同源视觉链 → 测试公式 → 两次测试转换 → PLC 报文 → TCP → ACK 校验
        n=2：全局变量 reset 到初始 0
             → 本工位孔位能力链 → 测试公式 → 一次测试转换 → 机器人报文 → TCP → ACK 校验
        其他：图内技术错误，停止本次正常输出
~~~

- 两个计数都是 integer、初始 0、job 生命周期；使用各自稳定变量 ID。
- 第一拍两路 YOLO 只接收同一相机曝光，不分别取图或计数。
- 第二拍算法在 reset **完成后**才进入。reset 输出 0 仍是有效的执行依赖。
- Switch 和当前算法使用 increment 输出的本帧 n；全局值归零不会把本帧 n=2 改成 0。
- 相机硬件等待期间不进入 PLC 采样或计数。每次循环体调用只接收一帧；持续执行由普通 While 负责，启用“不限制迭代次数”，超时为 0。Runtime 外层设为 single，不再套第二层 continuous。
- 计数不是 ACK 成功数。PLC 读取、帧序或发送出现技术错误时必须停止并重新同步，不能猜下一帧的物理拍次。

## 2 实际执行的算子链

视觉部分按以下有类型的数据关系运行：

~~~text
原图 / frame → 前置 ROI 掩码 → YOLO → 单对象检测 bbox
                              掩码原图 + bbox → Crop
                                              → 二值化 → 轮廓
                                              → 最小外接圆 / 形状测量
                                              → 明确的几何特征取点
                                              → 点坐标回源
~~~

测量使用原始测量图，而不是 YOLO 的 overlay。检测区域提取要求恰好一个已选对象，不能默默选择首项。掩码、裁剪区域和合成图均为测试配置，不代表已确认原项目的测量区域。

新增的通用桥接能力包括最小外接圆、检测 bbox 提取、几何特征取点和点坐标换空间。轮廓质心明确不叫“Blob 内心”。局部点回源使用真实坐标变换，ROI 偏移只补偿一次；已经回到目标空间后再回源不会再次加偏移。

### 不能作为旧算法等价依据的测试选择

| 路径 | 本夹具选择 | 未确认的现场语义 |
| --- | --- | --- |
| 工位 1 第一拍 | 两路最小外接圆；两路点相加、加参考点；两次非交换测试转换 | 业务计算表达式、参考点使用方式、两次 2D 参数 |
| 工位 1 第二拍 | 明确使用最小外接圆进行能力测试 | 旧节点是 Blob 内心，其定义未知，本圆算法不替代它 |
| 工位 2 第一拍 | 左路圆；右路最小面积旋转矩形中心；没有固定坐标节点 | 原外接矩形种类/取点、被裁切左路配置、业务公式 |
| 工位 2 第二拍 | 固定坐标节点依赖 reset、在 YOLO 前执行；最小外接圆 | 固定坐标的业务表达式、转换参数 |

坐标拼接的通用 `add` 模式是分量相加，支持严格配对和显式单点广播。夹具中用于合并结果的“测试公式”也选择相加，但这不意味着旧“图像坐标计算”节点的业务公式已经确认。

## 3 固定文本坐标

- 工位 1：`station1_reference.txt`，内容为 `10 20`。
- 工位 2：`station2_reference.txt`，内容为 `-10 15`。
- 上述数值是模拟**像素坐标**，不能作为机器人或 PLC 的设备单位标定。
- 会话读取模式为 `session`：根入口准入时先冻结所有可达 TXT/CSV 的字节、解析配置和 SHA-256，包括尚未选中的拍次分支。
- 图内固定坐标节点仍在各条路径声明的位置执行；提前冻结文件不等于提前放行业务节点。
- 当前 Job 中改写或删除同一路径文件不会改变已冻结的坐标内容；新 Job 读取新版本。已撤回工位专用的加载级副本，不再声称模型、图像及 TXT 均由另一层工位快照管理。运行期间不要替换现场资源文件。
- Reader 默认仍为 `perInvocation`，保留旧工程每次读取行为。文件选择、编码、分隔符、表头等配置沿用原生参数表单。

核心 `CoordinateSpace2D` 当前单位契约是 pixel。接入 frame 或修改空间标签不构成像素到设备单位的换算；真实单位、轴向、比例及舍入须按计划 U03 明确。

## 4 相机、PLC 和网络占位

| 配置 | 本夹具值 | 使用限制 |
| --- | --- | --- |
| 相机 | `fixture-camera-1` / `fixture-camera-2`，硬件触发、hardware 等待、retryCount=0 | 假设备标识；正式运行前需配置两个不同的真实设备并确认周期起点 |
| 帧序 | `sequencePolicy=contiguous` | 检测同一会话内的重复/缺帧；真实设备须提供可用的 blockId |
| PLC | 127.0.0.1:15001 / :15002；D0、uint16、count=1 | SLMP/MC 3E 读取占位，不是拍照触发器，不赋予读值业务含义 |
| 工位 1 网关 | PLC :16013；机器人 :16011 | 仅本地夹具端口，不代表历史 front/back 对应关系 |
| 工位 2 网关 | PLC :16023；机器人 :16021 | 同上 |
| YOLO | `a.onnx`、`b.onnx`、`holes.onnx` | 目录不附带真实模型；测试临时文件只是外部推理边界占位 |

正常协议在图内完成，不由测试驱动补业务逻辑：

- PLC：例如 `"-106,165"`，双引号整数对，无客户端换行；夹具显式选择 half-away-from-zero 舍入。算子默认 `requireInteger`，不会默认把任意小数截成整数。
- 机器人：例如 `"82.00,103.00"`，两位小数、双引号；不在客户端追加网关负责的 `EY`。
- ACK 校验关联当前请求与通道。PLC 的历史 ACK 只可核对 X 的 D6 幅度/D7 符号，不证明 Y 写入；机器人 ACK 不是动作完成信号。
- 每条事务仅一个坐标对。多孔数量、配对、顺序和发送协议未确认前，不支持假定批量坐标或自动逐孔动作。
- TCP 默认单次事务，不新增长连接。ACK 超时后的输出结果属于 uncertain，不自动重发。
- 夹具启用 `rejectTrailingResponse`；它拒绝同一接收块内 LF 后的多余响应。单次连接关闭策略不提供跨事务去重或 exactly-once 保证。

## 5 安全复现

以下 pytest 只替换外部边界：IMV SDK、ONNX 推理后端、SLMP 本地模拟服务和本地网关。中间的 YOLO 后处理、图像处理、几何计算、变量、报文和 ACK 算子实际执行。端口由测试分配；不访问真实相机、PLC 或机器人。

~~~powershell
Set-Location 'C:\Users\jsdfhasuh\my_scripts\emo_master'
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest -q `
  tests/runtime/test_hardware_trigger_normal_chain.py `
  tests/runtime/test_spawned_hardware_stations.py `
  tests/runtime/test_parallel_workflows.py `
  tests/core/test_hardware_normal_fixture_package.py
~~~

运行 `build_project.py` 仅重新生成工程 JSON 和算子元数据，不执行工程：

~~~powershell
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' `
  'C:\Users\jsdfhasuh\my_scripts\emo_master\examples\hardware_trigger_normal_fixture\build_project.py'
~~~

`project.json` 可供 Designer 查看和编辑；缺真实资源/参数时不要在生产端启动。该工程默认不自动启动。

### 分层测试结论

真实 WorkflowRunner + 外部边界模拟已覆盖：四帧 n=1/2/1/2、结束计数 1/0/1/0、第二拍先 reset、每帧一次 PLC 读取、每两帧三次 YOLO、两路同源、FIFO 缓冲夹具、延迟/分包 ACK、固定文件版本、每两帧一次 PLC 报文和一次机器人报文。无帧等待/取消、PLC 失败不推进计数另有测试。

保存/重载与资源测试使用既有 `buildRuntimePackage` / `installRuntimePackage`：收集 TXT 和模型输入、校验导出字节摘要、换目录后编译及固定坐标准入；不导出实时数据库或无关文件。模型占位文件能被收集**不等于** ONNX 推理验证。当前示例使用正常 StartJob 和 resultScopes 采集，不拿通用运行包测试替代完整页面准备管线的验收。

改正后的测试实际启动两个 While worker：一条流程无触发等待时，另一条处理四帧，真正执行中间算子、SLMP 读取、坐标报文和网关 ACK，并验证独立重启。另有超过两个 Job 和原生 Qt 测试。历史 M5 的工位表单结果已过时，不作为当前架构证据。实际模型对照、硬件缓存容量/节拍、物理同步和现场设备准入仍 NOT_RUN。业务 NG 按当前计划暂缓。

完整状态见同一份[迁移计划](../../docs/plans/2026-10-09-two-station-hardware-trigger-migration-v1.md)和[通用并行改正记录](../../docs/testing/2026-10-09-generic-parallel-correction.md)。旧 `examples/two_station_normal_path` 仍是直接输入模拟坐标的**控制骨架示例**，不计入本目录的视觉链验收。2026-10-10 全部工作区联合交付的分层实机步骤见 [dot 验收清单](../../docs/testing/2026-10-10-dot-joint-acceptance.md)。

## 6 普通工作流并行运行

- Designer：**运行 → 运行目标与并发…**，勾选 `station1-run`、`station2-run`，点击一次“开始运行”。不要同时启动单帧链或辅助子流程。运行目标是临时选择；并发上限是现有工程 runtime 设置，修改后按原机制保存工程。
- 每个根入口创建独立 Job。While 等待硬件触发，另一 Job 不被阻塞。右侧“运行摘要”的下拉框切换当前 Job；“停止运行”停止所选，右侧或“运行 → 停止全部”停止全部自有 Job。
- 操作端使用“运行目标…”选择同样两个入口，然后点一次“开始”；页面标签来自实际启动的工作流，不是另一份工位声明。
- 展示仍使用 resultScopes：作用域根是 `stationN-run`，调用路径为 `wait/loop_body → process/subflow`，范围是 `stationN` 单帧链。每帧结束产生结果，不等待长期 While 根结束。
- 并发上限默认 2，可设更大正整数，也可显式不限制（工程值 null）。不限制不等于无限内存、显存或设备资源；展示缓存/图像传输仍有自身资源预算。
- 新任务需要展示名额时，可回收已终止且完成资源退役的旧展示结果；不影响仍运行的其他 Job。节点检查保留各流程最近两次运行，并继续服从检查图像的字节和读取预算。
- 相机、PLC 和 TCP 地址仍在原节点中配置。没有工位专用身份限制；实际设备独占、身份别名和物理首拍同步必须在现场接入时确认。通用运行界面不再以一个专用确认框假装完成同步。
- 运行期间不重新加载工程；全部 Job 停止并释放后再重载或更新。不因一个流程启动/停止失败而重启成功的流程作补偿，启动结果不确定也不盲重发。
