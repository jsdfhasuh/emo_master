# ROI 编辑器职责归位与公共图片控件验证

日期：2026-11-10。执行者：Codex。

## 基线与工作区

- HEAD：`51bff936ee2f2a0e4c3712441f8e622e33312c3c`。
- 这是包含上轮 ROI 界面改动及本轮未提交修改的工作区验证，不是该 SHA 干净版本的验证。
- 保留原有未跟踪 `test/`，没有修改现场工程、设备设置、凭据或安装产物，没有提交、推送或重启用户进程。
- 上轮布局验证保留在 [原记录](2026-11-10-roi-editor-layout.md)；本记录对应当前的算子自有布局实现。

## 实施范围

1. ROI 的三栏布局、结果页签、参数区域由 `plugins/builtins/roi/ui/editor.ui` 声明。控制器负责装配图片控件、工具栏和折叠区。平台 `builtin_layout.py` 不再按 ROI ID 或根控件名重排界面。
2. 平台提供可选 `mountVariableBindings(scroll)` 接入方法，把已有绑定控件交给算子安放。平台继续拥有绑定刷新、收集、校验和应用，不增加第二套绑定数据。未实现该方法的编辑器沿用原布局路径。
3. 从节点大图查看器提取 `ui/image_view.py:ImageView`，节点查看器和 ROI 共同复用缩放、平移、图像坐标转换及原图持有。节点查看器原有手动缩放范围 0.1–8 倍保持不变；ROI 使用 0.01–20 倍。自动适应允许更低比例，避免大图被裁掉。
4. ROI 原图提供选择/移动模式，选择模式左键绘制、移动模式左键平移，中键始终可平移。四个结果预览支持滚轮及左键/中键拖动。视图操作不写入 ROI 参数、不请求新预览、不改变导出原始像素。
5. 通用 `CollapsibleSection` 和 `FormWheelGuard` 位于 `ui/widgets.py`；ROI 形状、端口及默认布局仍只在算子侧。
6. 插件 UI 校验允许标准 `QSplitter` 控件，不放宽控制器信任策略或自定义控件限制。初次真实 Runtime 测试暴露原允许列表拒绝分隔器并触发表单回退；增加通用控件支持和注册回归后重新验证通过。
7. 最小可运行例子位于 `examples/image_preview_editor/`，是布局与控件演示，不是生产插件或工程。开发指南补充接入约定。ROI 算法、输入输出合同及 Runtime 预览资源生命周期未改动。

## 已执行

环境：Windows，`C:\Users\dd109\.conda\envs\emo_master\python.exe`，Python 3.10.21 / PySide2 5.15.2.1。

### 定向源码回归

以下命令 **373 passed in 224.07s**，无跳过：

```text
python -m pytest tests/designer/test_image_view.py tests/designer/test_roi_editor_layout.py tests/designer/test_roi_preview_export.py tests/designer/test_roi_preview_retirement.py tests/designer/test_operator_editor_bindings_host.py tests/designer/test_operator_editor_manager.py tests/designer/test_operator_modes.py tests/designer/test_builtin_operator_editor_ui.py tests/designer/test_node_image_viewer.py tests/core/plugin/test_registry.py tests/plugins/test_roi_operator.py tests/runtime/test_draft_pure_preview.py tests/designer/test_plc_editor.py tests/sqlite_writer/test_editor_layout.py -q --disable-warnings --maxfail=1
```

涵盖公共图片视图、三种 ROI 图形的缩放/平移后坐标回写、四种结果的正确像素导出、预览失效与回收、节点结果窗口、非内置算子绑定挂载与失效引用校验、注册、原有 PLC 与 SQLite 编辑器。使用临时项目、模拟服务或本地测试资源，不访问实际相机或 PLC。

首次尝试附带 `--timeout=120`，环境未安装 pytest-timeout，pytest 在收集前拒绝参数；移除此选项后执行上述完整命令，不将前次退出记为通过。

### 原生 Qt

对 `test_image_view.py`、`test_roi_editor_layout.py`、`test_operator_editor_bindings_host.py`：

- `QT_QPA_PLATFORM=windows`：**25 passed in 20.21s**。
- 同上并设 `QT_SCALE_FACTOR=1.5`：**25 passed in 26.11s**。
- 包含 980/1280 逻辑像素宽度，分别输出截图；已查看两种缩放下 980 宽截图，无控件遮挡。
- 测试经真实 `.ui` 加载与编辑器窗口入口执行。截图使用生成图片；这是原生 Qt 自动化，不是用户现场人工操作验收。

### 静态与示例

- 受影响的控制器、公共图片控件、窗口接入、示例和新增交互测试通过定向 Ruff 检查。
- `git diff --check` 通过；仅提示 `.ui` 下次由 Git 处理时按仓库配置转换为 CRLF。
- 在非 pytest 进程中确认示例模块可导入，`emo_master` 实际解析到本仓库 `src/emo_master`。
- 非内置 `example.image_preview` 示例通过窗口接入测试，证明绑定安放不依赖 ROI 特判；示例不发起 Runtime 请求。

## 未执行与加载方式

未执行全仓库测试、冻结/安装包自测、现场工程人工 UI 或真实设备验收。没有重启当前 Designer/Runtime。要加载新 Python 控制器与注册 UI，需正常关闭并重新启动使用本仓库源码的相关进程；现有安装包不因源码修改自动更新。
