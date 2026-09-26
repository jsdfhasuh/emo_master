# 算子图标 v1 实施与本地验收记录

日期：2026-09-10。实现、源码/GUI 回归和本地冻结 ZIP 隔离验证均已通过。
2026-09-10 用户后续已授权整理提交并推送；未打 tag 或触发正式发布，没有修改外部构建仓库。

## 实施范围

- manifest 可选 `iconResource`、GUI 无关的受限 PNG/SVG 校验、非致命 `iconIssues`、
  只读注册快照与 SHA-256。扫描结果为 49 个 active builtins、0 个 rejected。
- `OperatorInfo` 新增字段 14/15；`GetOperatorIconAsset` 按 ID、版本和预期 SHA 查询，
  不提供路径访问，不要求加载项目。生成代码由项目脚本生成。
- 目录异步加载、首次就绪门禁、四个有界取图 worker、缓存、取消和重试预算；
  Runtime 会话与控件绑定分别隔离，关闭共享一次三秒预算。
- 卡片自绘、画布固定标题图标位、侧栏、节点详情和编辑器窗口共用 provider。
  图标不进入项目持久化、拖拽 MIME 或 Job 快照，不修改算法、端口、相机/PLC 调用。
- 三个原创 SVG 示例：Canny `1.1.1`、Huaray Camera `1.1.1`、YOLO `1.2.1`。
  只更新资源和对应元数据版本；摘要与来源见 [资源记录](../../operator-icon-assets.md)。
- 无 GUI 资源自检及显式 `--gui-icons` 像素自检；发布前隔离 ZIP 验证门禁。

