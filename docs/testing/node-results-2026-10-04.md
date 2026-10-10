# 流程设计节点结果与两次运行图片：实施和验证

本轮实现了「明确运行 → 点击节点 → 节点结果 → 本次/上一次 → 大图」的三批代码。
它使用真实 Runner、原事件订阅和 typed loopback gRPC，没有用示例图片替代检测结果。
专项与原生窗口的结果、完整 CI 和原性能/稳定性问题分别记录；本报告不批准现场发布。

## 代码与环境

- 工作分支：`agent/runtime-workflow-architecture-v1`。
- 起始 HEAD：`dcda2491dd0c2123b23e45cb1f7a068bdd5bd836`，工作区已有未提交页面/UI改动。
- A：`bbe7d590fc9a7c1da9e5458d3a2441197cb4e1da`，统一面板与两次接受任务的状态模型。
- B：`91c657c6d6df64f0712b2106d143800dc5691647`，检查会话、真实图片接管、可选兼容接口及读图工作者。
- 正常合并远端非重叠 Windows 包验证改动：`cd36f3bf2b244ba736124c1fcc63dbd21da63dbf`，包含远端 `590e05d41f691f4debb5d9da4473f1aaf7b33e99`。
- C 是包含本报告的提交：大图联动、退出顺序、冻结工程引用、严格图片计账、独立滚动边界及最终证据。
- C 测试使用 `cd36f3b` 加工作区任务改动；原始 JSON 保存完整 HEAD、dirty、命令和 `src/tests/scripts/proto/examples` 每文件 SHA256。
- 最终只含任务增量的独立代码候选树：`29196707609593ee251147719c47e496e8f77d5c`。其源码来自外部 index 和 git archive，未依赖原未提交页面改动；最终提交另包含随后整理的文档和证据，代码摘要保持一致。
- 解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10.21，x64。
- OS 实际为 Windows 11 专业工作站版，10.0.26200、64位；Python 平台字段为 `Windows-10-10.0.26200-SP0`，Qt 原生平台为 `windows`。
- PySide2 5.15.2.1、grpcio 1.78.0、protobuf 6.33.6、NumPy 1.26.4、opencv-python 4.10.0.84。没有重装或升级环境。

任务前备份位于仓库外 `task_backups/emo-node-results-20261004-180945`。
MainWindow、开发指南和布局测试只暂存相对于原 dirty 内容的任务增量；原页面编辑、样式和工作区代码不纳入本轮提交。
`raw/user-change-preservation.json` 核对156个原dirty/未跟踪文件内容未变；三个重叠文件的原用户补丁
在纯任务候选上仍可完整应用，合并结果逐字匹配当前工作文件。另一个后来出现的
`docs/evidence/p4/a-first/evidence.json` 改动保留且不提交，未推定其来源。

## 实际入口和使用

保存项目并退出旧 Designer，再从仓库根目录执行：

```powershell
& "$env:USERPROFILE\.conda\envs\emo_master\python.exe" scripts/dev.py run-designer --local
```

明确点击运行后，在流程画布点选节点。右侧「节点结果」提供：

1. 图像输出：正式输出端口及已注册的保存结果图，尺寸、实际编码和资产时间；缺图保留原因。
2. 数值输出：端口、类型和真实值。`0`、`false`、空值保留；集合显示数量，复杂数据使用有界摘要。
3. 执行信息：Job、workflowRunId、nodeRunId、循环索引、算子上报耗时、诊断、错误和跳过原因。
4. 输入数据：执行时已有真实摘要，不回放完整输入图片，也不按当前草稿推断当时输入。

「本次/上一次」按两次明确接受的任务切换。画布仍显示当前任务，草稿配置可继续编辑。
有图像端口时默认图像标签；无图像端口时隐藏该标签，默认数值或执行信息。
点击「放大查看」或双击有效图片打开一个非模态 Qt 大图窗口；缩放、平移、100%、适配、全屏和 Esc 均可使用。
大图跟随任务、节点和端口，历史选择不会在大图变成后台最新任务。
查看、切标签、开关大图均不创建 Job；大图/检查租约没有停止 Runtime 的权限。

