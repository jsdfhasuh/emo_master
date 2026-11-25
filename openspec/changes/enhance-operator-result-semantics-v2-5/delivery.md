# PR20 交付与独立复审记录

## 当前状态

本地候选已修复八项审计缺口，包含 main 的 SQLite 后台写入。P3.5 桌面验收通过，P3.6 的修复提交、PR 正文与对应 CI 待完成。最初审计的 main 为 `ed98a0212820efd0e6436029b69e82dc6e313fc7`。

任务状态以 [tasks.md](tasks.md) 为准。原 P0、主体实现、独立复审与各自测试证据按固定提交保留。

## 原生桌面验收（2026-11-25）

用户明确授权 Codex 操作原生桌面并计入本次 P3.5。执行环境为 Windows build 26200、Python 3.10.21、PySide2 5.15.2.1、Qt 5.15.2。通过工程入口打开演示副本，连接独立数据目录中的本机内嵌 Runtime。验收时 HEAD 为 `cd7058bd48f1c06c020d0d4ee2d7f818c0e12244`，合入中的 main 为 `5f337cb515af00468e508f6979323170fd990474`，60 个暂存文件的 Git 内容树为 `c344489c93733197ea3b87331e144950ecc519e5`。其中含主线增量、审计修复、回归和文档。走查开始时没有未暂存差异或未解决冲突。

| 桌面操作 | 结果 |
|---|---|
| 正式运行与历史导出 | 首次完整校验、写入测试 Runtime 并运行在线版本。Job `3192b4d5-086c-41e9-8568-f1255b3031f7` 完成。在运行日志打开 compare 的完成事件，概览为 `false`，输入为 0，备用配置为 999，变量覆盖后的调用值和指标中的实际右值均为 5。节点起止耗时 2 ms，算子内部耗时 0.005 ms。查看了指标、身份及原始 JSON，复制值为 `false`。从该任务导出 14 条 JSONL，节点记录与桌面复制的完整记录逐字段一致。 |
| 单算子调试与历史回看 | 会话 `5f0e93b646f549c581272dfaa650fd01`，代次 1。第一次 `81191d6e347c45c1bc974f4ab0a97434` 输入 0 和 −1，结果为 true。准备阶段右值为 5，实际右值为 −1。第二次 `0801dbeac731451694c1849adee11168` 改为 0 和 2，结果为 false。选中首轮结束日志并打开其执行结果，历史详情显示旧输入和 true。再次复制的整条 JSON 与首轮保存的文本相同，sourceIdentity 中的算子为 vision.compare.number 1.0.0。 |
| Annotate 动态字段与附件 | 在动态类别样式工作流用文件按钮载入小图和模拟检测。会话 `0c2e3732d9cd4c6b96c7e8e8f830e6f2`，执行 `dec14ed00b354cd4b4a4bcfd6934e246`。叠加图有一个标为“人员 0.90”的框。展开提交参数中的 classStyles → person → color，三个中文通道条目依次为 0、128、255，displayLabel 为“人员”。复制的 person 对象、原始记录与结果包内的样式一致。选中 drawnCount 导出结果包，重读 value.json 为整数 1，端口、执行 ID 和数据哈希一致。另存的叠加 PNG 为 3,256 字节，SHA-256 与本次输出资产一致。 |

本地原始证据保存在本轮审计目录的 `independent-acceptance` 子目录，包括 `desktop-job.jsonl`、`desktop-job-node.json`、两轮 `desktop-debug-*.json`、`desktop-annotate.json`、`desktop-person.json`、`desktop-drawnCount.emodebug.zip` 和 `desktop-overlay.png`。

同一候选另行复跑了 11 个 Runtime 与算子测试文件，210 项通过。调试、导出与 SQLite 专项 58 项通过，按各自分组记录。实际注册 82/82 个算子，核对 420 条参数路径，嵌入式与回环 gRPC 的 82 份定义一致。协议生成一致性、Ruff、mypy 与 OpenSpec strict 均通过。现场相机、PLC 和打包产物未参与本次验收。

