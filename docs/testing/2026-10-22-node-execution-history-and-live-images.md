# 节点两次执行与运行中图片验证记录

日期：2026-10-22。结论：**审查修正后，受影响回归 229 项通过，Windows 原生 Qt 5 项连续三轮通过。未打包、发布或操作现场设备。** 第 3 至 6 节保留首次实现结果，审查问题、修正及最终证据见第 9 节。

## 1. 源码与范围

- 仓库：`C:/Users/jsdfhasuh/my_scripts/emo_master`。
- 核对 HEAD：`d39169251c9bedc339c440a29a56df5eafba3be5`，分支 `agent/runtime-workflow-architecture-v1`。
- 实际测试对象是该 HEAD 加本工作区修改，不是未经修改的 HEAD，也没有创建新提交。
- 本功能首次实现的 27 个源码/协议/测试文件 SHA-256 快照见 [source-hashes.json](node-execution-results-2026-10-22/source-hashes.json)。它包含 14 个手写源码模块、生成的 `runtime_pb2.py`、proto 和 11 个测试/夹具文件；同文件内的既有修复仍保留。审查修正后的当前快照见 [review-repair-final-source-hashes.json](node-execution-results-2026-10-22/review-repair-final-source-hashes.json)；原快照不覆盖。
- 遵循 [实施计划](../plans/2026-10-22-node-execution-history-and-live-images.md) 与 [现行契约](../runtime-node-results-contract.md)。`AGENTS.md` 已包含用户要求的“正常业务路径优先、局部修改，不做极端环境容错设计”。
- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10 / PySide2 / QWidget。

没有把既有工程规范、在线图、项目配置、设备 UI、菜单或 `read_only_graph.py` 的未提交修改算成本功能新增改动，也没有回退它们。`node_result_coordinator.py` 原有的会话工程引用修复保留，读图继续使用检查会话的工程引用。

## 2. 实现清单

| 范围 | 文件与实际变化 |
| --- | --- |
| 节点历史 | `state/run_result_history.py`：按 workflow/node 保留两次实际调用；开始轮换、完成更新；冻结定义与保存回执；跳过不轮换；终态关闭未完成记录；保留仍被节点引用的旧 Job |
| 面板文字与联动 | `node_result_panel.py`、`node_result_coordinator.py`、`presenters/node_run_presenter.py`：本次/上次、原来源、四个标签与大图一致；取消/中断可读；同位置连续刷新不反复取消读图 |
| 后台读取 | `services/inspection_images.py`、`runtime_client.py`：精确执行筛选、同选择去重、上下文失效通知、原始/预览尺寸 |
| 图片冻结与保留 | Runtime `preview/store.py`、`run_inspection.py`：不可变预览、节点完成即可读、每节点两次、读者退出后释放、复用原额度与清理 |
| 原有通道接线 | `jobs/models.py`、`worker_main.py`、`supervisor.py`、`events/models.py`、`grpc_server/service.py`、`workflow/runner.py`：仅为已协商检查会话传递冻结图片；内部元数据屏障；复制在事件顺序锁/调度锁外；沿用保存回执 |
| 协议 | `proto/runtime.proto`：新增执行筛选与原始尺寸；用 `scripts/gen_proto.py` 生成桩 |

未改各业务算子、阈值、原图、循环控制、任务调度语义、设备控制或工程格式。未新增历史数据库、通用事务框架或后台补偿系统。

## 3. 首次实现检查结果

| 层次 | 最终结果 | 证据 |
| --- | --- | --- |
| 受影响 Designer + Runtime 回归 | **220 passed，0 failed，0 skipped**，72.45 秒；Qt 为 offscreen，Runtime 用实际 spawn 与本机 gRPC | [affected-regression.txt](node-execution-results-2026-10-22/raw/affected-regression.txt) |
| Windows 原生 Qt | **4 passed，0 failed，0 skipped**，22.85 秒；`platform=windows`、DPR=1.0 | [native-qt.txt](node-execution-results-2026-10-22/raw/native-qt.txt) 与下节截图 |
| Ruff | 本次 25 个手写 Python 源码/测试文件通过 | [ruff.txt](node-execution-results-2026-10-22/raw/ruff.txt) |
| mypy | `Success: no issues found in 507 source files` | [mypy.txt](node-execution-results-2026-10-22/raw/mypy.txt) |
| Proto | `scripts/gen_proto.py --check`，退出码 0，生成桩无漂移 | [proto-check.txt](node-execution-results-2026-10-22/raw/proto-check.txt) |
| 差异检查 | `git diff --check`，退出码 0；仅 Git 的既有 LF/CRLF 提示 | 最终工具输出 |

