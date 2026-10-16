# Designer 算子右键菜单验证记录（2026-10-06）

本轮完成流程设计中的原生节点右键菜单：配置算子、查看节点结果、复制算子、删除算子。菜单专项、相关回归和原生 Qt 路径通过；完整 CI 仍为 FAIL，唯一失败是本轮修改前工作区已有的窄工具栏问题。本结论仅覆盖菜单接入，不改变既有性能、资源稳态、Qt 间歇崩溃和现场发布结论。

## 1. 受测代码与修改范围

- 工作分支：`agent/runtime-workflow-architecture-v1`。
- 起始 HEAD：`9737f04312a2803f824e4bbeab6ea845549edaf7`。
- 代码提交：`e4b8b97ca698aed3be3ab598ddb10c1eab0bd877`；正常推送后用 `git ls-remote --heads origin refs/heads/agent/runtime-workflow-architecture-v1` 独立确认相同完整 SHA。
- 工作区进入本轮时有 233 个已修改或未跟踪文件，包括两个原有暂存文件。完整 CI 在起始 HEAD 加既有修改及本轮菜单代码的工作区执行，不应称为纯提交代码的完整 CI。
- CI、最终菜单专项、回归和原生窗口的源码与测试摘要为 `72681a581534ceaf111099331bb9065611a3952c5326c8d54a1e2d80d1fe0fdc`。本轮提交后重新计算相同，详见 [工作区保护记录](node-context-menu-2026-10-06/workspace-preservation.json)。
- 另从 Git 原始代码构建只包含四个本轮文件的隔离候选；其记录见 [clean-candidate.json](node-context-menu-2026-10-06/clean-candidate.json)。排除了原有 dirty 修改，该候选专项及保存加载回归 39 项通过。

正式代码和测试仅涉及：

| 文件 | 修改 |
| --- | --- |
| `src/emo_master/apps/designer/ui/flow_scene.py` | 节点右键事件命中、键盘菜单及注册回调；保留无 Qt 适配接口。 |
| `src/emo_master/apps/designer/ui/node_context_menu.py` | 独立菜单协调器，复用现有命令和结果面板，处理选择、运行状态与过期动作。 |
| `src/emo_master/apps/designer/ui/main_window.py` | 只提交菜单协调器导入和实例化两行；原有未提交 UI 改动未纳入。 |
| `tests/designer/test_node_context_menu.py` | 23 项菜单事件、事务、结果、保存加载与生命周期验证。 |

菜单行为详见 [使用说明](../designer-node-context-menu.md)。Runtime、执行顺序、算子语义、数据库和页面绑定没有因本轮接入而改变。

## 2. 环境与命令

| 条件 | 实际值 |
| --- | --- |
| 操作系统 | `Windows-10-10.0.26200-SP0`（Python platform 字符串） |
| Python | 3.10.21，Conda 既有环境；未重新安装依赖 |
| 解释器 | `C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe` |
| PySide2 | 5.15.2.1 |
| gRPC / protobuf | 1.78.0 / 6.33.6 |
| NumPy / OpenCV | 1.26.4 / 4.10.0.84 |
| 自动化 Qt 测试 | `QT_QPA_PLATFORM=offscreen` |
| 原生截图 | `QT_QPA_PLATFORM=windows`，1280×720 逻辑像素，DPR 1.0，96 DPI |

自动化使用 `PYTHONUTF8=1`、`PYTHONPATH=<当前源码>/src`、`HUARAY_CAMERA_SMOKE=0`，以及 `PYTEST_ADDOPTS=--ignore=manual_test_workspace`，后者排除本机手工测试目录，不排除仓库正式测试。未操作真实设备或现场数据。下面 `python` 均指上述解释器。

```powershell
python -m pytest -q tests/designer/test_node_context_menu.py

python -m pytest -q tests/designer/test_flow_scene_interactions.py tests/designer/test_flow_layout_qt.py tests/designer/test_node_result_panel.py tests/designer/test_node_run_inspection.py tests/designer/test_operator_editor_manager.py tests/designer/test_main_window_project_save_load.py tests/ui/page_designer/test_workspace_modes.py tests/sqlite_writer/test_editor.py tests/sqlite_writer/test_dependencies.py

python -m pytest -q tests/designer/test_node_context_menu.py tests/designer/test_flow_scene_interactions.py tests/designer/test_main_window_project_save_load.py

python scripts/ci_check.py
```

