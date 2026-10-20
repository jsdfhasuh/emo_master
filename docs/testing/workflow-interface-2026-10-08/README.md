# 工作流接口表单与双击入口验证（2026-10-08）

> 本文保留首次实现、审查前的验证快照。三项审查问题的修复、最终回归和任务范围隔离结果见 [审查后改进记录](review-fixes/README.md)。
> 原始 `.txt` 日志和 `.patch` 数据完整保存在 `raw-verification-logs.zip` 的对应路径中；本地另保留未改变的原文副本。归档校验见 `review-fixes/raw-log-manifest.json`。

## 实现与使用

- 双击 `Workflow Input` 节点标题或卡片主体，打开工作流接口配置的输入页。
- 双击 `Workflow Output`，打开同一个配置窗口的输出页。
- 工作流标签菜单的“设置工作流接口”使用同一窗口，不再连续弹出 JSON 输入框。
- 每行编辑接口名称和数据类型，可新增、删除；常用类型有中文解释，支持已加载插件类型、列表类型和手工输入自定义类型。
- 空接口合法；空名称、重名、首尾空格、空类型或无效描述符会即时提示并阻止确认。
- 输入和输出在确认时一起提交；取消、Escape、未修改确认不会捕获画布或改变草稿。
- 描述符的 `required`、`nullable`、`schemaVersion` 及已有别名保持兼容，不把描述符悄悄降级为字符串。
- 继续使用既有工作流控制器同步子工作流/循环引用、清理不兼容连线并保留位置。
- 确认配置仅更新项目草稿；保存项目后持久化。运行中禁止接口编辑。

首次实现阶段未修改项目格式、Runtime 执行协议、算子实现或已有示例工程，当时未提交、推送、打包或发布。后续提交状态以审查后改进记录及对话报告为准。

## 首次实现验证快照（审查前）

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 接口表单、画布双击、边界节点、菜单、系统节点 | PASS，64 passed | `focused-accepted.txt` |
| 150% 缩放下新增接口测试 | PASS，28 passed | `focused-150-accepted.txt` |
| 原生 Windows 100% 布局 | PASS，10 场景，全部无裁切 | `accepted-100/report.json` |
| 原生 Windows 150% 布局 | PASS，10 场景，全部无裁切 | `accepted-150/report.json` |
| Ruff、Mypy、protobuf drift、git diff --check | PASS | `ruff-accepted.txt`、`mypy-accepted.txt`、`proto-accepted.txt`、`diff-check-accepted.txt` |
| 完整仓库回归快照 | 非全绿：3763 passed、5 failed、8 skipped、28 subtests passed | `ci-snapshot.txt` |
| Designer 回归快照 | 非全绿：805 passed、2 failed | `designer-final.txt` |
| 撤销本次生产代码改动的隔离副本 | 5 项相同失败，BASELINE_FAILURE | `baseline-all-failures.txt` |
| 打包、发布、真实相机/PLC验收 | NOT_RUN | 不属于本次界面修改范围 |

完整回归与 Designer 回归是执行期间快照，早于最后的小窗口表格高度及类型列表细化；不能据此声称最终全仓库全绿。最终直接相关测试和两种缩放的原生截图均在细化后重新验证。

布局场景包括输入、输出、空接口、18 个接口及重名提示，分别测试 700×480 和 640×430 逻辑尺寸。截图程序不调用 Runtime，也不修改用户项目或应用设置。字体为 Microsoft YaHei UI，并检查了文字实际栅格化。

## 已有失败的归因

这五项失败全部在隔离副本中复现，未为消除失败而修改无关逻辑或断言：

1. `testEditorUsesLateCatalogWithoutMutatingSavedGraph`：示例已含参数元数据，而测试仍预期空 schema。
2. `testNormalizationPreservesSavedContractsAndOwnsFallbackSchema`：测试使用固定节点数组下标，取得的节点没有 `folderPath`。
3. `testRealWhileProcessesEveryImageAndResetsForNewRun[1]`。
4. `testRealWhileProcessesEveryImageAndResetsForNewRun[3]`：这两项使用的当前示例已包含 Huaray 相机，而环境无 IMV Runtime。
5. `testDirectoryResolvedRelativeToProjectAndDraftUnchanged`：同样使用固定节点下标，发生 `folderPath` KeyError。

`baseline-snapshot.json` 记录隔离副本位置与转换前后哈希；`baseline-restoration.patch` 记录恢复 JSON 入口和原双击行为的实际差异。保留其它未提交修改，不以旧 HEAD 代替当前基线，不在原工作区回滚文件。此比较仅对上述五个未修改的测试作归因，不是一次完整基线验收。

## 保留的中间证据

早期 `focused-first.txt` 记录了 PySide2 信号参数错误及测试鼠标释放问题；`ci-first.txt`、`ci-second.txt` 记录了已修复的静态检查问题。

早期截图验证记录了原生小窗口自动长高、150% 字体探针坐标错误；后续 `focused-150-verified.txt` 暴露了 offscreen 大字体下表格可视区域过小。最终通过滚动容器和表格最小可用高度修复，未降低原有布局断言。中间失败/截图保留，最终截图以 `accepted-100/`、`accepted-150/` 为准。

`task-source-hashes.json` 记录最终直接相关源码、测试及截图脚本哈希。主窗口与画布文件原本已有无关未提交修改，本次仅改接口入口、对应节点分流、提示与安全事件派发。
