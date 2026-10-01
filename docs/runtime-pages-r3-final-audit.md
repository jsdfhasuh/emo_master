# Qt 运行页面 R3 计划逐条完成审计

审计日期：2026-10-01 UTC

代码基准：`aad053fce2bcaa051313c4fea55365e030812cb3`

分支：`agent/runtime-workflow-architecture-v1`

## 审计结论

**R3 的正常 Designer 源码功能路径已形成可用闭环；整个计划仍不能标为完成。** 当前可以在正常主程序中设计页面、绑定已安装算子的受支持输出、明确启动一个正常 Job、观看同一 Job 的多页结果，并弹出共享会话的只读窗口。实时任务执行状态也已实现，和连接状态、产品 OK/NG 分开。此前发现的未知输出阻断编辑器、深层调用目录遗漏、切页后编辑错误页面，均已有实现修正和针对回归。

尚未完成的是：当前 Windows 普通 CI 中的 A18 原生线程增长与首次页面结果等待失败；原 1080p/5 Hz 性能验收；既有间歇任务终态延迟的充分解释；用户实际工程与目标屏幕/节拍验收；以实际工程源码态验收为前提的通用交付、冻结包和现场验收。**aad053f 的普通 CI 不是全绿：两项 Ubuntu 通过，push Windows 有1项FAIL，PR Windows 有3项FAIL。** 3332652历史四项通过和900秒小图成功均保留，不能覆盖当前失败。

入口、绑定、运行、显示和保存功能的逐条核对没有发现新的确定功能遗漏；当前稳定性缺口仍明确存在。aad053f仅修复了Supervisor持锁时等待bridge退出这一已确定机制，并增加5项确定性回归，没有扩展功能范围。终态资产提交仍在Supervisor锁内的机制已有受控实验，但两阶段改造尚有设计阻塞且未实施；二者不混称为Windows各项失败的共同根因。

## 审计范围与证据口径

- 逐条对照 [R3 主计划](plans/2026-09-25-qt-runtime-pages-v1.md) 全文，读取 [追加结果报告](runtime-pages-r3-expanded-results.md) 的历史与后续记录，并检查现行源码及对应回归断言
- 审计开始时仓库 HEAD 为上述版本，工作区干净；本轮仅编写此审计文档，不改生产代码或测试，不运行测试或性能负载，不访问用户 Windows 电脑，不操作硬件
- “已实现”指现行代码有相应真实入口与行为；“有验证证据”指已有针对回归及记录，不表示本版本所有环境均通过。当前精确版本的PASS/FAIL在第7节逐项列出。合成工程和替身测试均不作为用户实际工程验收
- 下面的源码链接相对此文档所在的docs目录，正文行号对应代码基准；即使旧报告较早段落写有“待执行”或曾经通过，也以后续明确版本的结果为准。此次文档提交不产生新的代码测试结论
- 用户已确认目前没有实际工程；本审计记录依赖，不再次要求提供，也不据此扩展冻结包或现场操作

## 用户现在可以实际操作的路径

1. 正常启动 Designer，打开已有工程，从工具栏进入“页面设计”。旧项目需明确同意启用页面，保存后才升级为 2.2
2. 创建页面和组件，从当前工程输出树选择节点输出；树中显示流程、节点友好名称及调用位置。未知类型有不可绑定原因，不会使整个编辑器失效
3. 保存后用原“开始运行”启动。页面采集作为同一个正常 Job 的可选部分冻结，保留正常数据库、计数和输出路径语义
4. 点击“观看当前工程任务”，选择当前工程已有 Job；已采集结果可读，新绑定不会改变正在运行的采集计划
5. 点击“弹出只读观察窗口”。内嵌页面与弹窗共享同一 DisplaySession/DisplayHub，合计最多两个窗口；分页、冻结和恢复均不创建新 Job
6. 明确停止并确认资源退休后再次运行；关闭只读弹窗只解除该窗口观察。正常任务和隔离草稿调试仍有各自明确语义

这是 Designer 所属的原生顶层只读窗口，**不是已经完成的通用独立部署运行端**。独立 `operator_view` 仍属于受限测试交付入口。

## 计划第 0 至 3 节