220 项是受影响范围，不是全仓库测试；原生 4 项与其中部分用例重复执行，不能相加称为 224 个不同用例。mypy 沿用仓库配置，保留了未标注函数体默认不检查的 informational notes，不声称额外执行全量 strict 检查。

## 4. V01 至 V08

| ID | 结果与证据 |
| --- | --- |
| V01 同 Job 三次执行 | `testThreeInvocationsInOneJobKeepTwoRecordsAndStartFinishRotateOnce`：5、2、3 后当前 3、上次 2；开始/完成只轮换一次；原实时观察仍是最新调用 |
| V02 运行中实际读图 | `testRunningLoopKeepsTwoMatchingInvocationsAndOriginalSavedImages` 用真实 foreach/spawn/gRPC，末端 gate 保持 RUNNING；Qt 联动还要求在 gate 启动前已显示第一张图，不靠任务结束取得图片 |
| V03 新 Job 与切换 | 实际两 Job UI/RPC 用例验证上一调用；`testRunTargetsHaveSeparateHistoryAndProjectSwitchReleasesBoth` 验证运行目标隔离/工程清理；同名跨 workflow、Runtime/session 绑定测试分别覆盖，不将模型层隔离宣称为现场并发验收 |
| V04 失败、取消、跳过 | 最后三次中第三次失败无本次有效图，但第二次可精确读取；真实协作取消、既有强停回归通过；Qt gate 显示“已取消”；无开始的 skip 不挤掉两次调用，保留原时间/任务来源 |
| V05 读者与淘汰 | `testRunningJobRetainsExactlyTwoInvocationsAndReaderSurvivesThird`：读取第一张时产生第二/第三次，第一张文件等实际 reader.close 后删除；剩两次；关闭会话也不能提前释放正在读取的文件 |
| V06 普通大图 | 实际 Runtime 的 2560×1440、3840×2160 均有比例正确预览；4K 原生 Qt 解码到 1920×1080；噪声 2K/4K 触发 PNG 超 4 MiB 后的继续缩小；原数组、保存尺寸与像素不变；下游原地修改不能改变冻结图 |
| V07 保存图与配置来源 | 实际 ImageSaver 在同一路径连续保存，上一调用仍返回对应注册副本的像素 22，本次 23；选保存节点默认图像页；原保存文件保留原始尺寸；编辑草稿节点名不覆盖已保存历史名称；Qt 夹具通过真实写配置接口提交 Designer 规范化差异后才启动 |
| V08 暂时断连/查看器 | 首次仅覆盖续约失败后的恢复，审查发现读图本身短断连的缺口。第 9 节已补修并验证查询/流读取失败和超时后的恢复，包括真实 Runtime + Qt 的已完成任务；原有会话过期、身份变化、关闭/重开大图的验证保留 |

暂时断连恢复的传输失败由受控客户端模拟；未拔线、切换现场网络或重启用户 Runtime。资源/删除失败的既有定点回归继续保留，但未扩大为多故障组合专项。

## 5. 原生窗口证据

真实入口加载当前 2.7 临时工程，后台本机 gRPC；测试并非只构造一个独立面板。新工程从 `WorkflowStore().toPayload()` 创建，经 `ProjectDocument` 校验、`saveProject()` 保存重读、实际 Runtime 编译和 Designer `loadProjectDirectory()` 打开。旧 `sampleProject` 只保留为明确的 2.1 兼容夹具，不当作新模板。

