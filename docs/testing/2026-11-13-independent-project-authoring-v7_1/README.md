# V7.1 独立工程草稿与 MCP 修复记录

执行者 Sol，2026-11-13。实际分支为 `main`，开始和本轮验证时 HEAD 均为 `3f717510751f4bd0d8277c3c2bf676236f51f49e`，起始仅有未跟踪的 `.tmp/`，没有后续提交。本记录针对该 HEAD 加本轮未提交差异，不能记作该 SHA 干净版本的测试。验证阶段没有创建分支、回退、提交或推送。随后用户明确授权“提交 推送上去吧”，本次按原 main 分支交付；源码与证据映射见 [提交与推送记录](publication.md)。没有合并、发布、部署或操作现场设备；远端 CI 以本次固定 SHA 对应的实际 run 为准，不能沿用旧 SHA 的结果。

V7.1 已比较合入原活动 [change](../../../openspec/changes/add-independent-project-authoring/tasks.md)，保留原 25 项完成任务及证据映射。历史 V6/V7 归档、SDK 1.5.0 handoff、原验收目录相对 HEAD 无差异，两个归档的原摘要分别 18/18 匹配。修复包来源及摘要见 [repair-source.json](repair-source.json)。`review/` 保留包内原件字节，属于修前审查资料，不能当作本轮运行结果；其中 `baseline-alignment-v7.md` 的相对链接按原包上下文保留，当前有效入口为 [活动 sources.md](../../../openspec/changes/add-independent-project-authoring/sources.md)。

| 缺陷 | 本轮确认与实际修复 | 本轮直接验证 |
| --- | --- | --- |
| F1 类型与测试环境 | 先复现 5 个 mypy 错误，修正 `_cache`、ResourcePlan 可空收窄及正式用途模型校验。干净 Python 3.10 环境实际发现原开发依赖缺 httpx、新 Starlette TestClient 需要 httpx2，再补 dev 依赖及固定 MCP SDK；产品 MCP 仍可选。 | mypy 633 文件通过，干净安装/收集成功；真实未安装 MCP 的环境以 `--no-mcp` 启动并经 HTTP 创建/读取/保存。完整原 CI 入口另记录。 |
| F2 历史 SDK 契约 | 错误断言可复现。历史 proto/wheel 对照不可变原 handoff 全部 40 个 manifest 文件；保留来源、版本、大小、摘要和 embedded fixtures 断言。当前生成检查沿用，不改历史包或生成代码、不发行新 SDK。 | 契约 3 项通过；无 Core/Qt 的独立旧 SDK 1.5.0 客户端：14 项 loopback 协议消费、5 项原 Runtime 测试通过（3 项实际 Runtime、2 项协调单元测试）。替代断言映射见 [F1/F2 记录](f1-f2.md)。 |
| F3 复制外置绑定 | 公开入口真实 PNG 复现复制丢绑定；在 node.copy 原有唯一事务内复制 parameterBindings/siteBindings，指向实际新 nodeId，保留路径/用途/resourceId，原绑定和磁盘字节不变。 | 真实 PNG、现场输出绑定、一次 undo/redo、保存重读、本地校验及未复制磁盘文件；包含在 authoring 全集中。 |
| F4 声明恢复 | 新增共用 `adopt_project_declarations`；HTTP `POST /api/project-drafts/{draftId}/declarations`，必填 draftId、expectedDraftRevision、targetId。按保存的算子/设备驱动精确版本查询明确目标，全部匹配后原子采用。 | 保存重启和 ZIP 导入均由真实 Runtime 公开入口恢复 validate/output_choices/参数编辑；另以明确标注的设备 fixture 验证设备、离线/版本冲突/缺锁、原子失败、重复采用及上下文修订。5 项新测试纳入 authoring 全集。 |
| F5 准备与取消 | 将锁等待、冻结、资源检查/复制、ZIP/摘要移到线程；预检操作持有临时文件直到真实准备/读取工作退出。gRPC 显式写入持有文件消费者，准备取消不进入下一阶段。 | 官方 SDK 显式取消的 prepare/stream 两个受控边界：等待释放前另一草稿读取成功、工作退出前文件保留、退出后清理、已提交编辑/重连修订保留；原编译取消回归通过。 |
| F6 导出语义 | 先以新断言复现旧响应两项失败，再改为 `checkReport={status:not_attached,draftRevision,performedByExport:false}`，去掉无作用域 precheck=not_run；不新增报告存储或强制检查。 | [修前失败](f6-before.txt)、[修后 18 项](f6-after.txt)；同修订 PASSED 后及修改后导出断言通过，Inspector 同修订实测通过。18 项与 authoring 全集重叠，不相加。 |

F4 只改变声明上下文时返回新修订，不改工程依赖、参数、绑定、dirty、保存时间或内容撤销历史；重复采用不会因 observedAt 变化反复生成修订。离线、缺锁或版本不符时保留原上下文且仍可保存。F6 的 not_attached 只表示本导出未附报告、导出没有执行检查，不否认先前预检，也不证明当前版本通过。

