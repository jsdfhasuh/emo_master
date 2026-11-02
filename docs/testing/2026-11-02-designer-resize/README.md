# Designer 响应式尺寸手柄缩放修复

2026-11-02；独立修复分支 `codex/designer-resize-zoom-fix`，不合并、不部署。

## 基准与边界

- 实际源码基准：`0e58440a0a6bea9b368d07b37e2d0b9cf8f0d377`（panel 分支）。
- 当时远端 main：`f0675253631f489998056e5af618378e115efc83`，是该基准的祖先。
- logging 分支：`b1ca6d386e8bb5cf4d21609e52f09acd0803126b`；其独立后续 Web 提交不修改本次 page_designer 文件。不合并该分支，不覆盖其后续工作。
- 保留既有 R03/R13/R15/R16 修复。仅改 `responsive_tools.py` 的尺寸手势，不改工程模型、吸附规则、Runtime 或设备功能。
- 原历史桌面报告未在本工作区 materialize；历史 49.7%/151.6% 结果仅为交接事实，不冒充本次现场证据。

## 根因与最小修复

`ResponsiveHandle` 是 graphics proxy 内的 QWidget。按下时 proxy 已建立隐式鼠标抓取；随后手柄显式调用 `QWidget.grabMouse()`，使窗口输入后续直接走 QWidget 抓取路由，绕过 proxy 的缩放局部坐标转换。再次把这些局部坐标当作 proxy 内坐标映射，造成非 100% 尺寸严重失真。

旧测试直接给 viewport 发送 QMouseEvent，绕开了真正的 QWidget grab 路由，所以不能揭示问题。本次使用 **QTest 的 QWindow 重载入口**，由 Qt 窗口分发选择接收控件，不直接把 move/release 交给 viewport 或手柄。

另有一个小偏差：手柄获得焦点后 view 滚动原点可改变（探测中 verticalScrollBar 从 -352 到 -349）。旧代码保存按下时 renderer 绝对点、减去后来映射的点，混入了滚动原点变化。现在保存屏幕起点，两端用同一当前视图变换映射再取差，不重复换算 DPR。

产品修复只有 6 行增加、3 行删除：移除显式鼠标抓取；保留 proxy 隐式抓取、键盘 Esc 及原事务行为；保存屏幕起点并同变换计算位移。

## 实际执行结果

环境：dot 自有云目录，Debian 13，Python 3.10.22，PySide2 5.15.2.1 / Qt 5.15.2，`QT_QPA_PLATFORM=offscreen`。使用现有模拟 Client 与 pytest 临时工程，不连接真实设备。

| 检查 | 结果 |
|---|---|
| 最初 QWindow 三档反例，修前 | 3 failed，见 `before.*`；50% 412×114 + 屏幕(45,30) 得516×360，150%高度反向缩为76 |
| 同一最终33项矩阵回到未修改基准 | **31 failed、2 passed**，`baseline-matrix.*`；未改正确尺寸期望 |
| 修后六个定向文件 | **102 passed / 52.33s**，`final-focused.*`；包括33项新增矩阵及已有响应式、pending、drop、旧grid resize回归 |
| 新文件再加保存重开三档 | **36 passed / 22.74s**，`final-window.*` |
| 最终六文件重跑（含保存重开） | **105 passed / 48.21s**，`final-all.*` |
| Ruff | 产品1文件+新增测试1文件通过 |
| mypy | 产品1文件 `--follow-imports=silent` 通过，不代表全仓库 |
| 启动导入 | `scripts/dev.py run-designer --local --check` 通过，无窗口/Job启动 |
| diff检查与独立源码审查 | 通过；独立审查无阻断发现 |

新增矩阵覆盖50/100/150%、中心与偏角、snap关/开、向左下(-80,+40)及右下(+80,+40)逻辑增量、候选尺寸与提交一致、一次Undo、零移动无脏/历史、按住移动后Esc取消再释放/继续编辑、pending属性刷新后proxy抓取仍保留，以及保存重开保持逻辑尺寸、预览逻辑宽度不因缩放改变。候选尺寸断言不等于可见ghost人工判读；已有定向ghost用例负责辅助几何检查。

证据未抹去中途失败：`harness-initial.*` 是初始QWindow夹具尚未正确进入手柄；`after-window.xml` 是扩展夹具没把第二个number手柄滚到可见区域时的22失败/11通过。它们不是产品缺陷计数。调整夹具只把手柄居中，未改尺寸断言。`before.*` 和 `baseline-matrix.*` 才是进入真实Qt抓取路由后的修前产品反例。

复现命令（依赖按仓库 Python3.10 安装）：

```sh
QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/page_designer/test_responsive_resize_window.py -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/page_designer/test_responsive_resize_window.py tests/ui/page_designer/test_responsive_editor.py tests/ui/page_designer/test_responsive_drag_pending.py tests/ui/page_designer/test_responsive_selection_pending.py tests/ui/page_designer/test_responsive_drop_preview.py tests/ui/page_designer/test_canvas_resize.py -q
```

## 尚未完成的验收

以上是**Qt窗口级自动化**，不是实际可见鼠标/OS桌面拖拽，也不是用户要求的原生桌面50/100/150%复测完成。云内独立Xvfb :97启动因沙箱无法建立监听socket失败；唯一提权尝试也在平台bwrap启动前失败，因此没有运行中的Xvfb进程。未访问用户电脑或使用其桌面，不绕过该限制。

仍需在获准的可见 Qt5 桌面执行用户要求的三档拖动、Undo、零位移、拖中Esc取消与继续编辑（含不同抓取点、snap开关），核对候选/保存值和实际预览几何。没有运行全仓库pytest、冻结程序、真实设备测试，不把本次结果宣称全面验收。