| 数据 | 小图 | 4K |
| --- | --- | --- |
| Job | `80260058-e2e4-4c86-9bba-b138ec294041` | `4693a75b-3df4-4bd7-98e7-5fb138efba08` |
| 第一张已显示调用 | `aa7e2768-1072-4f7a-b6b4-fe63321bd6aa` | `00dd7344-fda1-4f3c-ac78-ddc5f562ec19` |
| 最终上一调用 | `ca639e7b-8bf9-4cbc-973f-a74baa10bded` | `e8b38c31-e8eb-45ff-a899-72a255e1e554` |
| 原始/预览尺寸 | 16×12 / 16×12 | 3840×2160 / 1920×1080 |
| 取证时状态 | RUNNING | RUNNING |
| 当前/上一数值 | 3 / 2 | 3 / 2 |
| 主面板/大图 | 相同 QPixmap cacheKey | 相同 QPixmap cacheKey |

每组连续执行 30 次，最终切换到上一张与保存结果端口后的读图计数为 32；重复打开大图不增加读图次数。输入 index、输出 count、完整 nodeRunId、像素值和 viewer.identity 均有断言。原生窗口本次为 1360×850、DPR=1.0；未执行其它 DPI 或工控机分辨率矩阵。

- [4K 本次执行](node-execution-results-2026-10-22/screens/4k/live-current.png)
- [4K 上一次执行](node-execution-results-2026-10-22/screens/4k/live-previous.png)
- [4K 大图](node-execution-results-2026-10-22/screens/4k/live-viewer.png)
- [小图本次](node-execution-results-2026-10-22/screens/small/live-current.png)、[小图上次](node-execution-results-2026-10-22/screens/small/live-previous.png)、[小图大图](node-execution-results-2026-10-22/screens/small/live-viewer.png)

已抽查图像页与大图截图：右侧按钮、标签、图片、元数据与底部操作未互相覆盖。截图内的暗色矩形是受控纯色像素 22/23，不是无图占位；自动化按真实像素校验来源，未用真实相机图片冒充现场证据。

## 6. 开发中的失败与修复

以下是本轮实现期间已观察到的问题，最终通过不抹去曾经失败的事实：

| 问题 | 最小修复与复查 |
| --- | --- |
| 导航暂时清空节点选择后“上一执行”偏好丢失，已有两 Job viewer 用例失败 | 历史偏好与当前节点是否有两条记录分离，返回节点后仍选择对应上一调用；原生与 220 项回归均包含该用例 |
| 取消记录进入共享 presenter 时 `KeyError: 'CANCELLED'`；前一执行段最终为 1 failed、9 passed | presenter 增加 CANCELLED/INTERRUPTED 标签、无结果说明和原因文字，参数化测试并重跑真实 Qt 取消路径；本段修复后首组 12 passed |
| 读取过程中每个循环事件取消当前读图，普通连续循环可能一直无图 | 同位置自动刷新先交付一组一致结果；显式切换照常取消；小图/4K 原生测试均在循环结束前获得图像 |
| Windows 私有扩展路径与普通路径比较使保存结果图未命中 | 在已有受管 artifacts 范围内统一解析路径，仍保留 checksum；实际同路径覆盖的上次保存图通过 |
| 改为节点保留后原 Runtime 元数据额度未接回 | 恢复每任务/会话原额度检查；被读者持有的旧文件继续占用磁盘额度；退役对象只保留释放所需信息；定点额度测试通过 |
| 真实 Designer 载入后会规范化临时工程，启动落入正常配置比较对话框，两次受控测试等待 | 使用真实写配置 API 提交夹具差异，并断言已 COMMITTED 且无剩余差异；没有绕过产品的比较门槛，仅中断了本轮拥有的等待测试 |
| Ruff 检出夹具中的未使用 `result` 变量 | 移除无用赋值，最终 25 文件检查通过 |
| 保存图已有有效端口，但默认标签仍按普通对象输出选数值页 | 默认页同时考虑图像端口与保存结果图，真实 Qt 导航用例补充 imagePage 断言 |

