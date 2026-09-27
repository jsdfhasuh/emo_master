# P4 Designer 最小闭环执行记录

起始本地 HEAD：`dce0c66edaa2ea43e5992acf283162054661a6b7`，工作区干净，
工作分支 `agent/runtime-workflow-architecture-v1`。已核对完整第三批对象及其历史；
独立远端查询仍为 `2e45f3b70a5b317e39e855c38de3dd22f7eefe1d`。
正常补推遇到连接重置（curl 65 / unable to rewind rpc post data）；失败后再次 ls-remote 确认未同步。
未改 TLS、postBuffer、历史、main 或用户内容。原本地有完整代码，按授权继续开发。

## P4-A

显式开发开关 `EMO_PAGE_DESIGNER=1` 在现有 Designer 主工具栏增加流程设计/页面设计。
页面编辑需确认升级 2.2；未进入时仍保存 2.1。共用现有 WorkflowStore 和一个 ProjectEditSession，
页面事务与流程命令、拖动完成检查点进入同一有界历史；运行事件不进入历史。
保存/另存/加载/关闭由协调器接入原 ProjectController，失败不清 dirty；另存仅复制声明资源。
主窗口仅组装/分发；页面管理和协调器位于正式 `apps/designer/page_designer`。
渲染器新增受控 reload，保留 hub/session，旧图片/集合引用先释放；编辑状态禁用运行动作。

`python scripts/p4_validate.py --suite ui --output docs/evidence/p4/a-first --timeout 300`
实际通过页面/渲染/P1 专项、全 src/tests Ruff、src mypy；原始输出及受测 HEAD、dirty、
源码摘要、环境和退出码在 evidence.json。此批不宣称拖放与实时调试已实现，后续批次追加。

A 提交 `d727505`，96 passed / 0 skipped。批次推送再次连接重置，独立查询远端仍为 `2e45f3b`。
仓库忽略 *.log 与新计划文件；本报告和原始日志在 B 批显式纳入版本控制，未删改原输出。

## P4-B

正式静态输出目录从当前项目及 Runtime 返回的可信 manifest 元数据生成，未运行可绑定。
重复调用路径分别列出；现有单作用域限制明确拒绝。组件/输出使用实际 Qt MIME 拖放，
属性面板与拖放调用同一 schema 命令；网格重叠/容器循环拒绝，副本重绑隔离。
属性包括网格位置/跨度、文字/单位/小数、表格列/分页、判定映射及稳定 ID 导航。
编辑态只选择，预览态才导航；数据/页面选择不创建 Job 或订阅。

`b-first`：98 passed / 1 failed，Ruff 2 errors。拖放用例在布局尚未完成时使用根网格坐标，
暴露 drop 命中不稳定；修复为布局激活、命中已有控件时采用实际模型单元格，
测试直接向该控件发送 Qt DropEvent 验证重叠拒绝。保留原失败。
`b-fixed`：99 passed / 0 skipped，Ruff/mypy PASS。命令为同一 p4_validate --suite ui，
测试含实际拖组件/拖端口、类型拒绝、两个 count 实例、副本隔离、缺节点定位和容器移动回滚。

B 提交 `f94597406e2ed743ae731fbdc8287250915da6fc`。再次正常推送连接重置，随后独立远端查询仍为 `2e45f3b`。

## P4-C 与最终验收

正式 `PreviewController/LocalBackend` 在已有内嵌 Runtime 上显式启用 P2 PresentationService/aio；
没有第二个设备 Runtime。点击调试才取得当前草稿独立文档、Prepare/Start，实际 spawn 执行内置算子。
临时输入物化、SQLite 和输出隔离由 P2 完成。只读连接另走 DisplaySession，不 Start/Stop。
新绑定不热改快照；renderer reload 复用 hub/session。异步启动/退出最多一个工作单元，
项目切换等待本观察者收尾并要求重新选择；迟到启动结果不会附着新页面。
本地算子白名单与版本校验、明确图片资源登记、2.2 草稿打开和另存资源路径同步落地。

新增问题与修复均保留原证据：

- `c-first`：99 passed / 2 failed。静态目录测试被仍在完成的空 stub 目录覆盖；
  改为等待目录就绪再设置测试元数据。真实2.2资源草稿不能走旧 LoadProject 编译，
  新编辑路径改为只打开草稿，明确调试才物化/编译，不改默认旧入口。
