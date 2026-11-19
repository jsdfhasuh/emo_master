# 通用页面组合示例

将本目录整体复制到可写目录，使用 Designer 打开 `inspection.emoproj`。工程包含“生产监控”和“历史回看”两个可编辑页面，`inspection.emoweb` 由同一工程导出。`assets/` 包含模拟输入、固定张量的 ONNX 样例及页头图标。`archive/history.sqlite3` 初始仅有表和视图。

1. 在软件 Runtime 加载这个工程。生产监控启用已有宿主控制，在开始按钮可用时点击一次，等待本次作业结束。每次开始会采集一条模拟结果，保存主图与局部图，然后写入记录。
2. 连续运行五次，打开“历史回看”。列表每页三条，内置筛选、排序和翻页栏在工程中隐藏，可用图片下方“上一条／下一条”跨页。“回到最新记录”保持可用。展开“原始详情与日志”查看选中记录的多行日志。需要筛选时，可在页面设计器开启该列表的“显示筛选栏”；底层查询和记录联动不受隐藏配置影响。
3. 再运行一次，使用“回到最新记录”查看第六条。生产页的“记录键”与历史页相同。图片归档在 `archive/images/`，数据库中的图片引用相对工程目录。
4. Web 端连接同一个 Runtime，打开 **运行设置 → 页面包管理**，导入 `inspection.emoweb`，点击 **对当前目标启用 / 切回此版本** 同步下次运行的采集请求。修改工程页面后，可在 Designer 的页面设计窗口通过 **文件 → 导出 Web 页面包** 重新发布。

示例图像为合成图，几何和结果码由示例流程配置。首次在独立 Runtime 状态中执行，前六条的结果码依次为 `1、1、1、2、2、3`。归档回执取自 SQLiteWriter 的实际提交结果。

## 本轮页面配置

页头使用工程配置的 64×64 图标、26 px 主标题和 14 px 副标题。宿主按钮显示“开始检测／停止检测”，采用主／次按钮样式、8 px 圆角、44 px 最小高度和上下 10 px／左右 18 px 内边距。按钮仍执行平台既有启动与停止动作。

生产页三条判定启用“标题跟随状态颜色”，圆点放在左侧标题之前；右侧状态文字和整行背景继续使用同一状态映射。

图上的虚线来自本次检测结果：分别按 `upper`、`lower` 标签筛选并选择目标，经现有区域／中心提取算子后，连接新的通用“两点生成线段”算子，再送入 Annotate。工程没有预绘制参考线或 Hough 代用节点；历史主图保存的是这次 Annotate 的输出。详见[两点生成线段](../../docs/guides/line-from-points.md)。

如需重建，使用仓库声明的 Python 3.10 环境，从仓库根目录执行以下命令。归档验证在临时目录复制工程并启动一个回环软件 Runtime。

```powershell
$env:PYTHONPATH = 'src'
python examples/generic_page_composition/build_example.py examples/generic_page_composition
python examples/generic_page_composition/check_archive.py examples/generic_page_composition/inspection.emoproj
python examples/generic_page_composition/capture_preview.py examples/generic_page_composition/inspection.emoproj --output .tmp/generic-page-preview
```

截图脚本复制工程到临时目录，启动真实软件 Runtime 和 Qt 操作窗口，通过宿主按钮生成检测记录，然后抓取两个页面在 1366×768、1920×1080 下的截图。Linux 无桌面时可设置 `QT_QPA_PLATFORM=offscreen`；这种结果属于 Qt 离屏渲染，不是 Windows 人工 UI 或现场设备验收。

Annotate 的执行包携带字体及 SIL OFL 1.1 许可。 [Noto Sans SC Regular 2.004](https://github.com/notofonts/noto-cjk/blob/Sans2.004/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf)
