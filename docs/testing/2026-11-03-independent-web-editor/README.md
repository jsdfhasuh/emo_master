# 独立 Web 页面编辑器实施与验证记录

2026-11-04 源码提交及最新远端基线整合见[整体操作复查交接](../../handoffs/2026-11-04-independent-web-editor-review.md)和[推送前检查](push-integration-20261104/README.md)。下文保留各轮原始验证时的 SHA、工作区状态和产物摘要，不将旧结果改写为新提交的验收。

开始日期：2026-11-03。实施依据为用户指定的《EmoMaster 独立 Web 页面编辑器设计与实施计划》当前正文。

开始时工作目录：`C:\Users\jsdfhasuh\my_scripts\emo_master`；分支：`codex/logging-consolidation-20261103`；固定源码基线及开始 HEAD：`d92ffd37960e8b8f527136151ab9ccb80054e78f`。开始时工作区干净。本记录不表示提交、推送或部署。

四阶段代码已实现，前三轮复审的五项、两项及本轮四项缺陷均已修复，本轮三项计划细节已局部补齐。HEAD仍为上述固定提交，功能和修复位于未提交工作区。最新第三轮实际检查：Web后端 **183 passed、230 subtests passed、无skip**；Runtime/SDK/Qt采集必要源码回归 **190 passed**；前端 **105 passed、无skip**；最终构建真实React编辑保存重开 **13/13**，原交互 **12/12**，固定/跟随/溢出高度 **6组/12场景**；已安装Web wheel→源码软件Runtime完整两页及自动重连版本保持闭环 **PASS**。当前交付与全部修前/修后证据见 [第三轮收尾记录](review-round3/README.md)。Runtime和Web wheel本轮重建，SDK未改并复用核验。以下历史检查保留，未执行的人工/现场层级继续明确列出。

## 实施清单

- [x] 第一步：通用输出请求协议、来源目录、持久化、统一启动及 DisplayService 最小两页结果链路。
- [x] 第二步：完整 Presentation 源、草稿与资源，以及独立 Web 编辑器。
- [x] 第三步：源版本、发布、实时预览、覆盖诊断、查询恢复及回退。
- [x] 第四步：独立构建与备份恢复说明；闭环验证后移除 Designer 的 Web 专用入口，回归 Qt 页面。

## 四阶段当前状态

| 阶段 | 代码与本轮修正 | 软件验证及尚未执行层级 |
| --- | --- | --- |
| 1 数据契约及结果链路 | 已实现；本轮增加合并额度通用诊断、原请求只读返回真实任务工程修订；SDK协议未变 | Runtime190、后端183/230；真实软件Runtime执行通过，安装Runtime wheel完整检测未执行 |
| 2 独立编辑器 | 已实现；切页选择、剪贴板关联、直接父网格尺寸与属性、名称对齐已修复 | 最终React实际交互/正式保存校验/重开13/13，旧12/12，高度6/6，Node105；人工编辑验收未执行 |
| 3 发布与运行 | 已实现；自动/quiet恢复保留选版，明确刷新采用新版，后端受限恢复历史版本 | 模拟API的Edge两角色与已安装Web→源码软件Runtime闭环通过；W08完整Designer→浏览器重绑定链仍未执行 |
| 4 独立交付及入口整理 | 既有Designer Web专用入口移除保留；本轮重建前端/Web/Runtime及ZIP，复用SDK并核摘要 | Web安装、无Node CLI、Runtime解包隔离导入和源码核对通过；原生Qt人工、异机LAN、EXE/安装程序及现场未执行 |

## 验证边界

只使用临时模拟工程、本机测试 Runtime 和临时 Web 数据目录。不访问现场工程、相机、PLC 或现场凭据。
源码自动检查、真实浏览器操作、原生 Qt 自动化、打包安装与真实现场验收分开记录。
未执行、跳过及失败均保留原状态；后续修复后的成功不抹去先前失败证据。

## W01—W13