开发指南已同步；完整规则见 [节点结果契约](../runtime-node-results-contract.md)。

## 可复现原生演示

```powershell
$python = "$env:USERPROFILE\.conda\envs\emo_master\python.exe"
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONUTF8 = '1'
$env:QT_SCALE_FACTOR = '1'
& $python docs/testing/node-results-2026-10-04/capture.py `
  --output "manual_test_workspace/node-results-$(Get-Date -Format yyyyMMdd-HHmmss)" `
  --width 1600 --height 900 --detailed
```

该脚本实际打开 MainWindow，使用真实 spawn Runner 与本机 gRPC。
临时项目、数据库、输出和日志均在仓库外 TemporaryDirectory，不接设备或现场数据库。
A 为 360×240 的两个白块，B 增加第三个白块；实际计数依次为 2、3。
第三次明确运行注入缺图失败，检查 A 淘汰、返回本次、失败原因及 B 仍可查看。
逐节点核对输入图、Blob overlay、计数、比较输入和保存结果图，检查图像像素及完整执行身份。
只创建三个明确 Job；切换工程和关闭 Designer 后检查会话/图片资源为零，外部 Runtime 仍存活，最后才由测试所有者关闭。
输出目录必须不存在，不能覆盖旧证据。

## 验证结果

| 检查 | 结果 | 原始证据 |
| --- | --- | --- |
| A 专项/相关回归 | 111 PASS；该批只证明面板与摘要 | `raw/batch-a-*.txt` |
| B 真实 spawn/gRPC 与回归 | 121 PASS / 24.20 s | `raw/batch-b-*.txt` |
| 初次 C 扩展回归 | 4 FAIL、211 PASS；失败均保留 | `raw/affected-regression-first.txt` |
| 任务前 HEAD + 原 dirty 的独立副本 | 1 FAIL、37 PASS；复现窄工具栏失败 | `raw/before-task-layout-observer.txt` |
| 第一轮完整 CI | proto/Ruff/mypy PASS；3 FAIL、2730 PASS、8 SKIP、28 subtests PASS | `raw/full-ci.txt` |
| 最后 16 位图片补丁前完整 CI | 1 FAIL、2733 PASS、8 SKIP；sourceChanged=false，但摘要早于该补丁 | `raw/full-ci-final-second.txt` |
| 16 位解码后扩展回归 | 1 FAIL、216 PASS / 57.54 s | `raw/affected-regression-final.txt` |
| 窄侧栏文字/标签修复专项，200% Qt 缩放 | 19 PASS / 4.98 s | `raw/wrapped-header-and-tabs-final.txt` |
| 独立候选发现滚动依赖 | 2 新增 FAIL、217 PASS；不能提交此候选作为最终版 | `raw/candidate-c-final-regression.txt` |
| 面板自己持有滚动边界后的独立候选 | 219 PASS / 68.83 s；sourceChanged=false | `raw/candidate-c-release-regression.txt` |
| 工作区面板/大图/图片/严格布局专项 | 39 PASS、1 基线 FAIL | `raw/panel-owned-scroll-final.txt` |
| 独立滚动引出的短画布回归 | 3 FAIL、2734 PASS、8 SKIP、28 subtests PASS；其中2项为新增工具区回归，sourceChanged=false | `raw/final-stable-ci.txt/json/xml` |
| 180px滚动边界及摘要超额修复专项 | 48 PASS / 8.14 s，sourceChanged=false | `raw/summary-quota-and-short-canvas-final.txt` |
| 最后文案修正前完整 CI | proto/Ruff/mypy PASS；1基线FAIL、2737 PASS、8 SKIP、28 subtests PASS / 700.50 s，sourceChanged=false | `raw/final-task-ci.txt/json/xml` |
| 扩展候选组合装载 | 220 PASS、20 fixture ERROR，sourceChanged=false；这轮不能记为通过 | `raw/candidate-c-final2-regression.txt` |
| 同一候选工具区单独验证 | 20 PASS / 4.27 s，sourceChanged=false | `raw/candidate-toolbox-isolated.txt` |
| 最终独立代码候选，按测试目录组合 | 240 PASS / 70.67 s，sourceChanged=false | `raw/candidate-reviewed-regression.txt/json` |
| 最终独立候选原生窗口 | 13次PASS；三个尺寸×四倍率，另实际全屏1920×1080，全部sourceChanged=false | `raw/reviewed-native-*.txt/json`、`reviewed-native-*/measurements.json` |
| 最终冻结代码完整 CI | proto/Ruff/mypy PASS；1基线FAIL、2737 PASS、8 SKIP、28 subtests PASS / 708.42 s，sourceChanged=false | `raw/reviewed-code-ci.txt/json/xml` |

