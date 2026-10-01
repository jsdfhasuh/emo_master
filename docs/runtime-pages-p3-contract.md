# P3 原生 Qt 只读页面契约

这是显式开发入口的正式源码契约，不是现场发布批准。P0/P2 原失败、性能门槛、资源稳态
及 Qt 组合崩溃记录保持不变。运行要求 Python3.10 / PySide2 5.15；不替换 MainWindow。

## 入口与所有权

`python scripts/p3_demo.py --sample` 打开原生启动器；点击启动才创建临时项目/数据库/输出、
Runtime和一个真实Job。`--address HOST:PORT --job JOB_ID --project PATH/project.json`
只连接已有任务，不Prepare/Start/Stop/ReleaseJob。关闭观察窗口只detach；启动器保留会话，
直到启动器退出才在后台线程关闭session。只有样例模式清理自己创建的Runtime。
连接已有任务时，提供的项目必须与Job的capture来源摘要一致。

两页模板 `examples/p3_pages.json` 由 `examples/runtime_pages_p3.py` 填充正式来源和作用域。
两页共享一个image/count，详情另读同作用域的Blob集合（无第二路图像）。本地图像功能样例
约1Hz、小图；实际Blob/Count输出交替2/3。测试Selector仅选择已有输入，UI不伪造值。

## 公共接口与实时状态

- `DisplaySession.readSnapshot() -> SessionView` 同锁取得revision/generation/runtime/job、
  connection/detail、scopes、loading、started。ScopeView含ClosedResult、images、failures、readyNs。
  映射只读；图像基于不可写bytes。客户端保持无Qt依赖。
- `SessionView.job` 是可选的不可变执行状态观察。新服务端通过
  `job_status_v1` / `DisplayService.GetJob` 对 runtime instance、project、job
  三重身份做当前进程内的只读查询，不扫描持久历史任务。
  只有 `availability=AVAILABLE` 的七种已知执行状态可作为实时事实；读取失败、
  不支持该能力或身份失效均明确显示不可用，不沿用选择时的 RUNNING。
  执行终态与 `resourcesReleased` 分开显示，两者都不代表业务 OK/NG。
  未采集或已释放页面资源的任务可仅观察状态，不读取快照、订阅或图像。
  旧会话调用保持兼容，并明确没有已核验的实时执行状态。
- 原`observe(callback(result))`兼容，回调仍在后台；不得连到QWidget。
  `DisplayHub(session)`由GUI主线程创建，16ms有界拉取，零逐消息排队信号。
- `RuntimePages(presentation, hub=...)`配置驱动稳定pageId，按需最多实例化两页。
  source fieldPath已由服务端投影，UI不重复投影；capture摘要不匹配明确失效。
- 新封闭结果立即使前一图像/数值退出当前有效状态；loading与下一件执行中区分。
  连接失效无需等新图，来源缺失/跳过/失败、读图失败在对应控件呈现。
  COMPLETE仅表示执行/数据完整，绝非产品OK。
- `selectJob`提高generation并清状态；迟到decode丢弃。健康快照重建可恢复遗漏的最终结果。
  Qt提交所有控件之后才更新窗口displayed。详情/freeze取displayed，不读取后台latest。
- 窗口顶部的执行状态与客户端连接状态独立；冻结详情只冻结业务结果，
  执行状态仍随同一会话更新。状态变化纳入 Hub 拉取标记，无新图也可显示。
  普通 Designer 会话固定所选实例/工程/任务；运行实例变化需重新明确选择，
  不自动迁移到相同 jobId 的另一个实例。
- `session.pins().acquire(scope, generation, ttlMs)`在单worker异步AcquireLease；
  read返回PENDING/PINNED/FAILED/EXPIRED，release异步释放；最多30秒，不自动续租。
  到期清空冻结内容，不自动显示另一件。恢复实时明确放弃pin。
- detach、hide清UI引用；最后窗口停止hub定时器。DisplaySession.close可能等待后台收尾，
  启动器在后台执行，失败明确报告。借用Runtime的生命周期不归窗口管理。

## 组件范围

| 组件 | 正式支持 | 限制 |
|---|---|---|
| container | P1 grid/placement嵌套、滚动 | 无P4拖拽编辑 |
| image | 同一结果单源、GRAY/BGR/BGRA、自有QImage、算子已绘overlay | 未验证独立几何叠加；不猜坐标 |
| number | 正式冻结标量、decimals/unit、0与null区分 | 不推导计数，不从消息数累加 |
| text | 静态或绑定JSON值，false/null明确 | 显示超过4096字符明确截断 |
| indicator | indicatorStates中的JSON布尔/字符串键映射文字及固定四色 | 无默认OK；未映射明确提示；判定映射用标注模拟测试验证 |
| runtime_status | 无绑定时显示客户端公开连接状态 | runtime_status/global_counter业务来源仍不支持，明确不可用 |
| table | collection items或已投影list、列fieldPath、分页/排序 | 换resultKey重置，不查历史，不创建单元格Widget；缺列/非集合明确提示 |
| navigation_button | 稳定pageId、实时/已显示详情、freeze/resume | 无启动检测等设备动作 |

P1 Props兼容追加默认空indicatorStates/columns、pageSize=20（1—100）。映射最多16项，
仅canonical JSON布尔/字符串；颜色只能neutral/green/red/amber；列最多16、路径最多16级。
不改变presentation schemaVersion1.0，不在默认生产入口启用新格式。

## 资源与时钟

