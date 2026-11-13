# emo_master 独立工程草稿与 MCP 实现审查

日期：2026-11-13  
固定审查提交：`3f717510751f4bd0d8277c3c2bf676236f51f49e`  
对照材料：用户交付的 OpenSpec v7、该提交源码、验收材料及对应 GitHub Actions。  
执行者：ChatGPT Web。仅审查；没有修改、提交或推送仓库源码。

## 结论

主干已经实现，不是只有接口或占位代码。真实算子目录、独立会话、HTTP/MCP 共用服务、文件交付、独立候选检查都有对应实现；仓库也提供了实际 Inspector、软件 Runtime 和原生 Qt 自动化的验收记录。

但当前不宜按“全部完成且无遗留问题”收尾：固定提交的两条工作流均失败；另外存在节点复制资源绑定、恢复草稿的声明采用路径、预检准备阻塞事件循环和导出检查状态等具体问题。建议保留架构，进行局部修复与直接回归，不重写计划或扩大到生产运行、整体 Designer 改造。

### 证据等级

- **CI 已观察**：读取固定 SHA 对应的运行状态、job 和 Linux 主 CI／Linux Web job 日志。
- **静态代码结论**：根据同一 SHA 的函数和调用链判断行为；本文提供复验步骤，但没有把这些步骤写成已在本环境执行。
- **仓库验收记录**：Sol 提交的报告、JUnit 与 Inspector 结果，作为已有证据读取；本轮没有独立重跑。
- **未验证**：本轮未启动 MCP、Designer 或 Runtime，未跑全仓测试，未访问硬件，也未验证后续提交的修复情况。

## 1. 已有实现与证据

| 能力 | 核对结果 | 边界 |
| --- | --- | --- |
| 算子目录 | Runtime 已填充四个字段，authoring DTO 保留完整 schema 和未知/空列表区别 | 声明不是执行成功或硬件可用证明 |
| 工程会话 | DraftService 使用正式 ProjectEditSession/WorkflowStore，按草稿锁和修订执行 | 恢复后的声明元数据仍有缺口，见 F4 |
| HTTP/MCP | 共享 AuthoringService.invoke，工具结构由命令定义产生；MCP 为可选依赖 | 当前 CI 的测试依赖未完整接入，见 F1 |
| 上传及导出 | 上传完整性、资源版本、正式保存重读、固定产物和鉴权下载均有实现 | 节点复制与导出检查状态见 F3/F6 |
| 目标预检 | 独立候选 ZIP、真实资源和声明检查、WorkflowCompiler、可定位报告 | 预检不是执行；Web 侧准备工作需异步隔离，见 F5 |
| 交付示例 | Inspector 报告记载缺图修复、预检通过、实际下载和单独软件执行 COMPLETED | 是仓库记录，不是本轮复跑；Designer 部分由另一个原生 Qt 自动化证据说明 |

Inspector 报告中的客户端为 MCP Inspector 2.13.0，SDK 1.30.0，协议 2025-11-25，方式明确为“Inspector + HTTP helper”。报告记录的 ZIP 是 3020 bytes；缺资源时为 INCOMPLETE，修复后的目标检查为 PASSED / compileStatus=passed / executionStatus=not_run；之后独立的软件执行记录为 COMPLETED。

148 项相关检查与 280 项 Designer 检查是交付记录所述结果，存在重叠，不相加为本轮独立通过总数。不能仅凭这些定向检查结果推断全仓 CI 已通过。

来源：
- [src/emo_master/authoring/service.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/service.py)
- [src/emo_master/authoring/catalog.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/catalog.py)
- [src/emo_master/authoring/http.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/http.py)
- [src/emo_master/authoring/drafts.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/drafts.py)
- [src/emo_master/apps/runtime/project_config/candidate.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/apps/runtime/project_config/candidate.py)
- [docs/testing/2026-11-13-independent-project-authoring/inspector-report.json](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/docs/testing/2026-11-13-independent-project-authoring/inspector-report.json)
- [docs/testing/2026-11-13-independent-project-authoring/README.md](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/docs/testing/2026-11-13-independent-project-authoring/README.md)

## 2. 问题总表

