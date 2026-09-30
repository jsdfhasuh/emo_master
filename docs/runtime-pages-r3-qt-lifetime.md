# R3 后续 Qt 生命周期诊断（2026-09-30）

## 已交付候选保持冻结

v0补丁SHA256：`ceab857f8146e54d593cd36eb63e408eca65bf7c817783aaa7607ce4236c5cf0`。
所有以下诊断先在单独副本执行，测试串行运行，没有与性能测试或其他pytest进程并发。
此记录不是计划整体完成、Windows、长期稳态或现场设备批准。

## 缩小问题与证据

1. v0新增四文件顺序（current_job_viewing、capture_coverage、pending_inputs、normal_run_viewing）
   串行一次21项通过；不能抹除之前的原生崩溃。
2. v0整个Designer单跑355项通过，1项布局失败；core+Designer组合再次原生SIGSEGV，
   位置为全局计数对话框的`processEvents`。崩溃位置受先前执行顺序影响，不能只修最后一个测试。
3. 只记录的Qt消息/GC/场景销毁探针确认：MainWindow创建的FlowScene没有QObject父对象，
   QGraphicsView不拥有它；场景、图元和窗口回调形成Python引用环。窗口原生对象销毁后场景仍存在。
   所有权规则见[Qt 5.15 setScene文档](https://doc.qt.io/archives/qt-5.15/qgraphicsview.html#setScene)。
   9个场景包装器随后在Dummy线程的循环GC中被终结，稍后处理事件时发生原生崩溃。
4. 独立最小复现连续5次：创建窗口→正常关闭→GUI DeferredDelete→后台线程`gc.collect()`→GUI处理事件。
   未修正时5次都报告`QObject::~QObject: Timers cannot be stopped from another thread`。
   这是可重复的跨线程Qt生命周期错误，不能把线程栈中睡眠的icon worker当作直接根因。
5. 最小修正仅在原生MainWindow创建FlowScene后设置`scene.setParent(window)`。
   相同5次复现不再出现跨线程定时器销毁警告；保留Python引用时场景已随窗口在GUI销毁路径失效。
   诊断记录中的destroyed回调可能是排队投递，因此不单靠回调所在线程推断未修正原生析构线程。
6. 新`testWindowOwnsSceneAndDestroysItOnGuiThreadBeforeWorkerGc`在修正前稳定断言失败，
   修正后通过：验证父对象、GUI DeferredDelete后的原生失效、仅一次销毁通知，以及随后后台GC不再触发Qt销毁。
7. 带探针的同一core+Designer组合修正后走过566项，无原生崩溃，停在已存在的布局断言。

## 布局失败的独立对照

`testLongDetailsDoNotForceWindowBeyondViewport`断言最小高度<500，本环境实际503。
未修改5659c61的同一用例也失败。逐控件对照显示原窗口、工具栏、主分割区和内容高度完全一致；
页面入口新增按钮改变宽度，不改变该503高度。因此未通过降低阈值或调整字体来制造通过，
该项继续FAIL，不将其错误归类为本批新增页面入口回归。

## 修正范围与未证明范围

修正场景所属关系，不更换线程框架、不关闭全局GC、不跳过原有断言、不改变运行/保存协议。
独立代码复核确认该场景只属于一个MainWindow、创建于GUI线程，且保留非原生适配分支。
独立使用的FlowScene、拒绝关闭后被强制deleteLater的测试清理等仍是不同生命周期场景。
不能据此宣布每个历史Qt错误都已解释；后续组合与全套结果应单独报告，Windows和长稳态仍未测。

## 完整验证（v1诊断副本）

- PASS：新增场景所有权回归，修正前1 FAIL，修正后1 PASS。
- PASS：Ruff、mypy（279源文件）。
- 完整pytest已实际运行到结束：1221 PASS、1 SKIP、1 FAIL，223.84秒；本次没有SIGSEGV。
- 唯一FAIL仍是已在未修改基线复现的503<500布局断言，未改代码布局或测试门槛规避。
- core+Designer+页面/展示组合连续串行3轮：每轮681 PASS、1 FAIL（仍是同一布局断言），
  耗时96.16 / 92.26 / 94.79秒，三轮均无SIGSEGV；没有跳过或放宽断言。
- 历史崩溃原始日志、修正前后复现脚本/探针与失败记录均保留。

以上结果证明确定的场景所有权缺陷得到针对性修正，并拓展了组合覆盖；不能替代Windows、
实际用户工程、长时间运行和性能/资源稳态验收，不把所有历史Qt问题自动合并为一个已修结论。

## v2：剩余短窗口布局约束修正

继续诊断而非停在基线失败：字体实际为Noto Sans CJK SC，并非缺失字体回退。
100DPI下各部分原生最小高度为摘要127、详情滚动区被强制72、预览149、间隙16，
合计右侧364；加内容边距和工具栏/菜单/状态栏后为503。详情QScrollArea自身的可用最小高度是58，
但MainWindow另外写死72，阻止已经支持滚动的区域继续缩小。

最小修正删除这一条72px覆盖，交回Qt滚动区域的原生最小尺寸，未更改字体、工具栏间距、
摘要/预览内容或测试阈值。新建窗口实测1000×499，窗口最小616×489，摘要127、详情68、
预览149、预览图区域90。80行原文本完整保留；滚动条范围3702，最后可视行在视口31..53px内，
视口68px高。较早的内容通过普通垂直滚动访问，不承诺超长不间断字符串的全部字符同时可见。

- 原`testLongDetailsDoNotForceWindowBeyondViewport`断言保持原样，修正后通过。
- 新`testShortWindowKeepsSummaryPreviewAndEveryDetailLineAccessible`先等初始目录刷新，
  再填入80行数据，验证实际499px窗口、字体/文本保留、摘要/预览完整和最后一行可滚动到达。
- 两项聚焦测试通过；74项相关视觉/主窗口/侧栏/画布回归通过。
- 新窗口顶部和底部截图及字体/几何探针输出保留在交付日志中；原503px诊断截图不当作499px验收图。
- 此窄布局修正已经独立复核，未通过缩小字体、裁掉内容或降低断言来消除失败。


## v2完整聚合结果

完整`python scripts/ci_check.py`四阶段全部通过：protobuf、Ruff、mypy、pytest。
pytest为1223 PASS、1 SKIP，222.06秒，退出0；跳过真实相机烟测，未触达设备。
这一结论取代“当前源码仍有503布局FAIL”的状态，但保留v0/v1失败和修正前后证据。
不延伸为Windows、现场工程、性能或长期稳态已验收。
