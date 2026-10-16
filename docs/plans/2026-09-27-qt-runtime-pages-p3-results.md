# P3 原生 Qt 运行页面执行记录

起始 HEAD `d1c49a982ec9fe474d9c34c0af68680b72214887`，分支
`agent/runtime-workflow-architecture-v1`，起始 clean；不 reset、不改 main。
按 2026-09-27 用户授权进入 P3；P0/P2 原 FAIL、5Hz/200ms/5%/500ms、资源稳态及 Qt 组合崩溃不改判。

环境：Windows x64，Python 3.10.21、PySide2 5.15.2.1，正式源代码；真实显示使用 windows 平台，
普通 Qt 专项使用 offscreen。每套 evidence.json 记录 HEAD、dirty、依赖、源码起止摘要和原始日志 SHA。
全部临时项目、数据库、图像与输出，无真实设备。原型只复用验证 watchdog；正式代码不 import prototypes。

## P3-A

新增 `ui/presentation/{renderer,images,hub}.py`，P1 配置驱动的 QWidget、QStackedWidget、
稳定 pageId 导航、容器/文字/数值/图像/按钮，最多两个按需页面与两个共享窗口。
新增 Qt-free `SessionView/ScopeView` 和 DisplaySession.readSnapshot；图数/错误/身份同锁读取，
字典只读，解码数组基于不可写 bytes。Qt 主线程16ms有界拉取，后台原 observe 不直接连 QWidget。

入口 `python scripts/p3_demo.py --sample`：先显示启动器，用户点击“启动本地图像示例”才启动。
真实 ImageLoader 两个稳定输入，由明确测试 Selector 交替选择，实际 Blob/Count 产生2/3变化；
Selector仅选择测试输入/低速调度，不在UI伪造值。窗口不会随首个结果自动退出。
已有任务：`python scripts/p3_demo.py --address 127.0.0.1:PORT --job JOB_ID --project PATH/project.json`，
仅连接，不Prepare/Start/Stop/ReleaseJob。启动器异步关闭自身session和自身拥有的测试服务。

页面配置在 `examples/p3_pages.json`，从正式项目来源/作用域补齐后按P1模型校验。
两页共享 image/count source ID；渲染器没有节点ID、固定计数或页面索引逻辑。
GRAY/BGR/BGRA、非连续stride、自有QImage copy及主线程断言均有测试。

证据：`docs/evidence/p3/a-fixed` 为7 passed，Ruff/mypy通过；首轮测试误用了当前PySide2缺少的
QTest.qWait（1 failed、6 passed），已换事件循环+短轮询并保留 `a-first`。
`a-visible` 实际 windows 平台、DPR1、两页图数变化、第二窗口共享、窄窗及键盘切页，退出确认成功；
overview/detail/narrow PNG是真实 QWidget.grab，非生成效果图。
`a-regression` 的 P1/P2 正式专项106 passed，未删改既有断言。

独立 `git archive d1c49a9` 完整CI基线仍原生访问冲突3221225477，见 `baseline`；不归因给新窗口。
后续批次及最终完整验收按实际执行追加，本阶段不据首批7项通过宣称整个P3完成。

## P3-B

从首批提交 `83dd9e5` 增量实现有限视图租约、GUI已提交结果详情、断线及换Job栅栏。
Qt-free PinStore 单worker、最多2个pin、16MiB、最长30秒不续租；Acquire/Release使用正式typed RPC，
不在GUI线程等网络。退出报告释放失败，服务端TTL仍为最终寿命上界。
详情取窗口displayed的resultKey；另一个窗口保持实时。租约到期清空旧图，不回落后台最新。
共享QImage缓存24MiB，计入控件仍持有的隐式共享图像；转换临时8MiB、窗口backing surface
每窗预留16MiB（两窗、高DPI尺寸限制），live解码与pin单独记录。此账本不替代RSS稳态验收。

提取P1纯captureDefinition复用，校验项目来源绑定摘要与结果capturePlanRevision；
布局变化可共享，重绑不把同名sourceId的旧结果误当新来源。隐藏页清图，重新显示取一致快照。
能力不兼容、断连、任务不存在即时失效；后台健康快照旧generation不覆盖换Job状态。