- `c-fixed`：7 passed / 1 failed。缺省端口元数据在旧控制器加载时导致边被修剪；
  开发工作区从可信 manifest 补端口，并在加载草稿时保留原边。并非把失败归咎于既有 Qt 崩溃。
- `c-draft-fix`：7 passed / 1 failed。已取得真实 COMPLETE 图数，但路径断言遭 Windows 长/短目录拼写差异；
  两侧解析实际路径后仍严格检查临时根归属，不删除隔离断言。`c-path-fix` 8 passed。
- `c-visible-first` 的程序断言 PASS，但人工读截图发现旧导航控件延迟销毁造成遮挡，**该轮视觉验收 FAIL**。
  修复 reload 先隐藏旧页面/导航，清理图片引用后 deleteLater；截图驱动显式处理 DeferredDelete。
  所有首轮 PNG/原始日志保留；后续 `c-visible-fixed` 与最终截图复核清晰。
- `c-lifecycle` 10 passed；后续补启动中换项目栅栏等，最终 P4 本身11项；与 P1/P3 合并专项104项。

环境：Windows-10-10.0.26200-SP0，Python 3.10.21（AMD64），解释器
`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`；PySide2 5.15.2.1、grpcio 1.78.0、
protobuf 6.33.6、numpy 1.26.4、opencv-python 4.10.0.84。
普通测试 QT_QPA_PLATFORM=offscreen；原生截图独立进程使用 windows，HUARAY_CAMERA_SMOKE=0。
全部测试使用临时项目/DB/输入/输出；未操作现场数据或硬件。

### 最终命令和结果

以下命令在仓库根目录执行，python 指上述解释器。每个输出目录不可覆盖。

| 命令（scripts/p4_validate.py） | 证据目录 | 实际结果 |
|---|---|---|
| `--suite ui --output docs/evidence/p4/c-accepted-ui --timeout 300` | c-accepted-ui | 104 passed / 0 skipped；Ruff、mypy PASS |
| `--suite affected --output docs/evidence/p4/c-affected --timeout 600` | c-affected | P1/P2 106 passed；Designer保存/流程回归48 passed；各0 skipped |
| `--suite visual --output docs/evidence/p4/c-accepted-visible --timeout 300` | c-accepted-visible | windows真实Qt完整路径1 passed，7张PNG，退出确认 |
| `--suite regression --output docs/evidence/p4/c-regression --timeout 600` | c-regression | 组合回归原生崩溃FAIL；proto drift PASS；崩溃后测试NOT_RUN |
| `--suite ci --output docs/evidence/p4/c-final-ci --timeout 600` | c-final-ci | proto/Ruff/mypy PASS；pytest原生崩溃FAIL，后续NOT_RUN |

另保留 `c-ci` 首次完整CI FAIL、`c-ui-final` 104 passed；不挑成功轮覆盖失败。
`c-entry-smoke` 调用真实 `scripts.p4_demo.main()`，仅用定时关闭驱动退出；验证3条流程边、
旧2.1、无dirty、无隐式Job，退出0。具体可复现 Python 命令在其 evidence.json，外层60秒watchdog。
300/600秒是整套进程树监督，不改变单任务导出/读图500ms。
不同测试集合有重叠，不把104、106、48相加宣称独立覆盖数量。

最终专项、受影响回归、原生截图及最终完整CI的代码摘要相同：
`37d003370bfdb9104a53ef21d8239ac7bb851885a0044b2dc39465c74b61607c`。
受测 Git HEAD 是 B 提交 `f94597406e2ed743ae731fbdc8287250915da6fc` + C 批 dirty 工作内容；
完整逐文件SHA256、dirty清单、命令/退出码、前后code_stable=true均在每轮 evidence.json。
提交后不把这些 dirty 测试误称为在一个尚不存在的干净 SHA 上运行。

两次完整CI及组合回归崩溃位置都是 `tests/designer/test_visual_layout.py:84`，
pytest原生码3221225477，外层4294967295。P3曾用干净d1c49a9 archive重现同位置，原证据仍保留；
本轮未改该测试、删断言或加skip。归类为已知组合稳定性风险继续存在，不声称已修复。
新编辑专项及windows用户路径无剩余断言失败，但不能证明任意组合不存在新的原生问题。
当前专项无skip；完整CI崩溃没有最终pytest统计，未执行部分明确NOT_RUN，硬件不算通过。

