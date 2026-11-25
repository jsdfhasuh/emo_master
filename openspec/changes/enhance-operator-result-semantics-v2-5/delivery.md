# PR20 计划更新与 P0 交付记录

日期：2026-11-25（Asia/Shanghai）。执行角色：**ChatGPT Web**。

## 基线与本轮范围

| 项目 | 固定版本 / 状态 |
|---|---|
| 审查源码 main | `ed98a0212820efd0e6436029b69e82dc6e313fc7` |
| 源码 tree | `87bcb6c714d0986b49210eddebf835aaf1922401` |
| 原 PR20 head | `a981262bffcfbda7a1216f2b1b147c69be04a3d8` |
| 旧计划基线 | `b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`，本轮已更新 |
| 已继承依赖 | PR19、PR25 均已合入上述 main |
| 审查源工作区 | 干净，无源码本地修改 |
| 文档工作区 | 从原 PR20 正常合并上述 main，再更新本计划包；不是干净原 HEAD 的文档校验结果 |
| 本轮阶段 | P0 完成；P1/P2/P3 待实施，PR 保持 Draft |

本轮不新增产品实现或产品测试。合并 main 继承的文件并非 PR20 新实现；最终差异以本分支相对上述 main 为准，限于 `openspec/changes/enhance-operator-result-semantics-v2-5/`。

## 已完成的工作

1. 删除过时的 PR19 并行文件所有权、等待依赖和特定外部执行者条款，统一基线与阶段。后续从 P1 继续，代码变动时只复核受影响审查条目。
2. 对 82 个实际 operatorId 阅读 Manifest、最终入口类、实际执行、共享 helper、继承和设备托管路径。记录逐字段用途、实际来源、条件/单位、特殊回执、补充或不补决定及源码依据。
3. 核实共用链路：参数 schema 全量相等校验、严格端口合同、OperatorInfo 传递、Job 事件/JSONL 投影、调试资源准备和既有参数导出。将发现的真实缺口写入设计、任务和规格。
4. 保留按需补数据的边界：优先解释已有 outputs/metrics/diagnostics，普通数值/几何算子不批量新增指标；取消“所有算子都实际运行”的验收负担。

## 审查覆盖

完整逐字段记录见 [operator-field-audit.json](reference/operator-field-audit.json)，可读索引见 [operator-field-audit.md](reference/operator-field-audit.md)。以下为每个 operatorId 的记录计数，共享字段按各自注册算子分别核对，不等同于平台去重字段数量。

| 项目 | 数量 |
|---|---:|
| 实际算子、Manifest/版本、schemaSource、最终审查结论 | 82 |
| 输入 / 输出端口 | 194 / 235 |
| 顶层配置参数 | 366 |
| 参数审查记录（含嵌套及数组元素） | 419 |
| 托管设备参数记录 | 163 |
| 现有 metrics / diagnostics 记录 | 131 / 221 |
| 复杂输出内部字段记录 | 313 |
| 逐算子处理决定 | 261 |
| 顶层缺少 description 的参数 | 301 |

所有 366 个顶层参数已有 title；301 只是 description 存在性缺口，不表示其余 65 个说明已全部准确。131 个现有 metrics 分类为 duration 52、business 32、result 29、parameter 18；duration 中包含 EmptyOperator 的固定零占位，已明确不是实测并计划移除。

## 审查对计划的实际影响

- **说明存放位置：** 端口只允许既定合同字段，改为在可选 resultSchema 中按真实端口 key 旁挂说明。参数说明沿用 paramSchema，并同步实际 Python 生成源；不只改 Manifest。
- **实际参数：** 区分调用、内部归一化/输入覆盖和设备解析，优先复用已有证据。CollectionFilter 的中性化、CollectionSort 默认键、Crop ROI、模型尺寸及调试隔离库均不能用原始配置冒充。
- **完整传递：** OperatorInfo 新增一个结果语义 JSON 字段；正式 Job 补可信身份和受控参数摘要，同步完成事件 JSONL 白名单；调试补 operatorId 并在资源准备之后采集。
- **安全副本：** 处理审查确认的认证字段/URL，同时覆盖现有 rawParams/effectiveParams/bindingSources 的展示与导出副本；保留实际执行原值。普通模型路径和业务文本不一律视作凭据。
- **业务含义：** 后台 ImageSaver 提交不等于保存完成，TCP/ACK 不等于物理动作完成，SQLite 节点 ok 不等于 receipt 已提交；保留 PR19/PR25 已有任务、产物和取消/故障契约。
- **指标精度：** 面积、质心、模板匹配 quality、PatchCore 热图、Annotate drawnCount 按各自真实计算口径说明。没有业务价值的重复指标明确不加。

