# 通用页面配置补齐 · V1.4 实现与验收记录

日期：2026-11-19（北京时间）。执行者：ChatGPT Web。

**五项能力已实现。本环境可执行的软件验证已通过；真实 Web 浏览器验收尚未执行。**
工作基线为 `a9554a0ea80311a6870f4544af2398b6446c088b`，分支 `feat/page-composition-config`。
本文验证的是该基线加本轮未提交修改，不能作为干净 `a9554a0` 的结果；未提交、未推送。

## 1. 五项改动

| 用户提出的差距 | 本轮实现 | 通用配置入口 |
| --- | --- | --- |
| 开始/停止文字和默认按钮外观固定 | 自定义文字、主/次/链接样式、字体、圆角、内距、最小宽高、图标；保持原命令及可用状态 | `shell.startControl/stopControl` |
| 页头和 logo 显示范围固定 | logo 从原资源等比显示，页头表面及主/副标题角色可配置 | `logoWidth/logoHeight/headerSurface/headerTypography` |
| 左标题不跟色、缺左动态圆点 | 标题跟随当前状态、圆点置于标题前；空/未映射值清除旧表现 | indicator 的 `titleFollowsState/markerPosition/showMarker` |
| 没有两个选中中心的连接虚线 | 通用两点构线算子，输出既有 lineCollection，复用 Annotate 的 dashed/dot | `vision.geometry.line_from_points` |
| 历史筛选/排序/分页不能独立隐藏 | 三开关分别控制 UI；外部按条导航和同记录图片/详情继续工作 | `historyView.showFilter/showSort/showPagination` |

Qt 与 Web 共用字段。结构化样式分别生成 QSS 和 CSS，本轮没有任意 QSS 字符串编辑入口。
作者预览显示新配置但不执行启动/停止。旧完成计划保持原结论，本轮任务在
[complete-page-configuration](../../../openspec/changes/complete-page-configuration/README.md) 中跟踪。

附带关闭了已观察到的同名运行识别问题：Web 查看、停止目标和实时预览下拉增加短运行 ID，完整 ID 在提示中；请求仍使用原始完整 ID。

## 2. 自动化与生成物

| 实际执行范围 | 结果 | 证据层次 |
| --- | --- | --- |
| 构线、几何桥、Annotate、注册及参数/核心版本检查 | 21 项通过 | 源码算子与图像结果检查 |
| Qt 状态表现及历史控件 | 32 项通过 | 原生 Qt 自动化；包含隐藏栏后的跨页/返回最新 |
| Qt 作者属性入口 | 2 项通过 | 实际表单、保存和重新加载当前工程 |
| Qt 宿主页头/按钮 + 原多流程控制回归 | 3 项通过 | 原生控件及软件 Runtime 开始/停止 |
| Web 状态/历史、作者字段、宿主、恢复及编辑器相关测试 | 66 项通过 | JSDOM/React 实际控件事件和现有测试；不是浏览器截图 |
| 官方生成物、工程发布、Web 后端资源/历史往返 | 11 项通过 | 源模型/包构建 + HTTP TestClient；后端 Runtime 使用测试夹具 |
| TypeScript、Vite build | 通过 | 当前前端源码实际构建 |
| 本轮 28 个 Python 文件 Ruff；相关 helper mypy | 通过 | 静态检查 |
| OpenSpec 2.1.0 strict | 通过 | 本轮规格格式与结构；关闭 CLI 遥测/更新检查 |
| SDK 1.8.0 | 通过 | 42 份交付文件哈希匹配；wheel 内 16 个 Python 文件与源文件逐字节一致；隔离导入不加载 emo_master |

没有把复测次数重复算作新的场景。测试主要覆盖五项能力的正常路径、直接反例和保存/发布，不增加硬件故障或全部配置排列组合。

复核发现并修复了一个作者入口问题：默认未启用自定义页头时，新子组原先仍能编辑但保存会丢弃；现在这些组随页头总开关启用/禁用，关闭再开启保留当前输入。