### 用户路径、图片与预算证据

实际入口、操作步骤及支持表见 [P4 契约](../runtime-pages-p4-contract.md)。
主程序新增的入口是主工具栏“流程设计 / 页面设计”；两页配置由Qt操作产生，没有手改页面JSON。
最终截图及 [路径记录](../evidence/p4/c-accepted-visible/screens/path.json)：

- [拖入组件后](../evidence/p4/c-accepted-visible/screens/01-component-drop.png)
- [拖入节点输出并绑定后](../evidence/p4/c-accepted-visible/screens/02-output-drop.png)
- [保存另存、重开](../evidence/p4/c-accepted-visible/screens/03-saved-reopened.png)
- [真实总览](../evidence/p4/c-accepted-visible/screens/04-real-overview.png)
- [同结果详情](../evidence/p4/c-accepted-visible/screens/05-real-detail.png)
- [第二次明确调试，真实计数3](../evidence/p4/c-accepted-visible/screens/06-new-explicit-debug.png)
- [最终重开](../evidence/p4/c-accepted-visible/screens/07-final-reopen.png)
- [实际保存的两页配置](../evidence/p4/c-accepted-visible/screens/two-pages.json)

这是原生Qt窗口，以QTest点击、DragEnter/Drop事件及属性表单驱动；PNG由QWidget.grab生成，
不是生成图，也不冒称人工鼠标完整验收。分别从两幅实际本地图片产生Count=2和3，
两次均为明确启动的不同Job；每个Job的图像/计数关联同一resultKey，不冒充连续5Hz验收。
保存页面后修改流程再保存、另存/重新加载，presentation完全相同；输入资源摘要/内容随另存保留。
真实 typed loopback 读图/解码，两个页面共享一次读取；借用任务的Designer关闭后第二客户端和外部Runtime存活。

在一次低速小图结果上记录12次布局重载，耗时23.97—33.60ms，中位28.77ms；没有新Job/订阅/读图。
同机perf_counter_ns分开记录scopeEnd、decodeReady、guiCommit、paint；该次scope→GUI约43.68ms、
GUI→paint约2.39ms，仅是小图功能样例，不代替原P2成本或原1080p/5Hz门槛。
hub记录一个窗口、57600字节Qt图像、一份live/UI共同解码引用、一次转换、一次读图解码；
继续使用P3的24MiB图片缓存、8MiB转换scratch、16MiB单窗surface、有限冻结与原P2总预算。
控件销毁/重载先清引用，未扩大额度。短时这些数字不证明RSS或30分钟资源稳态。

| 本轮目标 | 状态 | 边界 |
|---|---|---|
| P4-A 单一状态、页面管理、保存/另存/撤销/退出检查 | PASS（最小子集） | 旧格式未进入页面时不升级，失败不清dirty |
| P4-B 拖组件/端口、类型检查、副本隔离、布局属性 | PASS（最小子集） | 调用路径明确，单作用域，不新增业务计算 |
| P4-C 内嵌预览、实际图数、重开与生命周期 | PASS（最小子集） | 原生windows用户路径、借用会话和迟到启动栅栏 |
| 当前专项新问题 | 已修复并复测 | 所有首轮失败与视觉FAIL保留 |
| 完整CI / Qt组合稳定性 | FAIL | test_visual_layout原生崩溃仍存在 |
| 原1080p/5Hz及P0/P2/P3性能目标 | 原FAIL保留；本轮NOT_RUN | 未放宽目标，也未做泛化P0重跑 |
| 长期资源稳态、工控机/冻结包/真实设备 | NOT_RUN | 继续阻塞稳定性和现场发布 |
| P4全能力/P5/发布 | 未完成 / 未进入 | 浮动编辑预览、完整动作编辑、多作用域与独立叠加不在本轮 |

结论：交付 P4 Designer **两页编辑最小闭环**，不将主计划三页全能力或P3整阶段改判通过。
对应正式模块、专项功能与真实窗口成立；组合稳定性/原性能/发布准入仍未通过。
停止于本轮，不进入P5、不制作现场包。
