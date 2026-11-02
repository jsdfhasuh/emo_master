# Designer 性能探针报告

采集时长：0.7 秒。
源码 HEAD：8df8599fc026c424a49e87cc53fb45f6b371f16f；分支：codex/designer-resize-zoom-fix；有未提交修改：True。具体源码文件 SHA256 见 report.json。
函数分析：已开启，以下为GUI线程CPU统计。
界面响应额外延迟：P95 73.42 ms，最大 171.13 ms；超过50 ms的记录 3 次。

## CPU耗时分类

| 环节 | 自身累计CPU毫秒 |
| --- | ---: |
| 其他GUI调用 | 312.50 |
| 工程模型与历史 | 31.25 |
| 响应式布局求解 | 31.25 |
| 页面选择、拖动与尺寸调整 | 15.62 |
| Qt布局测量与应用 | 15.62 |
| 流程场景 | 0.00 |
| 画布导航 | 0.00 |
| 画布适配与输入 | 0.00 |
| 属性面板与编辑 | 0.00 |

## 自身CPU耗时最高的函数

| 函数位置 | 次数 | 自身CPU毫秒 | 含子调用CPU毫秒 |
| --- | ---: | ---: | ---: |
| `copy.py:128 deepcopy` | 47190 | 78.12 | 78.12 |
| `src/emo_master/core/presentation/responsive.py:66 _solveResponsive` | 159 | 31.25 | 46.88 |
| `~:0 <method 'show' of 'PySide2.QtWidgets.QWidget' objects>` | 174 | 31.25 | 93.75 |
| `~:0 <method 'exec_' of 'PySide2.QtCore.QEventLoop' objects>` | 1 | 31.25 | 46.88 |
| `src/emo_panel_protocol/models.py:394 visit` | 39 | 15.62 | 15.62 |
| `src/emo_master/apps/designer/state/workflow_store.py:724 _normalizeBoundaryNode` | 78 | 15.62 | 15.62 |
| `src/emo_master/apps/designer/state/workflow_store.py:825 _serializedNodes` | 39 | 15.62 | 15.62 |
| `src/emo_master/apps/designer/ui/theme.py:53 eventFilter` | 4276 | 15.62 | 15.62 |
| `src/emo_master/apps/designer/page_designer/responsive_tools.py:90 paintEvent` | 247 | 15.62 | 15.62 |
| `src/emo_master/ui/presentation/responsive_layout.py:82 setGeometry` | 99 | 15.62 | 78.12 |
| `~:0 <method 'isAncestorOf' of 'PySide2.QtWidgets.QWidget' objects>` | 8307 | 15.62 | 15.62 |
| `~:0 <method 'hasHeightForWidth' of 'PySide2.QtWidgets.QWidgetItem' objects>` | 2196 | 15.62 | 15.62 |
| `~:0 <method 'widget' of 'PySide2.QtWidgets.QWidgetItem' objects>` | 2196 | 15.62 | 15.62 |
| `~:0 <method 'save' of 'PySide2.QtGui.QPixmap' objects>` | 1 | 15.62 | 15.62 |
| `~:0 <method 'get' of 'dict' objects>` | 110447 | 15.62 | 15.62 |
| `~:0 <built-in method builtins.__import__>` | 1221 | 15.62 | 15.62 |
| `~:0 <built-in method processEvents>` | 24 | 15.62 | 156.25 |
| `~:0 <method 'y' of 'PySide2.QtCore.QRect' objects>` | 1189 | 15.62 | 15.62 |
| `~:0 <method 'connect' of 'PySide2.QtCore.SignalInstance' objects>` | 48 | 15.62 | 15.62 |
| `queue.py:154 get` | 11 | 0.00 | 0.00 |
| `queue.py:193 get_nowait` | 11 | 0.00 | 0.00 |
| `queue.py:209 _qsize` | 11 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:3564 effectiveRunTargetIds` | 3 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:3572 <genexpr>` | 6 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:3568 runTargetsToolTip` | 3 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:3742 _pollOperatorCatalog` | 11 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:4400 _drainLocalLogFailures` | 5 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:4422 _pollOperationalLogs` | 5 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:4855 <genexpr>` | 3 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:4856 <listcomp>` | 3 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:4852 _activeDebugWindows` | 3 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/ui/main_window.py:4858 hasActiveDebugSession` | 3 | 0.00 | 0.00 |
| `copy.py:182 _deepcopy_atomic` | 39936 | 0.00 | 0.00 |
| `copy.py:201 _deepcopy_list` | 1287 | 0.00 | 78.12 |
| `copy.py:227 _deepcopy_dict` | 5967 | 0.00 | 78.12 |
| `copy.py:243 _keep_alive` | 7254 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/services/operator_catalog_worker.py:22 <listcomp>` | 11 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/services/operator_catalog_worker.py:20 poll` | 11 | 0.00 | 0.00 |
| `src/emo_master/apps/designer/services/operator_icon_worker.py:117 poll` | 22 | 0.00 | 0.00 |
| `site-packages/pydantic/_internal/_typing_extra.py:575 signature_no_eval` | 78 | 0.00 | 0.00 |