## 7. 复跑入口

环境变量：`PYTHONPATH=src`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`。以下命令使用上文 Conda Python；完整文件集合由原始运行记录对应本节列出。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$designer=@(
  'test_run_result_history.py','test_node_run_inspection.py','test_node_result_panel.py',
  'test_run_inspector_ui.py','test_inspection_images.py','test_node_image_viewer.py',
  'test_runtime_client.py','test_workflow_runtime_controller.py'
) | ForEach-Object { "tests/designer/$_" }
$runtime=@(
  'test_inspection_preview.py','test_run_inspection_store.py','test_run_inspection_rpc.py',
  'test_live_node_results.py','test_preview_snapshot_store.py','test_preview_snapshot_origins.py',
  'test_legacy_snapshot_policy.py','test_worker_event_batch_barriers.py',
  'test_worker_event_batch_persistence.py','test_worker_capture_diagnostics.py',
  'test_job_supervisor_contracts.py','test_runner_io_summary.py',
  'test_image_batch_while.py','test_image_batch_boolean_while.py'
) | ForEach-Object { "tests/runtime/$_" }
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest -q @designer @runtime -o faulthandler_timeout=90

$env:QT_QPA_PLATFORM='windows'
$env:EMO_NODE_RESULTS_SCREEN_DIR='C:/Users/jsdfhasuh/my_scripts/emo_master/docs/testing/node-execution-results-2026-10-22/screens'
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest -q -s tests/designer/test_node_image_viewer.py -o faulthandler_timeout=90

& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe scripts/gen_proto.py --check
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m mypy --config-file mypy.ini src
```

## 8. 交付边界

本计划的正常软件路径已具备继续人工验收的条件，没有新增阻断问题。未执行全仓库测试、冻结包、CI、打包发布、现场工程、真实相机/PLC、性能/节拍专项或长期压力测试；历史性能/稳定性报告的失败不因此改判。

没有停止或重启用户原有 Designer、训练进程或任务。修改不会热加载到已运行 EXE；使用新功能需要后续以配套更新的 Designer 和 Runtime 启动，这不是本轮已执行的动作。

## 9. 审查修正与最终验证

本次仍基于 `d39169251c9bedc339c440a29a56df5eafba3be5` 加工作区修改，未创建提交。相对首次实现，仅修改两个生产模块和两个测试文件；未改 Runtime 执行引擎、调度、业务算子或工程格式。

### 9.1 审查发现与最小修正

| 问题 | 修正与直接验证 |
| --- | --- |
| 读图时短暂断连，随后续约正常，但同选择去重使图片一直无法恢复 | `inspection_images.py` 将查询 RPC、资产流的 UNAVAILABLE/DEADLINE_EXCEEDED 及本地显示超时接入已有续约恢复；不新增工作者或重试计时器。成功后正常续约不多读，相同选择刷新不形成紧密重试，切换选择取消旧恢复意图 |
| 任务终态轮询反复清掉读图错误提示，却不能实际触发重读 | `node_result_coordinator.py` 删除这段失效触发。已有真实两 Job Qt 用例增加一次读图短断连分支，确认已完成任务中错误提示保留、成功续约后恢复同一图片、恢复前后 Job 数量都是 1 |
| Qt 等待条件成立后额外处理事件，断言有时读到下一次执行 | `test_node_image_viewer.py` 改为处理事件后取样并立即返回；增加等待 helper 的直接反例，在同一次 GUI 取样中验证像素、数值、输入及完整执行身份 |
| 原生重复执行时，图片已提交但尚未发生实际绘制就检查绘制时间 | 首帧与末帧都明确等待 `firstImagePaintNs > 0`。首帧仍必须在循环末端 gate 开始前完成真实绘制；没有删掉绘制、一致性或 RUNNING 断言 |

非传输错误不作为重连重试条件：身份失效保留原清理通知，取消和解码错误不触发续约重读。所有恢复只读当前图片，不执行 Start/Stop，不补执行业务任务。

