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
- 原`observe(callback(result))`兼容，回调仍在后台；不得连到QWidget。
  `DisplayHub(session)`由GUI主线程创建，16ms有界拉取，零逐消息排队信号。
- `RuntimePages(presentation, hub=...)`配置驱动稳定pageId，按需最多实例化两页。
  source fieldPath已由服务端投影，UI不重复投影；capture摘要不匹配明确失效。
- 新封闭结果立即使前一图像/数值退出当前有效状态；loading与下一件执行中区分。
  连接失效无需等新图，来源缺失/跳过/失败、读图失败在对应控件呈现。
  COMPLETE仅表示执行/数据完整，绝非产品OK。
- `selectJob`提高generation并清状态；迟到decode丢弃。健康快照重建可恢复遗漏的最终结果。
  Qt提交所有控件之后才更新窗口displayed。详情/freeze取displayed，不读取后台latest。
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
