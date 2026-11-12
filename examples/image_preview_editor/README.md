# 算子自有图片编辑界面示例

本示例展示 `.ui` 自行布局、控制器复用图片视图，以及接收平台参数来源控件。它不是已注册的生产算子，不创建工程，不访问 Runtime、相机或 PLC；应用按钮仅演示窗口流程，不写入现场配置。

在仓库根目录、已安装本仓库依赖的 Python 3.10 环境运行：

```powershell
python -m examples.image_preview_editor.demo
```

两幅图均支持滚轮缩放、左键或中键拖动。`ImageView.fit()` 适应窗口，`setZoom(1)` 恢复原始比例，`setImage()` 接收 QPixmap，`setImageBytes()` 接收编码图片。缩放和平移只改变视图，不修改图像或执行算子。图片小于视口时自动居中；放大超出视口后可平移查看。

## 注册与职责

真实算子的 manifest 使用现有 `editor` 声明，例如将示例类放入自己的插件包后：

```json
{
  "editor": {
    "schemaVersion": "1.0",
    "kind": "customUi",
    "openMode": "window",
    "uiResource": "ui/editor.ui",
    "controllerEntry": "your_plugin.editor:ExampleEditorController",
    "fallback": "schemaForm",
    "previewMode": "pure"
  }
}
```

这是 manifest 的编辑器片段，不是完整插件或工程。复制 `.ui` 到声明的位置，使用实际控制器模块路径，并遵守既有插件校验、控制器信任与预览权限要求；本次没有放宽第三方控制器信任策略。

- `.ui` 定义算子布局，控制器决定显示哪些输出、工具栏和折叠状态；平台不按算子 ID 重排这个示例。
- 公共组件位于 `apps/designer/ui/image_view.py` 和 `widgets.py`，不含 ROI 参数或输出端口知识。
- `mountVariableBindings(scroll)` 是可选控制器方法，在 `bind()`、`loadParams()` 后调用一次。平台传入自己创建的参数绑定滚动区；算子把它放入自己的布局或折叠区。平台仍负责绑定值、刷新、校验和应用。不要复制绑定表单、替换绑定数据或重复挂载。
- 未实现该方法的编辑器保持原来的默认安放方式；现有 PLC、SQLite 专用布局也保持不变。
- 显示-only 示例没有实现预览请求。生产接入应使用已有 EditorContext 预览与资源生命周期，不自行读 Runtime 缓存。完整实现参考 `plugins/builtins/roi/editor.py`。
- ROI 的 `_RoiCanvas(ImageView)` 仅负责选区绘制和图像坐标回写。它使用 `mapToScene()` 转换鼠标坐标，选择模式下左键画区域、移动模式下左键平移，中键始终可用于平移。

对应自动化测试：`tests/designer/test_operator_editor_bindings_host.py`、`test_image_view.py` 和 `test_roi_editor_layout.py`。
