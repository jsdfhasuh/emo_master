# 独立完整工程草稿服务与 MCP 编排：本轮交付记录

日期：2026-11-13。执行者：Sol。计划来源：ChatGPT Web OpenSpec v7。

本轮在 `main` 上实施，验收时实际 HEAD 为 `b61a96f6d8135497531eabf18245118de253ee03`，与计划参考基线相同，没有后续提交需要重复实现。以下证据针对该 HEAD **加当时的未提交修改**，不是该 SHA 干净版本的验证结果。验收完成后用户明确授权提交并推送工作区；本次在原 `main` 上提交本轮源码、规范、文档、测试与示例，临时环境和构建缓存留在本地。未新建分支、合并、发布或部署常驻服务。

开始时工作区只有此前整理的 v7 计划归档。活动变更为 `openspec/changes/add-independent-project-authoring/`；v6 原始归档未修改，v7 原始归档的 18 项 SHA-256 校验全部匹配。活动任务及本轮记录另行更新，不改写归档的原始摘要。

## 完成范围

| 阶段 | 本轮结果 | 实现与证据 |
| --- | --- | --- |
| A | 完成 | 新增 `emo_master.authoring`，同一业务服务提供 HTTP 与可选 MCP；真实 Runtime 算子/设备目录、完整详情、实际命令 schema、独立完整工程会话、修订/事务/撤销重做、离线校验及正式保存重读。实际外部客户端已贯通。 |
| B | 完成 | 真实字节上传/替换、新物理路径与旧字节保留、资源绑定、当前草稿页面输出绑定、正式导入导出、固定鉴权下载；真实 Designer 正式入口打开、修改、保存、重开及编译已通过原生 Qt 自动化。 |
| C | 完成 | 单工程 ZIP 流 RPC、独立候选副本、真实声明/资源准备/编译、定位诊断与修复后新修订再检查；运行中 A 未被候选 B 替换；实际 MCP → gRPC 异步边界取消、工作退出后清理及重连读取已验证。 |
| D | 完成 | 公开命令接入流程复制/删除/入口/接口、Subflow/Loop/Dispatch/Join、设备与变量、资源/现场参数、页面布局/终端/导航、Runtime 与生产配置；高级代表通过新 HTTP 命令入口和正式保存重读验证。 |

活动 `tasks.md` 的勾选依据下面的本轮证据。没有已知尚待实施的 A–D 任务；人工桌面走查、真实硬件、既有安装产物升级和远程部署的验证边界单列在后文。

目录链路沿用既有四字段及编号（18/19/20 和回复字段 3），由真实 manifest 和 Runtime 实例填值，经过服务实际使用的 `emo_panel_protocol.generated` 到 HTTP/MCP。未重复添加同义字段，没有手改生成代码。`nextCycleParameterPaths=null` 与 `[]` 分别保留“未提供”和“明确空列表”；未知 Core 版本和实例身份不补造值。当前目录失败返回错误，不伪装为空列表。

完整工程复用 `ProjectEditSession`、`WorkflowStore`、`PresentationStore`、正式模型和保存入口；新工程为 2.9 / 页面 1.2 / 设备 1.0。每草稿持有独立会话、锁、修订和目录声明缓存。页面只读继续使用局部 `snapshot()`；完整事务、保存、导出和候选检查仍保留完整校验及隔离。

Designer 调整仅包含本轮实际复用规则的局部调用适配：`workflow_controller.py` 使用提取后的流程引用/Loop 合同/连线修剪；三个端口呈现模块使用共用的端口适用规则；`MainWindow._applyEditorConfiguration()` 使用共用参数/变量/端口校验。`PresentationStore` 的可选写入校验回调默认关闭，由草稿会话启用，在原有事务内拒绝新引入的非法绑定。未改造整体 MainWindow、桌面事务/撤销系统、画布或 Qt ROI 绘图。

## 本轮实际验证

环境为 Windows、Python **3.10.21**、PySide2/Qt Widgets。官方 Python MCP SDK **1.30.0**，外部 **MCP Inspector 2.13.0**（Node **v24.21.0**），实际协商协议 **2025-11-25**。文件方式为 **Inspector + HTTP 辅助**，上传下载使用同一工作区 Bearer 凭据。可选依赖固定 `mcp==1.30.0`，未升级项目 Python 基线。