| 编号 | 范围 | 当前验证状态与证据 |
| --- | --- | --- |
| W01 | 独立打开、目标与目录 | 真实 Edge → 独立 Web → 本机软件 Runtime 已通过；无 Designer 进程参与。实际目录、工程身份与修订来自 RPC。见 browser-final-report.json。 第三轮已安装Web→源码软件Runtime重新通过，见review-round3/installed-full-browser/report.json。 |
| W02 | 两页草稿、重开、布局与静态资源 | 后端完整源保存重建、前端模型测试、真实浏览器保存重载/上传/示例与运行图片读取通过；新源 1.2、两页 targets=[web]。最终实际关闭浏览器和Web进程再重建后，源和静态图仍可用。 第三轮补齐切页、剪贴板及网格实际编辑/保存/重开13/13；静态图与浏览器/Web进程重建通过。 |
| W03 | 图片、数量、判定、表格绑定与稳定身份 | 浏览器绑定 blob.overlay、count.count、judge.result、blob.blobs；详情表格面积为 1600/2400；源码测试检查不兼容类型、节点改名身份不变和布局修改身份稳定。 第三轮复制删除后稳定来源/作用域恢复并经正式validate_source；额度诊断可关联来源/Qt/页/控件，16/16/2边界不变。 |
| W04 | 示例/实时预览、切页不执行 | 浏览器在明确 Start 前核对没有任务，保存与示例发布不启动；实时预览明确选择 Job 并来回切页。缺失覆盖/类型拒绝由前后端测试验证。 第三轮示例/实时页签与导航共用清选择；固定高度及溢出旧断言保留，预览显示所选任务真实工程/执行/采集修订。 |
| W05 | 正式发布、明确启动与同次结果 | 真实安装算子在合成图上运行，DisplayService 同个 resultKey 的 count=2、verdict=true、image.ownerResultKey 一致；浏览器实际收到 WS 结果并显示图片/OK/两行表格。 第三轮安装后Web完整两页再次通过，图片/count2/OK及详情1600/2400；不等同Runtime wheel安装后检测。 |
| W06 | 运行中发布、当前任务冻结、下次生效 | 真实浏览器流程保持原 RUNNING Job；Runtime 源码集成用非空实际 plan/result 修订核对冻结，3→4 来源只在下一次明确启动后生效。后续浏览器复验也直接比较结果中的非空修订。 第三轮通知及自动重连仍选旧包/同Job，明确刷新才换包；新来源覆盖提示与任务冻结保持。 |
| W07 | Web/Designer/本机统一启动、Qt 行为 | 真实 HTTP Web Start；真实 Designer RuntimeClient.startJob(capturePresentation=False)；真实 ProductionRuntime.load/start 均执行软件算子并采集外部请求。Qt flag 合并双作用域共5来源通过。Designer GUI autoStart 未人工操作，仅核对其共用调用路径。 第三轮190项受影响Runtime/SDK/Qt采集源码回归重新通过，Qt原生桌面操作本轮未重跑。 |
| W08 | 流程变化、失效来源及修复 | 通过正常工程写入删节点后，已保存外部来源保留诊断；正常新任务有效3来源、失效1来源UNAVAILABLE；无效入口scope不造结果。随后正常工程写入还原judge，保留四来源ID，Validate/Submit并明确新任务，四来源全部AVAILABLE、count=2/judge=true（runtime-source-repair-recheck.log）。页面源与失效回退由后端检查；未实际浏览器操作删节点/重绑定整条链。 完整Designer改流程→浏览器重绑定→发布→明确新任务的操作链仍未执行，不能以源码修复检查替代。 |
| W09 | 一次断连、查询与继续发布 | 后端测试模拟一次已提交丢回复，查询原request并明确complete，不重复Submit；验证前断连保留PREPARED可继续，未知无回执可结束跟踪。并发旧complete覆盖新发布的确定性反例已修并通过。此项为源码/模拟传输故障，未执行跨机器物理断网。 第三轮增加普通WS断连后的页面版本保持；同Job、包ID/修订、用户动作与零自动控制均核验；原发布未知不重放断言继续通过。 |
| W10 | 回退与后续采集一致 | 后端测试旧完整源重新派生/提交请求并启用；失效旧源回退拒绝且旧正式页不变。真实连续任务中回退不重启任务；旧包激活API不能绕过编辑器请求协调。 第三轮已安装Web→源码Runtime运行中回退保持冻结采集，并保留后续请求一致性检查。 |
| W11 | Web 关闭与恢复、原任务与后续采集 | Runtime 源码集成关闭订阅及gRPC观察连接后，任务仍RUNNING且结果序号增长。最终浏览器复验实际关闭浏览器和整个Web进程，直接DisplayService观察结果序号仍增长、revision不变；重建Web进程和浏览器后重新订阅同个Job，图片、OK、两行表格和静态图正常。新任务采集已保存请求见W06/W12；已释放结果不可用由既有服务测试覆盖。 第三轮真实软件Runtime确认一次自动恢复收到新结果且旧包不变，明确刷新才切版；整个Web/浏览器关闭恢复再通过。 |
| W12 | Runtime 正常重启恢复请求 | 同DB正常关闭/重建真实RuntimeService，无Web进程，用Designer RuntimeClient启动采集3来源；随后ProductionRuntime恢复后load/start亦采集3来源。见 runtime-observer-restart-checks.log、runtime-launcher-recheck.log。 第三轮190项包括正常重启外部请求恢复；Runtime wheel安装后完整检测仍未执行。 |
| W13 | Designer 入口与独立构建交付 | Runtime/Web/SDK wheel及已构建前端已交付；Web独立安装、无Node CLI、无emo_master/Qt/推理导入通过。Designer移除Web目标/导出/两种预览/launcher挂接；46项属性编辑菜单及20项Qt正常结果、预览、导出独立运行检查通过；历史targets保留。Runtime wheel逐模块核对并解包隔离import通过，未执行冻结EXE和现场安装。 第三轮Web/Runtime wheel、SDK、已构建前端与交付ZIP配套摘要和源码一致性检查通过；EXE/安装程序、异机及现场未执行。 |