| 条款 | 当前判断 | 代码或证据 |
| --- | --- | --- |
| §0 保留既有架构，优先正常工程运行与观看 | 已遵循。沿用 Qt Widgets、Runtime、JobManager、PresentationService、DisplaySession；没有另造执行器 | [正常启动控制器](../src/emo_master/apps/designer/controllers/runtime_controller.py) 第64行；[附着正常 Job](../src/emo_master/apps/runtime/presentation/service.py) 第108行 |
| §1 页面作为既有工程显示层 | 受支持范围内已实现；功能完成不等于性能、通用交付、现场验收完成 | [普通 GUI 运行回归](../tests/ui/page_designer/test_normal_run_path.py) 第19行 |
| §2 默认入口与页面入口分离 | 已修复，原生 Designer 默认创建 PageCoordinator，不依赖 demo 环境开关 | [MainWindow](../src/emo_master/apps/designer/ui/main_window.py) 第1363行 |
| §2 正常运行未提供页面采集 | 已修复，原 StartJob 可携带采集请求，冻结当前绑定 | [MainWindow](../src/emo_master/apps/designer/ui/main_window.py) 第1369行；[RuntimeService](../src/emo_master/apps/runtime/grpc_server/service.py) 第352行 |
| §2 四算子白名单被当成通用能力 | 普通运行已使用安装的可信注册信息；隔离调试与测试包白名单保留 | [freezeNormalCapture](../src/emo_master/apps/runtime/presentation/normal_capture.py) 第24行；[隔离调试边界](../src/emo_master/apps/designer/page_designer/preview.py) 第19行 |
| §2 单作用域/单图全局限制 | 普通路径已扩展为有界多作用域/双图，前后端及能力协商同步 | [采集 profile](../src/emo_master/core/presentation/capture_limits.py) 第6行；[协商](../src/emo_master/apps/designer/services/runtime_client.py) 第724行 |
| §2 停止后再次运行 | 普通路径已实现；测试宿主的一次启动保护按 §5.3 保留 | [再次运行前释放](../src/emo_master/apps/designer/services/runtime_worker.py) 第374行；[测试宿主](../src/emo_master/apps/runtime/release_host.py) 第19行 |
| §2 属性输入与固定外观限制 | 类型化输入、字段选择、动作、有限外观及屏幕适配已实现 | 见本报告 §5.1 |
| §2 新旧图像采集无条件叠加 | 已有下一次运行 ALL/NONE 选择；NONE 不关闭页面采集、正式事件或业务计数 | [工具栏](../src/emo_master/apps/designer/ui/main_window.py) 第1063行；[worker 配置](../src/emo_master/apps/runtime/jobs/worker_main.py) 第71行 |
| §2 演示图像预算被当作现场要求 | 已公开有界配置及超额提示；现场规格尚缺，未自动缩图或擅自扩容 | [预算与 UI 提示](../src/emo_master/apps/designer/page_designer/tools.py) 第233行 |
| §3 实际工程副本与需求记录 | 未完成，真实工程、图像规格、屏幕、节拍及设备边界尚未取得 | [计划 §3](plans/2026-09-25-qt-runtime-pages-v1.md) 第50–56行；合成/离线证据继续单列 |

## 计划第 4 节正常运行与两页观看

| 编号 | 实现与验证判断 | 主要依据 |
| --- | --- | --- |
| 4.1 正常入口与显式升级 | 已实现，有回归证据。创建协调器不改项目元数据；启用页面需明确确认；未启用工程保留旧格式 | [coordinator.py](../src/emo_master/apps/designer/page_designer/coordinator.py) 第20、65行；[入口回归](../tests/ui/page_designer/test_current_job_viewing.py) 第121行 |
| 4.2 实例输出树与直接绑定 | 已实现，有回归证据。使用稳定 ID 定位，主标签显示友好名称及流程/调用位置；未知合法类型显示拒绝原因；调用路径上限与模型的32层一致 | [editing.py](../src/emo_master/apps/designer/page_designer/editing.py) 第32行；[tools.py](../src/emo_master/apps/designer/page_designer/tools.py) 第174行；[目录/绑定回归](../tests/ui/page_designer/test_editing.py) |
| 4.3 一个明确正常 Job 与原语义 | 已实现，有真实 Runtime/GUI 回归。原控制器使用同一 Job，采集附着到已创建 Job；正常计数/输出与隔离调试不混用 | [RuntimeService](../src/emo_master/apps/runtime/grpc_server/service.py) 第381行；[attachNormal](../src/emo_master/apps/runtime/presentation/service.py) 第108行；[正常图数判定回归](../tests/runtime/presentation/test_normal_capture.py) 第216行 |
| 4.4 当前工程明确选 Job 与能力协商 | 已实现，有回归证据。先读能力，按 projectId 筛选，多任务显示状态和模式；不取全局最后一个任务，不因观看创建设备 Runtime | [watchCurrent / _chooseJob](../src/emo_master/apps/designer/page_designer/preview.py) 第242、270行；[现有端点约束](../src/emo_master/apps/designer/services/runtime_client.py) 第660行；[选择反例](../tests/ui/page_designer/test_current_job_viewing.py) 第136–225行 |
| 4.5 旧任务不能凭空补来源 | 已实现，有回归证据。CaptureCoverage 比对冻结身份，缺失来源显示原因；未采集/已释放任务在具备相应服务时只读执行状态，不读取或重建页面结果 | [coverage.py](../src/emo_master/core/presentation/coverage.py)；[attach / refreshCoverage](../src/emo_master/apps/designer/page_designer/preview.py) 第301、400行；[覆盖回归](../tests/ui/presentation/test_capture_coverage.py)；[状态-only真实 RPC](../tests/runtime/presentation/test_live_job_status.py) 第398行 |
| 4.6 同次图数判定与多观察者 | 已实现，有真实正常 Job 回归。图像/标量/业务判定使用同一 resultKey；两作用域有独立身份；popup共享session/hub，不重新采集。执行状态单独显示，不以 COMPLETE 推导 OK | [普通两页回归](../tests/ui/page_designer/test_normal_run_path.py)；[真实双作用域与popup](../tests/ui/page_designer/test_retained_acceptance_multiscope.py) 第144行；[job_status.py](../src/emo_master/ui/presentation/job_status.py) |
| 4.7 停止再运行与不确定 Start | 已实现，有成功/失败反例。Start 使用保留的请求ID核实；超时只查询同一次请求；再运行前检查终态、所属worker/IPC/export退休；关闭观察者不调用外部Stop | [请求账本](../src/emo_master/apps/runtime/grpc_server/service.py) 第251行；[核实与释放](../src/emo_master/apps/designer/services/runtime_worker.py) 第345、374行；[release](../src/emo_master/apps/runtime/presentation/service.py) 第267行；[失联启动/停止再启回归](../tests/runtime/presentation/test_normal_capture.py) 第285、321行 |
| 4.8 统一保存、草稿与拒绝原因 | 已实现，有回归证据。页面和流程同一事务/历史；保存前提交可见字段，非法输入阻止操作并保留；顶栏/侧栏/键盘/预览返回统一页面身份。保存失败保留旧项目文件 | [ProjectEditSession](../src/emo_master/apps/designer/state/project_edit_session.py) 第90行；[原子保存](../src/emo_master/apps/designer/state/project_store.py) 第132行；[统一导航](../src/emo_master/apps/designer/page_designer/workspace.py) 第147行；[导航回归](../tests/ui/page_designer/test_page_navigation.py)、[保存回归](../tests/ui/page_designer/test_workspace.py)、[未应用字段回归](../tests/ui/page_designer/test_pending_inputs.py) |