SDK 仍使用工程 2.10、页面 1.3、Panel Contract 1.0。只升级共享 SDK 至 1.8.0，不增加旧工程、旧草稿、旧包或旧 SDK 兼容机制。生成脚本保留已有交付文本的行尾，避免跨系统重建引入大量无关差异。

## 3. 同一工程及 Qt 实际截图

交付示例位于 `examples/generic_page_composition/`，包含可编辑的 `inspection.emoproj` 和同工程的 `inspection.emoweb`。
工程的检测输入是明确的合成图与固定输出 ONNX 示例，用于演示页面及数据链路，不是训练完成的现场识别模型。

本轮移除了独立静态边缘图/Hough 连线；现在每轮从检测输出选中 upper/lower 两个目标并提取中心，生成真实线段，再标注到本次图像。新增缺点分支只接受本次显式空列表，不代替已有业务 NG/缺目标判断。

软件 Runtime 的完整归档验证完成了 6 次运行、主图像素与当前结果一致、新连线位置、选中记录和跨页等检查。
正式交付数据库仍为 0 条记录；截图另在临时副本中通过真实 `OperatorWindow` 点击开始 4 次，全部 COMPLETED。

- [生产监控 · 1366×768](qt-production-1366x768.png)
- [历史回看 · 1366×768](qt-history-1366x768.png)
- [生产监控 · 1920×1080](qt-production-1920x1080.png)
- [历史回看 · 1920×1080](qt-history-1920x1080.png)
- [Qt 实际记录](qt-preview.json)

图片均为 `QWidget.grab()` 的实际输出，没有拼接或重绘。Qt 使用 Linux offscreen；虚拟桌面默认 800×600，脚本显式设置实际控件视口并验证图片尺寸。**这不是 Windows 人工桌面或冻结 EXE 验收。** 1366×768 保留正常滚动，较长 UTC 时间可换行。

正式工程 SHA-256：`b7f466d4c5d4fcaa20bd6d75150b8bad65225d7ed6ca9084d9bff4273666775e`。

## 4. 唯一待执行项：真实 Web 浏览器验收

当前环境启动系统 Chromium 时，本地 AF_UNIX IPC 的 socket 创建被拒绝，返回 EPERM。
工具提供的提权启动入口也被自动审批拒绝，原因是 `sandbox_approval=false`。未改权限或绕过限制。

[web-preview.json](web-preview.json) 记录 `BLOCKED_BEFORE_BROWSER_LAUNCH`，截图列表为空。
前端已实际构建并打包到 Web 后端；下游浏览器生产/历史页面、控制按钮及两种分辨率检查未执行。
不使用旧 V1.3 截图或 DOM 测试替代本项。

在允许浏览器本地 IPC 的开发环境中，安装测试用 Playwright，并设置 `CHROMIUM_PATH` 为实际 Chromium/Edge 可执行路径后运行：

```text
python examples/generic_page_composition/capture_web_preview.py examples/generic_page_composition/inspection.emoproj --output docs/testing/2026-11-19-page-configuration
```

脚本复用真实软件 Runtime 与 Web Gateway，在临时工程副本中导入同工程页面包并执行正常旅程；这段下游流程尚未得到本次浏览器执行验证。通过后保留实际截图/JSON，再勾选 V1.4 `tasks.md` 的 3.4。

复现 Qt 证据：

```text
python examples/generic_page_composition/capture_preview.py examples/generic_page_composition/inspection.emoproj --output docs/testing/2026-11-19-page-configuration
```

Linux 无桌面环境可设置 `QT_QPA_PLATFORM=offscreen`；Windows 原生走查应使用正常桌面，不把 offscreen 等同于人工验收。

未验收项：本轮完整 GitHub CI、Windows 原生人工操作、冻结 EXE、现场相机/PLC/工位精度。它们没有被宣称通过，也没有因此扩展本轮五项软件能力的范围。