| ID | 优先级 | 问题 | 证据状态 |
| --- | --- | --- | --- |
| F1 | P1，交付阻塞 | 新 authoring 代码 5 个 mypy 错误；测试依赖接入也未完整 | 类型错误为 CI 已观察；依赖缺口为静态核对，尚未观察到修复 mypy 后的实际 collection 结果 |
| F2 | P1，交付阻塞 | 新协议与固定 SDK 1.5.0 交接快照的字节一致断言冲突 | Linux Web CI 已观察 |
| F3 | P2，正常编辑缺陷 | node.copy 未复制指向节点的 ResourcePlan 资源和现场参数绑定 | 静态调用链确定；未在本环境运行复现 |
| F4 | P2，恢复链路缺口 | 已保存/导入草稿的采用声明丢失，查询目录不能重新关联到该草稿 | 静态调用链确定；未在本环境运行复现 |
| F5 | P2，响应性缺陷 | 候选准备、校验、文件复制和 ZIP 压缩直接在 async invoke 的事件循环线程执行 | 同步调用位置确定；未测量真实模型规模下的耗时 |
| F6 | P2，结果语义不一致 | 同修订预检成功后，导出仍固定返回 not_run/not_checked | 静态实现与已提交 Inspector 报告相互印证 |

优先级为本次审查建议，不表示这些问题会破坏全部现有工程。F3–F6 属于日常编辑/交付行为，不是极端并发或异常组合测试要求。

## 3. F1 — 主 CI 与测试环境