完整专项命令保存在各 JSON 的 `command` 数组中；候选记录另有 `candidateTree`、`candidateDirectory` 和源码摘要。
布局回归从已经隐藏的旧控件迁移到可见结果面板，仍保留 1000×499、最小窗口边界、80行内容、字号不缩小和最后一行可达的断言。
新增包裹身份文字、QSplitter、滚动边界、鼠标点击窄标签箭头的验证；没有删除窄工具栏断言。
最终候选组合把工具区测试与 Designer 测试放在一起收集；没有修改 fixture、排除测试或调整断言，240项全部执行。
早先追加在其它目录之后的工具区测试遇到 fixture 装载错误，原始20个ERROR继续保留。

8个SKIP分别为：6个Windows符号链接权限不足测试、1个未启用的真实Huaray相机smoke、1个只适用于非Windows的平台拒绝测试。
这些不计作PASS；没有为了完整CI关闭任何正式断言。
`evidence-index.json` 索引全部122轮原始检查、最终13次原生矩阵、全部图片绘制样本和阶段资源；
未绘制的样本保持0/缺失值，原失败、sourceChanged=true及fixture错误不从索引删除。

复现专项可执行：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONUTF8 = '1'
$env:HUARAY_CAMERA_SMOKE = '0'
$env:PYTEST_ADDOPTS = '--ignore=manual_test_workspace'
Remove-Item Env:QT_SCALE_FACTOR -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
& $python -m pytest tests/core/test_run_inspection.py tests/designer/test_run_result_history.py `
  tests/designer/test_node_result_panel.py tests/designer/test_node_image_viewer.py `
  tests/designer/test_inspection_images.py tests/designer/test_runtime_client.py `
  tests/runtime/test_run_inspection_store.py tests/runtime/test_run_inspection_rpc.py -q
& $python scripts/ci_check.py
```

仅忽略手工冻结工作目录，没有排除正式测试。故障注入外层 watchdog 为180/240秒，完整 CI 为1200秒；未放宽5秒单选择读图/解码期限或原P2/P3目标。

## 原始失败与归因

- `testNarrowToolbarKeepsRunActionAndHasOverflow` 在任务前副本已失败；独立纯任务候选通过该断言，当前工作区仍保留原 UI 改动下的失败。
- 最初新协调器的 `event()` 与 QObject 原生 event 冲突、成功读流后误取消解码、先关闭通道导致检查租约无法 close、工程切换后按新 loaded project 校验旧会话，均已修复并复测。原始失败未删除。
- 200% 原生截图揭示元数据/按钮重叠。图片页加入滚动；换行信息按真实宽度预留高度。纯候选随后揭示依赖未提交外层滚动的问题，最终由节点结果模块自己的 `nodeResultScroll` 收口。
- 独立滚动降低窗口最小高度后，`testShortCanvasCanScrollToEveryCategoryAndCard` 与 `testAncestorScrollingKeepsToolboxWithinVisibleCanvas` 在完整CI中失败。这两项是本轮新增回归，已通过结果滚动表面的180px最小边界修复，专项与最终纯候选保留原断言并通过；没有归为旧崩溃。
- 新的16位PNG预算检查在 Qt reader.read 前按原生64 bpp拒绝超限；解码后、转换前核对真实 sizeInBytes，测试真实像素和转换峰值。
- `affected-regression-first` 在页面预览关闭时出现 `QWidgetItem has no attribute screenChanged`。本轮未改该renderer路径，任务前专项没有复现，后续完整回归也未复现；归因仍未确认，不能称旧问题、已修复或稳定性通过。
- `final-layout-ci` 出现一次 grpc connectivity 线程在 channel 关闭后调用的警告。归因未确认，原始栈保留。
- `final-code-ci` 与 `final-layout-ci` 执行期间补了回归/滚动修复，sourceChanged=true；即使单轮测试完成，也不能作为最终冻结源码的完整CI证据。
- 原 P0/P2/P3 性能 FAIL、A18、Qt 组合崩溃与资源稳态缺口保留。低速本地图不是原1080p/5Hz验收；本轮不改变那些结论。

