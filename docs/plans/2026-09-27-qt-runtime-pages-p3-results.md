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