第4节的**合成工程源码闭环有证据**。该节所要求的用户实际工程副本验收仍未发生，不将上述结果改称真实项目验收。

## 计划第 5 节使用能力和交付

### 5.1 六项组件使用能力

| 要求 | 当前实现 | 验证范围 |
| --- | --- | --- |
| 类型化 OK/NG 映射 | boolean/string 类型、值、显示文字、颜色独立输入；界面生成规范 JSON；false、空字符串不被真值判断丢弃 | [property_adapters.py](../src/emo_master/apps/designer/page_designer/property_adapters.py) 第15–65行；[属性回归](../tests/ui/page_designer/test_usable_properties.py) 第15、35行 |
| 已知字段选择及友好树 | Blob/Detection 静态字段选择；集合投影不二次套用路径；未知结构明确不自动推断；树显示流程/调用位置/节点名称 | [字段适配](../src/emo_master/apps/designer/page_designer/property_adapters.py) 第68行；[属性回归](../tests/ui/page_designer/test_usable_properties.py) 第47行 |
| 导航、详情、冻结、恢复 | 使用既有只读 Action；详情锁定 GUI 已提交结果。两个窗口可分别锁定两个作用域的旧结果；后台新结果不能替换冻结内容 | [actionFromFields](../src/emo_master/apps/designer/page_designer/property_adapters.py) 第98行；[renderer.act](../src/emo_master/ui/presentation/renderer.py) 第387行；[真实双scope冻结回归](../tests/ui/page_designer/test_retained_acceptance_multiscope.py) 第182–234行 |
| 字体、字号、卡片与屏幕适配 | 默认值兼容；字体为有界选项，字号自动或8–48；窗口按当前屏幕/DPI及16 MiB表面预约限制适配，不再固定1600×1000 | [Props](../src/emo_master/core/presentation/models.py) 第81行；[surfaceBounds / fitToAvailableScreen](../src/emo_master/ui/presentation/renderer.py) 第29、167行；[显示回归](../tests/ui/presentation/test_usable_components.py) 第54、62、104行。现场屏幕效果未验收 |
| emptyText 与故障区分 | 合法 null / 空集合采用自定义空值文字，表格保留 null / 0行区别；零、false独立；缺失、错误、断线、过期保留错误提示 | [renderer.submit](../src/emo_master/ui/presentation/renderer.py) 第474行；[CollectionView.submit](../src/emo_master/ui/presentation/table.py) 第134行；[null/空值反例](../tests/ui/presentation/test_usable_components.py) 第17、40行 |
| 离线状态模拟 | OK/NG/等待/错误带模拟标识；没有真实执行状态；不访问 Runtime/设备/计数 | [setSimulationState](../src/emo_master/ui/presentation/renderer.py) 第201行；[模拟边界回归](../tests/ui/page_designer/test_retained_acceptance_multiscope.py) 第251行；[执行状态模拟回归](../tests/ui/presentation/test_job_status_rendering.py) 第104行 |

