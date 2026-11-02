# 日志导出

日期：2026-10-29。源码基线：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`，叠加当前工作区修改。用户要求增加日志导出功能。本轮只修改日志导出模块、日志窗口及 MainWindow 事件接入，保留其他已有工作。

## 使用与范围

入口：“运行 → 打开日志 → 导出日志”。菜单显示可导出条数：

- “导出当前筛选结果”：遵循全部/流程设计/页面设计分类、等级、来源、Job、工作流、节点、事件和搜索条件。
- “导出全部缓存日志”：忽略当前筛选，导出日志窗口仍保留的全部记录。默认窗口缓存上限为 10,000 条；不包含已清空、已淘汰、尚未从 Runtime 接收或过去会话的历史文件。

保存框默认选择 JSON，也支持 JSONL 和 TXT。JSON 是含导出时间、范围、筛选、缓存条数、导出条数和 entries 的结构化文件；JSONL 每行一条完整日志，便于程序逐行处理；TXT 包含可读时间/等级/消息和每条日志的完整 JSON 详情。三种格式均为 UTF-8，保留中文、换行、工程/Job/工作流/节点身份、原始错误码、嵌套 payload、数组、布尔值和 null，不受 JSON 树展开或分页状态影响。

点击导出时，从底层缓存按当前筛选取深拷贝快照，再打开保存框。包含尚未刷新到表格的 pending 日志，保存框期间的新日志、原 payload 变化、清空视图或筛选变化不会改变已经捕获的导出内容。

取消保存或拒绝覆盖安静返回；空结果就地提示，不打开保存框。新增按钮不是 QDialog 默认按钮，搜索 Enter 不会触发导出。补全后缀后如命中另一个已有文件，会对实际目标再次确认。

文件沿用仓库的同目录临时文件 + os.replace 保存方式，成功前不替换目标。普通写入/文件占用失败保留原文件，并清理临时文件。成功通过 `designer.log_export.completed / INFO / DESIGNER_LOG_EXPORTED` 记录范围、格式、路径和条数；失败通过 `designer.log_export.failed / ERROR / DESIGNER_LOG_EXPORT_FAILED` 记录原始原因，并显示失败提示。这些诊断复用 MainWindow.appendLocalEvent 进入现有日志和 Designer 本机 JSONL。

## 实现

- `src/emo_master/apps/designer/services/log_export.py`：三种文件格式与保存。
- `src/emo_master/apps/designer/ui/log_dialog.py`：菜单、筛选快照、保存路径、操作结果。
- `src/emo_master/apps/designer/ui/main_window.py`：新建和重新创建日志窗口时接入结构化导出诊断。
- `tests/designer/test_log_export.py`：导出正常路径、筛选/快照完整性和直接失败反例。

## 验证

解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`；`QT_QPA_PLATFORM=windows`。测试使用模拟日志和临时目录，不连接设备。

- 导出专项 **13 passed in 2.09s**，见 [export-final.xml](export-final.xml)。覆盖三格式完整中文/嵌套 350 项数组、pending 日志、分类/等级/搜索叠加、保存框期间改变视图/数据、全部缓存的保留上限、空结果、取消、覆盖拒绝、保存失败原文件保留/临时文件清理，以及失败实际进入 MainWindow 和本机 JSONL。
- 日志窗口、分类、批量刷新、本机与全局日志集成回归 **56 passed in 10.19s**，见 [regression.xml](regression.xml)。未运行全量测试。
- Ruff 与本轮限定文件 `git diff --check` 通过。
- [原生 Qt 截图](page-logs-json-tree.png) 来自既有界面测试的模拟日志，已检查底部“导出日志”按钮与分类横栏、JSON 树、其他按钮的布局。没有手工操作原生系统保存框；保存路径与选择格式在 Qt 测试中提供。

保留首轮导出测试 **12 passed / 1 failed** 的 [export-initial.xml](export-initial.xml)：测试先假定全部范围的值为 `all`，实际实现用更明确的 `all_cached` 表示当前缓存。修正该预期后 13 项通过，没有修改产品逻辑或弱化缓存边界断言。

本轮未执行 EXE 构建、部署、提交或推送。运行已有安装包不会自动获得源码改动；该导出入口属于 Designer，无需为本轮导出功能改变 Runtime 协议。