| 证据层次 | 本轮实际执行及结果 |
| --- | --- |
| 集中源码/网络/Qt 自动化回归 | **148 passed，5 条依赖弃用提示**；无失败或跳过。包含所有新增 authoring 测试、候选预检、既有 gRPC/ROI 预览、WorkflowStore、页面会话及 ROI/轮廓相关测试。见 [原始输出](regression.txt)、[JUnit](regression.xml)。 |
| 直接受影响 Designer 回归 | **280 passed**。覆盖参数与变量编辑、模式端口、流程导航/接口/边界、页面会话和草稿直接反例。与 148 项存在重叠，不能相加作为唯一用例数。 |
| 外部真实客户端 | 最终 Inspector 重跑 `success=true`；缺 PNG 时目标返回 `INCOMPLETE/E_CANDIDATE_INPUT_MISSING`，定位到 `loader/params.imagePath`；真实上传、新修订绑定、mask 页面、undo/redo、本地保存后目标返回 `PASSED/compileStatus=passed/executionStatus=not_run`。见 [原始报告](inspector-report.json)。 |
| 实际文件往返 | 已发送 PNG 原始字节，核对摘要和上传新修订；已携带同一凭据 GET 下载 ZIP，3020 字节，SHA-256 `70fd93aad4570e5606c16997ddba20b2ab6502fd21e47a493e0eae1945968b48`；无凭据请求被 401 拒绝。 |
| 原生 Qt 自动化 | `QT_QPA_PLATFORM=windows`，真实 `MainWindow` 显示，走 `loadProjectSelection()`、节点/流程改名、`saveProjectAction()`、正式重读、重开及真实编译。核对页面/资源摘要/设备/变量/生产配置保留。包含于 148 项；单独初次执行亦为 1 passed。见 [原生窗口截图](designer-native.png)、[单独 JUnit](designer-native.xml)。这是自动化，未记为人工操作。 |
| 隔离软件 Runtime 执行 | Inspector 脚本在预检之后，通过明确的独立验收操作调用 `LoadProject/StartJob`，下载工程运行结果 **COMPLETED**，Job `3ac973f1-67ee-46a7-b7dc-20befe36919d`。编排服务未增加运行工具，预检本身未创建 Job。 |
| 真实取消与无 MCP 启动 | SDK 1.30.0/协议 2025-11-25 显式取消经过实际 MCP → gRPC 边界；编译工作退出前保留资源/额度，退出后清理，草稿与已提交修改仍在，重连先读后可再次检查。`--no-mcp` 子进程禁止导入 MCP/PySide2，HTTP 新建/读取仍成功。包含于 148 项。 |
| wheel 安装自测 | 最终 wheel 在清空 `PYTHONPATH`、工作目录离开源码入口的独立 venv 安装；服务、候选模块和协议从 venv `site-packages` 导入，未导入 Qt；HTTP 创建/保存、可选 MCP 生命周期和非法 JSON 导入定位均通过。依赖复用 `--system-site-packages`，不声称全新离线依赖安装验收。见 [实际输出](installed-wheel-smoke.txt)。 |
| 工具检查 | `scripts/gen_proto.py --check`、新增及直接改动文件 Ruff、`git diff --check` 均通过；OpenSpec CLI **2.0.0** 严格校验活动变更通过。 |

高级公开命令测试使用明确标记的 `SourceCatalog` 源码 fixture，验证复合 ROI 的 regions/points/条件字段、设备/变量、复杂控制流、双终端页面/导航/布局及生产配置的字段和引用保留。它不冒充真实远端集成。真实 gRPC 目录和外部 Inspector 分别验证源 manifest 到客户端的真实返回；未知/空列表/非空列表由同一目录适配测试验证，无需部署历史版本矩阵。原有 ROI 版本修复和功能断言未删除或改为另一固定版本。

最终审查额外修复了公开小型 JSON 工程导入错误：正式模型字段类型错误现在转换为 `E_PROJECT_INVALID/422` 并保留如 `project.name` 的位置，目录创建前失败。该反例在集中回归与安装 wheel 中均已验证。

另已对外部 Inspector 最终下载的**同一份工程**补做正式 Designer 衔接：复制 `examples/independent_project_authoring/` 至临时目录，正式打开、改名、保存、重开及真实编译，projectId 和页面/资源/生产字段保持一致。单独执行 `test_external_inspector_download_sample_continues_in_native_designer` 为 **1 passed in 4.40s**，后纳入集中回归；见 [JUnit](inspector-designer-native.xml)、[原始输出](inspector-designer-native.txt) 和 [对应窗口截图](inspector-designer-native.png)。因此外部客户端流程和 Designer 交付之间也使用了同一下载物，而非仅由等价工程代表。

## 复现命令

以下命令在仓库根目录执行，使用本轮 Python 3.10 环境；未将凭据写入命令、工程或报告。

