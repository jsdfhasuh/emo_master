# 预览显示资源提示改进

日期：2026-10-29。源码基线：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`，叠加已有未提交工作及本轮局部修改。未回退已有工作。

同日后续已按用户要求接入现有运行日志，见 [后续验证记录](../2026-10-29-presentation-operational-logs/README.md)。下文保留首次提示改进时的范围与验证证据。

用户提供内网截图，左侧预览显示“结果超过显示容量”。截图对应 Runtime 的实际原因未取得；本轮只完善提示与就地诊断，不声称已复现或修复现场的导出忙碌根因。

## 修改

- 保留 `BUDGET_EXCEEDED` 协议错误码，区分现有 Runtime 已提供的通道忙、每路图片超限和异常后共享缓冲未释放原因。
- 图片超限显示实际 MiB 与上限；仅错误码或未知原因时保留不确定性，不推断成图片过大或通道忙。
- Runtime 补齐单图 8 MiB、单结果图片累计 16 MiB、非图像输出累计 1 MiB 的详细字节数。阈值和资源释放行为不变。
- 图片提示保留原始原因、数据源、流程/节点/端口、Runtime、任务、结果范围、结果编号、执行及采集版本；右键“复制提示详情”取菜单打开时的文本，后续画面更新不改变选中的诊断。
- 保持可选图片缺失/跳过分支的自定义占位文字；下一张有效图片清除旧错误。未增加错误历史、自动重试、旧图冒充新结果或设备操作。

## 验证

解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10 / PySide2。

- `tests/runtime/presentation/test_multi_capture.py`：**23 passed in 39.40s**。包含实际 collector 对忙碌、隔离、大小及新增详情的验证；设备均为测试夹具。
- `QT_QPA_PLATFORM=windows` 下执行 `test_budget_messages.py`、`test_components.py`、`test_loading_continuity.py`、`test_usable_components.py`、`test_renderer.py`：**73 passed in 3.52s**。日志见 `native-ui.log`，JUnit 见 `native-ui.xml`。
- `native_preview.py` 使用真实 Windows Qt 菜单和 QAction，在 100% / 150% 两种缩放下验证菜单打开后画面恢复，复制内容仍对应原失败结果。剪贴板输出使用测试替身，不依赖共享系统剪贴板。结果见 `native-preview-1x.json` / `native-preview-1.5x.json`。
- 已检查两张原生组件截图，四种提示完整显示、无文字裁剪。截图为明确标注的模拟结果，不代表真实设备验收。
- 本轮源码、测试与验证脚本的 Ruff 通过；相关已跟踪文件 `git diff --check` 通过。

## 保留的早期失败与验证边界

- 最初复制测试直接替换 PySide2 C++ 类的 `QMenu.exec_`，未可靠拦截原生菜单。首批在 10 个用例后中断；独立运行得到复制未触发断言失败，另一次触发 30 秒测试超时。修正测试为替换模块中的 Python 菜单子类，同时保留原生菜单 smoke 验证；没有减弱“更新后仍复制原诊断”的断言。
- 一次扩展批次结果为 **59 passed、1 failed、4 errors**。其中复制测试失败按上项处理；4 个 `test_migration_operator_pages.py` 用例在夹具编译 `two_station_screw_vision` 时因未传设备描述符报 `Device-bound execution requires the managed device Runtime`，未进入 UI 验证。本轮未修改这些测试或现场工程，不将其记为通过。
- 没有进行全量测试、冻结 EXE 自测、内网部署或真实相机/PLC 验收。当前修改在源码工作区，未提交、构建或发布。
- 新界面能识别旧 Runtime 已提供的三类原因；旧 Runtime 只返回错误码时仍无法推断细因。新增加的总量/单图数字详情需要运行包含本轮修改的 Runtime。
