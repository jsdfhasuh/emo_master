# R3 使用能力扩展：源码验证记录

基于 `d5c7c2db488746691a2117d3e28d4bc41f253b7d`，2026-09-30。
用户已要求继续全部计划，并授权原开发分支上的远端开发、Git 同步和本地测试。
用户同时确认暂时没有实际工程样本。本批不因此把合成工程改称现场验收。

## 实现及支持边界

| 项目 | 本批源码能力 | 尚未证明 |
| --- | --- | --- |
| §5.1 组件使用 | 布尔/字符串判定输入及规范编码；已知集合字段选择；导航、显示结果详情、冻结、恢复；有限字体/卡片属性；合法空值与故障分开；带标识的离线状态模拟 | 实际工控屏幕、真实工程操作验收 |
| 窗口与图像预算 | 每窗仍预留 16 MiB，随实际屏幕/DPI变化重算边界；同一已验证图像资产别名只转一次 Qt 图片 | 整个进程 RSS 上界、任意屏幕/字体 |
| §5.2 普通运行 | 最多16个绑定 source ID/16个作用域；同一8 MiB Job原图缓冲支持单图8 MiB，或双图各4 MiB；别名复用；公开 profile，启动前协商 | 双1080p BGR不在此双图profile内；实际图像规格未取得；无隐式缩放 |
| 独立作用域 | 独立身份/开始和封闭水位；安静作用域不被其他作用域的历史周转挤掉；仍在8 MiB元数据总额度内；压力过期、旧客户端reset、迟到数据栅栏 | 跨工位产品追溯、不限作用域/历史 |
| 生命周期 | 图像 lane 独占、超时包含排队、整个缓冲隔离；原导出/读取500 ms期限和128/256 MiB预约额度保留 | 无限稳态、现场设备节拍 |
| §5.3 交付 | 原测试包/测试宿主保护保留 | 实际工程源码态验收缺失，因此未扩展通用交付、冻结包或发布 |

普通“开始运行”的两项新增 GUI 回归使用两张不同图片（原图/结果图）或两个不同调用作用域，
均为一个正常 Job；验证实际像素、别名共享、独立 invocation/resultKey、两页及协议额度。
隔离调试和测试交付仍保留其明确测试限制，不能把普通运行支持范围套入测试宿主。

## 代码接点

- 编辑：`page_designer/property_adapters.py`、`tools.py`、`editing.py`，沿用原事务与统一草稿
- 模型/显示：`core/presentation/models.py`、`ui/presentation/{renderer,hub,table}.py`
- 采集：`core/presentation/capture_limits.py`、`runtime/presentation/{normal_capture,collector,exporter,service,store,rpc}.py`
- 只读客户端：`clients/runtime/{display_session,pins,view_state}.py`
- 明确启动协商：原 `RuntimeController`、`RuntimeWorker`、`RuntimeClient`、MainWindow
- 观测工具：`scripts/r3_measure.py`、`r3_soak.py`、`r3_resources.py`

## 功能与正确性证据

以下分组数用于定位覆盖，不能相加冒充完整套件：

- 类型适配/模型116 PASS；最终组件UI42 PASS；用户路径/DPI/资产别名13 PASS
- 新普通 GUI 多来源路径2 PASS；原客户端/控制器/worker51 PASS
- 后端最终重点35 PASS，包含19项多来源、额度、迟到和旧客户端回归
- 曾出现后端 aggregate 469 PASS/1 FAIL：新的迟到快照保护错误阻止原 out-of-range cursor recovery。
  已把“已接受服务器游标”与“请求游标”分离；原失败及新栅栏用例均通过
- 独立审查曾发现安静scope被历史淘汰、旧读取者在reset后接受迟到过期值、DPI迁移预算、Qt别名重复转换，均有针对修正和测试
- 同一源码完整 `env -u PYTHONPATH QT_QPA_PLATFORM=offscreen python -m pytest -q`：
  **1394 PASS、1 SKIP，252.52秒**；唯一跳过是未授权/未启用的真实相机烟测

### Qt 间歇失败仍须单列

本批两次完整检查在原计数器对话框测试的 `processEvents` 处出现 native SIGSEGV。
不能因上述完整pytest成功就把这两次记录改判。

已定位其中一个确定机制：原独立 `FlowScene` 测试留下无父对象的scene/node循环，
不是MainWindow已修复的所属scene。强制下一次实际counter-worker线程GC时，22项前缀
复现SIGSEGV及10条跨线程停止timer警告。替换22处独立scene构造，明确GUI线程销毁后，
同一强制控制22 PASS、零该警告；原164个断言不变，并新增生命周期回归。