验收时 GitHub PR 仍为 Draft，头提交为上述 `cd7058bd`，10 项检查通过，GitHub 报告其与 main 存在合并冲突。本地候选已处理冲突，尚未提交至 PR。[远端检查](https://github.com/jsdfhasuh/emo_master/pull/20/checks)

## 实施审计后的功能修复（2026-11-25）

| 使用场景 | 修复及本地证据 |
|---|---|
| IntegerAdd 两组备用操作数都得到 11 | 正式 Job 留存本次实际采用的备用值及来源，可区分 10＋1 与 3＋8。已接线的端口从输入摘要读取。覆盖变量覆盖、自动零值与溢出失败。 |
| 单算子历史中的不同输入得到相同结果 | Worker 在准备资源后、调用算子前，按 executionId 保留有界输入摘要。历史回看和失败记录都可显示对应输入。 |
| 正式 Job 需要先看业务结论 | 运行概览从匹配版本的输出、诊断和指标选取结果。比较显示布尔值，PatchCore 显示实际得分、阈值和判定，SQLite 即使节点完成也显示失败回执和影响行数。 |
| YOLO 历史只看到数量 | CPU ONNX 定向执行验证了最高分目标的类别和置信度进入正式事件投影，并与最终检测对象一致。零个检测时保留数量零。 |
| 托管 RTSP 的超时来源 | 将设备配置的打开上限、实际传给解码器的打开超时和读取超时随连接记录。模拟解码器覆盖连接复用、打开失败和等帧超时。 |
| 未匹配版本时仍解释模型字段 | 仅对匹配身份和字段声明的模型算子归纳路径。保留未知的 modelSource 成员，版本不匹配时显示原始结构。 |
| 现场容易误读的参数说明 | 同步 ROI 多边形约束、SQLite FILE_REFERENCE 的 ImageSaver.result 来源、Annotate 保留灰度时取三个 BGR 分量最大值，并核对注册后的定义与实际行为。 |
| 单算子大 JSON 附件的预览与导出 | 预览和脱敏结果包处理已知认证 URL，保留一般业务值。独立保存原始附件及后续执行的 output_ref 继续读取原值；结果包的长度、哈希与包内文件一致。 |

Windows Python 3.10 / PySide2 下选取受影响的 22 个测试文件联合运行，371 项通过。覆盖真实调试 Worker、CPU 推理、SQLite 回执和后台文件引用、模拟 RTSP，以及 Designer 的 Qt 离屏详情、历史和导出。82 个算子静态核对通过，包含 194 个输入、235 个输出、419 条参数路径、313 条嵌套输出字段。该增量沿用现有 Runtime 协议。

另外直接重放了原审计的两个例子。同一绝对值调试会话中，两次执行结束后按各自 ID 回查，分别得到输入 −7 和 7，两次输出均为 7。小型 YOLO 模型实际检出一个 `screw`，完成事件经生产 JSONL 投影后保留类别和置信度 `0.8999999761581421`。保存对话框文案调整后，相关 Designer 用例再次运行，46 项通过。Ruff、mypy（660 个源文件）、协议生成一致性、OpenSpec strict 和差异检查均通过。

本轮未连接生产设备。原生桌面验收按 [独立走查步骤](desktop-walkthrough.md) 执行，待记录实际正式 Job 和调试执行标识。下文旧 CI 对应各自固定提交，本轮修改仍待提交后的 CI 验证。

### 与最新 main 的 SQLite 后台写入共同验证

GitHub PR20 头仍为 `cd7058bd`，main 已前进到 `5f337cb5`。在独立本地候选中处理了 SQLite Writer 的 Manifest 与 Python 实现两处内容冲突，同时保留后台写入、字段映射来源统计、FILE_REFERENCE 上游约束和 PR20 的字段说明。默认同步写入。选择后台模式时，节点返回 `QUEUED`，同一 writeId 的最终事件才报告数据库的实际结果。

| 验证 | 在最新候选上实际观察到的结果 |
|---|---|
| 正式 Job → 生产 JSONL 投影 → Designer 离屏详情 | 节点概览为 `QUEUED`。后续“SQLite 后台写入结果”显示同一 writeId `12dd7ee8-0cfa-45d1-ac12-69648307ed22`、`COMMITTED` 和影响 1 行。测试同时查验了临时业务库中的该行。 |
| 真实单算子 Worker → 执行查询 → Designer 离屏详情 | 执行 `f5119cbd58974833862487c2e178b0c3` 的“提交时输出（已入队）”保留排队回执，“后台最终回执”显示已提交。业务写入发生在隔离库，原始 JSON 与执行查询一致。 |
| 相关自动化 | SQLite Writer、正式与调试 Runtime、YOLO、RTSP 和 Designer 的联合回归 256 项通过。新增阶段展示的定向分组 18 项通过，包含对失败回执的 0 行与排队时 null 的区分。各组覆盖有重叠。 |
| 元数据与静态检查 | 82 个算子全部注册，SQLite 新增 writeMode 后，实际核对参数路径共 420 条。Ruff、mypy（661 个源文件）、协议生成一致性、OpenSpec strict 及差异检查通过。 |
| 桌面演示准备 | 用最新候选重新读取并编译 2.10 / 页面 1.3 的示例，两条工作流通过，三个节点的参数声明均与当前注册结果一致。P3.5 保持待验收。 |

## 原 P0 交付记录（历史）

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


## ChatGPT Web 独立复审与本次补充

固定源码 `3d6448d3da18790c5f97bfdecb5fc845ebd67a1c`，原审查 worktree 干净。本补充在该提交的独立工作副本中只修改文档；包内文档校验针对“该提交 + 本补充”，不是声称原提交已经包含这些更新。问题详情及分层证据见 [独立复审](reference/pr20-review-20261125.md) 与 [复现摘要](reference/pr20-review-20261125/evidence.json)。

| 项目 | 本轮实际确认 |
|---|---|
| 静态覆盖 | 独立运行检查：82 注册、0 拒绝，194 输入、235 输出、419 条参数路径；保留指标 130、诊断 221、复杂输出字段 313；固定耗时占位删除 1 项 |
| 源码 CI | [45538005304](https://github.com/jsdfhasuh/emo_master/actions/runs/45538005304)，同一复审 SHA，attempt 1 success；Linux 9,882 通过/40 跳过，Windows 9,916 通过/6 跳过 |
| Web CI | [45538005340 attempt 2](https://github.com/jsdfhasuh/emo_master/actions/runs/45538005340/attempts/2)，同一复审 SHA，3 jobs success；首轮 Windows 时序失败记录保留，复跑成功不等于根因已修复 |
| R1 | 实际演示工程、真实 manager/Worker 执行 NumberCompare 成功；Qt 离屏详情未调用定义查询，缺概览与指标来源的生效参数；原始 JSON/复制保留 |
| R2 | 真实 RuntimeService、Worker 与 RPC handler 返回合成认证输出；变量已脱敏而 outputs 未脱敏。原始 JSON/导出传播由源码确认，没有声称已手工操作 GUI 验证 |
| R3 | 使用真实 Annotate schema 的离屏展开，动态样式子字段与颜色元素说明缺失；此复现已匹配定义，独立于 R1 |
| 其他定向检查 | 既有两条 UI/目录测试通过；算子侧代表归一化、控制流、SQLite、ImageSaver 模拟后台受理等小复现未发现额外阻断问题 |
| 未完成 | R1–R3 产品修复、修复提交回归与 CI、P3.5 独立桌面、真实相机/PLC 验收；不记为通过 |

本轮交付包包含 OpenSpec 更新、复审证据、Codex 执行提示词及 PR 正文模板。包的 strict 校验与补丁适用性结果见包根目录 VALIDATION.md；文档校验不能关闭产品缺陷。

### 后续修复交付记录填写要求

执行者追加实际修复提交 SHA、工作区状态、三个定向场景结果、受影响检查与适用 CI 链接；注明实际执行角色，保留计划/P0/独立复审由 ChatGPT Web 完成。P3.5 单列并由独立验收者据实际结果更新；本节不预填未来通过次数或修复提交。

## Codex 复审修复与开发验证

执行角色为 **Codex（6.2 Sol）**。计划、P0 与独立复审归属 **ChatGPT Web**。在本地和远端均为 `3d6448d3da18790c5f97bfdecb5fc845ebd67a1c` 时，将包内 11 文件补丁应用到干净 PR20 worktree。下表开发验证使用该提交加本轮修复、回归和演示的工作区。

| 场景 | 实际结果 |
|---|---|
| R1 | 真实 NumberCompare 单算子调试经 Runtime、Worker 和查询到共享详情。按 `sourceIdentity` 精确匹配 ID/版本，显示中文结果、耗时单位和已有生效证据，保留原始记录以及会话、代次和执行归属。 |
| R2 | 一次真实 `variable_read` 使用合成认证 URL。查询、原始 JSON、复制、原始文件和 ZIP 导出均隐藏认证内容，业务查询参数保留。使用同一执行 ID 的 `output_ref` 准备与取值仍取得原始 URL。既有 FlowSwitch 测试同时核对嵌套业务值、类型与副本独立性。 |
| R3 | 使用真实 Annotate 参数定义展开动态类别、`displayLabel`、`color` 和颜色数组元素。原始类别 key、未知字段、非敏感值和复制内容保留。 |
| 桌面交接准备 | 2.10 / 1.3 演示保留数值比较入口并加入小图与合成检测的动态类别样式场景。生成、编译、保存重读、实际 Designer 加载入口及离屏调试导出通过，`drawnCount=1`。P3.5 独立交互仍待验收。 |

Windows Python 3.10 / PySide2 离屏的 Runtime 和 Designer 受影响分组 173 项通过。82 个内置算子仍全部注册且零拒绝。逐字段静态核对、协议生成一致性、OpenSpec strict、Ruff、mypy 和差异检查通过。真实设备连接和独立原生桌面交互未执行。

### 修复提交检查

实际修复提交为 `bb386ed8e4fd85f271712580cccc4c54cedfe09e`。该提交的干净 worktree 上重新执行三条复审回归，3 项通过。GitHub 检查核对的是同一个修复 SHA。

- 源码 CI 四个分片及两平台覆盖汇总均通过。JUnit 显示 Linux 9,885 通过、40 跳过，Windows 9,919 通过、6 跳过，零失败或错误。两平台均实际执行并通过 R1–R3 的三条回归。[源码 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45572492587)
- Web CI 三项首次运行通过，含 Windows 合成数据下的所属 Runtime 联调。[Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45572492542)

随后提交补录本节、README 与清单，范围限这三份 OpenSpec Markdown。该文档提交的自动检查另见 [PR20 Checks](https://github.com/jsdfhasuh/emo_master/pull/20/checks)。P3.5 依照 [独立桌面走查](desktop-walkthrough.md) 交接。