本轮改动限于 authoring catalog/drafts/files/service/http、开发依赖、历史契约测试、直接回归、验收脚本和活动规范/指南。没有提取或改动 Designer 业务规则，仅给既有 Designer 衔接测试增加实际下载目录参数，继续走正式 MainWindow 加载/保存入口。原四字段目录链、候选 RPC、ROI 动态版本及页面快照优化直接复用。

本轮协议组合为 Python **3.10.21**、官方 MCP SDK **1.30.0**、MCP Inspector **2.13.0**、协商协议 **2025-11-25**，文件方式为 Inspector + HTTP 辅助。干净环境使用独立 venv，`include-system-site-packages=false`；依赖及实际导入位置见 [F1 环境](f1-clean-environment.json)、[F2 环境](f2-sdk-consumer-environment.json)。开发环境有依赖弃用提示，未当作失败。此验证仅证明所述组合，不扩大为全客户端/平台矩阵。

| 本轮检查 | 结果与证据 |
| --- | --- |
| authoring 全集 | **59 passed，9 warnings，40.54s**，无跳过；[输出](authoring-current.txt)、[JUnit](authoring-current.xml)。包含高级流程、嵌套 ROI、设备/变量 fixture、页面/生产配置、取消、无 MCP、保存导入和资源公开入口。 |
| 最终源码的干净环境 authoring | **59 passed，9 warnings，42.99s**，无失败/跳过；[输出及前后 SHA-256](clean-authoring-current.txt)、[JUnit](clean-authoring-current.xml)。使用 F1 同一独立 venv，与上一行同一测试集，不相加。 |
| 相关 Runtime/ROI/store/page | **113 passed，24.25s**，无失败/跳过；[输出](regression-current.txt)、[JUnit](regression-current.xml)。与 authoring 全集的测试文件不重叠。 |
| 外部 Inspector | [报告](inspector-report.json) success=true；实际目录/详情、缺 PNG INCOMPLETE → 上传/新修订绑定 → PASSED、声明采用后 local PASSED、同修订导出、实际鉴权下载/无凭据拒绝。ZIP **3018 bytes**，SHA-256 `80b35a5facfc301797e89159291a5872f0c13ef4cff204918fa1d36b6b941533`。 |
| 同下载物 Designer | **1 passed，2 warnings，3.68s**，Windows 原生 Qt 自动化；正式打开/改名/保存/重开及真实编译，字段/资源保留。[输出](inspector-designer-native.txt)、[JUnit](inspector-designer-native.xml)、[截图](inspector-designer-native.png)。用例与 authoring 全集重叠，但另指向本轮 Inspector 实际下载物。 |
| 隔离软件 Runtime | 对同下载工程单独显式运行，**COMPLETED**，见 Inspector 报告；预检本身仍 executionStatus=not_run，未新增 MCP 运行工具。 |
| Ruff / mypy / proto | 原全源命令通过；mypy **633 文件**，未加全局忽略或修改生成代码。最终工作区的 [Ruff](ruff-current.txt)、[mypy](mypy-current.txt)、[proto](proto-current.txt) 输出另存，proto 命令退出 0、没有标准输出。干净原 CI 入口亦通过三项前置检查。 |
| OpenSpec | CLI 2.0.0，`openspec validate add-independent-project-authoring --strict` [通过](openspec-current.txt)。 |
| Web 原完整 Python suite | 独立旧 SDK 客户端环境按原 Web job 的 `python -m unittest discover -s tests -v`：**229 tests，222 passed，7 skipped，67.546s**；[输出](f2-web-backend-complete.txt)。跳过为原有 opt-in，未新增跳过；其中实际 Runtime 入口已另行显式开启验证。 |
| 干净依赖环境原 CI other | 自然结束 **exit 1：6185 passed、292 failed、21 errors、3 skipped、3375 deselected、66 subtests passed，2263.82s**；原分片选择并执行 6501 个唯一用例。依赖环境干净，源码为当前工作区，包含本地 ignored 测试，不是干净 clone 或远端 CI 的同范围证明。[公开聚合统计](f1-other-summary.json) 不包含私有用例原文；原件保留在本地，摘要见 [原件清单](local-ci-evidence-manifest.json)。 |

主要复验命令（本机解释器为 `D:/Conda/envs/emo_master/python.exe`；干净 CI 为 `.tmp/v7_1-f1-clean/Scripts/python.exe`）：

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONIOENCODING='utf-8'
$env:QT_QPA_PLATFORM='windows'
python -m pytest tests/authoring -q --tb=short -o junit_family=legacy `
  --junitxml=docs/testing/2026-11-13-independent-project-authoring-v7_1/authoring-current.xml
python -m pytest tests/runtime/test_candidate_project_precheck.py tests/runtime/test_runtime_grpc_service.py `
  tests/runtime/test_runtime_grpc_network.py tests/runtime/test_operator_editor_preview.py `
  tests/designer/test_workflow_store.py tests/core/presentation/test_edit_session.py `
  tests/core/presentation/test_snapshots.py tests/plugins/test_roi_regions.py `
  tests/plugins/test_contour_point_filter.py tests/plugins/test_merged_contour_circle.py -q
python scripts/authoring_acceptance.py --inspector <Inspector的clients/launcher/build/index.js> `
  --output .tmp/independent-authoring-v7_1-inspector