第三条在隔离候选目录执行，不把原工作区回归结果冒充干净候选验证。每次结果记录均保存完整命令和 cwd。辅助脚本的实际执行内容保存在 [helpers](node-context-menu-2026-10-06/helpers)，使用 `.py.txt` 避免成为额外测试或产品代码；复跑时复制成临时 `.py`，为证据选择新目录，不能覆盖原结果。

## 3. 最终验证

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| 新增菜单专项 | PASS | [focused-final/raw.log](node-context-menu-2026-10-06/focused-final/raw.log)：23 passed，pytest 4.07 秒，完整命令 4.734 秒。 |
| 受影响回归 | PASS | [regression/raw.log](node-context-menu-2026-10-06/regression/raw.log)：78 passed，pytest 7.20 秒，完整命令 8.219 秒。 |
| 只含本轮修改的隔离候选 | PASS | [clean-run/raw.log](node-context-menu-2026-10-06/clean-run/raw.log)：39 passed，pytest 5.65 秒，完整命令 6.422 秒。 |
| 原生 Qt 右键、鼠标复制、运行禁用、Esc、退出 | PASS | [native/result.json](node-context-menu-2026-10-06/native/result.json)、[native-run/raw.log](node-context-menu-2026-10-06/native-run/raw.log)：实际 Qt 事件，`starts=0`、`stops=0`，窗口及工作者退出。 |
| 完整 CI 的 proto-drift / Ruff / mypy | PASS | [ci/raw.log](node-context-menu-2026-10-06/ci/raw.log)：mypy 检查 332 个源码文件，无错误；保留 untyped-functions 提示。 |
| 完整 CI | FAIL | 2955 passed、1 failed、8 skipped、28 subtests passed；pytest 903.52 秒，[命令总时长](node-context-menu-2026-10-06/ci/result.json) 906.672 秒，退出码 1。 |
| CI 跳过项 | SKIP | 8 项，保留原日志；`pytest -q` 未逐项打印跳过原因，不能算通过。菜单专项无跳过。 |
| 本轮原生 125%/150%/200% DPI、多显示器菜单定位 | NOT_RUN | 本轮实际原生截图条件只有 DPR 1.0；缩放 0.5、1.0、1.75 的画布命中由专项覆盖，不能等同 OS DPI 验收。 |
| 真实设备、1080p/5 Hz 性能与长期 Qt 稳定性、冻结包及现场 | NOT_RUN | 不在本轮菜单接入范围；旧失败与缺口保持原结论。 |

专项覆盖标题和节点命中、单节点选择、键盘菜单、空白画布与连线不误触；配置使用已有编辑器；复制和删除只形成一次撤销，重做与中文目录保存重开正确；删除仅移除目标及关联线。也覆盖运行期间禁用、触发时再次检查、边界与控制节点限制、工程／工作流／工作区切换、清空、隐藏、关闭、过期动作和单菜单复用。

结果动作使用已有节点结果协调器，以真实格式的执行摘要验证 `count = 0`，重复右键不反复清空已选择图像，不新增 Job 或订阅。本次菜单验证未执行真实检测 Job；原生截图使用明确标记的 Qt 测试节点，不能称为检测结果截图。

## 4. CI 失败归因及开发期失败

完整 CI 唯一失败：

```text
tests/designer/test_visual_layout.py::testNarrowToolbarKeepsRunActionAndHasOverflow
tests/designer/test_visual_layout.py:314
assert window.startButton.isVisible()
```

窗口尺寸 640×500 时运行按钮不可见。为区分原有 dirty 状态和本轮菜单改动，进行三种独立单项复现：