新增 9 项回归：读图恢复 3 项、非传输错误 3 项、切换选择 1 项、等待 helper 1 项、真实 Qt 读图短断连分支 1 项。已有成功去重、单工作者、迟到隔离、两次历史、保存图与原图检查继续执行。

### 9.2 失败证据与检查结果

审查复跑曾得到受影响回归 `219 passed, 1 failed`、原生 Qt `3 passed, 1 failed`，失败点是小图循环提取 `count` 的时序；后续单跑通过不撤销该失败记录。修正前新增定点回归为 **5 failed、3 passed**，见 [review-repair-before-fix.txt](node-execution-results-2026-10-22/raw/review-repair-before-fix.txt)。

第一阶段修正后整组曾为 229 passed，原生第三轮仍在 4K 的实际绘制时间断言失败，见 [review-repair-native-qt-3.txt](node-execution-results-2026-10-22/raw/review-repair-native-qt-3.txt)。该失败促使测试明确等待绘制；不将这些中间结果冒充最终完成。

| 最终检查 | 结果 | 证据 |
| --- | --- | --- |
| 受影响回归 | **229 passed，0 failed，0 skipped**，84.10 秒；offscreen + 实际 spawn / 本机 gRPC | [review-repair-regression-final.txt](node-execution-results-2026-10-22/raw/review-repair-regression-final.txt) |
| 原生 Windows Qt 第 1 轮 | **5 passed**，26.50 秒，DPR 1.0 | [review-repair-native-final-1.txt](node-execution-results-2026-10-22/raw/review-repair-native-final-1.txt) |
| 原生 Windows Qt 第 2 轮 | **5 passed**，25.97 秒 | [review-repair-native-final-2.txt](node-execution-results-2026-10-22/raw/review-repair-native-final-2.txt) |
| 原生 Windows Qt 第 3 轮 | **5 passed**，26.34 秒 | [review-repair-native-final-3.txt](node-execution-results-2026-10-22/raw/review-repair-native-final-3.txt) |
| Ruff | 原功能范围 25 个手写 Python 源码/测试文件通过 | [review-repair-ruff-verified.txt](node-execution-results-2026-10-22/raw/review-repair-ruff-verified.txt) |
| mypy | 507 个源码文件无错误，沿用原配置及 informational notes | [review-repair-mypy.txt](node-execution-results-2026-10-22/raw/review-repair-mypy.txt) |
| Proto | `scripts/gen_proto.py --check` 退出码 0，无桩漂移 | [review-repair-proto-check.txt](node-execution-results-2026-10-22/raw/review-repair-proto-check.txt) |

原生每轮明确选择 5 个窗口用例，`1 deselected` 是不涉及 Qt 窗口的等待 helper；该 helper 已在 229 项整组中执行通过。三轮是同一批用例重复验证，不能加总成 15 个不同用例，也不能与 229 项相加。

最终第一轮 4K Job 为 `0d6a8278-c85b-4ae9-a2fe-9326ca9f7c12`，小图 Job 为 `c5c614f4-8855-4286-9b4a-46116bf584f4`。六张新截图位于 `screens/review-repair-final/`，包括 [4K 上一次执行](node-execution-results-2026-10-22/screens/review-repair-final/4k/live-previous.png) 和 [4K 大图](node-execution-results-2026-10-22/screens/review-repair-final/4k/live-viewer.png)。主面板与大图仍共享显示像素，4K 原图 3840×2160、预览 1920×1080，当前/上次数值为 3/2。

复跑受影响回归沿用第 7 节文件集。原生窗口命令使用 `-k 'not WaitReturnsTheSample'`，连续运行三轮；最终截图输出目录改为 `docs/testing/node-execution-results-2026-10-22/screens/review-repair-final`，不覆盖旧截图。

断连由受控客户端模拟，恢复后的实际读取仍经过本机 gRPC 和真实 Runtime；没有拔线或操作现场网络。两 Job 用例沿用明确标记的旧格式兼容夹具，新增循环工程仍走既有 2.7 生成入口。未扩展为打包、现场设备、长期压力或性能节拍验收。

## 10. YOLO 默认叠加图与缺图原因修正

