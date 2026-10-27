# 2026-10-23 节点位置不计入在线配置差异

## 范围与基线

- 源码 HEAD：`e8c24fcfd7ebf964f0f506a7d73a42405069dff9`。
- 分支：`agent/runtime-workflow-architecture-v1`；验证对象为该提交上的当前混合工作区，不是干净提交快照。
- 保留原有未提交修改，仅调整配置比较口径、两个 Designer 入口并补充测试和说明。
- Python 3.10.21 / PySide2 5.15.2.1，解释器为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。

## 行为

- `projectConfigurationChanges()` 在同工程比较时忽略 `workflows.<id>.layout`。当前 `WorkflowLayout` 只有 `nodePositions`。
- 配置表、数量、无差异按钮状态及正式运行前检查共用该口径；仅坐标不同显示“运行配置一致，无需写入”。
- 名称、参数、设备引用、节点与连线仍比较。没有按宽泛的 `layout` 分类过滤名称。
- 原始 `projectChanges()`、候选选择、Runtime 写入与执行摘要不变；不同工程 ID 的完整替换保留所有布局差异。
- 本地位置仍保存，可撤销、重做，并通过实际 Designer 打开保存后的临时工程验证。

## 失败证据与修正

- 修改产品代码前，新 UI 测试得到 `2 failed`：纯位置变化错误显示 1 行；参数变化混入 `/workflows/main/layout/nodePositions/capture`。
- 第一轮补充测试的模拟客户端未接收 `replacementPolicy`，异常提示进入模态窗口后发生 Windows Qt access violation；该轮不计为通过。
- 使用现有消息记录夹具后确认异常为模拟客户端参数不完整。修正测试替身签名，并断言替换策略，不修改产品错误处理。
- 连线反例起初把已经为空的 Designer 规范化连线清空，未产生真实变化。改为在现有节点间加入明确连线，保持“连线变化必须触发比较”的断言。

## 验证

- 首轮定向 core / UI：`34 passed`。
- 相关 8 文件 offscreen 回归：`108 passed`，覆盖配置比较、写入、导航、运行入口、Runtime 配置、工程替换和在线图。
- Windows 原生 Qt，`QT_QPA_PLATFORM=windows`、`QT_SCALE_FACTOR=1.5`：`9 passed`，含保存后 Designer 重新打开。
- 150% 窗口截图已查看：0 项差异、运行配置一致、禁用的校验与写入按钮、正常关闭按钮，无文字遮挡。
- 5 个相关 Python 文件 Ruff 通过，`git diff --check` 通过。
- 比较模块与配置对话框 mypy 通过。纳入主窗口时仍有原有工作区问题：`main_window.py:1471` 的 `self.pageCoordinator.showDevices` 存在 `union-attr`，该行在本轮开始前已存在，未改动。

测试文件：

```text
tests/core/project/test_configuration.py
tests/designer/test_configuration_layout.py
tests/designer/test_project_configuration_ui.py
tests/designer/test_configuration_visual_navigation.py
tests/designer/test_runtime_menu_publication.py
tests/runtime/test_project_configuration.py
tests/runtime/test_review_project_replacement.py
tests/runtime/test_online_graph.py
```

## 验收边界

测试只使用临时工程、模拟客户端或隔离 Runtime。未连接现场相机 / PLC，未修改现场工程，未构建或替换 EXE，未提交或推送。
源码运行需重新启动 Designer；冻结 EXE 需要重新打包才包含本次修改。上述结果不是全仓库测试或真实设备验收。

## 提交前复审与独立候选

- 2026-10-23 用户明确要求审查、整理、提交和推送后，重新核对 HEAD 与远端，均为上述基线，无待推送旧提交。
- 审查未发现本次候选的阻断问题。同工程过滤、其他配置差异、完整工程替换、显式选中写入、保存和撤销边界保持一致。
- 候选共 8 个文件；共享的 `main_window.py` 和 `project_configuration.py` 仅暂存相关代码段，不混入其他功能。
- 同时纳入工作区已有的关闭按钮状态修正及对应测试，以保证无差异时显示“关闭”的完整行为。
- 原始已验证暂存树为 `18a9baf805b2f713819fb4986aab1632b135c240`；后续只追加本节记录，源码和测试不再修改。
- 从该树独立导出快照后，相关 8 文件回归为 `108 passed in 57.85s`；不是借用未暂存功能的混合工作区结果。
- 同一快照 Windows 原生 Qt 150%：布局相关 9 项加提交后关闭按钮 1 项，共 `10 passed in 7.70s`。
- 候选的 6 个 Python 文件 Ruff 通过；3 个相关源码文件 mypy 全部通过；暂存差异空白检查通过。
- 前述设备状态 `union-attr` 属于保留在本地的其他未提交工作，不进入本次候选，未为通过检查而修改它。
- 建立 9718 个文件的 SHA-256 保护基线，包括其他工作区改动和本地工程、现场测试材料；不清理或删除这些内容。
- 公共候选的凭据、现场地址与私有工程引用模式扫描未命中。未打包、发布 Release 或部署，未连接真实设备。
- 首次验证快照导出因历史证据文件路径过长失败，保留原始材料，改用短路径导出后完成上述验证。
