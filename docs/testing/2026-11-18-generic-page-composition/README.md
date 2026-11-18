# 通用页面组合验收

2026 年 11 月 18 日，北京时间。验证起点为 `82d05914bda9c0390a2f94bcf6b2794e59ff3484` 加本轮未提交改动。与 OpenSpec 参考提交 `31afa5ecab8b7ddb53f0d6bdedc620f0a243e340` 比较，相关源码差异在 Designer 的流程标签和调用链导航。验证使用 Python 3.10、PySide2／Windows Qt Widgets、Playwright Chrome 和安装后的 Panel SDK 1.6.0。

## 使用

复制整个[示例目录](../../../examples/generic_page_composition)到可写位置。在 Designer 打开 `inspection.emoproj`，通过「页面设计」编辑生产监控与历史回看。使用「文件 → 导出 Web 页面包」发布当前工程。

软件 Runtime 加载同一工程。Web 端上传 `inspection.emoweb`，对当前目标显式启用，等待采集请求同步，再使用宿主的「开始」。每次单次运行会归档一条。历史页可以翻条、按声明列筛选、回到最新和展开本条日志。操作细节见[示例说明](../../../examples/generic_page_composition/README.md)。

## 实测

| 路径 | 结果 |
| --- | --- |
| A · 设计、保存和发布 | Windows 原生 Qt 自动化经 Designer 正式打开入口加载交付工程。修改页面宽度、卡片表面、主值字号、按钮样式、绑定、单位及整数状态映射，覆盖恢复默认和撤销／重做，保存重开再导出页面包。Chrome 经正式 Web 编辑器验证外观设置、恢复默认、撤销／重做、保存重开和实际 Runtime 历史预览。 |
| B · 同次生产与冻结 | 用同一工程的临时副本开启已有连续运行模式。Qt 宿主开始后冻结当前结果，等下一次实际结果进入后核对画面与记录键，恢复后显示新结果，再通过宿主停止。Web 端也核对冻结第 5 条、写入第 6 条、恢复后显示第 6 条。 |
| C · 归档与回看 | 最终工程通过实际 Runtime 运行五次后追加第六次。SQLiteWriter 回执、数据库行和 ImageSaver 保存的主图及局部图关联同一键。每页三条，第六条加入后按原键恢复第 3 条，下一条仍取第 2 条。Qt 和 Web 实际验证切页返回、回到最新、筛选及清空，主图与生产图像像素一致。 |

归档数据见[最终工程六次运行](archive-journey.json)和[Web 旅程记录](web-journeys.json)。Web 五加一验收期间一次启动在工作进程开始前返回 `E_HEARTBEAT_TIMEOUT`，核对失败终态且无归档后显式重试。最终 Web 截图使用补齐实际虚线与端点后的工件。

| 画面 | 1366×768 | 1920×1080 |
| --- | --- | --- |
| Qt 生产监控 | [截图](qt-production-1366x768.png) | [截图](qt-production-1920x1080.png) |
| Qt 历史回看 | [截图](qt-history-1366x768.png) | [截图](qt-history-1920x1080.png) |
| Web 生产监控 | [截图](web-production-1366x768.png) | [截图](web-production-1920x1080.png) |
| Web 历史回看 | [截图](web-history-1366x768.png) | [截图](web-history-1920x1080.png) |

另见 [Designer 页面设计](designer-page.png)和 [Web 历史整页](web-history-full-page.png)。Qt 截图通过 Windows 平台插件渲染。较高视口按逻辑尺寸检查，物理 Windows 任务栏仍由操作系统占用。Web 截图来自连接软件 Runtime 的真实浏览器。

官方 `openspec validate enhance-generic-page-composition --strict`、`scripts/gen_proto.py --check`、正式 SDK 生成、已安装 SDK 的 manifest 一致性与 Vite 构建均通过。字体和许可通过正式 Python wheel 的无界面绘图验证。现场 PLC、相机、检测精度、冻结 Windows EXE 和远端部署不在本次软件模拟验证范围内。

最后一组相关 Designer、Qt 和发布用例通过 220 项。Web 前端全套通过 196 项，预览尺寸调整后定点复测通过 88 项。Web 后端 221 项在整套执行中通过，1 项聚合夹具改用正式声明列和聚合请求后单独通过，7 项可选集成用例跳过。另有浏览器冒烟、目录刷新及布局检查通过。实时环境检查使用本地软件 Runtime。

交付工程 SHA-256 为 `f5d46de55ed754d3ffcec15781313a99c6ec34e03cebc0009315e032cf56dc15`，发布包 SHA-256 为 `60f027c9250993c5baca1d5a26af94e7f1126afb50fd669ff3cd8086f1dac62c`。上述验收记录采集于提交前的本地 `main` 工作区。
