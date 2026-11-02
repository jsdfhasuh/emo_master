# 空白块（Panel SDK 1.2.0）

`spacer` 是透明的布局组件。固定空白块默认占位 24×24 逻辑像素；不输出标题、文字、卡片背景或边框。空白块不接受数据绑定、动作、子组件或历史查询来源。

`props.spacerMode` 默认 `fixed`，默认值在契约中可以省略。`flex` 表示弹性占位，仅用于响应式布局：在上下排列中分配有限可用高度，在并排排列中分配所在行宽度，在自动换行中只分配本行宽度。多个弹性块使用 `responsive.weight` 分配剩余空间；空间不足时保留最小占位和正常组件内容。固定块不参与组内等宽或拉伸。

尺寸继续使用 `responsive.minWidth`、`preferredWidth`、`minHeight`、`preferredHeight`。页面逻辑像素在 Web 中使用 CSS 像素；各端的系统字体可以产生不同自然内容高度。旧网格页面仅支持固定空白块的行列跨度。

Web 使用正常浏览器页面的剩余可视高度作为根级纵向弹性布局的可用高度；嵌套容器按它实际分配到的高度计算，不通过读取数据或重建查看会话触发布局。

针对性验证（从 web_panel 目录）：

```powershell
npm --prefix frontend run build
npm --prefix frontend test
python -m pytest tests/test_packages.py tests/test_official_packages.py -q
python frontend/tests/spacer_browser.py
```

浏览器验证使用构建后的前端和 HTTP 合成夹具。`CHROMIUM_PATH` 可指定现有 Chrome/Chromium 可执行文件，脚本使用 headless 模式。验收几何和截图写入 `frontend/test-tmp/spacer-browser/`；该结果不代表真实 Runtime 或现场设备验收。