开发接入步骤见 [注册说明](../../plugin-registration-flow.md#941-为算子添加自带图标)，
契约与范围见 [实施计划](../../plans/2026-09-10-operator-icon-registration-v1.md)。

## 环境与基线

| 项目 | 实测环境 |
| --- | --- |
| 分支/代码基线 | `agent/runtime-workflow-architecture-v1` / `090757fb772d922bfead87f4e043164639892c7f` |
| Python | Conda 环境 `emo_master`，Python 3.10.20，Windows x64 |
| GUI | PySide2 5.15.2.1；测试 offscreen，截图 Windows 原生 Qt |
| RPC | grpcio 1.78.0、protobuf 6.33.6；本机随机端口真实服务及嵌入式模式 |
| 解析/推理/冻结 | defusedxml 0.7.1、onnxruntime 1.23.2、PyInstaller 6.22.2 |
| 硬件边界 | `HUARAY_CAMERA_SMOKE=0`；无真实相机、PLC 或远程设备操作 |
| 修改前 CI | 751 passed、1 skipped |
| 最新 CI | 916 passed、1 skipped；proto drift、ruff、mypy 全通过，229 个源文件类型检查 |

开发中曾在 Qt 事件泵遇到 Windows native access violation。测试清理已调整为先关闭新建
根窗口再集中释放，由父控件拥有的 Qt.Tool 不重复独立删除；卡片延迟绑定改用父对象
拥有的单次 QTimer；关闭时先给已取消 worker 短暂退出机会，再按需处理事件。
调整后完整 CI 先后得到 904、908、909 passed，各有 1 skipped，无相同 native 崩溃。
这是本机复测结果，不据此承诺所有 Qt/显卡环境都无风险。

## 自动化结果

`python scripts/ci_check.py` 最新通过。唯一跳过项为需显式启用的真实 IMV 相机 smoke。
图标 GUI 正常路径没有被 skip，故障注入和正常显示采用不同断言。

| 覆盖 | 结果及关键证据 |
| --- | --- |
| 格式和注册 | 路径越界、只读快照、非致命诊断、PNG CRC/顺序/解压预算、SVG QName/DTD/属性/路径/变换及结构限额 |
| 资源契约 | 真实 unary deadline/取消、旧元数据、UNIMPLEMENTED、版本/SHA/长度/MIME 校验；断开真实服务器再重连同一端口更新 scope |
| 调度缓存 | 请求合并、优先级和队列上限、bytes LRU、GUI 256 项/32 MiB 上限、最多一次补试和目录恢复预算 |
| GUI 换绑 | 删除控件、快速换图、工作流切换、同 nodeId 的项目重开、目录失效与 Runtime 会话变化 |
| 启动和关闭 | 首次目录期间编辑器/导入门禁、启动入口取消、共享关闭预算、目录 worker 尚未退出时重复关闭不误报成功 |
| 渲染 | SVG/PNG 在 DPR 1/1.5/2 的真实像素；透明图片失败；完整 MainWindow 在 QtSvg 缺失或抛错时仍可兜底 |
| 回归 | 项目/工作流与既有视觉算法套件通过；图标刷新前后业务内容、选中节点、位置和运行态保持不变 |

源码命令 `python -m emo_master.apps.windows_entry --self-test --gui-icons` 全通过：
49 个内置算子、3 个编辑器 UI、3 个图标，以及 QSS、SQL 迁移和 ONNX CPU 推理。
每个示例在 24/36/48 物理像素各测一次，共九组 `renderSource=custom`，
验证资源 SHA、非透明像素和示例特征色。不传 `--gui-icons` 时不创建 QApplication。

提交前复核日志在本机忽略目录 `build/operator-icon-v1/ci-pre-push.log`；
鼠标点击修复的全量日志为 `build/operator-icon-v1/ci-popup-click-fix.log`。
源码自检结果已保存为 [source-self-test.json](source-self-test.json)。

## 实际界面截图

截图来自 `scripts/operator_icon_visual_check.py` 的真实 MainWindow，不是界面 mock。
脚本使用临时 Runtime 数据库和内存 settings，`WA_DontShowOnScreen` 避免操作用户窗口。
通过 `QT_FONT_DPI=96` 校准后再指定 `QT_SCALE_FACTOR`，并断言窗口实际 DPR。
单独画布导出明确传逻辑目标矩形，避免 QImage 的 DPR 二次放大造成裁切。

| 比例 | 实际 DPR | 主窗口 | 卡片 | 画布 | 验证报告 |
| --- | --- | --- | --- | --- | --- |
| 100% | 1.0 | [main](100/main.png) | [cards](100/cards.png) | [canvas](100/canvas.png) | [JSON](100/report.json) |
| 150% | 1.5 | [main](150/main.png) | [cards](150/cards.png) | [canvas](150/canvas.png) | [JSON](150/report.json) |
| 200% | 2.0 | [main](200/main.png) | [cards](200/cards.png) | [canvas](200/canvas.png) | [JSON](200/report.json) |

每组报告含 10 个消费者：3 个卡片、3 个画布节点、3 个侧栏条目、1 个详情图标；
全部 `renderSource=custom`，摘要匹配，显示 worker 均正常退出。截图已目视检查标题、
图标槽、端口和状态填色；脚本另断言节点不互相重叠。
完成/运行中/失败着色为脚本注入的展示状态，未启动相机或模型作业。
编辑器窗口的 provider 接入由集成测试覆盖，不把本组截图算作编辑器页面实测。

## Windows 包体

实际外部构建配置只读核对自 `jsdfhasuh/python_build_scripts`：

- `configs/emo-master.json` blob：`bcf4bf17d2fc63cf5f09ca746a7081419bab71d6`。
- `pyi_rth_onnxruntime.py` blob：`5976519c223e9e9661aa3d3ad0210a0805992218`。
- 路线为 Python 3.10、PyInstaller 6.22.2、onedir/windowed、windows_entry，
  收集整个 builtins、QSS、迁移、ONNX 依赖及其导入顺序 hook。
  外部配置或 hook 后续变化须重新验收，不能把这两项 SHA 当成永远固定。

本地镜像构建位于忽略目录 `build/operator-icon-v1`。首轮构建完成但隔离启动超时，
控制台诊断确认 `_sqlite3` 缺少 DLL；构建日志同时警告缺少 `ffi.dll`。
仅在本地生成的 spec 增补 Conda `Library/bin` 的这两项运行库，未改外部构建配置。
这项本机 Conda 补充不是对官方 Python 构建器的已验证修改。

最终 onedir/windowed EXE 在临时工作目录自检通过，然后重新压缩为
`build/operator-icon-v1/EmoMaster-verified-portable.zip`（122339420 字节）。
ZIP SHA-256：`68a867260c9326532d9864fe40bf4569477bda40934ab8bfa8d2adc541b450ed`。

使用正式门禁脚本解压到全新临时目录后再跑一次，进程退出码 0，
`builtins/operatorIcons/designerQss/migrations/onnxruntime/guiIcons` 全部 `ok`，
3 个示例共九组 `renderSource=custom`，没有错误。完整结果保存在
[package-self-test.json](package-self-test.json)；其中 QSS 路径落在解压包的 `_internal`，
不是源码目录。原始执行日志位于 `build/operator-icon-v1/package-verified.log`。
归档 JSON 仅对 QSS 路径去掉机器绝对前缀，分别改为相对仓库根和解压根的路径；
校验状态、资源摘要与像素结果均未修改。原始源码报告仍保存在本机
`build/operator-icon-v1/source-self-test.json`，原始包体报告位置见上述验证日志。

发布工作流在正式上传前调用 `scripts/verify_windows_package.ps1`，
必须在全新目录清空 `PYTHONPATH` 后以冻结 EXE 完成上述九组像素及全部旧自检；
120 秒超时或缺少任一成功结果都会阻断发布。

## 后续修复：点击搜索框导致浮层收起

2026-09-10，根据实际操作反馈复现：应用级事件过滤器先收到 `QWidgetWindow` 的
鼠标按下事件，原逻辑只识别 QWidget 及其祖先，因而把该事件误判为点击浮层外部，
在 QLineEdit 处理点击前关闭浮层。这不是图标资源或搜索匹配错误。

修复在 `OperatorBubble.eventFilter` 中限定外部点击判断只处理 QWidget 阶段，
保留分类按钮例外、Esc 和应用失活关闭逻辑。新增
`tests/designer/test_operator_bubble_mouse_events.py`，通过 QTest 向 QWindow 发鼠标事件，
覆盖连续点击并输入、卡片单次创建、空白内容、分类切换/切回、外部点击不吞操作以及
Esc/应用失活。此前截图只覆盖显示，未覆盖这条原生窗口事件路径。

相关 26 项测试通过，最终完整 CI 为 916 passed、1 skipped；协议、lint 和类型检查通过。
以上测试使用真实 PySide2 5.15 的 offscreen 窗口，不是操作用户正在运行的窗口。
本次只更新源码，上一节记录的 ZIP/EXE 未重建，不包含这项后续修复。

## 尚未验收

- 正式远程构建工作流及安装器安装/卸载，本次未触发发布。
- 第二台离线机器、真实远程 Designer/Runtime 分离部署及长时间反复开关面板的总进程内存趋势。
- 实际设备、现场基准项目与操作员交互验收；现有自动化不能代替现场验收。
- wheel/sdist 安装后资源，当前交付路线为 Windows 冻结包。
- 全量独立配图不在本轮，仅三个示例；资源风格仍可由用户后续审阅。

因此本地实现状态与正式发布/现场验收状态必须分开，不将这些项目写成已完成。