未知组件属性仍由严格模型拒绝，不能静默丢弃；见 [models.py](../src/emo_master/core/presentation/models.py) 第15行。

### 5.2 算子、作用域和多图

| 要求 | 当前判断与明确边界 | 依据 |
| --- | --- | --- |
| 区分可列出、可绑定、可执行/采集、可交付与设备许可 | 普通采集读取已安装可信插件的真实端口契约，不使用测试四算子表。列出的类型仍需组件兼容和采集范围校验；未知类型不增加适配 | [catalog.py](../src/emo_master/core/presentation/catalog.py)、[normal_capture.py](../src/emo_master/apps/runtime/presentation/normal_capture.py) |
| 两张不同图，原图/结果图 | 已实现并验证像素及来源；最多两个不同图像来源。单图最多8 MiB，双图每路4 MiB、Job原图共享总额8 MiB，不隐式缩放 | [capture_limits.py](../src/emo_master/core/presentation/capture_limits.py) 第6行；[实际采集限制](../src/emo_master/apps/runtime/presentation/collector.py) 第113行；[双图回归](../tests/runtime/presentation/test_multi_capture.py) 第99、185、213行 |
| 两页/两调用作用域 | 已实现，有独立 invocationId/resultKey/水位。安静scope不被活跃scope历史周转挤掉；某来源失败不会拼接另一scope旧值 | [结果存储](../src/emo_master/apps/runtime/presentation/store.py)；[多作用域回归](../tests/runtime/presentation/test_multi_capture.py) 第126、255、421、480行 |
| 两个明确运行任务 | 后端有界支持与回归：最多两个占用采集资源的Job，终态需明确释放才重新准入；Designer普通控制器仍一次拥有一个正常运行任务 | [服务准入](../src/emo_master/apps/runtime/presentation/service.py) 第99行；[双Job回归](../tests/runtime/presentation/test_multi_capture.py) 第143行。没有需求依据扩展成无限任务或在同窗混看不同产品 |
| 同来源复用与按需图像 | 来源别名只复制/编码一次；同会话共享解码/Qt图片。GUI按可见页面汇总图像需求；隐藏时仍推进正式元数据，恢复时读取同Job已采集结果。当前PR Windows三项首次结果等待FAIL另见第7节，不能称全场景稳定通过 | [collector.output](../src/emo_master/apps/runtime/presentation/collector.py) 第79行；[DisplayHub](../src/emo_master/ui/presentation/hub.py) 第95、111行；[图像需求回归](../tests/runtime/presentation/test_image_demand_client.py)、[窗口需求回归](../tests/ui/presentation/test_image_demand_views.py) |
| 大图、预览副本、几何来源 | 原算法图像不改；当前只有原尺寸有界传递，没有通用自动缩图/另取原图的产品工作流。计划要求先有实际规格再决定，故该项是需求依赖，不是已交付的任意大图能力。未知来源几何不按尺寸猜测 | [collector.image](../src/emo_master/apps/runtime/presentation/collector.py) 第113行；[provenance.py](../src/emo_master/apps/runtime/presentation/provenance.py)；[来源反例](../tests/runtime/presentation/test_images.py) 第203行 |

两张1080p BGR原图每张约5.93 MiB，超过现有双图每路4 MiB边界；不能因为单图1080p功能回归或小图双来源通过，宣称双1080p已经支持。当前公开范围另有最多16个绑定source ID、16个scope、8个同时未完成结果；见同一采集profile。

### 5.3 通用交付

**门槛未满足，尚未扩展。** `TestReleaseChannel` 仍只接受显式启用的测试release，保留一次启动保护；`operator_view` 必须读取 `emo-test-host-1` 描述和测试交付仓；测试包保留四算子、版本、资源与路径校验。

依据：[release_host.py](../src/emo_master/apps/runtime/release_host.py) 第19–36行；[operator_view/main.py](../src/emo_master/apps/operator_view/main.py) 第63–81行；[test_delivery.py](../src/emo_master/core/project/test_delivery.py) 第17–27行。

普通工程重复运行已由第4节路径验证，不需要删除测试宿主保护来冒充完成。通用独立运行端/交付范围、冻结EXE及发布，需同一实际工程先完成源码态正常运行和观看，再据其插件/资源/设备要求决定。当前不把这些未完成事项从整个计划里删掉，也不越过门槛预先发布。

## 计划第 6 节正确性和兼容约束