本轮源码基线仍为 `d39169251c9bedc339c440a29a56df5eafba3be5` 加工作区修改，未提交或发布。
生产修改限于 YOLO 的 `operator.py`、`manifest.json`、Designer 的
`node_run_inspection.py` 和 `node_result_coordinator.py`，没有改执行引擎、RPC 或工程格式。

- `drawOverlay` 的插件声明、参数校验默认值和实际执行默认值统一为 `true`。成功执行后默认输出
  `overlay`，零目标也输出无框图像；不改输入像素、检测集合或裁剪像素。
- 显式 `drawOverlay=false` 保留关闭语义。未启用的 `overlay` / `croppedImage` 在本次
  完成事件的 `diagnostics.imageOutputMessages` 中分别给出原因。结果面板直接展示该次执行的
  说明，不发起无效读图，也不读取后来修改的草稿参数来解释历史。
- 已有实际输出的端口优先于子流程汇总诊断；失败、取消、开始和旧事件仍走原有状态逻辑。
  新字段沿用既有摘要额度，没有新增状态服务、补偿或重放。
- 没有改写用户工程、示例中的显式关闭参数或在线 Runtime 配置，没有重启用户程序。
  配套更新 Designer / Runtime 并重新启动后生效。现有关闭节点需显式勾选并写入 Runtime，
  重新执行才会有新图片；旧执行不补生成图片，也不根据当前参数补猜关闭原因。

新增测试先行得到 **4 failed、2 passed**，失败分别证明缺少关闭原因和默认值仍为 false。
首轮三文件验证为 **52 passed**，真实小型 ONNX 推理及快照链路为 **4 passed**；首轮扩展回归
为 **305 passed**。随后增加“已有输出不被汇总诊断遮住”的反例并重跑最终完整受影响集合：

| 最终检查 | 结果 | 证据 |
| --- | --- | --- |
| 受影响回归 | **306 passed，0 failed，0 skipped**，101.59 秒 | [yolo-overlay-regression-final.txt](node-execution-results-2026-10-22/raw/yolo-overlay-regression-final.txt) |
| 原生 Windows Qt 定点控件/历史选择检查 | **5 passed，21 deselected**，1.72 秒；与上组重叠，不相加 | [yolo-overlay-native-qt.txt](node-execution-results-2026-10-22/raw/yolo-overlay-native-qt.txt) |
| Ruff | 本轮 8 个 Python 源码/测试文件通过 | `python -m ruff check`，本轮命令输出 |
| mypy | 本轮 3 个 Python 生产模块无错误 | `python -m mypy --config-file mypy.ini`，保留原 informational notes |
| diff 检查 | `git diff --check` 退出码 0 | 仅已有 CRLF 转换提示 |

306 项复跑文件集为第 7 节的全部文件，加上
`tests/plugins/test_yolo_inference_operator.py`、`tests/plugins/test_yolo_onnx_backend.py`、
`tests/designer/test_yolo_class_filter_form.py`、`tests/runtime/test_yolo_inspection_outputs.py`、
`tests/runtime/test_real_onnx_result_pipeline.py`、`tests/runtime/test_builtin_vision_operator_workflows.py`。
使用同一 Conda Python、`QT_QPA_PLATFORM=offscreen` 和 `-o faulthandler_timeout=90`。
原生定点命令选择 `test_yolo_class_filter_form.py` 与 `test_node_result_panel.py`，
`QT_QPA_PLATFORM=windows`，`-k 'YoloOverlayDefaults or DisabledImageMessage'`。

新增 ONNX 链路夹具通过 `WorkflowStore` 生成完整 2.7 无设备工程，完成格式校验、保存重读、
编译、真实 CPU 推理、图片快照解码与执行身份核对。该小模型验证管线，不代表现场模型精度。
回归含既有真实 spawn / 本机 gRPC 测试；原生定点仅验证 Qt 控件及状态逻辑，不称人工 UI
或冻结包验收。没有执行全仓库测试、打包、部署或真实相机/PLC 验收。

## 11. YOLO 参数显示调整

