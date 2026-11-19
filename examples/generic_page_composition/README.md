# 通用页面组合示例

将本目录整体复制到可写目录，使用 Designer 打开 `inspection.emoproj`。工程包含“生产监控”和“历史回看”两个可编辑页面，`inspection.emoweb` 由同一工程导出。`assets/` 包含模拟输入、参考线、固定张量的 ONNX 样例及页头图标。`archive/history.sqlite3` 初始仅有表和视图。

1. 在软件 Runtime 加载这个工程。生产监控启用已有宿主控制，在开始按钮可用时点击一次，等待本次作业结束。每次开始会采集一条模拟结果，保存主图与局部图，然后写入记录。
2. 连续运行五次，打开“历史回看”。列表每页三条，可用“下一条”跨页。展开“原始详情与日志”查看选中记录的多行日志。按 `result_code` 筛选，示例值 `2` 对应“需复核”。
3. 再运行一次，使用“回到最新记录”查看第六条。生产页的“记录键”与历史页相同。图片归档在 `archive/images/`，数据库中的图片引用相对工程目录。
4. Web 端连接同一个 Runtime，打开 **运行设置 → 页面包管理**，导入 `inspection.emoweb`，点击 **对当前目标启用 / 切回此版本** 同步下次运行的采集请求。修改工程页面后，可在 Designer 的页面设计窗口通过 **文件 → 导出 Web 页面包** 重新发布。

示例图像为合成图，几何和结果码由示例流程配置。首次在独立 Runtime 状态中执行，前六条的结果码依次为 `1、1、1、2、2、3`。归档回执取自 SQLiteWriter 的实际提交结果。

如需重建，使用仓库声明的 Python 3.10 环境，从仓库根目录执行以下命令。归档验证在临时目录复制工程并启动一个回环软件 Runtime。

```powershell
$env:PYTHONPATH = 'src'
python examples/generic_page_composition/build_example.py examples/generic_page_composition
python examples/generic_page_composition/check_archive.py examples/generic_page_composition/inspection.emoproj
```

Annotate 的执行包携带字体及 SIL OFL 1.1 许可。 [Noto Sans SC Regular 2.004](https://github.com/notofonts/noto-cjk/blob/Sans2.004/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf)
