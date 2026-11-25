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
