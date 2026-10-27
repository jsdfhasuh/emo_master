# 2026-10-26 Designer 运行期间界面刷新修复

## 范围与依据

- 基准源码：`633de0e197f5553168a7786440199f68f3410bed`。本轮修改保留在工作区，未提交、打包或部署。
- 用户反馈：Designer 和页面预览窗口拖动卡顿，要求修复后自行体验。
- 修复前的只读采样中，系统 64 GB 内存约有 33 GB 可用；Designer 主线程在约 42 秒窗口内消耗约 82% 的单核 CPU。采样栈指向运行事件触发的重复结果面板文本重建，以及工具栏刷新触发的工程脏状态比较和草稿序列化。
- 当时的 Runtime 记录显示 RTSP 解码约 25 FPS、完整流程完成约 0.87–1 FPS，YOLO 算子自身平均约 37 ms。算子耗时包含其输入处理、推理和输出创建，不能据此认定完整流程已达到 25 FPS。本轮修复聚焦已定位的 UI 开销，没有重新验收真实相机吞吐量。

## 改动

1. 普通运行事件先逐条更新模型、历史和日志，再通过单个 100 ms 定时器合并面板及运行工具栏刷新；新事件不会推迟已安排的刷新。保留非原生 Qt 调用路径的同步行为。
2. 节点/流程失败、任务完成/失败/中止及生命周期状态仍立即刷新。切换任务使用当前所选状态，关闭窗口取消待执行刷新。
3. 运行状态刷新不再触发工程标题脏状态检查、页面草稿快照及撤销说明比较；真正的编辑和撤销仍更新标题。
4. 已接受任务使用其已有节点定义，避免刷新结果时重复构建草稿；统一结果视图取消重复刷新，未变化的文本不重建文档。

涉及 6 个 Designer 源码文件、1 个新增测试文件和 1 个现有测试文件；未修改 Runtime 推理、采集配置或现场工程。

## 验证结果

环境为 Windows、`emo_master` Conda Python/PySide2 环境。各测试集合有重叠，不能直接相加为去重后的总数。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 初轮相关 offscreen 回归 | 94 passed，4 skipped，50.25 秒 | 控制器、结果面板、检查器、失败反馈、页面工作区及并行运行 |
| 扩展 offscreen 回归 | 597 passed，14 failed，4 skipped，486.27 秒 | Runtime/run 相关 Designer 测试、全部 `tests/ui/page_designer` 及核心编辑会话测试；不是全绿结果 |
| 调整测试后的定点 offscreen 复验 | 22 passed，11.83 秒 | `test_runtime_event_refresh.py`、`test_floating_run_panel.py` |
| 最终原生 Windows Qt 回归 | 65 passed，21.53 秒，无跳过 | 上述两个文件及 `test_runtime_qt_ownership.py`、`test_run_failure_feedback.py` |
| 修改前源码对照 | 12 failed，6 passed，62.05 秒 | 下述 12 个剩余失败均在固定基准 SHA 上复现 |
| Ruff | 通过 | 6 个修改的源码文件及 2 个测试文件 |
| mypy | 通过，6 个源码文件 | `--follow-imports=silent` 定点检查，不是全仓库类型检查 |

新增测试覆盖事件突发合并、逐条模型/日志更新、连续事件不推迟绘制、Qt 心跳、关键状态即时显示、任务切换、关闭清理、未变文本保留选择，以及真实编辑/撤销仍更新工程脏状态。offscreen 中 4 个跳过项需要 Windows QMessageBox，已在最终原生 Qt 回归中实际执行通过。

## 首轮失败与边界

扩展回归中的 2 项失败来自测试方式，已调整并复验通过：收起面板的测试原先要求每条事件同步绘制，现先断言模型立即更新，再通过已有 `waitUntil()` 等待可见内容；新增测试原先比较会更新导出时间戳的 `WorkflowStore.toPayload()`，现比较实际草稿会话数据。原有结果、选中标签、收起状态和工程不变断言均保留。

新增测试开发中还修正了两处夹具：节点执行必须先发送匹配的 `node.started`；渲染计数前必须等待初始算子目录加载结束。这些早期失败不作为产品修复或通过证据。

其余 12 项未修复，均已在固定基准 SHA 复现：

- `tests/ui/page_designer/test_current_job_viewing.py` 中 11 项失败：模拟 Job 响应缺少现行协议使用的 `source_job_id`，在未修改的 `preview.py:324` 触发错误，部分测试最终表现为 Qt 等待超时。
- `tests/designer/test_visual_layout.py::testLogMessageAppearsInInitialViewport`：`header.visualIndex(9)` 预期为 3，实际为 4。

基准对照通过临时 Python 导入钩子读取 `git show <固定 SHA>:<源码路径>`，替换本轮 6 个模块后运行完整 `test_current_job_viewing.py` 及上述布局测试；未回退工作区文件。后续定点通过结果不覆盖扩展回归的原始失败结论。

没有执行全仓库 pytest、冻结 EXE、打包、部署或修复后的真实设备性能验收。原生 Qt 自动化证明相关交互及状态规则通过，不等于已测得用户拖动窗口的帧率。

## 用户体验入口

当前已打开的 Python 程序不会自动加载此次源码修改，需要用户正常关闭并重新启动 Designer。未代替用户停止任务、关闭窗口或重启程序。

在仓库根目录可用原有入口启动：

```powershell
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' scripts/dev.py run-designer --local
```

使用原有工作流检查主窗口和页面预览拖动、节点/流程切换、运行面板收起和展开、停止响应。普通结果刷新合并不改变 Runtime 处理帧数，不把约 10 Hz 的面板刷新目标当作视频处理 FPS。

最终原生 Qt 复验命令：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest -q `
    tests/designer/test_runtime_event_refresh.py `
    tests/designer/test_floating_run_panel.py `
    tests/designer/test_runtime_qt_ownership.py `
    tests/designer/test_run_failure_feedback.py `
    --tb=short -rs --timeout=90
```