对应运行：
- [CI 43356280900](https://github.com/jsdfhasuh/emo_master/actions/runs/43356280900)
- 已读取 Linux runtime job `131659030521` 的日志。

该 job 的 proto 生成一致性和 Ruff 检查通过，随后全源 mypy 失败，尚未进入该 job 的 pytest 阶段：

```text
src/emo_master/authoring/catalog.py:71  Need type annotation for "_cache"
src/emo_master/authoring/files.py:68    ResourcePlan | None 的 items 访问未收窄
src/emo_master/authoring/files.py:140   Resource.purpose 的 str 与正式 Literal 枚举不匹配
src/emo_master/authoring/files.py:211   ResourcePlan | None 的 items 访问未收窄
src/emo_master/authoring/files.py:227   ResourcePlan | None 的 items 访问未收窄
Found 5 errors in 2 files (checked 633 source files)
```

**修复范围**：补 cache 类型，按真实控制流收窄可空 ResourcePlan，使用正式枚举/适配方式处理 purpose。不要关闭 mypy、全局添加 ignore 或删除这些检查。

**接着检查测试依赖**：`.github/workflows/ci.yml` 只安装 `requirements-dev.txt`；该文件引用的 requirements.txt 和 dev 列表未列 httpx 或 mcp；pyproject 只把 mcp 放在可选 extra。新 tests/authoring 在模块级 import httpx，并在实际测试中使用 mcp。不能假定开发者 `.tmp` 环境中的依赖自动存在于干净 runner。

这是一个尚被 mypy 提前退出遮住的 CI 接入缺口。本轮未看到修复 mypy 后的下一次实际报错，不把预测当作已经发生的日志事实。

**最小验证**：在与 CI 一致的干净测试环境安装约定的依赖组合，运行现有检查入口和 authoring 测试。可以在需要 authoring 的测试环境安装 mcp extra，不必将 MCP 变成基础 HTTP 必装项；“关闭 MCP 不需要可选 SDK”的原验收继续保留。

来源：[src/emo_master/authoring/catalog.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/catalog.py)；[src/emo_master/authoring/files.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/files.py)；[.github/workflows/ci.yml](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/.github/workflows/ci.yml)；[requirements-dev.txt](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/requirements-dev.txt)；[requirements.txt](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/requirements.txt)；[pyproject.toml](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/pyproject.toml)；[tests/authoring/test_cancellation.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/tests/authoring/test_cancellation.py)。

## 4. F2 — Web 协议快照校验未同步处理

对应运行：[Web panel isolated checks 43356280800](https://github.com/jsdfhasuh/emo_master/actions/runs/43356280800)。

已读取 Linux web job `131658977242`：229 tests，1 failure、7 skipped，其余 221 通过。失败为：

```text
test_vendored_tree_is_exact_current_handoff_manifest_and_generated_sdk
web_panel/tests/test_contract_manifest.py:39
```

该测试将固定 `sdk-1.5.0` 交接包中的 proto 和生成代码，与当前仓库 proto/生成代码逐字节比较。当前增加 CandidateProject 消息/RPC 后，二者不再相同。这是交付/测试基准问题，不能仅凭此断言新增字段破坏了旧客户端 wire 兼容。

**建议**：明确哪些目录是不可变历史交接证据，哪些是当前消费者使用的契约。如果升级当前契约，就更新对应消费版本、产物和清单；如果保留固定历史包，则保留其原摘要验证，并将历史字节校验绑定原交付版本，当前源继续做生成一致性和实际协议兼容检查。不要为了变绿改坏历史原始包、删除有意义的检查或把新输出伪标成旧交付。

状态补充：本次工作流中的既有 Owning Runtime integration (synthetic data, Windows) job 成功，不意味着新的 authoring A–D 已得到这条 job 的全面验证。两平台 web-panel job 失败；本文只对已读取的 Linux job 给出上述详细数量。

来源：[web_panel/tests/test_contract_manifest.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/web_panel/tests/test_contract_manifest.py)。

## 5. F3 — 复制节点丢失外置资源绑定

位置：`src/emo_master/authoring/drafts.py` 的 `_command()` / `node.copy` 分支。

当前分支深拷贝 node、生成新 nodeId、调整名称/位置并处理 params，然后返回。资源绑定不在 node 内，而在 `resources.parameterBindings` / `resources.siteBindings`；该分支未为新 nodeId 复制这些引用。

仓库自带交付示例就能说明影响：loader 的 `params.imagePath` 是空字符串，真正输入来自资源计划中针对 `main/loader/imagePath` 的绑定。复制后得到相同空 imagePath 的新 loader，但资源计划仍只绑定原 loader，因此复制节点不具备原节点的输入来源。

**建议复验步骤（本轮未运行）**：
1. 使用正常公开入口创建并绑定一个 Image Loader，上传真实 PNG。
2. 执行 `node.copy`。
3. 读取资源计划，检查新 nodeId 是否有对应 imagePath 绑定。
4. 预检应定位到新节点的缺输入；当前代码没有自动保留原引用。

**最小修复**：在同一工程事务内复制指向原节点的资源/现场参数绑定，重定向 workflowId/nodeId，复用同一已登记资源字节。遇到不适宜直接复制的特殊绑定时需给明确处理结果，不能默默丢失。不要复制整个磁盘资源或另建资源管理器。

**直接回归**：一个“已绑定图片节点 → copy → 校验 → undo/redo”的用例即可；同时核对原绑定未被修改。无需全算子复制矩阵。

来源：[src/emo_master/authoring/drafts.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/drafts.py)；[examples/independent_project_authoring/project.emoproj](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/examples/independent_project_authoring/project.emoproj)。

## 6. F4 — 保存/重启/导入之后，声明不能正常重新采用

位置：`Draft`、`DraftService.__init__/create/_operator/_manifest/_manifests`，以及 `AuthoringService.invoke` 的目录工具。

当前 `operators` 与 `device_types` 是 Draft 的内存字段。正常重启只从 project.emoproj 建立新 Draft；导入也走 create，因而这些字典重新为空。

`_manifest()` 只查询该 Draft 的 operators。`get_operator` 工具只查询 RuntimeCatalog 的全局目录缓存，并没有关联 draftId；`node.configure` 也没有声明刷新参数。于是“重新查询目录”不能恢复已保存草稿的采用声明。

可观察后果（由代码推导）：
- 本地 validate 会继续给已有节点增加 `DECLARATION_UNAVAILABLE`；有设备时还会缺设备类型声明。
- output_choices 仍会列出持久化端口，但带 `plugin_missing / trusted plugin manifest unavailable`，不是整个列表消失。
- 设备资源绑定依赖 draft.device_types，恢复后可能被“device declaration required”拒绝。
- 普通参数仍有保存的 paramSchema 可用，目标 precheck 也独立查询目标注册表，因此不能扩大成“重启后一切都不能编辑或预检”。

**建议复验步骤（本轮未运行）**：资源齐全的图像草稿在本地 validate 通过后保存 → 重启服务 → 成功调用 get_operator 查询同一目标同一版本 → 再次 validate/output_choices；与重启前比较。ZIP 导入后也检查同一路径。

**最小修复**：提供现有草稿按明确 targetId、正式依赖版本重新采用声明的途径，或保存有来源/版本信息的草稿侧声明快照以供离线使用。两者择符合现有架构的最小方式，不一定同时实现，不自动假定最近查过的 Runtime 就属于此草稿，不新增分布式目录或多版本管理平台。

可以离线标 INCOMPLETE；问题不是“未知必须伪装为已知”，而是目标已可查询时缺少把真实声明重新关联到已有草稿的正常入口。禁止以创建再删除一个临时同类节点作为正式恢复步骤。

来源：[src/emo_master/authoring/drafts.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/drafts.py)；[src/emo_master/authoring/service.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/service.py)；[src/emo_master/authoring/validation.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/validation.py)；[src/emo_master/core/presentation/catalog.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/core/presentation/catalog.py)；[tests/authoring/test_drafts.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/tests/authoring/test_drafts.py)。

## 7. F5 — 预检准备仍同步阻塞事件循环

位置：`AuthoringService.invoke()` 的 precheck 分支、`FilesService.snapshot_zip/_freeze/_write_zip`、`RuntimeCatalog.precheck()`。

普通命令通过 `asyncio.to_thread` 执行。但 precheck 分支直接在 async invoke 内进入同步 snapshot_zip；其进入过程包含等待草稿线程锁、完整模型/资源检查、文件复制、ZIP 压缩以及 read_bytes。随后 catalog.precheck 在发起 gRPC 前又同步读取文件计算摘要。

因此，在本次同步准备期间，同一个事件循环无法处理其他请求任务。对当前 109-byte PNG，这段时间可能很短；对实际模型文件、较大工程或等待同草稿写锁，会放大成服务响应停顿，连取消处理也可能要等同步准备结束。本文未做真实负载计时，不声称必然卡住若干秒。

**最小修复**：把冻结/资源复制/压缩/摘要等阻塞工作放到既有工作线程边界，明确文件仍由实际工作持有，直到异步 gRPC 不再读取后清理。避免只是外层请求取消就删除线程仍在使用的临时目录。无需引入独立任务平台、队列集群或新进度服务。

**直接回归**：用受控较慢的准备过程，同时对另一草稿做一次读取，确认循环仍能服务；复用既有取消/清理测试。无须增加全库性能压力矩阵。

来源：[src/emo_master/authoring/service.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/service.py)；[src/emo_master/authoring/files.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/files.py)；[src/emo_master/authoring/catalog.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/catalog.py)。Python 官方说明：在事件循环线程执行阻塞工作，会延迟其它 asyncio 任务；可使用 executor/to_thread 隔离。参见 [Python 3.10 Developing with asyncio](https://docs.python.org/3.10/library/asyncio-dev.html#running-blocking-code)。

## 8. F6 — 导出状态与刚完成的同版本预检矛盾

位置：`FilesService.export()` 与提交中的 inspector-report.json。

导出响应固定填写：

```json
{
  "precheck": {"status": "not_run", "draftRevision": "同一修订"},
  "checkReport": {"status": "not_checked", "draftRevision": "同一修订"}
}
```

而 Inspector 报告中 afterRepair 已对 `28ea9a2faa5a4c6c9a2f32cdca967add` 返回 PASSED / compileStatus=passed，紧接的 download 仍对这个修订返回 not_run/not_checked。并不是一个假设异常，已提交的验收记录本身就显示该语义不一致。

**影响**：客户端无法分清“这个工程版本未检查”和“导出接口没有附带此前检查报告”。文件仍可下载，不能把它描述为导出文件损坏。

**最小修复**：导出可附带匹配 draftRevision、targetId、runtimeInstanceId 的已返回报告；或者明确返回“本导出未附带报告/导出动作未执行检查”，不要据此断言该版本从未检查。保留“不能拿旧报告证明新修订通过”的约束，不要求建立持久化报告系统或强制每次导出重跑预检。

**直接回归**：同一图像样例先预检成功再导出，核对同版本状态；修改后导出不得沿用旧通过结果。

来源：[src/emo_master/authoring/files.py](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/src/emo_master/authoring/files.py)；[docs/testing/2026-11-13-independent-project-authoring/inspector-report.json](https://github.com/jsdfhasuh/emo_master/blob/3f717510751f4bd0d8277c3c2bf676236f51f49e/docs/testing/2026-11-13-independent-project-authoring/inspector-report.json)。

## 9. 建议的修复顺序与收尾条件

先修 F1/F2，让现有 CI 能实际运行完整相关检查，尤其确认新测试依赖在干净环境中安装。随后修 F3/F4 的日常编辑恢复链路，处理 F5 的阻塞准备和 F6 的状态语义。每项对应一个直接回归或扩展现有样例即可。

不建议把整套 OpenSpec 推倒重来。当前任务表可以保留已交付证据，但对相关项目追加缺陷状态/重新打开对应收尾项，不能在已知 CI 红和这些问题存在时继续写“没有遗留问题”。历史证据、原始 v6/v7 归档及摘要不要覆盖。

交付前应具备：固定新 SHA 的 CI 结果；节点复制资源引用验证；已保存与导入草稿的声明恢复验证；大文件准备不阻塞独立读取的局部验证；导出与预检状态一致。最后复用原 Inspector 图像链与受影响的 Designer 测试，不要求额外访问生产设备。

## 10. 本轮范围声明

本轮读取并分析了指定提交的 authoring/catalog、drafts、commands、files、service、http、validation，Runtime 目录装配与 candidate 入口，正式页面输出目录、相关测试与示例、交付报告及 CI 配置/日志。没有逐行审阅每一个历史归档文件，也没有在本环境启动应用或重跑验收。

这是基于固定提交的独立代码/证据审查。没有修改实现、运行现场设备、创建 PR、推送或改动 CI。对后续代码变化应另按其 SHA 复核。
