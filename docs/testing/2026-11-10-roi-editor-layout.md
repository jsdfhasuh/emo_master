# ROI 编辑器布局与中文显示验证

日期：2026-11-10

后续已完成算子自有布局与公共控件接入，当前实现及追加验证见 [职责归位记录](2026-11-10-roi-editor-reuse.md)。以下保留上轮证据，不代表后续职责拆分后的文件归属。

## 版本与范围

- 基线 HEAD：`51bff936ee2f2a0e4c3712441f8e622e33312c3c`。
- 测试对象为该提交加本轮未提交修改，不是该 SHA 的干净版本。
- 源码改动：`operator_editors/builtin_layout.py`、`operator_editors/workspace_window.py`、`plugins/builtins/roi/editor.py`。
- 测试改动：新增 `test_roi_editor_layout.py`，更新 ROI 导出、预览回收和算子模式测试。
- 原有未跟踪目录 `test/` 保持不变；未修改现场工程、设备参数或安装产物，未提交或推送。

## 行为

- 原图与结果预览并排，右侧为可滚动的区域参数；两处水平分隔条可调整宽度。
- 结果使用“裁剪图像、遮罩图像、裁剪掩膜、原图掩膜”页签，默认裁剪图像。
- ROI 类型与插值选项显示中文；载入、应用及预览仍使用原有内部枚举值。
- 高级设置、预览记录和参数来源默认折叠；折叠不清空参数或绑定。
- 保留原尺寸查看和导出图片，详情、JSON 和完整 ZIP 导出移至更多菜单。
- 原有预览失效处理、完整像素导出和异步回收行为保留。

## 已执行验证

环境：Windows、Conda `emo_master`、Python 3.10.21、PySide2 5.15.2.1。

1. 以下定向源码测试共 165 项通过（69.85 秒）：
   `tests/designer/test_roi_editor_layout.py`、`test_roi_preview_export.py`、
   `test_roi_preview_retirement.py`、`test_operator_modes.py`、
   `test_builtin_operator_editor_ui.py`，以及
   `tests/plugins/test_roi_operator.py`、`tests/runtime/test_draft_pure_preview.py`。
2. `QT_QPA_PLATFORM=windows` 下布局测试 12 项通过，覆盖 980/1280 逻辑像素宽度；已查看 980 宽截图。
3. 同上并设 `QT_SCALE_FACTOR=1.5`：布局测试 12 项通过；已查看 980 宽截图。
4. 补充“展开预览记录不挤坏图片和参数、不改参数或预览快照”的测试后，原生 Windows Qt 下完整 `test_roi_preview_export.py` 16 项通过（21.14 秒），包括四种结果的页签切换和对应 PNG 字节比对、JSON/ZIP、预览失效及导出失败回归。

布局测试通过 `OperatorEditorManager.open()` 加载真实 UI 和控制器，使用模拟 Runtime、临时数据及生成图片。预览导出测试使用隔离的 Runtime 服务和临时图片，不访问真实设备。截图为原生 Qt 自动化结果，不是人工操作验收。

## 未执行

- 全仓库测试、冻结或打包产物自测。
- 用户现场工程人工 UI 验收、真实相机/PLC 验收。
- 未重启正在运行的 Designer；从本仓库源码启动的 Designer 需正常退出后重新启动以加载修改。
