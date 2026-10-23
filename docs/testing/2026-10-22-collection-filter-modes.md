# 集合筛选：对象选择、组合条件和引脚展示

## 范围与基线

- 日期：2026-10-22。
- 源码基线：`d39169251c9bedc339c440a29a56df5eafba3be5`，在已有未提交修改上局部实施；未提交或推送。
- 环境：Windows，仓库 Python 3.10 / PySide2；解释器为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。
- 修改 `vision.collection.filter` 至 1.2.0；没有重构其他集合算子、迁移工程格式、改动现场工程或访问真实设备。

## 实现结果

1. 筛选对象单选：检测结果、连通域、轮廓、形状测量、直线、圆、模板匹配结果。
2. 当前对象适用的条件按组启用，条件之间保持 AND；一个类别列表内匹配任意项。
3. 关闭条件或切换类型时保留参数，包括尚未完成的隐藏输入；这些条件不参与执行与校验。
4. 新节点先选择对象。旧节点根据集合输入连线带出类型，只有输出连线时可从对应集合输出识别；打开编辑器不改写原节点。
5. 缺少新增开关的旧参数根据已有筛选值和绑定识别启用组，保留旧过滤效果；圆度继续保留原显式开关。
6. 画布只显示当前类型的集合输入、保留/剔除集合、两项数量。保留集合排在数量之前，默认连接使用主集合结果。
7. 启用空间筛选时默认选择“中心位于区域内”，显示 ROI 输入；关闭时隐藏未连接的 ROI。
8. 已连接但不适用的引脚保留并标注，不能继续新增此类连接。手动显示全部不重新展开其他类型的端口。
9. 动态类型和空间配置显示可能用到的引脚，不把绑定字段的固定备用值当作实际值。
10. Qt5 不可见行从表单布局中移出，控件仍由表单持有；再次显示保留输入，刷新 schema 会释放旧的脱离布局控件。

完整端口声明和工程整体结构不变。执行前检查显式类型与实际输入是否一致。
`xValidateWhen` 使公共快照参数校验、全局变量有效值校验与算子对关闭条件的处理一致，
不会删除参数草稿。新增启用开关没有固定 schema 默认值，防止默认值合并改变旧节点语义。

## 验证结果

| 层次 | 结果 |
| --- | --- |
| 修改前相关基线 | 38 passed |
| 最终选定扩展源码回归 | 448 passed，1 failed，24.26 秒 |
| 失败项的原始 HEAD 独立复现 | 同一个版本断言失败，1 failed，2.57 秒 |
| 原生 Qt，100% 缩放 | 70 passed，10.48 秒 |
| 原生 Qt，150% 缩放 | 70 passed，16.31 秒 |
| 临时 2.7 工程、参数来源和 Runtime 执行专项 | 8 passed |
| 插件及几何相关专项 | 161 passed，和扩展回归有重叠，不累加为独立总数 |
| Ruff | 通过 |
| Mypy | 本轮 9 个相关源码文件通过 |
| 本轮文件的 diff 检查 | 通过，仅有 Windows LF/CRLF 提示 |

扩展回归覆盖表单、参数来源、集合算子、引脚显示与默认连接、坐标算子、手动隐藏、连线端点、
通用编辑器、YOLO 参数展示、工程图保存重读、全局变量、快照校验和插件注册。

唯一失败为 `tests/designer/test_global_variables_ui.py:32` 的
`testDefinitionsRenameUndoDeleteProtectionAndPackages`：断言工作流包版本为 `1.1`，实际为 `1.4`。
将原始 HEAD 的 `src`、`tests`、`pyproject.toml` 通过 `git archive` 导出至独立临时目录后，
运行同一测试复现相同断言。因此本轮未修改这个无关测试，也未将其跳过后记作通过。

独立基线目录：
`C:/Users/jsdfhasuh/AppData/Local/Temp/emo-filter-baseline-bbdc3097e2ee4a4aa22993e769e93852`。

## 联调中保留的失败记录

- 首次 Designer 集成为 13 passed、5 failed：空间开关仍对应 `none`、关闭的无效列表被默认值处理抹掉，以及测试夹具未先捕获新增连线。分别修正默认关系、输入保留和夹具顺序。
- 首次临时工程夹具把节点端口写成完整声明对象而不是节点要求的类型字符串，4 项校验失败；修正夹具后保存、重读与执行通过。
- 变量运行夹具先后遗漏类型化 `VariableBinding` 与变量读取上下文；补齐真实调用前提后通过，没有修改运行器绕过错误。
- 初次原生截图暴露 Qt5 隐藏行仍留空白；补充相邻可见行间距断言。PySide2 未暴露 `QFormLayout.takeRow`，该次 19 项失败；改为 `takeAt` 后移除空行，保留控件并通过原生复验。
- 最终保留唯一的基线包版本断言失败，不弱化正确断言，不将任何未执行项记为通过。

## 原生截图

- [100% / 620 宽](collection-filter-2026-10-22/native-100/filter-parameters-620.png)
- [100% / 460 宽](collection-filter-2026-10-22/native-100/filter-parameters-460.png)
- [150% / 620 宽](collection-filter-2026-10-22/native-150/filter-parameters-620.png)
- [150% / 460 宽](collection-filter-2026-10-22/native-150/filter-parameters-460.png)

已查看 100% 的 620 宽和 150% 的 460 宽最终截图，并通过控件边界、不重叠、可见按钮、
鼠标点击及相邻行间距断言。截图来自临时测试窗口，不是用户正在运行的 Designer 进程。

## 复验入口

新增测试：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' -m pytest tests/designer/test_collection_filter_presentation.py tests/plugins/test_collection_filter_configuration.py tests/runtime/test_collection_filter_workflow.py -q
```

原生 Qt：设置 `QT_QPA_PLATFORM=windows`、`QT_SCALE_FACTOR=1` 或 `1.5`，运行
`test_collection_filter_presentation.py`、`test_coordinate_port_presentation.py`、`test_manual_port_visibility.py`。
`EMO_FILTER_SCREEN_DIR` 指定截图输出目录。

## 交付边界

本轮为源码、临时工程执行和原生 Qt 自动化验证，不是全仓库测试、冻结安装包、人工现场或真实设备验收。
没有打包、部署、修改现场工程，也没有重启用户进程。源码启动需要重新启动 Designer 和 Runtime；
已打包 EXE 需重新构建后才能包含本轮改动。