```powershell
$env:PYTHONPATH='src;.'
$env:QT_QPA_PLATFORM='windows'
python -m pytest tests/authoring tests/runtime/test_candidate_project_precheck.py `
  tests/runtime/test_runtime_grpc_service.py tests/runtime/test_runtime_grpc_network.py `
  tests/runtime/test_operator_editor_preview.py tests/designer/test_workflow_store.py `
  tests/core/presentation/test_edit_session.py tests/plugins/test_roi_regions.py `
  tests/plugins/test_contour_point_filter.py tests/plugins/test_merged_contour_circle.py `
  -q --tb=short -o junit_family=legacy --junitxml=<新的结果路径>
# 本轮 148 passed, 5 warnings in 43.42s

python -m pytest tests/authoring/test_drafts.py tests/core/presentation/test_edit_session.py `
  tests/designer/test_variable_node_migration.py tests/designer/test_variable_write_modes_ui.py `
  tests/designer/test_operator_modes.py tests/designer/test_coordinate_port_presentation.py `
  tests/designer/test_collection_filter_presentation.py tests/designer/test_workflow_navigation.py `
  tests/designer/test_workflow_interface_dialog.py tests/designer/test_workflow_boundary_nodes.py -q
# 本轮 280 passed in 42.64s

python scripts/authoring_acceptance.py --inspector <Inspector的clients/launcher/build/index.js> `
  --output <新的空证据目录>
python scripts/gen_proto.py --check
openspec validate add-independent-project-authoring --type change --strict --no-interactive
```

本机 OpenSpec CLI 位于 npm 的 npx 缓存，实际调用的是 `C:/Users/jsdfhasuh/AppData/Local/npm-cache/_npx/5914905422e702ee/node_modules/.bin/openspec.cmd`。Inspector launcher 位于 `C:/Users/jsdfhasuh/AppData/Local/npm-cache/_npx/5a9d879542beca3a/node_modules/@modelcontextprotocol/inspector/clients/launcher/build/index.js`。缓存路径只是本机复现定位，不是部署依赖约定。

本轮构建使用 `python -m pip wheel . --no-deps --no-build-isolation`。最终本地产物为 `.tmp/independent-authoring-package/wheels-final/emo_master-0.6.6-py3-none-any.whl`，1,729,007 字节，SHA-256 `24ddd053d3185676930988ab2e69ec0cc5c82721b0a91003513927f80305b86b`。已核对 wheel 中本轮 authoring 和协议生成模块字节与最终源码一致；安装自测导入路径亦已记录。未执行独立 SDK 发行或修改既有已安装应用。

## 使用与交付物

启动、目标配置、MCP 连接、HTTP 请求、上传新修订绑定及鉴权下载的完整步骤见 [服务指南](../../guides/independent-project-authoring.md)。启动入口为 `python -m emo_master.authoring` 或 `emo-authoring`；凭据通过进程环境 `EMO_WORKSPACE_TOKEN` 配置，默认仅监听 `127.0.0.1`，MCP 路径为 `/mcp`。`--no-mcp` 可单独运行基础 HTTP 服务。

可交付示例在 [工程文件](../../../examples/independent_project_authoring/project.emoproj)，连同同目录 `assets/` 一起复制；[示例说明](../../../examples/independent_project_authoring/README.md) 给出 Designer 打开和服务导入步骤。它来自最终 Inspector 实际下载物，仍为正式最新格式，没有凭据、目标地址或现场设备配置。最终原始 ZIP 保留于 `.tmp/independent-authoring-20261113-final/download.zip`，其字节摘要见上表。

## 未验证与范围边界

- 未进行人工鼠标/键盘桌面走查。曾启动隔离窗口辅助进程尝试检查，但计算机使用工具未返回可操作目标窗口，该尝试未算通过；只停止了本轮拥有的辅助进程。正式 Designer 打开/修改/保存由上述原生 Qt 自动化证明。
- 未访问真实相机、PLC 或现场工程，没有硬件验收；高级设备代表使用内置模拟设备声明且不连接设备。
- 未测试用户现有安装包、冻结产物升级、远程 TLS/LAN 部署、其他客户端/协议组合；安装证据限于本轮 wheel 和已记录依赖环境。没有把这些未执行项记作失败或已通过。
- 没有本轮已知的软件实现阻塞。服务提供独立工程编辑和候选编译，完整浏览器流程画布、Qt ROI 绘图移植、自动生产发布/启停均保持本轮规范边界。

所有验收服务和软件 Job 均为临时隔离实例，没有操作用户正在运行的 Designer 或在线生产工程。用户授权后的提交和推送由 Sol 执行，并在对应 GitHub 提交下附含“ChatGPT Web 计划来源”的评论，列明实际改动、上述已验证项和未验证边界。固定提交 SHA 及远端状态以该提交和评论关联，不将验收基线改写为新 SHA 的干净版本重跑结果；本活动变更仍保留供核对，不执行归档。