`b-first` 保留11 passed/1 failed（测试错误地按最后组件定位导航，新增冻结按钮后失效），
已按动作类型定位；`b-fixed` 14 passed、Ruff/mypy PASS。新增实际loopback/Job用例覆盖
初始晚观察者、只读数组、GUI N/后台N+1详情、有限租约及过期UI、断线重连、换Job、
迟到解码、绑定摘要不匹配、隐藏/销毁清理。故障门有自身3秒期限，整套外层300秒watchdog。

P3-B P1/P2回归：b-regression，106 passed，未修改既有断言。

## P3-C

第二批提交 `2e45f3b` 已正常推送。正式组件补齐判定映射、客户端连接状态和QAbstractTableModel
集合表格。默认兼容的P1 Props新增indicatorStates/columns/pageSize；颜色固定枚举，
无隐含业务OK规则。表格复用服务端已投影集合，支持items envelope及list，分页/排序只影响视图，
换resultKey清理旧行；最多4表/窗口、每表2MiB保守保留预算。真实详情页新增同scope的Blob集合，
仍只有一个图像来源。GRAY/BGR/BGRA转换只分配一个通道转换scratch后取得QImage自有副本。

`c-first` 26 passed；`c-final-ui` 30 passed；`c-ownership-ui` 31 passed，各轮Ruff/mypy均PASS。
逐次新增真实gRPC读超时/损坏资产、坏JSON、协议不兼容和借用Runtime启动器生命周期测试。
坏JSON使用1e999，不放宽P2非有限值拒绝。所有新增失败注入都有外层watchdog及finally清理。
三个窗口开关周期不StartJob，启动器关闭仅断开自己的会话，外部Runtime仍活着。

`c-visible` 使用实际windows插件，100%/125%/150%三组均PASS，分别记录DPR1/1.25/1.5，
overview/detail/frozen-table/narrow截图、中文、键盘导航、一个Job/两个窗口和shutdown_confirmed。
截图是QWidget.grab；冻结表格及150%窄窗已人工视图检查。窄窗按设计滚动显示底部表格/分页，
不会压缩为不可读文字。多屏移动没有执行，不用单屏截图代替。

### 可复现命令