## 实现与检查证据

- Runtime / SDK：`runtime-sdk-checks.log`（61项）、`runtime-launcher-recheck.log`（31项）、`runtime-qt-regression-final.log`（26项）、`runtime-observer-restart-checks.log`（2项）。这些范围重叠，不相加成总用例数。
- Web 后端：`backend-final.log`；包括真实软件 Runtime 独立进程集成、草稿、资源、发布、回退、类型与权限。`backend-concurrent-complete-recheck.log` 对应旧发布并发完成的直接反例。
- 前端：`web_panel/frontend/tests/editor.test.mjs` 和既有renderer/result队列测试；首轮 TypeScript/Vite 构建与94项测试通过；审查修复后为97项，见review-fixes。
- wheel：`installed-editor-wheel.json`、`delivery-artifacts.json`、`wheel-build-final.log`、`wheel-install-final.log`；Runtime归档和导入级别见 `runtime-wheel-content.json`。wheel是Python分发包，不含冻结解释器。
- 真实浏览器自动化：`web_panel/frontend/tests/independent_editor_e2e.py`；每次在 `web_panel/test-results/independent-editor/<时间>-<随机后缀>/` 生成独立report、截图和进程日志。使用本机已安装Edge的headless Chromium，未操作真实设备。

首轮最终真实浏览器记录为 `browser-attempt-10.log`，原始证据目录 `web_panel/test-results/independent-editor/20261103-211628-bca086/`。归档副本为本目录 `browser-final-report.json`、`live-detection.png`、`formal-running-details.png`、`reconnected-details.png`。已目视核对最终截图。此次在正式运行页实际点击“启动工作流”，检查正式发布包两页显示，再检查编辑器实时预览、非空实际结果采集修订、发布/回退不重启、整个Web服务关闭恢复。`browserErrors=[]`，静态资源HTTP200。

