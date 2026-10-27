# 多视频局部循环索引优化与验证

## 范围与基线

- 源码基线及结束 HEAD：`e8c24fcfd7ebf964f0f506a7d73a42405069dff9`，另有保留的未提交工作。
- 仓库：`D:/jsdfhasuh/documents/my_project/emo_master`，用户工作目录是同一 checkout 的 C: 路径。
- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10 / PySide2。
- 本轮先测变量事务的实际耗时，再选择局部改法。不把 node.started/completed 的观察时间差
  当作数据库 profiler；也不直接把全部变量换成缓存或异步写入。
- 保留此前异步旧快照实现。没有改共享变量、持久化变量、CAS、SQL FULL、事件事务、
  设备、模型、识别阈值、取帧间隔或正式 Image Saver 语义。
- 无提交、推送、打包、部署、真实相机/PLC 操作或正在运行的 Designer/Runtime 重启。

原工程：
`D:/jsdfhasuh/Downloads/Compressed/emo_master_file_list_riders_20261023/motorcycle-file-list-riders.emoproj`

原工程 SHA-256 始终为：
`c380474ac8f464e06e4c08d8543f7a0f41646a2bfd4428159b754f0a7c87d3e0`。

## 实测后采用的实现

1150 次变量 mutation 中，事务体（读取、类型检查、整数计算、UPDATE）仅累计 0.263 秒，
BEGIN 准入累计 144.536 秒，commit 累计 135.499 秒，连接/配置 28.978 秒，关闭 22.519 秒。
mutation 总计 331.894 秒。这里使用验证脚本包装真实调用，未更改生产事务。

因此没有缓存全局状态，而是复用 **While v2 已有的具名内存状态端口**：

1. 人物 `targetIndex`、候选车辆 `vehicleIndex` 从全局变量改为各自循环的 input/output 状态。
2. 循环入口提供整数 0；循环体将当前索引用于选择，输出索引 + 1 给下一轮。
3. 当前人物索引显式传到保存子流程，保持原 `{batchSampleIndex},{targetIndex}.png` 文件名。
4. `batchSampleIndex` 仍按原 Job 生命周期、原子自增和数据库提交执行，159 帧对应 159 次写入。
5. 不自动转换任意用户变量。只转换已确认用途的模板副本；新工程有独立 projectId 和输出目录。

平台改动限于：

- `plugins/builtins/collection_select/`：兼容可选 `index` 输入，输入优先、严格整数验证，
  非索引模式不静默忽略误接线；所有原集合类型和越界策略保留。
- `plugins/builtins/integer_add/`：无共享状态的整数加法，支持默认值/动态输入，精确 64 位计算和溢出报错。
- `apps/designer/ui/operator_mode_presentation.py`：索引模式显示 index，已有不适用连线保留提示。
  新增端口排在原集合输入之后，不改变原默认接线顺序。

集合选择保留原 1.1.0 标识。这是可选输入兼容扩展；平台要求精确版本匹配，直接提升版本会使
未改动原工程的依赖锁拒绝加载。没有放松依赖校验。新整数算子为 1.0.0。

## 9 视频前后运行

两次均由源码 RuntimeService 启动真实 spawn Worker，在独立 DB/Job/日志/输出目录运行。
ALL 旧快照、实时 inspection 会话、PresentationService 采集全部开启，完整事件由独立 sink 保存。
不是直接 WorkflowRunner 代替实际 Runtime；也不是冻结安装包/人工桌面验收。

证据根目录：
`D:/jsdfhasuh/Downloads/Compressed/emo_master_file_list_riders_20261023/validation/`

| 项目 | 本轮原工程复测 | 局部索引工程 |
| --- | ---: | ---: |
| 目录 | `variable-profile-20261023-164150` | `local-loop-state-20261023-170200` |
| Job | `d74a1d51-e1e0-408c-937d-670bdeac0490` | `abf169e7-8f20-4177-87d7-89cfa3baf248` |
| Worker PID | 14892 | 32664 |
| accepted → completed | 528.825 s | 380.502 s |
| StartJob → 观察终态 | 529.359 s | 381.047 s |
| 视频 / 取帧 / 裁剪 | 9 / 159 / 32 | 9 / 159 / 32 |
| completed 节点 | 9623 | 9591（删除 32 次人物索引读节点） |
| mutation 次数 | 1150 | 159 |
| mutation 总累计时间 | 331.894 s | 43.933 s |
| BEGIN 准入累计 | 144.536 s | 10.853 s |
| mutation commit 累计 | 135.499 s | 20.185 s |
| 单独变量 records 读取次数 | 465 | 0 |
| WARN / ERROR | 0 / 0 | 0 / 0 |
| 快照提交 / 合并 / 实写 | 5997 / 4919 / 1078 | 5965 / 4993 / 972 |
| 快照失败 / 丢弃 | 0 / 0 | 0 / 0 |
| 提升快照端口数 | 47 | 46（少了人物索引读节点） |

前后全部 159 份采样信息相同，包括视频路径、帧号、hasNext；32 张图文件名、逐像素、PNG
SHA-256 全部相同。原工程、模型和 9 视频在原工程复测前后哈希不变；优化运行也检查原工程
哈希，并检查新工程、同一模型和 9 视频未被运行改写。