基线仍为 `d39169251c9bedc339c440a29a56df5eafba3be5` 加现有工作区修改；本轮没有提交、
发布、修改现场工程或停止用户程序。只修改通用表单、参数来源布局、显示注解合并和 YOLO
参数显示注解；既有工作区中的导航、图标及其它修改保持原样。

- 类别数组改为单行编号输入 `0, 1, 3`，支持中文逗号和粘贴 JSON 数组。空值显示「全部类别」，
  保存仍为 `[]`；非法编号仍报错并保留待编辑内容，不能静默改为检测全部类别。
- 普通布尔控件增加「已开启 / 已关闭」，带 `xUnit` 的整数控件显示单位，例如 `1280 像素`、
  `300 个`。数值、布尔类型和默认绘图行为不变。
- 固定值与变量来源改为同行，来源选项没有可用变量时隐藏多余选择器；已有失效引用仍能查看
  和移除。来源选择器也沿用表单滚轮保护，固定值仍保留，切回固定来源可继续编辑。
- 旧 schema 沿既有显示注解合并入口获得紧凑类别控件，不改写保存的参数、默认值或 schema；
  单纯打开窗口不会制造未应用修改。类别名由具体模型决定，本轮不新增模型元数据 RPC，
  不把自定义模型的编号擅自对应为 COCO 名称。

| 检查 | 结果与边界 |
| --- | --- |
| 首轮定点 | 74 passed，包含 YOLO 控件、参数来源、标题、通用表单和算子；新增窗口组另为 4 passed |
| 扩展回归 | **243 passed，1 failed**，16.26 秒；23 个相关文件，见 [regression.txt](yolo-parameter-presentation-2026-10-22/regression.txt) |
| 失败基线复现 | `testDefinitionsRenameUndoDeleteProtectionAndPackages` 在独立 HEAD archive 的源码和测试上同样失败：测试期待工作流包 1.1，实际为 1.4；见 [baseline-head-package-version.txt](yolo-parameter-presentation-2026-10-22/baseline-head-package-version.txt)。未放宽断言或修改无关打包代码 |
| 原生 Windows Qt，100% | **4 passed**，2.00 秒；实际 OperatorWorkspaceWindow 的 460 / 620 逻辑像素宽度、鼠标切换、应用、变量绑定与控件布局，见 [native-100.txt](yolo-parameter-presentation-2026-10-22/native-100.txt) |
| 原生 Windows Qt，150% | **4 passed**，2.09 秒；同一组重复缩放验证，不相加为不同用例，见 [native-150.txt](yolo-parameter-presentation-2026-10-22/native-150.txt) |
| 静态检查 | Ruff 本轮 6 个 Python 文件通过；mypy 本轮 4 个 Python 生产模块无错误；`git diff --check` 通过 |

视觉检查采用原生 Qt 生成的窗口截图，而非 mockup。100% 的
[620 宽度](yolo-parameter-presentation-2026-10-22/screens/native/yolo-parameters-620.png) 和
[460 宽度](yolo-parameter-presentation-2026-10-22/screens/native/yolo-parameters-460.png)，以及
[150% 的 620 宽度](yolo-parameter-presentation-2026-10-22/screens/native-150/yolo-parameters-620.png)
已检查文字、输入框、来源选项和底部按钮无重叠。截图为临时测试上下文，不是用户现场工程；
模型路径是展示用参数，没有加载现场模型。offscreen 截图在本机缺少文本渲染，仅用于逻辑测试，
不作为视觉通过依据。

复跑新增窗口组：使用既有 Conda Python，设置 `QT_QPA_PLATFORM=windows`、
`QT_SCALE_FACTOR=1` 或 `1.5`，执行 `python -m pytest -q tests/designer/test_yolo_parameter_presentation.py`。
设置 `EMO_YOLO_FORM_SCREEN_DIR` 可保留截图。扩展回归包括 `test_param_form*`、
`test_parameter_*`、`test_yolo_class_filter_form`、新增窗口组、节点参数对话框、相机参数表单、
全局变量 UI / 生命周期 / review fixes、算子编辑器 manager / builtin UI、schema_utils、
small_window_layout_repair、builtin_parameter_titles，以及两个 YOLO 插件测试文件和
`test_yolo_inspection_outputs`。没有声称全仓库全绿、冻结包或真实设备验收；运行中的程序不会热加载此次源码。