Qt原生自动化记录为 `designer-qt/source-properties-retest.log/xml`（46通过）与 `designer-qt/qt-normal-loop.log/xml`（20通过）。首轮新增菜单测试因空工程无页面、Qt预览本应禁用而失败；修正为先用既有fixture建立Qt页面，没有放宽产品断言，原 `source-properties.log/xml` 保留。

`testRpcResultsCasLookupAndRestartWithoutWeb` 覆盖真实gRPC请求、CAS、结果、重启、Designer客户端与本机controller。`testRunningPublishNewTaskQtMergeAndStaleSource` 覆盖连续任务、客户端关闭恢复、运行中请求更新、下次任务、Qt合并与失效作用域。Web测试 `test_lost_submit_reply_is_queried_then_explicitly_completed_without_replay`、`test_validation_disconnect_retains_candidate_for_explicit_continue`、`test_invalid_rollback_keeps_active_page_and_original_source` 分别给出发布恢复和回退的直接证据。

## 保留的失败与修正

- 初始模拟工程漏设 imagePath，编译失败后修正生成器；`demo-runtime-start.log` 保留。
- Qt资源检查曾提前遮挡既有准入错误，已恢复原有准入顺序并重跑；`runtime-initial-failure.log`、`runtime-launcher-checks.log`、`runtime-qt-assertion-failure.log`保留。
- 浏览器早期 GetJob 请求漏身份、Snapshot请求类型错误均为验收脚本问题，已修；早期attempt-2虽然脚本PASS，截图揭示切页断连/详情空，因此不作为最终闭环依据。随后加强断言并修复前端WS依赖、Collection envelope表格读取。
- Windows正常深目录中的静态图片超过260字符导致读取失败，已增加局部扩展路径IO处理；真实长路径反例通过。attempt-5/8/9另有目录rename拒绝访问，单加扩展路径不能解决；`package-rename-probe.json`记录同源/目标/数据未改变，100ms后rename成功，Restart Manager未捕获用户态锁持有者，不能指定具体扫描进程。仅针对Windows 5/32/33、源存在且目标不存在的本地rename加一次100ms重试；第二次仍失败就保留异常，不重放Runtime写入。直接上限反例和实际深目录安装通过，最终浏览器复验通过。原失败日志保留。
- 旧重复complete迟到可能把页面A重新启用而Runtime配置已B；`review-concurrent-complete-before-fix.json`保留反例。复用原发布锁串行状态变更并锁内重读记录，确定性回归通过。
- 最初直接把Runtime wheel当ZIP import失败（namespace目录需正常安装/解包），`runtime-wheel-import.log`保留；解包后隔离导入通过，不将首次失败改写为通过。
- 整仓wheel初次重建受setuptools旧build/lib缓存影响仍含已删Web launcher，已清理该生成文件并重建；`runtime-wheel-stale-build-cache.json`保留。首轮最终618个打包Python模块与当时src逐字节一致，旧launcher不存在。本轮重建620个Python模块与当前src逐字节一致，见review-fixes/runtime-wheel-content.json。

## 交付产物

| 产物 | 路径 | SHA256 |
| --- | --- | --- |
| Runtime Python wheel | build/independent-web-editor/runtime/emo_master-0.6.6-py3-none-any.whl | 13c6f88f658a7b63968cc361ac1367ff3881c85ace8b3138c231fb1c5a801025 |
| 独立Web（内含已构建前端） | web_panel/dist/emo_master_web_panel-0.1.0a1-py3-none-any.whl | 2fd3a9ee34870f3c7c3294cda0068f5383f566c79969ad9d91be5ebb4fedc686 |
| 轻量SDK 1.5.0 | web_panel/contracts/panel-contract-v1/python/emo_panel_protocol-1.5.0-py3-none-any.whl | 97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d |