只读查询两个临时 DB：batchSampleIndex 最终都为 **158，revision=160**。
优化 DB 的 Job 变量只有这一项，不存在 targetIndex/vehicleIndex 的影子数据库状态。
Worker 已退出并释放 Job 资源；实时图交接 `pendingFiles=0, readers=0`。

本轮先后单次实测的事件口径耗时减少 **28.05%**；变量 mutation 次数减少 **86.17%**。
不是锁定磁盘/其他应用负载的重复统计实验，不承诺任意机器相同比例。此前异步快照试跑
412.395 秒是另一时段记录，不与本轮 528.825 秒混作同一个基线，也不抹去旧结果。

剩余热点仍明显在 Runtime 主进程的数据库提交：优化后显式 commit 2621 次、累计 318.262 秒。
该累计可能与 Worker 的等待重叠，不能把两进程累计时间直接相加。没有在本轮继续扩展
事件批量协议、降低持久性或增加多视频并行；也不能把局部变量开销下降比例当整链路加速比例。

## 最终自动化与 Designer 验证

最终 **277 passed in 23.77s，退出码 0**，共 22 个相关测试文件，不是全仓库 CI：

- 新增整数加法、动态集合索引、局部嵌套循环、Designer 动态索引四个测试文件。
- 原集合算子、数值桥接、模式展示及 Runtime 模式回归。
- 全局变量、进程共享、CAS/RPC、旧计数器、页面变量展示。
- While v2、图片 While、多视频 ForEach、既有异步快照及 Worker 收尾。
- 内置参数标题和插件注册。

局部状态覆盖：空集合、每组重置、下一次 Job、取消后再运行、两个并发 runner，以及禁止
任何全局变量访问的断言。动态选择覆盖全部七种集合类型、输入优先、错误类型/模式、越界。
Ruff 检查 3 个源码和 4 个测试文件通过；Mypy 检查 3 个源码文件通过。

最终交付目录：
`D:/jsdfhasuh/Downloads/Compressed/emo_master_loop_state_riders_20261023/`

其 `validation/` 保存 `regression-final.txt`、`ruff-final.txt`、`mypy-final.txt`、`comparison.json`、
`project-checks.json`、`source-hashes-delivery.json` 及最终 GUI 报告/图片。

实际调用 MainWindow.loadProjectSelection，访问全部 8 个流程，验证动态 index 连线，
另存临时工程后重新打开和编译。100% / 150% 均通过；使用临时 QSettings/RuntimeClientStub，
不会写入在线 Runtime 或现场设置。首轮 offscreen 环境字体为空，连最小 drawText 探针也不出字，
不能把那些空白截图当像素验收。仅在验证进程指定 Windows Fonts 并加载微软雅黑，加入文字
栅格探针后重新渲染并检查最终 `*-final.png`，确认 index 与集合/输出端口可读。

完整视频运行之后，仅缩短画布提示、将两张循环体画布横向间距拉开，并把可选 index 端口放到
原集合端口之后。`layout-only-report.json` 证明去掉 layout 后模型内容完全相同（字典键顺序
不影响含义）；没有改识别参数、边、循环状态、模型或保存逻辑。最终再次保存重读、编译、
执行以上 277 项和两种缩放的实际 Designer 打开验证；没有把 UI 调整说成又做了一轮视频基准。

最终工程 SHA-256：`73bcf716ec9df79a22a03281a1f630863930cf35f6bf1b9bc75020e3628c35f5`。
运行时工程 SHA-256：`863f2e7021e35766710c18d3498115d228548b9d06a0089216b9d301be2e70df`。

## 保留的初次失败

这些是辅助脚本/新夹具修正记录，不冒充产品回归通过：

- profiler 首次漏传独立目录，原验证脚本的防覆盖断言拒绝启动；未改写旧证据。随后显式传目录。
- 生成器误将完整 manifest port 对象写入要求 string 的保存字段，被 ProjectDocument 拒绝；
  随后按规范 normalizePortType。第二次因集合选择版本锁不匹配而拒绝原工程兼容检查；
  失败候选保留在 `initial-select-1.2-rejected.emoproj`，不能使用。
- 新循环夹具首次缺失显式 loop ports，第二次使用对象而非 Runtime 合同要求的 payload。
  分别保留两个 3-failed 输出，修正夹具后 3 passed；未删减断言或放松编译/输入校验。
- 布局辅助脚本两次误用 WorkflowLayout/position 的访问形态，均在保存前失败，原日志保留。
- 初次空白字体截图保留为环境失败证据；只将加载字体并通过文字探针后的最终图片用于视觉检查。

## 使用与边界

打开新工程 `motorcycle-local-loop-state.emoproj`。新输出目录为同目录的 `outputs/riders`，
不会覆盖原工程或原输出。源工程和新工程皆可在本轮源码注册表下编译。
源码修改不会热加载到已经运行的窗口；使用包含本轮修改的源码 Designer/Runtime，
安装包则需另行构建。本轮没有进行安装包、人工当前桌面、真实设备或现场部署验收。