仓库根目录使用 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`（下列简写python）。

```powershell
python scripts/p3_demo.py --sample
python scripts/p3_demo.py --address 127.0.0.1:PORT --job JOB_ID --project PATH/project.json
python scripts/p3_validate.py --suite ui --output docs/evidence/p3/NEW-ui
python scripts/p3_validate.py --suite visual --output docs/evidence/p3/NEW-visible
python scripts/p3_validate.py --suite measure --output docs/evidence/p3/NEW-measure --timeout 600
python scripts/p3_validate.py --suite broad --output docs/evidence/p3/NEW-regression --timeout 600
python scripts/p3_validate.py --suite ci --output docs/evidence/p3/NEW-ci --timeout 600
```

输出目录必须是新目录，保留历史原始结果。600秒仅是整套进程树watchdog，单任务导出/读图500ms不变。
正式模块不import prototypes；scripts仅沿用受监督测试执行工具。

### 性能证据口径

`scripts/p3_measure.py`，种子20260926，1920×1080 uint8 BGR，5Hz请求调度，96次、前8次预热、
三组交替顺序配对。两侧均保留P2捕获、两个客户端、旧PreviewSnapshotWriter，UI侧额外两窗共享第一个
客户端，一窗总览一窗详情。没有增加导出槽、改输入或单任务期限；为保持原P2捕获负载，测量不加Blob
表格来源（另有真实低速表格功能验证）。这是两页最小子集增量，5页50控件与0/1观看端矩阵NOT_RUN。

所有原始每件执行/调度、两个消费者接收解码、GUI提交/首次paintEvent、年龄与资源样本保留。
同机perf_counter_ns，主要延迟仍是scopeEnd→GUI提交；另列decodeReady→GUI与GUI→paint。
绘制观测不是显示器物理可见。两个窗口并排，不以后台模型就绪冒充画面绘制。

首轮 `c-measure` 原始FAIL保留：三个UI轮次每消费者及每窗口均88/88；实际4.36/4.45/4.76Hz；
两窗最差GUI提交P95分别196.83/191.28/163.33ms。调度最大迟到2610/2246/926ms，
包含终态收尾的数据年龄6149/6992/10866ms，原5Hz和500ms未通过。
首轮界面loading清空期间年龄有null、未覆盖最后0.5秒观察尾部；后续脚本补记上一已显示结果持续年龄、
逐样本可用性和最终观察尾部后重跑，不删除首轮、不改变起点或预算。

补正后的 `c-measure-age` 三组结果均FAIL，分母始终88：

| 配对组 | 两客户端解码 / 两窗口提交与paint | UI侧实际Hz | 两窗最差scope→GUI P95 ms | 最差ready→GUI P95 ms | 最差GUI→paint P95 ms | 相对P2执行P95回退 | 最大UI年龄ms |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | 各88/88 | 4.573 | 164.44 | 41.15 | 31.01 | +4.71% | 6977 |
| 1 | 各88/88 | 4.459 | 185.95 | 48.83 | 35.09 | -40.14% | 7556 |
| 2 | 各88/88 | 4.722 | 163.98 | 36.97 | 30.86 | -43.30% | 6275 |

负回退数反映本机轮次波动，不解释为Qt让检测加速；不挑最好轮。请求5Hz并未实现，
不能把较低实际频率下88/88和200ms子项通过作为原负载全部通过。年龄包含结果后的Job收尾，
不删除这段长尾。短窗Qt图像采样最高6220800字节，转换次数96、合并计数0；
实际转换中旧图与新图并存仍受24MiB准入检查，采样值不冒充瞬时峰值。
owner、Job、两个exporter的RSS/句柄/CPU及服务端磁盘资产、共享内存、元数据预留、租约均保留原始曲线；
固定额度和短窗曲线不是长期稳态PASS。

`c-broad`：core/runtime/e2e 462 passed、1 skipped（本机symlink不可用），proto drift PASS。
此skip单列，不算通过。未调整原测试断言、未屏蔽Qt原生崩溃。

`c-ci`完整CI本次运行1095 passed / 2 skipped，proto/Ruff/mypy均PASS。两项跳过分别是symlink
环境限制、未开启真实IMV相机smoke（本任务禁止真实设备）；不计为通过。
基线archive的Qt访问冲突发生于`tests/designer/test_visual_layout.py:84`，pytest原生码3221225477，
外层ci进程退出4294967295；本次组合成功不是崩溃修复证明，不修改P0/P2原记录。

性能测量之后的收尾小改动：第三页创建前先回收隐藏页，避免旧表格额度错误阻止新页；冻结视图的
平台连接状态始终取真实会话；无presentation打开空页；借用启动器文案不冒称本地图像样例。
补GUI旧ScopeView解码引用计账（live/UI按数组身份去重，尚未释放pin另保守计入），保持P2准入额度。
对应回归包括三页配额、pin过期仍显示真实CONNECTED、无页面及GUI停在N时后台N+1的联合保留量。
这些改动不在前述测量受测代码摘要中；测量证明其各自记录的代码状态，不声称最终代码已性能达标。

**最终完整CI `c-final-ci` 为FAIL**：proto/Ruff/mypy先通过；pytest在约45%处再次发生原生访问冲突，
仍为`tests/designer/test_visual_layout.py:84`，原生码3221225477、外层4294967295。该次未运行到的
后续测试为NOT_RUN；不能用前一轮1095 passed覆盖这个最终结果，也不能用分项成功替代组合通过。
源码摘要与dirty均记录，code_stable=true。未删测试、未改断言、未增加skip绕过崩溃。

最终页面专项 `c-accepted-ui`：32 passed、0 skipped；Ruff/mypy PASS，源码稳定。
这支持本批窗口/组件/生命周期功能结论，不替代最终组合CI的FAIL。没有剩余新增页面断言失败。
最终原生窗口复验 `c-accepted-visible`：100%/125%/150%三组全部PASS、退出确认，包含最终解码引用账本。
交付截图优先使用该目录的scale-1/overview.png、detail.png、frozen-table.png，以及各缩放的narrow.png。
最终CI、最终专项和最终截图的源码摘要一致：
`f172de91235d5cccfaff6c784cb53df7ea32359f7037f5f574a99cc194d2bab2`。
提交前核对全部40份原始日志SHA256与evidence及暂存Git blob一致；diff检查通过。

提交批次：A `83dd9e5`（渲染器/明确启动样例），B `2e45f3b`（视图租约/生命周期），
C为本报告所在提交（组件/测量/证据收口）。改动集中于正式`ui/presentation`、Qt-free
`clients/runtime`、P1 Props与capture摘要辅助、examples/scripts/tests和本计划/证据；
不改默认生产入口、main或真实设备路径。所有P0/P2历史证据仍保留。

### 验收边界

| 任务单项 | 本轮状态 | 证据/限制 |
|---|---|---|
| Q01 配置/布局/稳定页身份 | PASS | 两份布局与更换pageId；三页按需回收专项 |
| Q02 真实图数与结果身份 | PASS | 一个真实spawn Job、loopback typed gRPC、2/3变化，两页与第二窗口共享 |
| Q03 空页/未绑定/来源与值状态 | PASS（支持范围） | 0/false/null、缺失/跳过/超时显式；无支持的业务来源不伪造 |
| Q04 Qt线程及像素所有权 | PASS | 主线程断言，GRAY/BGR/BGRA、非连续stride、原数组改写/释放后pixelColor |
| Q05 初始结果/晚观察者 | PASS | 一致快照直接初始化，无需下一件 |
| Q06 迟到/失败/重连/换Job/销毁 | PASS（单scope） | 真实网络+有界decode门、绑定摘要校验、坏JSON失效 |
| Q07 切页/共享窗口 | PASS | 不增加Job/读图转换，窗口关闭不影响另一窗口；非新设备所有者 |
| Q08 显示N详情与有限冻结 | PASS | GUI暂停时后台N+1；另窗继续；租约到期/恢复/关闭释放；预算拒绝 |
| Q09 连接与读图错误 | PASS（已测故障） | 不依赖下一张成功图片；超时、损坏、协议不兼容、NOT_FOUND，健康空闲不伪报故障 |
| Q10 集合表格 | PASS | 真实Blob、模拟已投影list，分页/排序/空集合/换结果/额度 |
| Q11 缩放/窄窗/中文/键盘 | PASS（单显示器） | windows DPR1/1.25/1.5实际PNG；多屏移动NOT_RUN |
| Q12 生命周期 | PASS（本地受测路径） | 借用启动器退出不StopJob/不关闭外部Runtime，自己线程停止；无真实设备 |
| Q13 模拟/真实区分 | PASS | indicator外观测试标模拟；样例真实Blob/Count；UI不计算判定 |
| Q14 原性能完整验收 | FAIL | 原5Hz与年龄未过；完整矩阵、长稳态NOT_RUN，不以低速截图替代 |

组件支持表与公共API详见 `docs/runtime-pages-p3-contract.md`。未解决项目：

- **FAIL / 阻塞性能验收**：原5Hz节拍、500ms年龄；历史P2相对无展示基线的5%回退FAIL仍保留，
  本轮仅测P2已启用时的Qt增量，不抵消原P2成本。
- **FAIL / 未修复的已知稳定性风险**：既有Qt组合访问冲突。新窗口本轮未复现原生崩溃，不据此推断所有组合稳定。
- **NOT_RUN / 阻塞完整集成**：独立几何overlay可信链、多作用域详情（P2当前不支持混合作用域）、
  runtime_status/global_counter真实业务绑定、5页约50控件的0/1/2观看端完整矩阵。
- **NOT_RUN / 阻塞稳定性与发布**：不少于30分钟压力/资源稳态、极端总资源证明、多屏移动、
  Windows冻结包、目标工控机及真实设备。Windows原生源码窗口不等于冻结包验收。

四层结论：**共享Qt正式代码已实现；受支持单作用域真实双页功能集成通过；性能与组合稳定性未通过；
不允许现场发布**。本轮是P3正式最小子集，不笼统标P3全部完成。停止于此，不进入P4/P5。