| 约束 | 核对结果与对应验证 |
| --- | --- |
| 单一执行/设备拥有者；只读路径不启动 | 正常采集附着原Job；popup复用现有hub/session；观看/模拟测试在Start/Stop/相机入口和计数写入处设失败守卫。[真实只读反例](../tests/ui/page_designer/test_retained_acceptance_multiscope.py) 第251行；本次没有设备验收 |
| 输出路由前复制；结果身份与游标分离 | worker在routing前采集自有值，图像复制进有限共享buffer；客户端使用开始水位、封闭结果身份和游标栅栏，防旧结果覆盖。[runner.py](../src/emo_master/apps/runtime/workflow/runner.py) 第169行；[collector.py](../src/emo_master/apps/runtime/presentation/collector.py) 第138行；[multi_capture反例](../tests/runtime/presentation/test_multi_capture.py) |
| 非阻塞、有界、超额失效和真实退休 | 原图lane采用非阻塞acquire，忙时明确BUDGET_EXCEEDED；封口mailbox满后保留有界pending seal，不扩展队列；只有parent返还credit；资源仍归实际worker/export所有。[collector.py](../src/emo_master/apps/runtime/presentation/collector.py) 第58、131、168行；[mailbox恢复回归](../tests/runtime/presentation/test_capture_mailbox_recovery.py)；[release](../src/emo_master/apps/runtime/presentation/service.py) 第267行 |
| 断线、换代、缺口、过期可识别 | 现行session对Runtime/项目/Job及连接epoch校验；状态失败显示不可用，旧RUNNING不会保留为“实时”；旧服务器能力不足明示。[display_session.py](../src/emo_master/clients/runtime/display_session.py) 第252、295、494、568行；[live_job_status回归](../tests/runtime/presentation/test_live_job_status.py) |
| GUI线程、冻结、图片预算与关闭 | Qt更新要求GUI线程；hub最多两窗；冻结最多两pin、总16 MiB、最长30秒，关闭/过期/取消不提前返还未退出工作的预算。以下为实现与针对回归依据，当前A18及首次结果等待FAIL仍单列。[hub.py](../src/emo_master/ui/presentation/hub.py)、[pins.py](../src/emo_master/clients/runtime/pins.py)；[Qt拥有者回归](../tests/designer/test_runtime_qt_ownership.py)、[冻结/多窗口回归](../tests/ui/presentation/test_session_views.py) |
| 草稿/正常/隔离调试/release语义与原子保存 | 既有正常计数/输出保留；调试快照和测试release分离；项目JSON写临时文件后原子替换，失败保留旧版；测试包不任意加载导入代码。[normal_capture.py](../src/emo_master/apps/runtime/presentation/normal_capture.py)、[project_store.py](../src/emo_master/apps/designer/state/project_store.py) 第132行；[快照隔离回归](../tests/core/presentation/test_snapshots.py)、[交付仓回归](../tests/core/project/test_delivery_store.py) |

这些是实现与回归证据，不是“任何时间、任何第三方插件和任何设备均无风险”的证明。原A/B约束继续保留；历史native失败没有被单次完整成功覆盖。

## 计划第 7 节性能和稳定性

### 当前代码基准的精确检查结果

代码树为 `b32fdfa5062c70933ff10e656692afae7e9941fb`。push实际检出 `aad053fce2bcaa051313c4fea55365e030812cb3`；PR实际检出合并提交 `ed8af73221f7eebcc5a06095948301563bebe331`，将aad053f合入 `7f141ce627da39d9e174e4a79344fc468607ebef`。已核验PR合并树与aad053f树相同；不能把PR的提交SHA写成aad053f。

