# SQLite 保存配置界面整理

日期：2026-10-28 至 2026-10-29（Asia/Shanghai）。

源码基线：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`。开始时工作区干净；以下结果针对本次未提交源码修改。

收尾时工作区出现其他并行修改，涉及 Runtime 工程配置写入、协议生成文件及 SQLite 缺失目标诊断文案。本次未编辑或撤销这些修改，未将其计为本次界面交付。SQLite backend 的并行差异只扩充缺失文件 / 表的错误文字，不改变本次结构详情使用的字段。

## 用户问题与改动

用户截图中的数据库列结构被拼成多行文字；参数来源固定占据窗口底部；映射表嵌套在整页滚动区中，实际可见行数很少。用户随后要求文字更容易理解、减少专业术语。

- 主页面改成“保存内容”“保存位置”“更多设置”。保存内容页只有表格自身滚动，参数来源不再挤占主表高度。
- 表格常用列为“保存到哪一列 / 保存什么内容 / 数据格式 / 没有数据时”；类型显示整数、小数、文字、图片 / 文件等中文，保存的枚举值保持原样。
- 新表的允许空值、默认值两列按需展开；隐藏、切页、重排不丢草稿值。无效默认值会展开对应设置并定位。
- 数据库检查结果只显示简短摘要；“查看各列说明”打开完整只读表格。修改设置后旧检查结果失效。
- 新建表预览默认展示可读的列清单，SQL 放到高级页，保留原有明确创建确认。
- SQLite 的重复算子身份正文收起，窗口名称保留；其他算子身份展示不变。
- manifest 与 OperatorMeta 的参数显示名称同步更新，参数键、数据库写入规则和变量绑定值不变。

## 已执行验证

环境：Python 3.10.21、PySide2 5.15.2.1。所有数据库操作使用测试临时目录。

| 验证层次 | 结果 | 范围 |
| --- | --- | --- |
| Qt offscreen / 源码针对性测试 | 50 passed，8.80 秒 | SQLite 编辑器与布局；参数标题元数据；通用变量引用与刷新 |
| 共用窗口回归 | 4 passed，5 deselected，2.30 秒 | PLC 页面与参数来源可达性，其他 5 项未选中 |
| 原生 Windows Qt，完整 Designer 样式，100% | 6 passed，4.57 秒 | 两种窗口尺寸、最后一行编辑、变量绑定、真实临时 SQLite 结构检查、建表选项 |
| 原生 Windows Qt，完整 Designer 样式，150% | 6 passed，5.19 秒 | 同上 |

主测试命令：

```powershell
$env:PYTHONPATH='src'
$env:QT_QPA_PLATFORM='offscreen'
& "$env:USERPROFILE/.conda/envs/emo_master/python.exe" -m pytest -q tests/sqlite_writer/test_editor.py tests/sqlite_writer/test_editor_layout.py tests/core/plugin/test_builtin_parameter_titles.py tests/designer/test_parameter_titles.py tests/designer/test_global_variable_review_fixes.py tests/designer/test_global_variables_ui.py --tb=short
& "$env:USERPROFILE/.conda/envs/emo_master/python.exe" -m pytest -q tests/designer/test_small_window_layout_repair.py -k ProductionPlc --tb=short
```

原生验证设置 `QT_QPA_PLATFORM=windows`、`QT_SCALE_FACTOR=1` 或 `1.5`，在创建 QApplication 前启用 Qt 高 DPI 属性，使用 `applyDesignerStyle(app)`，再运行 `tests/sqlite_writer/test_editor_layout.py`。设置 `EMO_SQLITE_LAYOUT_SCREEN_DIR` 可保存截图。正式样式截图位于 `verified-native-100/`、`verified-native-150/`；这些是临时测试数据，不是用户现场工程。

## 失败与后续修正记录

不以最终通过覆盖前期证据：

1. 初轮原生验证为 3 failed / 3 passed：旧编辑器夹具没有传入 Designer 实际提供的 `variableDefinitions`，绑定面板根本未创建。新增测试夹具补足真实入口，保留绑定可见性断言。
2. 随后的 offscreen 布局验证有 2 项键盘编辑失败：Qt 委托提交需要处理事件。编辑开始和 Enter 后补事件处理，并要求实际焦点是 QLineEdit，保留最终文本断言；修正后 6 passed。
3. 只配置 Fusion 调色板的原生 100% / 150% 各 6 passed，但未加载 app.qss，不能作为最终外观证据；对应 `final-native-*` 为中间截图。
4. 完整样式的 150% 首轮为 1 failed / 5 passed：重复分组标题和边框压缩可见行数（可用高度 346，小于十行所需 360）。移除重复容器装饰后，原有十行断言通过；正式样式 100% / 150% 各 6 passed。
5. 最后组合 offscreen 测试初次为 1 failed / 49 passed：窗口初次显示的屏幕适配回调覆盖测试指定尺寸，offscreen 虚拟桌面将窗口限制到 552 像素高。测试先等待初次显示完成，再模拟明确调整窗口尺寸，保留原几何与十行断言；最终 50 passed。此项只修改测试时序，不修改产品的屏幕适配行为。

## 验证边界

- 已验证生产 OperatorEditorManager 装配的原生 Qt 编辑器与临时 SQLite 文件；不是单纯静态截图。
- `test_user_path.py` 已适配分页交互，未执行其完整 Designer → 工作流运行链路。其既有 `schemaVersion == '2.1'` 历史断言保留，未借本次布局工作改写历史工程夹具。
- 未运行全仓库测试、人工现场验收或真实设备验证；未更改用户工程、凭据、正式数据库或生产 Runtime。
- 未重新打包 EXE、未部署或重启用户当前程序，未提交或推送。