$env:EMO_AUTHORING_HANDOFF_PROJECT_DIR=(Resolve-Path .tmp/independent-authoring-v7_1-inspector/download).Path
python -m pytest tests/authoring/test_designer_handoff.py::test_external_inspector_download_sample_continues_in_native_designer `
  -q --tb=short -o junit_family=legacy --junitxml=docs/testing/2026-11-13-independent-project-authoring-v7_1/inspector-designer-native.xml
Remove-Item Env:EMO_AUTHORING_HANDOFF_PROJECT_DIR
python -m ruff check src tests
python -m mypy --config-file mypy.ini src
python scripts/gen_proto.py --check
openspec validate add-independent-project-authoring --strict
```

原 CI other 进程在最后两项新增测试修正前完成导入：设备不匹配反例最初使用不满足正式版本语法的值，先得到 422；无锁导入反例最初把 create 独有的 createdIds 与 read 返回比较。已分别改用合法不匹配版本 `9.9.9` 和正式 read 快照，保留原 409/版本诊断及工程不变断言。上述两次 59 项全通过均针对修正后的测试，干净运行前后的 6 个源/测试文件摘要一致；最终代码及测试字节另记录在 [source-snapshot.json](source-snapshot.json)。原完整 CI 的失败证据不删除，两个快照不混称同一结果。

完整分片的失败分类还需区分源码范围：既有 `.git/info/exclude` 排除的 8 个 core 配方/迁移测试文件和一个 Designer 迁移测试文件，合计 **276 failed / 21 errors**。这些文件 `git ls-files` 无返回，本轮没有改动或解除排除。其余 **16 failed** 来自受 Git 管理的测试：2 项为上述较早导入的新测试，12 项为 Designer ROI/弹出搜索/图标/布局测试，1 项为打包验证脚本测试，1 项为页面交付探针。原入口完整执行、没有过滤这些文件；因此本次总数不能直接当作 GitHub 干净 checkout 的 CI 结果。六份原始 CI 诊断包含本地忽略用例，提交前原样移到本地 `.tmp/v7_1-private-ci-evidence/` 保留并核对摘要，不入库、不推送。公开版仅保留聚合统计及原件大小/摘要，不删除失败证据。

对范围外失败只做一个代表性基线核实：将固定 `3f717510` 的源码以 `git archive` 解到本轮临时目录，不创建分支或 worktree，用同一干净解释器/native Windows 执行 `tests/designer/test_draft_pure_preview_ui.py::testSpecializedLocalImageErrorRecoveryPreviewAndClose[roi-2.1]`，同样因 `RoiCanvas._image` 缺失失败，见 [基线反例](f1-baseline-roi-representative.txt)。仅这一代表可认定为基线可复现；其他配方/PLC/Designer 失败是本轮未改文件中的失败，未逐项复跑基线，不一概标作历史问题，也不扩成本轮全面 UI 整改。

启动、连接、真实上传/新修订绑定和携带凭据下载步骤见 [服务指南](../../guides/independent-project-authoring.md)。本轮实际下载工程为工作区 `.tmp/independent-authoring-v7_1-inspector/download/project.emoproj`，资源在同目录；原 ZIP 为 `.tmp/independent-authoring-v7_1-inspector/download.zip`，已按相同摘要复制一份 [完整交付 ZIP](inspector-delivery.zip) 到本证据目录。Designer 自动化复制实际下载目录后修改，不改下载原件；将 ZIP 整体解压后，用“文件 → 打开工程”选择其中的 `project.emoproj` 可继续人工验收。

固定审查 SHA 的远端主 CI [43356280900](https://github.com/jsdfhasuh/emo_master/actions/runs/43356280900) 和 Web [43356280800](https://github.com/jsdfhasuh/emo_master/actions/runs/43356280800) 为历史失败，实际故障层次见 [F1/F2 记录](f1-f2.md)。本次提交前 fetch 确认 `origin/main` 仍为固定 `3f717510`，与本地无提交分歧。推送授权不意味着 CI 已通过；新 SHA 的 run 与状态在对应提交评论中记录。原 CI other 的未关闭失败、完整 Runtime 分片、浏览器矩阵、人工 UI、冻结包和硬件验收分别保留，不扩大为全面整改。第 6.7 收尾保持未勾选，活动 change 不归档，不宣称无遗留问题；历史 148/280 项不算本轮结果。