## 真实尺寸、DPI与绘制

最终证据目录为 `node-results-2026-10-04/reviewed-native-*`，均来自最终纯任务代码候选。
此前 `final-scroll-*` 是180px边界和摘要超额提示之前的工作区版本，连同其余轮次全部作为原始证据保留。
每轮记录请求尺寸、实际客户区、DPR、屏幕可用区、面板几何、模型提交和 paintEvent 时钟。
最终矩阵包含三个请求尺寸和100/125/150/200% Qt缩放，共12项，另有一次实际全屏1920×1080。

| 请求逻辑尺寸 | 100%实际 | 125%实际 | 150%实际 | 200%实际 |
| --- | --- | --- | --- | --- |
| 1280×720 | 1280×720 | 1280×720 | 1256×640 | 936×468 |
| 1600×900 | 1600×900 | 1512×778 | 1256×640 | 936×468 |
| 1920×1080 | 1896×984，另全屏1920×1080 | 1512×778 | 1256×640 | 936×468 |

本机桌面1920×1080、可用区1920×1032；请求超过可用逻辑空间时 Windows 限制客户区，不能把请求尺寸写成已测尺寸。
Qt_SCALE_FACTOR 是受控 Qt 倍率，不是另一台设备或物理OS多DPI验收。
短窗口通过原生滚动到达元数据、动作和四个标签，不缩小字体或修改结果数据。
前面矩阵的功能脚本可返回PASS但人工视觉检查失败，不能追认为视觉PASS；后面的脚本新增相应几何和鼠标可达断言。

图片提交、主面板首图绘制、大图首图绘制分别记录 monotonic_ns。QWidget.grab也会绘制，
这些值证明Qt实际绘制路径，不能当作屏幕曝光、DWM呈现或工业机可见延迟的测量。
资产时间来自实际元数据的壁钟字段，不与monotonic直接相减。缺失或无效测量保留分母，不挑最好一轮。
窗口构建、加载、明确布局的时长保存在各 `measurements.json`；功能矩阵期间可能与CI并行，不能替代隔离性能基准。
本机Python 3.10的monotonic采用GetTickCount64，分辨率15.625ms；0ms不表示没有布局或绘制成本。
最终13次窗口构建为328—453ms，明确布局记录为0—16ms，仅是该低速、五节点样例的功能测量。

可直接查看最终截图：

- [上一任务A的图像与任务身份](node-results-2026-10-04/reviewed-native-1600-1/03-previous-A-image.png)
- [跟随A的独立大图](node-results-2026-10-04/reviewed-native-1600-1/04-previous-A-viewer.png)
- [本次B的实际count=3](node-results-2026-10-04/reviewed-native-fullscreen/06-current-B-count.png)
- [执行信息](node-results-2026-10-04/reviewed-native-1600-1/07-current-B-execution.png)
- [真实输入摘要](node-results-2026-10-04/reviewed-native-1600-1/08-current-B-inputs.png)
- [200%倍率下滚动到图片](node-results-2026-10-04/reviewed-native-1280-2/02b-current-B-image-scrolled.png)

## 固定额度与样例资源