修正后仍记录过一次原位置aggregate崩溃；另有保留全套collection的前缀454 PASS、
带线程/销毁记录的core+designer733 PASS及上述全套pytest成功。
此时仍不能宣布全部Qt间歇问题已经解决；原始失败保留，继续针对回调/生命周期诊断。

## §7 观测工具及实际已运行范围

工具只使用固定离线输入、受控测试触发、临时数据、正式Runtime/Job及只读客户端。
不访问设备、不改生产目录/数据库；失败必须保留证据和未确认回收的目录。

`r3_measure.py` 默认保留原1080p/5Hz、96次/前8次预热、三组交替对照。
原P2/P3脚本未改。测量包装仅在受监督进程内启用，记录分段时间、全部分母、迟到、
解码/提交/原生paint、资源和缺失；trace/log均有额度，超额明确失效。
嵌套时间不可相加；仪表自身有开销，不替换历史基准结果。

已运行的只是4输入/1预热/1对照组的四臂诊断烟测：全部子进程退出0、source稳定、
无trace溢出、所有者收尾；每消费者及窗口3/3结果解码/应用/paint。
测得3个输入对应9次旧PNG编码；页面开启再增加3次overlay导出编码。
重复编码次数得到验证，不能据此认定它是全部性能回退根因。
短样本原始阈值比较仍为FAIL（捕获回退42.66%、Qt增量7.96%、采样数据年龄520.5ms）；
整体标 **NOT_ASSESSED**，未以小样本或offscreen宣称性能验收。

`r3_soak.py` 默认900秒、上限1800秒，一图/一scope、正常StartJob、两个只读session、
两个共享Qt窗口。记录进程RSS/native线程/句柄、采集额度、完整ordinal位图分母、
GUI提交/原生image paint；JSONL最多32 MiB。它是有界资源观测，不是任意时间稳态证明。
使用外部所属进程树watchdog；结束必须确认实际Job/IPC/导出所有者退休。

已运行5秒/25输入烟测：两个消费者各25/25 COMPLETE，两窗各25/25提交与paint，
无导出/监督器错误，全部所有者收尾。Linux日志含Fontconfig缓存目录警告，不能用此
环境的短烟测承诺屏幕/性能。**正式长窗口与Windows本批复测尚待执行**。

## 完成判断

功能实现、性能稳定、可交付范围、现场许可分别判断。已有源码/离线证据不能替代实际工程。
§5.3实际工程门槛未满足；旧性能FAIL保留；正式长稳态/本批Windows证据仍待取得。
因此当前不能称“整个计划完成”。

## 发布候选前的最后完整检查

同一源码再次执行 `env -u PYTHONPATH QT_QPA_PLATFORM=offscreen python scripts/ci_check.py`：
protobuf、Ruff、mypy（281源文件）、pytest全部通过；**1394 PASS、1 SKIP，253.34秒，退出0**。
这是继完整pytest成功后的另一次完整执行。前述native崩溃记录仍保留；候选可用于独立Windows验证，
并不宣布间歇Qt、长期稳定性或性能/现场验收完成。本批Windows及正式测量结果需按实际运行追加证据，
不能引用此前d5c7c2d的成功来替代新源码验证。

## 计数器对话框的独立所有权修正

后续精确探针证明：重复40次刷新后，原实现的40个已完成QThread仍为native-valid，
正常shutdown、删除dialog及主线程/后台GC都不回收。原lambda接收器还会在直接删除dialog后
进入已删除的控件。该缺陷可重复，但探针没有复现native SIGSEGV，不能把二者强行归因。

修正为明确QObject接收上下文和sender身份校验、GUI亲和线程的finished/deleteLater退休，
并为删除dialog后仍活跃的请求提供应用所属的停止/等待路径。保留原取消与等待语义；
不能取消的RPC仍可能延长应用退出，不以Future.cancel伪报线程已停。
修正后40个worker均在GUI线程native销毁，所有延迟投递/退出场景无回调异常或保留对象。
新增回归及重点套件10 PASS，独立审查通过。

此修正后的完整 `scripts/ci_check.py`：protobuf/Ruff/mypy PASS，pytest **1398 PASS、1 SKIP**，
256.70秒，退出0。此前已发布4d1f149在GitHub Ubuntu仍出现DeferredDelete处native崩溃；
该失败记录继续保留，后续提交的双平台CI/本机结果必须独立核对。