## 本轮验证

| 验证 | 结果与范围 |
|---|---|
| 清单与版本 | 82 个 operatorId 唯一，集合/版本与固定源码 Manifest 一致 |
| 端口与参数覆盖 | 输入/输出 key 完全匹配；Manifest 所有声明属性路径均有审查记录，实际实现补充路径另行说明 |
| 字段证据 | 每条字段含义、参数来源和处理决定均有证据；2,664 次源码引用指向 143 个存在的文件，2,569 次带行号引用全部在固定源码范围内 |
| 设计复核 | 计划、任务和三项规格的身份、参数阶段、元数据传递及测试范围一致 |
| OpenSpec | CLI 2.0.0，对本 Change 执行 strict 校验通过，0 issues |
| 差异范围 | 本轮新增差异限于本 Change；相对审查基线没有产品源码变化 |
| 未执行 | 产品测试、原生 Qt 走查、真实模型/设备、新 CI 运行；不记为通过 |

OpenSpec 使用该 CLI 官方支持的关闭遥测/更新检查选项，在本地执行：

```bash
OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1 OPENSPEC_NO_UPDATE_CHECK=1 \
  openspec validate enhance-operator-result-semantics-v2-5 --strict --no-interactive --json
```

不为文档更新重跑整个产品测试。后续实现仍须完成 tasks.md 中既定的 P3 验证，P0 的静态覆盖不能替代运行验收。

## 基线 CI 状态

2026-11-25 北京时间 09:39 查询，源提交均为上述 `ed98a021`：