每DisplaySession沿用P2有界队列8、live解码16MiB、单图8MiB；2个pin另限16MiB。
窗口为详情保留的旧ScopeView解码引用单独预留16MiB/窗；stats按数组身份去重统计live与UI联合持有量，
并保守另计尚未释放的pin（可能重叠，不漏计取消清理中的pin）。两窗解码保留预留合计64MiB，
这是新增UI所有权的显式计账，不扩大P2的live16MiB或单图8MiB准入，不宣称总RSS验收通过。
每hub最多2窗口、共享Qt图片24MiB（包含控件仍持有的隐式共享副本），转换scratch8MiB。
每窗最多4个实例化table，每表保留行+排序索引/临时预留2MiB；隐藏页清表。
两窗表格额度16MiB，单次JSON解析临时对象另预留2MiB；源值仍受256KiB/4096项/16384节点限制。
单格显示最多1024字符并标注截断。每窗backing surface预留16MiB，高DPI限制最大尺寸。
QWidget/字体/驱动/Qt内部缓存及进程RSS不等于上述图片账本，长期稳态尚未验收。

GUI性能记录scopeEnd→decodeReady→guiCommit→首次paintEvent，绝不把计时起点改成解码完成。
paintEvent是绘制观测，不是显示器物理呈现。records最多256、首次paint去重128。
hub统计coalesced_results，性能分母保持全部88个预热后结果；读/导出500ms期限不改。
完整性、实际频率、年龄、执行回退与UI增量分别列出，低速截图不可替代5Hz验收。

## R3 §5.1 渲染补充（2026-09-30）

同一个 RuntimePages 支持上述受限外观属性；标题/值/故障标签强制纯文本，
不能借富文本图片或任意 QSS 绕过图像账本。判定色仍来自显式 bool/string 映射，
执行 COMPLETE 不推导 OK。字体使用系统/通用字族，无自定义字体下载。

`emptyText` 用于 AVAILABLE 的合法 null，以及合法空集合；空表保留 `0 行`，null表保留
`null` 标记。0、false、空字符串、空集合、缺失各自保留语义。
OPTIONAL_ABSENT、BRANCH_SKIPPED、NODE_FAILED、图片过期、断线等继续显示明确原因，
不被自定义空值文字覆盖；未绑定/等待触发也不会伪装成合法空结果。

离线 OK/NG/等待/错误通过同一渲染器检查外观，始终标注“离线模拟”；
OK/NG 只演示已配置绿色/红色映射，未配置则明示；图像区是标注占位，不伪造采集图片。
无 DisplaySession、Runtime、计数访问，不登记真实 displayed resultKey；无真实结果不能冻结详情。
连接实际任务时清除模拟状态。实际详情继续取 GUI 已提交的 scope/resultKey，不取后台最新值。

取消固定1600×1000尺寸假设。当前屏幕可用尺寸/宽高比及像素比例确定有界最大尺寸；
“适配当前屏幕”是本地视图操作。每窗口16 MiB backing surface预留保持不变，
`surfaceBytes()`/hub `window_surface_estimated_bytes` 按向上取整物理宽高×4记录估算值，
不等于整个Qt/驱动缓存或进程RSS。图像、转换、冻结、表格额度均不扩容。
实际现场屏幕/来源规格仍未取得；虚拟屏幕单元测试不是现场适配或性能通过证明。

## R3 A18 可见页面的本地图像需求

在显式 imageDemand=True 的 GUI 会话里，DisplayHub 在 GUI 线程汇总所有可见、实时窗口当前页的 image 绑定。
切页、重载、隐藏/显示、冻结/恢复、关闭窗口都更新同一拥有者的需求；最后一个窗口关闭或直接销毁即删除拥有者；hub 销毁也通过无 Qt 调用的清理撤销兴趣和有限 pin。
每个窗口生命周期只安装一个销毁回调，反复 detach/attach 仅更换弱拥有者令牌，不积累旧回调。
隐藏页面、只有数值的可见页面及全部窗口隐藏不会持续拉图/解码，但正式结果和元数据仍跟随同一个 Job。
一个窗口隐藏不会撤销另一窗口的来源兴趣；同会话的其他本地需求拥有者也独立保留。
同结果补图的 readyNs/imageStates 变化触发有界 GUI 拉取提交；可见未就绪图片显示普通文字“当前结果图像准备中”，
不把未请求图片当作执行失败。默认 headless DisplaySession 的持续解码契约不变，旧隐藏测试不删除/弱化。

冻结详情仍取实际 GUI displayed 的 ClosedResult。目标详情页尚未读取的同结果图片可在已有 finite-pin worker
取得有限租约后，送入会话原有队列/单解码线程补读；不读取当前 live 的另一件图片来补旧详情。
隐藏的冻结窗口暂停尚未开始的补图，最多允许正在进行的一个资产操作完成；再次显示保留原 ticket/resultKey/期限，
不自动续租。已经取得的图片和租约独立保留，另一个窗口可继续实时更新；过期/失败保持明确状态。

pin 仍最多两个、合计 16 MiB、最长 30 秒。缺图用现有 decoder 的压缩输入 scratch 读入并验证 PNG 头，
按 OpenCV 输出字节上界原子预留 pin 容量后才分配解码数组；不按每张一律 8 MiB 提前拒绝小图双 pin。
别名只计一次；预算不足明确 PIN_BUDGET；正在解码的取消/过期 pin 在实际退出前保留预算，之后释放租约与引用。
排队任务只持 ticket，不持窗口/回调闭包；关闭、取消、代际切换、到期均收回准入状态。全部旧图片、Qt 与 surface 预算不变。

这些是 A18 的客户端按需工作与正确性证据，不能替代原1080p/5Hz性能门槛、RSS稳态、Qt组合崩溃或现场验收。