表中为第三轮收尾后的wheel；Runtime和Web本轮重建，SDK未改动并复用核验。上一版wheel和归档保存在 `build/independent-web-editor/previous-before-review-fixes/`；首轮验收文本及摘要保存在 `review-fixes/first-delivery-record.md`、`first-delivery-artifacts.json`。三份wheel、启动/恢复说明和本记录另汇总到 `build/independent-web-editor/independent-web-editor-delivery.zip`；源码/浏览器/Qt/打包检查及历史失败记录另存 `build/independent-web-editor/verification-evidence.zip`。归档不含运行数据库、账号存储或现场数据。Web wheel不含Python解释器或依赖wheelhouse，离线安装需另备依赖，不能当作冻结EXE。Runtime wheel校验为归档、源码匹配和解包导入层级；实际软件Runtime执行使用当前源码环境。

## 本机可复现步骤

以下只创建隔离合成工程和测试状态，不访问现场工程。先打开一个PowerShell，在仓库根目录运行：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:QT_QPA_PLATFORM='offscreen'
& 'D:\Conda\envs\emo_master\python.exe' scripts/independent_web_editor_demo.py --directory D:/EmoWebDemo/runtime-20261103 --port 50061
```

该脚本使用当前 WorkflowStore 创建完整2.9/1.2工程，空devices；经ProjectDocument校验、saveProject保存重读和WorkflowCompiler编译，包含合成图片→Blob→计数→数值比较，默认250ms连续运行。它不声称已完成人工Designer打开验收。退出可Ctrl+C，后续复用目录仅接受该demo工程身份。

第二个PowerShell用独立Web环境启动：

```powershell
& web_panel/.venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWebDemo/web-20261103 create-user admin
& web_panel/.venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWebDemo/web-20261103 serve --mode grpc --runtime-address 127.0.0.1:50061
```

独立新环境的wheel安装步骤见 `web_panel/docs/DEPLOYMENT.md`。浏览器打开 `http://127.0.0.1:8780/editor`，登录后：

1. 选择当前工程，点击“创建首期两页结构”；检测页依次绑定 目标检测/blob/overlay、数量/count/count、判定/judge/result。
2. 详情页表格绑定blob.blobs，添加列“面积”，字段填 `area`；可上传静态PNG/JPEG/BMP，保存草稿后重开。
3. 示例预览应标注示例数据；点击发布不会产生任务。进入运行页面，明确选择main并开始。
4. 返回编辑器刷新任务，明确选择该Job做实时预览。检测页应显示两个目标、数量2、OK；详情页表格面积1600/2400。
5. 修改布局后发布，当前Job保持运行。新增来源在下一次明确开始的Job中生效；版本回退重新校验并更新后续采集请求。

自动复验命令：

```powershell
$env:PYTHONPATH='web_panel/backend'
$env:EMO_WEB_PRODUCT_PYTHON='D:\Conda\envs\emo_master\python.exe'
$env:PYTHONUTF8='1'
& web_panel/.venv/Scripts/python.exe web_panel/frontend/tests/independent_editor_e2e.py
```

前端开发构建须从实际D盘仓库目录执行（本机C路径为junction，Vite从C工作目录可能产生跨盘realpath错误）：

```powershell
Set-Location D:/jsdfhasuh/documents/my_project/emo_master/web_panel/frontend
npm run build
npm test
```

## 独立审查修复后的补充验证

五项问题的修前反例、局部修复及修后检查详见 [修复记录](review-fixes/README.md)。本轮真实软件Runtime浏览器证据分别为 `review-fixes/source-full-browser/` 与 `review-fixes/installed-full-browser/`；后者用Python `-I`从site-packages加载最新Web wheel，报告记录实际导入路径。两次均无浏览器错误、静态资源HTTP200，检查正式运行页明确Start、同次图片/数量/判定、详情切页、运行中发布与回退冻结、整个Web进程关闭恢复。实时预览1366×768终端容器断言通过。

