# 算子日志覆盖盘点与补齐范围 · V2.3

## 1. 审查对象和结论

基线：[9d59fd16a1d0d3ec0c60725c839ff38183d6916b](https://github.com/jsdfhasuh/emo_master/tree/9d59fd16a1d0d3ec0c60725c839ff38183d6916b)。完整读取 82 份内置 manifest、166 个内置 Python 文件，定位 82 个不同 operatorId、58 个入口模块，核对共享 helper 与继承类。逐项入口见 [operator-coverage.json](reference/operator-coverage.json)。

这是静态源码检查；没有在目标机器导入全部依赖、验证全部插件注册或运行全部设备。外部插件只确认正式执行器接入合同，不盘点用户未提供的第三方实现。

| 层次 | 当前源码事实 | 本版要求 |
|---|---|---|
| 正式任务基础记录 | WorkflowRunner 产生节点生命周期事件，OperatorExecutor 为实际进入执行的普通注册算子注入 logger | 可读出当前事件实际提供的信息；未调度、跳过或缺失数据不伪造执行结果 |
| 主动业务日志 | 4 个内置类显式调用日志接口，其余 78 个未发现该类调用 | 有 node.log 时展示 data，没有时仍展示真实执行摘要，不批量加重复日志 |
| 执行结果信息 | 很多算法已有 metrics / diagnostics；ioSummary 是框架提供的有界摘要 | 通用展示作为必做项，当前事件直读，不逐次后台补查或复制结果到日志 |
| 单算子调试 | 独立事件 wrapper 与独立执行结果；不经 WorkflowRunner | 在已有调试窗口复用展示组件，保持调试会话身份与结果来源 |
| 预览 | 缓冲日志及部分失败摘要；成功预览无统一持久日志流 | 只读取现有失败信息，不增建成功预览历史、存储或 RPC |

基础生命周期与 logger 依据：[runner.py](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/apps/runtime/workflow/runner.py)、[operator_executor.py](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/apps/runtime/execution/operator_executor.py)。

## 2. 四个主动日志实现的条件

| operatorId | 实际调用条件 |
|---|---|
| vision.io.huaray_camera | init / dispose 有生命周期日志；executeNode 的 captureDevice 托管路径先返回，详细采图日志在后面的兼容直连分支 |
| communication.plc.slmp_read | executeDevice 托管路径先返回；显式读操作日志位于兼容直连分支 |
| communication.plc.slmp_write | executeDevice 托管路径先返回；显式写操作日志位于兼容直连分支 |
| vision.io.sqlite_writer | 正常结束与已处理错误主动记录 receipt；仍受真实执行、取消与日志配置影响 |

不能将 4 / 82 称为日志功能完成率，也不能承诺这四类每次执行都产生相同业务日志。内置源码未发现 getDeviceLogger 调用；设备 Manager 的诊断是独立已有来源。

依据：[工业相机](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/huaray_camera/operator.py)、[PLC 读写](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/_communication_operators.py)、[SQLite](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/sqlite_writer/operator.py)。

## 3. 常用算子已有数据

| 算子 | 当前已有信息 | 不应误读成什么 |
|---|---|---|
| YOLO 检测 | metrics.latencyMs / detectionCount；diagnostics.text / modelPath / backend / provider / inputShape / imageOutputMessages | 不是 node.log.payload.data 中通用的 result / elapsedMs / count 字段 |
| YOLO 分类 | 耗时、分类诊断文本；outputs 中类别、置信度和 accepted | 不能保证全部分类概率保留在日志查询投影中 |
| PatchCore | 耗时、分数 / 阈值诊断文本、模型与后端信息；标量输出 | 没有任意历史结果图恢复能力 |
| Threshold | 耗时、前景像素数 / 比例、阈值与模式 | 无需再逐项重复写一条 node.log |
| Integer Add | 成功 outputs.value 或失败 code / message | 无自定义指标不等于没有基础记录；参数回退值不自动等于已记录输入端口 |
| 托管 PLC | attempts / wordCount / valueCount / latencyMs、deviceOperation 诊断 | 不能假定经过兼容直连分支的主动日志 |

依据：[YOLO](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/yolo_inference/operator.py)、[YOLO 分类](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/yolo_classification/operator.py)、[PatchCore](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/patchcore_inference/operator.py)、[Threshold](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/threshold/operator.py)、[Integer Add](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/plugins/builtins/integer_add/operator.py)。

## 4. 两种入口的数据来源

正式 Job：直接展示选中事件的实际 payload。node.log.payload.data、完成事件 metrics / diagnostics / ioSummary 是不同来源，不能合成后冒充原始事件。日志查询投影不保留完整 outputs，图像 / 集合摘要不能展开成未记录的全部结果。

单算子调试：ReadOperatorDebugEvents 提供外层 sequence / type / generation / executionId 和内层 event；GetOperatorDebugExecution 提供对应执行结果。worker 成功时已有 outputs、outputAssets、metrics、diagnostics、elapsedMs；失败时只展示实际返回的字段。没有自动 node.completed 或 ioSummary，不虚构该事件，也不创建正式 Job 归属。已有 DebugConnection.snapshot 可提供两类数据，接入同一个展示组件即可。inputSources 不是完整输入，finishedAtMs 使用单调时钟，不能当日历时间。

依据：[调试 worker](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/apps/runtime/operator_debug/worker.py)、[调试 manager](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/apps/runtime/operator_debug/manager.py)、[Designer 调试服务](https://github.com/jsdfhasuh/emo_master/blob/9d59fd16a1d0d3ec0c60725c839ff38183d6916b/src/emo_master/apps/designer/services/operator_debug.py)。

## 5. 实施与验证的最小范围

通用展示扩展 tasks 4.8，调试入口单列 4.9；不新增算子生产协议、后端查询、日志数据库或逐算子渲染器。补业务日志只针对后续确认的实际排障缺口，本轮不把它作为所有算子可读化的前提。

G6 按真实数据来源取代表，检查通用展示与两种入口接线；不要求 82 个算子全部执行，不要求为日志界面加载 YOLO / PatchCore 模型或连接相机 / PLC。Q1 中追加一次软件单算子调试，Q2 保留历史检索与导出，两条走查数量不变。复制、值语义、截断、会话与轮次身份检查保留，避免把其他执行结果显示给当前选择。

原型、静态参考图和本盘点均不构成产品验收结果。正式日志等级、限流与保留范围继续按现有机制工作；本版不声称每轮所有业务中间数据均完整保留。