| 池 | 上限 / 收口 |
| --- | --- |
| 定义和节点摘要 | 每任务64条、512 KiB，两任务合计1 MiB；冻结名称最多2048 UTF-8字节，仍计入原额度 |
| 编码图片 | 每任务64 MiB、会话128 MiB，原工程512 MiB/全局2 GiB共同计账；删除失败保持计账 |
| 检查会话 | Runtime最多两个；30秒租约、每10秒续约；失效不StopJob |
| 检查资产读者 | 最多两个；实际iterator退出才释放，被淘汰但仍被读取的资产保持所有权 |
| 客户端工作 | 一个工作者、最新一个待处理选择、一个交付槽；迟到结果按项目/选择代际拒绝 |
| 编码读取/暂存 | 本地单次读取4 MiB；精确大小拼接，读取或Qt编码副本暂存最多8 MiB |
| 当前解码 | 原始与最终bitmap分别核对8 MiB；包括16位彩色PNG，超限在分配前拒绝 |
| 转换临时 | 编码缓冲释放后才转换；原始bitmap最多8 MiB |
| Qt显示 | 合计24 MiB；QImage实际bytes + QPixmap宽×高×4，两个显示位置共享cacheKey去重，缩放使用transform |

360×240样例的实测可见图片为QImage 345600 bytes，Qt图片合计691200 bytes。
最终13次两任务摘要为15200—15202 bytes、编码图片11835 bytes；当前解码峰值345600 bytes、所有阶段转换/编码临时峰值86400 bytes。
实际值及所有阶段走势以最终measurements为准。
切换工程、关闭Designer后摘要、编码图片、检查会话、reader、retiredJobs、pendingFiles均为零，清理在测试临时目录最终移除之前核对。
两处显示同一个cacheKey；打开、重复打开、关闭再打开大图不增加读图次数。
网络不可达时close不能保证即时物理删除，30秒租约负责最终清理；实际工作退出前不归还额度。
这里没有证明进程RSS、codec内部内存、传输库缓存、原页面所有缓存或平台总资源长期稳态通过。

## 剩余限制和阶段结论

| 本轮验收范围 | 结论 |
| --- | --- |
| 统一四标签、真实摘要、任务选择与冻结身份 | PASS |
| A/B不同图片与数值、最后调用、失败/取消/强杀保留、淘汰和租约清理 | PASS（真实spawn/typed loopback gRPC专项） |
| 大图跟随、缩放/平移/全屏、共享缓存与关闭不增读图、不停外部Runtime | PASS |
| 有界摘要、编码、解码、Qt图片和转换池专项 | PASS；不等同于整平台RSS或长期稳态 |
| 13次原生Qt功能矩阵、可达性及代表截图视觉核对 | PASS；仅本机实际尺寸/Qt倍率 |
| 当前dirty工作区完整CI | FAIL：1个任务前已复现的窄工具栏断言；8个SKIP |
| 原1080p/5Hz性能、A18/Qt组合稳定性与平台资源稳态 | 原失败/缺口保留；没有获得新的通过证据 |
| 跨机、物理多DPI、冻结EXE、工控机与现场设备验收 | NOT_RUN |

- 历史通过当前画布的稳定nodeId选择；删除的历史节点还没有独立历史节点树。
- 循环/重复调用只查看任务内最后一次执行；不提供逐件或逐调用回放。
- 保存结果图选项只保留该Job最后一份有效artifact对应的节点，目前PNG/JPEG/BMP；没有长期历史档案。
- 单次编码读取4 MiB和当前解码8 MiB超出时明确拒绝本地显示，不代表服务端编码资产不能保留。
- 原1080p/5Hz、检测P95回退≤5%、结果年龄≤500ms等原验收仍未通过；本轮不重新标PASS。
- Qt间歇组合崩溃、上述未定位组合异常、平台资源稳态继续阻塞稳定性/现场发布。
- 跨机器、多物理DPI设备、冻结EXE、工业机性能、真实相机/PLC/机器人和现场数据库均NOT_RUN。

本轮止于节点结果查看功能，不进入长期档案、完整输入回放或现场发布。