| 源码状态 | 单项结果 | 证据 |
| --- | --- | --- |
| 原始 Git HEAD，排除原有 dirty 和本轮改动 | PASS | [baseline-visual](node-context-menu-2026-10-06/baseline-visual/result.json)，1 passed。归档与 Git blob 核对采用 CRLF/LF 归一化，实际归档摘要保持原始字节。 |
| 原始 HEAD 加进入本轮前的全部 233 个 dirty 文件，排除菜单改动 | FAIL（本轮起始工作区基线） | [visual-start-dirty/raw.log](node-context-menu-2026-10-06/visual-start-dirty/raw.log)，同一断言失败；每个原有文件按起始 SHA-256 校验后还原到仓库外。 |
| 原始 HEAD 加四个菜单文件，排除原有 dirty | PASS | [visual-task-only/raw.log](node-context-menu-2026-10-06/visual-task-only/raw.log)，1 passed。 |

因此该失败不归因于本轮菜单代码，也不改判为修复。前一轮同项完整 CI 的失败保留在 [SQLite 边界修复报告](sqlite-review-round2-fix-2026-10-06.md)。单项通过不等同完整干净 CI 或工具栏已修复。

开发期原始失败均保留：

- [focused-first](node-context-menu-2026-10-06/focused-first/raw.log)：21 failed，夹具把含 Input／Output 边界节点的实际流程误当成只有两个算子。修正为按节点类型识别普通算子，并继续断言边界和其他节点保持。
- [focused-second](node-context-menu-2026-10-06/focused-second/raw.log)：20 passed、1 failed、1 error。连线夹具未调用场景渲染；工作区 monkeypatch 未在关闭前恢复。改为使用现有渲染契约和作用域受控的 monkeypatch。
- [focused-third](node-context-menu-2026-10-06/focused-third/raw.log)：21 passed、1 failed。新夹具误用了无 Qt fallback 专用方法，改为现有 `connectPorts()` 和 `renderEdge()`。
- 首次及第二次日志还含 Windows `access violation`，栈位于 Qt 已导入后的 ONNX native import。新增测试现沿用应用的 `emo_master` 先于 PySide2 的 DLL 预加载顺序。最终专项、隔离候选、原生窗口及完整 CI 没有出现该诊断，仍不足以证明此前 Qt 组合崩溃或长期稳定性已修复。

上述为本轮新夹具问题，未将它们一律归为基线，也未删除或放宽既有断言。修正后的本轮菜单专项最终 FAIL 为 0，完整 CI 仍 FAIL。

## 5. 截图与工作区保护

实际原生菜单：

![算子右键菜单](node-context-menu-2026-10-06/native/node-menu.png)

完整窗口和运行中禁用状态：

- [Designer 选中测试节点](node-context-menu-2026-10-06/native/designer-selected-node.png)
- [运行中菜单](node-context-menu-2026-10-06/native/running-menu.png)

进入本轮前先在仓库外保存起始身份、原始 index、已暂存差异及受影响文件原件。提交使用独立临时 index：以 HEAD 为基准，仅加入四个本轮文件，其中 MainWindow 只加入两行接线。随后只将这些路径的已提交 blob 同步到正常 index；没有 reset、stash 或整文件纳入 MainWindow 的其他改动。

已逐字节验证：232 个其他原有 dirty 文件未变；MainWindow 去掉本轮两行后与原件一致；原有暂存补丁未变。`.gitignore` 和 `docs/plans/2026-10-06-standalone-runtime-production-v1.md` 仍保留原暂存状态。它们及其他原有改动没有因本次提交而上传，工作区仍然 dirty。

证据目录的局部 `.gitattributes` 保留日志和图片原始字节。[manifest.json](node-context-menu-2026-10-06/manifest.json) 记录 SHA-256；提交前同时核对工作文件与 Git 暂存 blob 字节。代码提交与证据提交正常推送；最终同步 SHA 见交付答复及分支历史。

## 6. 本轮结论

菜单接入的专项、受影响回归、项目事务和原生 Qt 操作通过，可在保存项目、关闭旧 Designer 并重新从源码启动后使用。完整 CI 的原工作区窄工具栏失败继续阻塞全量通过；跳过和未执行项不算通过。没有替换执行器、发布现场版本或扩大本轮功能范围。