| 检查 | 实际结果 | 证据与范围 |
| --- | --- | --- |
| cloud完整检查 | **PASS：2256 PASS、4 SKIP、27 subtest，427.37秒**；protobuf、Ruff、mypy通过，源码前后摘要相同 | aad053f对应代码树；本轮文档审计没有重新执行检查 |
| push Ubuntu | **PASS：2256 PASS、4 SKIP、27 subtest，459.52秒** | [push Ubuntu原检查](https://github.com/jsdfhasuh/emo_master/actions/runs/36874302447/job/110409666291) |
| PR Ubuntu | **PASS：2256 PASS、4 SKIP、27 subtest，377.30秒** | [PR Ubuntu原检查](https://github.com/jsdfhasuh/emo_master/actions/runs/36874309302/job/110409687013) |
| push Windows | **FAIL：2257 PASS、1 FAIL、2 SKIP、27 subtest，669.25秒** | [push Windows原检查](https://github.com/jsdfhasuh/emo_master/actions/runs/36874302447/job/110409666856)；唯一失败为A18原生线程身份断言 |
| PR Windows | **FAIL：2255 PASS、3 FAIL、2 SKIP、27 subtest，1088.25秒** | [PR Windows原检查](https://github.com/jsdfhasuh/emo_master/actions/runs/36874309302/job/110409686809)；三项均在首次页面结果等待失败 |

push Windows的精确失败为 [testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners](../tests/ui/presentation/test_retained_acceptance_lifecycle.py)：`A18_THREAD_CHECKPOINT phase=navigation index=399`，新原生线程ID为9120、9128，原线程身份子集断言失败。该用例在此停止，不能声称该次1000次切页/30次浮窗及最终退休均完成。仅线程ID和检查点不能说明创建者、保留原因或泄漏类型。

PR Windows的三项失败均位于 [test_image_demand_client.py](../tests/runtime/presentation/test_image_demand_client.py) 的第一个 `until(lambda: latest(session))`，原等待期限15秒：

- `testQueuedPinAndThousandDemandChangesReleaseEveryAdmission[expiry]`，第369行
- `testAggregatePinReservationRejectsBeforeSecondDecode`，第424行
- `testLeaseReadyPinGetsNextFreeSlotBeforeMoreLiveAdmission`，第462行

这三个失败发生在取得初始结果之前，尚未进入其后pin、解码门控或额度断言；不能称为已证实的pin回收错误、第二张图解码预算错误，也不能直接称为终态等待或SQLite根因。它们确实阻止本版本宣称Windows整体通过。

下一项安全必要工作是补齐这三个原失败点的边界证据，**方案尚未实施、尚未运行**。源码核对已确认：采集在worker启动前附着，初始化通道独立，仅带jobId的客户端不启用GetJob身份门控，零图像需求仍提交元数据，终态最新结果仍有保留及快照补齐路径；目前没有证据指向一个可直接修正的小型入口错误。见 [普通启动附着](../src/emo_master/apps/runtime/grpc_server/service.py)、[DisplaySession](../src/emo_master/clients/runtime/display_session.py)、[结果存储](../src/emo_master/apps/runtime/presentation/store.py)。

拟只在上述三处原15秒断言已经失败、teardown尚未开始时，输出现有 `jobFailureDetails` 以及有界缓存状态：服务端根作用域的latest/high/open/pending，客户端连接/错误、received/generation、pending数量和所属线程是否存活。不新增RPC、SQL、轮询、等待或图像载荷。下一次必要Windows验证据此区分生产者、传输或客户端哪一边界未前进，再为确定机制编写回归；保持用户电脑暂停的安排，不以重新跑绿当成修复。

### 已完成的bridge退休小修

原 `_reap` 及启动失败清理在持有Supervisor锁时对仍存活的bridge执行 `join(timeout=1.0)`；bridge最后的 `bridgeStopped` 同样需要这把锁，因此调用方等待期间该finalizer无法完成。aad053f删除这两处锁内join，先请求停止；只要bridge仍活着，就保留进程、IPC、资源额度和工作目录所有权，由最后回调继续退休。锁外的有界 `waitForRetirement` 保留。

源码依据：[supervisor.py](../src/emo_master/apps/runtime/jobs/supervisor.py) 第466、470、600、654行。新增回归在旧实现上失败，覆盖force-stop、shutdown、process-exited三个参数，以及bridge仍在真实队列读取、线程已启动后启动流程失败两种场景；验证无锁内join、无提前close/额度返还、最后仅退休一次。见 [test_job_supervisor_contracts.py](../tests/runtime/test_job_supervisor_contracts.py) 第401–564行。相关单元34项、集成19项及上述cloud完整检查通过。

**该修正只闭合已确定的多余等待机制。** 没有改变SQL设置、事务/commit、资产fsync、原采集和读取期限，也没有证明它就是当前A18、首次结果等待或既有Windows终态延迟的根因。

### 终态资产提交机制与未实施的两阶段方案

现行普通事件处理在Supervisor锁内调用终态处理；`_markTerminal`先执行terminalCallback，再更新Job状态及终态通知。实际callback会提升旧预览资产，原子索引写入包含fsync。源码依据：[consumeWorkerEvent / _markTerminal](../src/emo_master/apps/runtime/jobs/supervisor.py) 第106、532行；[RuntimeService._onJobTerminal](../src/emo_master/apps/runtime/grpc_server/service.py) 第1218行；[PreviewAssetStore.promote / _atomicBytes](../src/emo_master/apps/runtime/preview/store.py) 第216、620行。

针对3332652已有一次有界控制实验：使用真实EventBridge及原资产提升代码，退出进程和队列使用替身；只在A的目标项目索引fsync处门控，释放后仍调用原fsync。门控期间A的完成事件已写入，但公开Job状态仍为RUNNING，B尝试取得Supervisor锁后不能推进事件；Supervisor/资产锁被占用，而EventStore和Repository锁可取得。释放后确认“索引fsync返回→资产提升返回→Repository发布COMPLETED”的顺序，公开资产读取返回新值，两个拥有者正常退休。该实验说明局部锁/发布顺序机制，**不证明远端Windows失败实际经历了同一慢段或同一根因**。

将终态处理拆成锁内登记、锁外资产提交、再发布状态的两阶段方案，**尚未通过设计审查，未写入生产代码，也没有其完整反例矩阵的实现验证**。剩余阻塞具体包括：

1. 真实 `RuntimeService.StopJob` 在 `record.isTerminal` 时直接返回成功，绕过Supervisor。`JobRepository.update`又先修改内存记录、再持久化。若未来两阶段发布失败后保留“看似终态”的内存，单改Supervisor不足以防止Stop过早确认；需明确定义最终化/恢复结果并覆盖真实RPC反例。见 [StopJob](../src/emo_master/apps/runtime/grpc_server/service.py) 第473–488行、[JobRepository.update](../src/emo_master/apps/runtime/jobs/repository.py) 第70行。
2. 拟议全局FIFO会遭遇跨Job同步重入：A的callback内同步终止B，B取得后续票据却排在尚未完成的A之后，可能等待自己所属的调用链。只防同Job自等不足，必须先定义拒绝或安全延期及其调用结果。
3. callback、状态持久化、终态通知及退休回调分别失败时，哪些责任继续由谁持有、如何唤醒等待者、如何有限重试close，尚未形成通过审查的完整契约。不能重跑有副作用的callback，不能因内存已terminal就释放额度或SQLite/工作目录；现有内存优先的降级路径也不能被无声改成另一种保证。

因此，本候选只包含bridge退休小修；不把上述设计当成已完成优化，也不将它列为可直接照方案实施的已审定补丁。

### 性能、长测及历史结果分列

| 验收对象 | 截至本版本的结论 | 不可替代的边界 |
| --- | --- | --- |
| 原1080p/5 Hz、200 ms、节拍回退≤5%、数据年龄≤500 ms | **未通过**。既有有效FAIL保留，后续有缺图/完整性不满足的实验为INVALID或NOT_ASSESSED，没有新的有效PASS | 不能以NONE吞吐改善、缩小图像、减少分母、去掉失败样本或探针成本推算覆盖原结果 |
| 分段观测与按需策略 | 已实现并取得多轮原始证据：旧预览、新采集、编码、asset读取、解码、GUI/paint、资源、收尾分开；ALL/NONE单列 | 区间存在嵌套；不相加p95，也不以差值断言磁盘、网络、GIL根因 |
| 900秒120×160、5 Hz有界长测 | **该profile已通过**：1955d02默认Runtime路径4500结果/GUI/paint齐全、无读取/丢弃错误、正式终态与拥有者退休 | 这是小图900秒结论，不是1080p性能、无限运行或本版本现场验收；[原记录](runtime-pages-r3-expanded-results.md) 第384–390行 |
| 3332652历史普通CI | push/PR四项曾通过：Windows各2253 PASS、2 SKIP、27 subtest；Ubuntu各2251 PASS、4 SKIP、27 subtest | [历史push](https://github.com/jsdfhasuh/emo_master/actions/runs/36868122596)、[历史PR](https://github.com/jsdfhasuh/emo_master/actions/runs/36868129528)。不能覆盖aad053f两组普通Windows检查合计4个失败用例 |
| 3332652独立A18诊断 | A18目标当次通过，但诊断全套1 FAIL、2252 PASS、2 SKIP：globalcounter原10秒终态等待失败 | [历史独立诊断](https://github.com/jsdfhasuh/emo_master/actions/runs/36868122402)。无原生身份失败文件，后续验证器报诊断不可用；不是当前普通A18失败的反证 |
| 其他独立SQL/A18诊断 | 无失败捕获、缺失证据或INVALID分别保留，不能用诊断完成/单项通过替代普通CI验收 | 既有SQL样本、状态及栈为非原子观测，范围见 [有限SQL诊断说明](testing/2026-10-01-runtime-sql-failure-scope.md)；不从“在commit”推导整个等待期间卡在同一次commit |
| 原Qt/native间歇失败 | 已有确定的Qt/worker所有权和fixture缺陷分别修复，历史native SIGSEGV保留；aad053f又出现明确A18 native身份FAIL | 不按线程类型豁免，不加计数容差；未解释全部native线程创建者/退休原因，不宣称整个进程任意长期无泄漏 |

R3 §7保留原1080p指标作为回归基准，尚未将其确认为全部现场SLA。它要求解释实际成本和透明报告，不能变成无实际规格时无限扩充诊断/反复追指标的任务。当前Windows失败及既有终态延迟可独立调查；在缺乏确定因果时，应保留FAIL和未决设计，不扩大队列、不猜测重写持久化或并发机制。

## 计划第 8 节按实际操作复核

| 计划中的操作 | 本轮最终判断 |
| --- | --- |
| 正常打开已有工程进入页面设计 | 正常入口和显式升级已实现、有合成工程回归；用户实际工程未验收 |
| 友好名选择输出并拖入控件 | 已实现、有原生拖放及未知类型回归；实际全部插件/实例清单未取得 |
| 正常明确运行看流程与两页 | 同Job源码路径有真实Runtime/GUI回归；当前首次结果等待FAIL及既有终态延迟仍未全部闭合 |
| 切页、重连、关闭观察者 | 已实现、有反例；popup同会话，无隐式Start/Stop；编辑目标错位已修复 |
| 停止再启动含不确定Start | 实现与针对回归齐备；Windows间歇终态等待不是已全部解释 |
| 流程与页面保存、另存、重开 | 已实现并有事务、失败保留、待提交字段和页面身份回归 |
| OK/NG、字号、空值、表格、详情 | 第5.1六项已实现并有回归；真实屏幕可用性未验收 |
| 所需算子、两作用域、原图/结果图 | 有界源码实现及像素/身份回归；真实工程所需范围未核实，双1080p不在当前双图额度内 |
| 不同尺寸及旧/新采集策略 | 边界、失败、ALL/NONE和原图不变有回归；实际预览方案与性能承诺未确定 |
| 原回归、Qt组合、长稳态、设备/冻结 | aad053f普通Ubuntu PASS、两个Windows检查FAIL；独立诊断历史另列；小图长测PASS；1080p性能未通过；设备/冻结及最终本机环境NOT_RUN |

## 计划第 9 节提交和停止条件

当前工作保持原开发分支，审计没有修改main、发布标签或冻结包。新增只读弹窗/实时状态沿用原协议与renderer，协议能力和对应P2/P3/P4契约已有同步变更，见 [6a05885](https://github.com/jsdfhasuh/emo_master/commit/6a05885)。[aad053f](https://github.com/jsdfhasuh/emo_master/commit/aad053fce2bcaa051313c4fea55365e030812cb3)仅含bridge退休小修与相应测试。本文件是文档记录，不改变功能、期限、预算或验收断言；文档提交本身不改变上述代码测试结果。不能用“新增测试/文档数量”计算计划完成率。

用户后续要求继续整个计划，使§5使用能力也进入已授权软件范围；这没有消除§5.3所明确依赖的真实工程门槛，也没有授权本轮诊断期间占用用户电脑或执行现场设备操作。当前电脑暂停，最终验收时再按用户安排使用。

## 剩余事项及完成条件

| 类别 | 仍缺什么 | 可以独立进行的工作与完成边界 |
| --- | --- | --- |
| 独立软件问题 | aad053f的A18 native线程身份FAIL、三项首次结果15秒等待FAIL；既有globalcounter/其他终态等待失败仍保留 | 保留原期限与断言，区分首次结果、终态、pin及资源退休阶段。根据证据有限调查；bridge小修已闭合其确定机制，未证明解决这些失败；不把再跑通过或新增探针称为因果闭合 |
| 下一项安全必要工作 | 三个首次结果超时点缺少当时的生产者/服务端缓存/客户端边界状态 | 按第7节方案，仅在原断言失败后、teardown前记录已有诊断及有界缓存；不新增RPC/SQL/等待/负载或图像载荷。该方案未实施、未运行；后续必要Windows验证以失败证据分流，再写确定性回归 |
| 尚未审定的并发改造 | 终态资产提交的两阶段方案尚未实施 | 先解决真实StopJob过早成功、跨Job同步重入FIFO自等、发布/通知/退休失败后的所有权与有限恢复契约，再评审实现和控制反例；当前不自动扩大生产改造范围 |
| 独立测量与性能结论 | 原1080p基准尚无有效PASS，部分候选实验不满足完整性 | 保留精确负载、完整分母、相邻对照和退出状态。若仍FAIL如实交付结果；无因果证据不扩大队列、不改编码/持久化保证。此次审计不再运行负载 |
| 实际工程与需求依赖 | 现有工程副本、节点/插件版本、调用结构、真实图像规格、目标屏幕、节拍、运行停止方式 | 取得后在副本执行第4与第8节操作；允许明确标注的离线边界替身，不能重新搭一个示例冒充原工程。用户暂时没有，当前保持待条件满足 |
| 交付依赖 | 通用独立运行端、工程交付范围、冻结包、部署与现场验收 | 先完成同一真实工程源码态验收，再决定支持的资源/插件和交付能力。现测试release保护继续保留，设备另需明确许可 |
| 最终用户电脑验收 | 最终候选在用户Windows环境的集中验证 | 依用户当前暂停安排，待源码与已知问题收敛后执行；远端runner成功不冒充这次本机最终验收 |

**本版本可准确表述为：R3正常源码功能及有界多来源使用能力已实现，bridge锁内join的确定缺陷已修；aad053f的cloud和普通Ubuntu检查通过，但普通Windows检查仍有明确失败。性能、稳定性、实际工程、通用交付和最终本机验收仍未完成。不能表述为当前CI全绿、整个计划或现场需求已经完成。**