## 含子调用CPU耗时最高的工程函数

用于从布局、渲染等入口向下追查；包含时间不能相加。

| 函数位置 | 次数 | 含子调用CPU毫秒 |
| --- | ---: | ---: |
| `src/emo_master/apps/designer/page_designer/responsive_tools.py:168 select` | 3 | 203.12 |
| `src/emo_master/apps/designer/page_designer/tools.py:554 select` | 3 | 156.25 |
| `src/emo_master/apps/designer/state/project_edit_session.py:95 document` | 39 | 125.00 |
| `src/emo_master/apps/designer/state/workflow_store.py:614 toPayload` | 39 | 109.38 |
| `src/emo_master/apps/designer/state/project_edit_session.py:90 payload` | 39 | 109.38 |
| `src/emo_master/apps/designer/state/presentation_store.py:43 snapshot` | 36 | 109.38 |
| `src/emo_master/apps/designer/page_designer/responsive_tools.py:206 updateSelection` | 6 | 93.75 |
| `src/emo_master/ui/presentation/responsive_layout.py:82 setGeometry` | 99 | 78.12 |
| `src/emo_master/apps/designer/page_designer/responsive_tools.py:58 __init__` | 48 | 78.12 |
| `src/emo_master/apps/designer/page_designer/responsive_tools.py:71 position` | 168 | 78.12 |
| `src/emo_master/core/presentation/responsive.py:66 _solveResponsive` | 159 | 46.88 |
| `src/emo_master/apps/designer/page_designer/tools.py:866 updateSelection` | 3 | 46.88 |
| `src/emo_master/core/presentation/responsive.py:54 solveResponsive` | 159 | 46.88 |
| `src/emo_master/ui/presentation/responsive_layout.py:53 _measurements` | 183 | 31.25 |
| `src/emo_master/apps/designer/state/workflow_store.py:724 _normalizeBoundaryNode` | 78 | 15.62 |
| `src/emo_master/apps/designer/state/workflow_store.py:825 _serializedNodes` | 39 | 15.62 |
| `src/emo_master/apps/designer/ui/theme.py:53 eventFilter` | 4276 | 15.62 |
| `src/emo_master/apps/designer/page_designer/responsive_tools.py:90 paintEvent` | 247 | 15.62 |
| `src/emo_master/apps/designer/state/workflow_store.py:636 <dictcomp>` | 39 | 15.62 |
| `src/emo_master/apps/designer/state/workflow_store.py:663 ensureBoundaryNodes` | 39 | 15.62 |
| `src/emo_master/apps/designer/state/workflow_store.py:675 _ensureBoundaryNodesForWorkflow` | 39 | 15.62 |
| `src/emo_master/apps/designer/ui/designer_actions.py:209 blockedReason` | 93 | 15.62 |
| `src/emo_master/apps/designer/ui/designer_actions.py:298 refresh` | 3 | 15.62 |
| `src/emo_master/apps/designer/page_designer/page_canvas.py:138 _applyPendingFit` | 6 | 15.62 |
| `src/emo_master/apps/designer/page_designer/tools.py:956 eventFilter` | 1108 | 15.62 |

## 响应延迟最大的时刻

| 采集后毫秒 | 额外延迟毫秒 | 工作区 |
| ---: | ---: | --- |
| 534.6 | 171.1 | page |
| 93.4 | 73.4 | page |
| 304.9 | 58.8 | page |
| 193.0 | 47.6 | page |
| 339.9 | 15.0 | page |
| 593.0 | 14.5 | page |
| 682.5 | 10.3 | page |
| 652.2 | 9.2 | page |
| 558.4 | 3.9 | page |
| 109.0 | 0.0 | page |
| 125.4 | 0.0 | page |
| 208.1 | 0.0 | page |
| 226.1 | 0.0 | page |
| 343.4 | 0.0 | page |
| 606.3 | 0.0 | page |

## 解读边界

- 函数分析会增加采集开销；不代表关闭探针后的帧率。
- 源码标识来自开始记录时的磁盘文件，不能证明进程已加载更新；更新后请重新启动对应目录的Designer。
- 响应延迟是Qt定时器调度延迟，不是显示器帧率，也不能单凭延迟认定某个函数是根因。
- 调用栈只含Python帧，不包含Qt/GPU原生栈；被长期持有的GIL可能使采样缺失。
- Windows线程CPU时钟可能较粗，短函数应结合调用次数、调用者与多次复现分析。
- 事件计数包含Qt事件转发，不等同于用户操作次数。
- 没有采集控件文字、绑定值、图片、工程内容、凭据或Python局部变量。

- 有限缓冲区省略计数：{'beats': 0, 'stacks': 0, 'marks': 0}；采样异常：0。
- report.json包含完整函数统计、调用者、延迟时间线、手动标记及卡顿期间的Python栈。
- 先看延迟时间点对应的调用栈，再核对CPU热点；调用次数很多但单次很短也可能形成累计负担。