## 12. 参数窗口显示原始算子名称

基线为 `d39169251c9bedc339c440a29a56df5eafba3be5` 加既有工作区修改。
本轮局部修改共享算子编辑器与设备绑定窗口的名称展示；不改工程格式、保存数据、参数或运行逻辑。

- 从 Runtime 算子目录的 `displayName` 获取原始名称，不从用户自定义节点名或节点 ID 猜测类型。
- 共享编辑器标题显示「自定义名 · 原始算子名 · 节点 ID」，同名时不重复；顶部显示原始名称
  和算子 ID，可选中、换行。设备绑定窗口也从同一目录显示原始算子信息。
- 重命名、撤销、重做、重复打开及专用编辑器回退保留原始名称；重复打开不覆盖未应用参数。
- 无参数表单的 `No schema fields` 改为「无可配置参数」，不制造参数或改变已有 schema。
- 新增测试以完整 2.7 临时工程完成校验、保存重读、编译和实际 Designer 打开，再检查参数窗口；
  确认仅打开窗口不改变工程、磁盘内容或 dirty 状态。测试工程用于展示，不代表用户截图中的
  `person_bbox` 已被确认属于某个类型。

| 检查 | 结果与证据 |
| --- | --- |
| 首轮定点 | **24 passed**：新增身份测试、manager、重命名、实际内置专用编辑器 |
| 扩展回归 | **170 passed，3 failed**，28.08 秒；[原始输出](operator-identity-2026-10-22/regression.txt) |
| 失败基线复现 | 三项均在固定 HEAD 的独立源码和测试中同样失败；[复现输出](operator-identity-2026-10-22/baseline-head-with-fixtures.txt) |
| 原生 Qt，100% | **8 passed**，3.19 秒；[输出](operator-identity-2026-10-22/native-100.txt) |
| 原生 Qt，150% | **8 passed**，3.81 秒；同一组重复缩放检查，不叠加；[输出](operator-identity-2026-10-22/native-150.txt) |
| 静态检查 | Ruff 本轮 7 个 Python 文件通过；mypy 本轮 4 个生产模块通过；`git diff --check` 通过 |

三项既有失败为 `test_device_ui_gaps.py::testOpeningAndRefreshingReadsKnownStatesWithoutLosingEdits`
的连接状态断言、同文件 `testNativeDeviceDialogProductionViewingWithRealRuntime` 对 `QAction.click()`
的调用，以及 `test_operator_icon_integration.py::testWorkflowProjectAndRuntimeSessionRebindConsumers`
的图标重连超时。本轮不修无关问题、不弱化断言。首次基线执行缺少示例设备夹具产生一个 setup
error，保留在 [首次输出](operator-identity-2026-10-22/baseline-head.txt)；补齐同一 HEAD 导出的
`examples/device_plugin` 后复跑，才确认三项均为同样失败。相关源码与测试的 Git blob 已核对
与固定 HEAD 一致，Python 导入路径也确认来自独立副本。

已目视检查原生截图：[100% / 460 宽](operator-identity-2026-10-22/screens/native-100/operator-identity-460.png)
与 [150% / 620 宽](operator-identity-2026-10-22/screens/native-150/operator-identity-620.png)，
原始名称、ID、空表单提示和底部按钮无重叠。截图来自临时测试工程，不是用户现场工程。

复跑新增组：使用 Conda `emo_master` Python，设置 `QT_QPA_PLATFORM=windows` 和
`QT_SCALE_FACTOR=1` 或 `1.5`，执行 `python -m pytest -q tests/designer/test_operator_editor_identity.py`；
设置 `EMO_OPERATOR_IDENTITY_SCREEN_DIR` 可保存截图。没有打包、发布、重启用户程序或连接真实设备。