| 检查 | 状态 |
|---|---|
| [源码 CI 45520434988](https://github.com/jsdfhasuh/emo_master/actions/runs/45520434988) | 09:37 结束，failure |
| Windows source other / runtime | 均 success |
| Linux source runtime | success |
| Linux source other | failure，Designer 原生进程 Aborted；Ubuntu 汇总随之 failure |
| [Web CI 45520435031](https://github.com/jsdfhasuh/emo_master/actions/runs/45520435031) | success |

失败位置是 `test_draft_pure_preview_context.py::testActualLocalImageButtonWithFreshRuntimeAndUnsavedNode[2.1-vision.preprocess.roi]`，不是之前的静态页面超时。同代码树的合并前 [45382159632 attempt 2](https://github.com/jsdfhasuh/emo_master/actions/runs/45382159632/attempts/2) 通过；该对照尚不能确定根因。

这是已有基线事实，不是本轮文档修改引入或本轮测试的结果。P0 已完成，可以进入 P1；相关 Designer 链路在实现/验收前针对性定性，不将其扩大为重新验收所有已合并工作或全仓整改。

## Codex 实施与开发验证

执行角色为 **Codex（6.2 Sol）**。计划及 P0 审查归属 **ChatGPT Web**。实施前 fetch 核对 PR20 头为 `db2dfc141fa35afb6a608e0df82da20b37dfd585`，main 为 `ed98a0212820efd0e6436029b69e82dc6e313fc7`，在独立干净 worktree 上从现有 PR20 分支实施。

注册校验已检查结果说明的结构、端口和参数引用。Runtime 使用现有 `OperatorInfo.result_schema_json` 传输，RuntimeClient 本地直连和回环 gRPC 的测试均能解出中文说明。注册、端口与目录相关的首轮测试为 65 通过，协议重新生成后的 `--check` 与 OpenSpec strict 均通过。该次源码验证使用 PR20 起点加实施中的工作区。

同源码树、尚无产品实现的 PR20 计划头 [45528883627](https://github.com/jsdfhasuh/emo_master/actions/runs/45528883627) 的 Linux other 分片通过，未复现 main 上的 Designer 原生进程异常。其 Linux runtime 分片记录另一项已有失败。`test_image_demand_client.py::testQueuedPinAndThousandDemandChangesReleaseEveryAdmission[generation]` 在准入释放计数断言中收到 2，预期 1；该分片为 3452 通过、1 失败、10 跳过。这两条基线证据的失败位置不同。

| 开发验证 | 当前结果 |
|---|---|
| 全量算子静态检查 | 82 注册、0 拒绝，覆盖 194 输入、235 输出、419 条参数审查路径、313 条复杂输出字段、130 个保留指标及 221 条诊断路径。EmptyOperator 的 1 个固定耗时占位已核对删除。[复核脚本](reference/check_operator_semantics.py) |
| 代表算子及注册 | 362 通过。图像阈值和裁剪、真实小型 YOLO/PatchCore ONNX 推理、集合/坐标、控制分支、回环 PLC/TCP、图像和通用文件写入。三个读取新增中文 Manifest 的旧测试指定 UTF-8，原有断言保留。 |
| 共享完成记录和调试安全副本 | 受影响 Runtime 测试 307 通过、1 跳过。跳过项为需显式启用的 65 秒调试租约测试。安全处理收敛后定向复验 33 通过，覆盖真实 Job、Histogram 1.1.0 注册身份、变量来源、准备后的路径和隔离调试 SQLite 库、JSONL/历史查询/导出、认证信息以及业务值保留。 |
| 后台与写入回归 | 77 通过，另单独运行模拟相机宿主测试 1 通过。包含 PR19 后台任务与 FILE_REFERENCE、SQLite 回执、PR25 取消及原有调试设备回归。 |
| Designer 源码自动化 | 141 通过。Windows/PySide2 offscreen 下验证本地与回环 gRPC 目录、正式 Job / 单算子调试详情、原始 JSON、导出及未知字段展示。 |
| Linux 原生异常的相关入口 | 现工作区在 Windows 原生 Qt 下执行基线报错测试的全部 8 个变体，均通过。本证据限 Windows 自动化。 |
| 桌面交接 | [演示与操作步骤](desktop-walkthrough.md) 使用 2.10 / 1.3 工程，已编译、保存重读，由真实 Designer 加载入口做脚本化冒烟。独立桌面交互仍由后续验收执行，P3.5 保持未勾选。 |

### 提交后的串行复验

实现提交 `d5b51161940625ea58a803392621e9fe01b7ecd7`，以下复验在该提交的干净 worktree 上执行，使用 Windows Python 3.10。并发汇总测试期间，本机记录了页文件不足、ONNX 原生访问异常和部分子进程启动心跳超时；保留原断言，按以下分组串行复验。

| 复验范围 | 结果 |
|---|---|
| 插件、注册、端口、通信工作流、SQLite 后台文件引用与回执 | 1,559 通过。真实相机 smoke 测试未运行。 |
| Runtime 执行、节点事件、查询/导出、单算子调试、后台任务和 Designer 日志 UI | 381 通过、1 跳过。跳过项为默认未启用的 65 秒调试租约测试。 |
| 真实 Job 检查 RPC 与受影响的两个模拟 RTSP 子进程场景 | 10 通过。与上述第二组有重叠。 |

该提交的 GitHub 检查见 [源码 CI 45533949835](https://github.com/jsdfhasuh/emo_master/actions/runs/45533949835) 和 [Web CI 45533949821](https://github.com/jsdfhasuh/emo_master/actions/runs/45533949821)。Web 三项均通过，含 Windows 合成数据的所属 Runtime 联调。

### CI 首轮回归修正

实现提交的 Linux Runtime 分片完成 3,455 通过、3 失败、10 跳过。3 条失败均是本变更新增说明后出现的示例快照一致性问题。Linux other 分片仅在连线示例的 4 个比较变体上失败，与本地复现相同。此前记录的 Designer 原生进程异常在这次 Linux other 运行中未复现。

- 双 PLC 2.7 和连线 2.1 兼容夹具的生成器保留各自历史参数声明，编译使用现行算子合同。已有兼容工程文件及完整比较断言保留。
- 视频 While 示例同步已有 VideoLoader 定义中的三条说明。工程格式、参数值和工作流沿用该示例。
- 复核 RTSP/RTSPS 认证 URL 时补齐裸查询令牌脱敏。具名业务查询和实际执行值保留，重复验证原始事件、持久化、查询和导出。

以上修正后的定向集合 72 通过；Ruff、mypy 和差异检查通过。后续检查对应头提交及结论在 [PR20 Checks](https://github.com/jsdfhasuh/emo_master/pull/20/checks) 留档。
