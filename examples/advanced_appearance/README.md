# 通用页面控件高级外观示例

[Designer 工程](inspection.emoproj)与 [Web 页面包](inspection.emoweb)来自同一配置。工程使用 2.10，页面使用 1.3。
打开时保留 `assets/` 和 `archive/` 的相对目录。示例沿用合成图片、固定检测输出及本机 SQLite，
开始检测后才写入记录，建议复制整个目录后运行。

| 页面 | 用于复现 |
| --- | --- |
| 生产监控 | 页头两个按钮、动态图片、文字、方向与复核指示、角度数值、运行状态、容器和导航按钮。第 4 次采样显示“符合／复核样本／需复核”和 `10.0°`。 |
| 历史回看 | 从 Runtime 读取实际归档记录，复现列表选中项、历史图片及数值。 |
| 控件对照 | 页面标题、页面导航、时钟、本次集合表、SQLite 表格及汇总值；嵌套文字可用来对照容器样式范围。 |

Designer 中选中组件，打开属性面板的“外观 → 高级外观…”，在弹窗中修改通用字段和 QSS。
开始、停止按钮从“页面设置 · 全局外观… → 页头”的对应按钮设置进入。
Web 加载对应 Runtime 及页面包，运行界面会应用包内 CSS。独立编辑器的草稿、保存和发布沿其现有流程。
样式示例与目标参数见 [OpenSpec 示例](../../openspec/changes/archive/2026-11-23-add-generic-control-advanced-appearance/examples/README.md)。

在仓库根目录用项目 Python 3.10 环境生成另一份示例。选择新目录可获得空白归档库。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m examples.advanced_appearance.build_example .tmp/appearance-demo
```

生成脚本经正式工程 API 保存重读、校验和编译，通过 `buildWebPanel()` 导出页面包并重新读取包内容。

浏览器回放使用独立环境安装的 SDK 1.10.0、本机 Edge 和临时 Runtime。按 Web 项目现有流程构建并打包前端后，
在项目 Python 环境执行以下命令。回放会在临时副本中连续采样，并经真实编辑、发布及启停采集截图。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:CHROMIUM_PATH = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
python -m examples.advanced_appearance.verify_web examples/advanced_appearance/inspection.emoproj --output .tmp/appearance-evidence
```

本次验收截图、操作记录和已验证范围见 [OpenSpec 验收](../../openspec/changes/archive/2026-11-23-add-generic-control-advanced-appearance/acceptance.md)。