W07补充：独立Web不再请求Qt页面采集；同个临时工程有失效Qt绑定时，已保存有效Web外部请求仍可执行并得到count=2；显式Qt采集依然拒绝失效绑定。Qt/Web绑定同两路图片的四个来源身份可共同使用两条物理通道；公开Snapshot/ReadAsset验证同次调用、独立结果归属和租约寿命，真正第三路图片仍拒绝。W02补充工程/实例切换、历史恢复和缩放尺寸保存重开12项真实React交互。

本轮Runtime wheel通过620个Python模块完整源码比对、归档完整性及解包后隔离导入；实际检测执行仍使用源码软件Runtime。Web wheel已安装在无Qt/emo_master/推理包的独立环境，CLI无Node检查和已安装Web→源码Runtime浏览器闭环均通过。

复验已安装Web交付物时，在上面的E2E命令前增加 `$env:EMO_WEB_USE_INSTALLED='1'`；省略或设为0使用后端源码。Runtime wheel复现构建为产品Python `-m pip wheel . --no-deps --no-build-isolation --wheel-dir build/independent-web-editor/runtime`；Web wheel沿用 `python -m build --wheel web_panel --outdir web_panel/dist`。最终三wheel与文档已齐备后运行 `python scripts/package_independent_web_editor_delivery.py`，或加 `--check-only`只校验，不重打包。

## 第二轮局部修复补充

应用目标协调和EditorStore发布协调均按稳定Runtime目标ID隔离；同目标连接改址/删除仍与发布串行，确认前继续核对实例、工程和配置修订。取消/失败保留真实请求状态且不重放。B的Start、Stop和发布可在A提交等待时独立完成。仅宣称本轮发布路径隔离，没有扩大为所有控制请求的全面并发改造。

固定预览显式传入所选终端完整逻辑高度，扣除原顶部16px后排弹性空白。1024×768在宿主900/1200下均内容752px、底部组件相对终端bottom=768px；示例与实时一致，超出保持尺寸并滚动；跟随窗口及正式运行默认测量不变。完整修前/修后、回归与复现见 [review-round2/README.md](review-round2/README.md)。

安装后Web wheel→源码软件Runtime的本轮原始浏览器证据为 `web_panel/test-results/independent-editor/20261104-070138-dad073/`，本目录 `review-round2/installed-full-browser/` 已归档report、截图、进程日志。browserErrors为空，4个静态资源响应200；两页同次图片/数量/判定、运行中发布/回退及整个Web关闭恢复通过。

本轮旧交付和文档在 `build/independent-web-editor/previous-before-review-round2/` 保存；上一轮证据不覆盖。Web wheel安装运行与Runtime/SDK只读复用检查分开记录。最终ZIP摘要位于交付目录 `archive-verification.json`，避免写入证据ZIP形成自身摘要循环。

## 第三轮完整计划复审收尾

四项实际编辑/恢复缺陷修前均已复现，代码修复、最终构建真实Edge操作、正式保存校验和重开通过；任务真实修订、额度具体诊断和正式名称已补齐。完整方法、前后数据、精确命令、保留失败、安装证据和产物摘要见 [review-round3/README.md](review-round3/README.md)。当前原始安装闭环证据为 `web_panel/test-results/independent-editor/20261104-080007-faec01/`，报告/截图/日志已归档；最终交付ZIP摘要见外部archive-verification.json。上一版10项产物/记录保存于 `build/independent-web-editor/previous-before-review-round3/`。

没有本轮明确要求的剩余代码缺陷。W08完整Designer→浏览器重绑定、Runtime wheel安装后检测及下列人工/异机/现场验收仍未执行；没有把这些层级勾为通过。

## 未执行层级

没有执行人工原生Qt桌面走查、Designer GUI autoStart实际点击、另一台电脑局域网操作、真实相机/PLC、现场部署、冻结EXE或安装程序验收。原生Qt的offscreen QWidget自动化、源码集成、真实浏览器headless操作和wheel安装按各自层级记录，不能替代这些验收。未提交、推送、合并或部署。
