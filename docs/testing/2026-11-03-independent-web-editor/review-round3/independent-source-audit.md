# 第三轮独立源码审读

审读者：runtime_capture（独立检查其他执行者本轮改动；本轮额度诊断不作为自己的独立审查对象）。日期：2026-11-04。实际 HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f`，分支 `codex/logging-consolidation-20261103`，加保留的当前未提交实现。仅写入本审读记录，没有修改产品或测试、放宽断言或操作真实设备。

## 结论

对用户本轮明确列出的正常使用路径，未发现新增的必须修复回归或权限绕过。下述结论来自实际源码逐项审读和已产生证据的复核，不能扩写为所有异常组合、原生 Qt、安装后 Runtime 完整检测或现场验收通过。

### 预览切页后的组件选择

`Editor.tsx` 使用 `componentContext(page, selected)` 只解析当前页面的组件和直接父布局。普通页面标签、示例/实时预览标签及 `PanelRenderer.onPage` 共用 `changePage()`，同步更新 pageRef/pageId 并清除 selected。属性更新、网格属性更新和直接绑定再次按当前页查找组件；即使残留 ID 也不能修改另一页对象。`restoreSelection()` 仍在撤销/重做时将选择校正为恢复后当前页的真实组件，没有删除历史选择恢复保障。

已复核 `editor-after-attempt2-browser.json` 的五个切页 case：示例标签、示例导航、实时标签、实时导航及编辑标签均 passed；每项有两次正式后端 `validate_source` 校验的实际保存，以及 saved/reopened 全量源。旧页标题未被修改，新页修改内容正确保存重开。此为真实 React/Edge、模拟 HTTP/WS，不能称为真实 Runtime 实时预览的完整浏览器验收。

### 复制、删除、粘贴绑定子树

`copyToClipboard()` 保存组件及完整子树，同时收集实际 bindings 引用的来源、来源作用域和 action.resultScopeId。`pasteFromClipboard()` 在任何赋值前完成全部作用域和来源 ID 冲突检查，再恢复定义并为组件生成新 ID。来源/作用域稳定 ID 不重建，等价默认字段参与既有定义比较。`tidyBindings()` 仍清理未使用来源，并在粘贴后由组件/动作重新派生页面作用域。`clearTargetContext()` 继续清空剪贴板、历史、选择、结果及旧上下文。

已复核最终 browser 报告 bound/nested/shared/static 四项 passed，以及 `editor_round3.test.mjs` 的子树/动作作用域、稳定身份、共享定义、静态资源引用和冲突前零修改断言。浏览器中的 static 是无绑定静态组件；真实资源上传/保存仍属于已有资源测试范围，不把该单项称为上传实测。

### 直接父网格与尺寸调整

`componentContext()` 返回组件直接父级布局和兄弟集合；属性区据此提供 row/column/rowSpan/columnSpan。resize 对 grid 修改 layout 跨度，保持 responsive 不变；对 responsive 保留实际逻辑尺寸及 zoom 换算。`setGridPlacement()` 保留列界、整数、跨度及同级重叠校验，失败有具体提示且不写入错误布局。编辑画布和 `PanelRenderer` 均采用一致的最小 64px 行轨道，预览按已保存跨度显示，未引入新布局格式或任意自由定位。

已复核最终 browser 两项：根网格 50×50 拖动使高度增加，继续水平拖动得到 2×2 span，预览高度 140px；嵌套网格子组件属性与拖动保存为 row=1/column=1/rowSpan=3/columnSpan=2，预览高度 216px。两项各有两次后端校验、保存重开一致以及实际 CSS grid 行列跨度断言。另已读 prior12 的响应式缩放拖动、无操作、取消等保留回归 12/12，固定/跟随/溢出高度组 6/6。

### 自动重连与旧页面版本恢复

`main.tsx.refresh()` 默认 pin 当前不可变页面包；只有明确刷新及明确启用/切换路径传 `adoptPublished:true`。自动重连和两个内部 quiet 同步入口未传该标志，仍读取当前选择版本；当前包不在激活目录时将它保留在可选项中。实例或工程改变时先清理旧 view、结果、选择及控制上下文；新请求携带 runtimeId、projectId、runtimeInstanceId。未发现自动 Start/Stop 路径。

后端 `find_panel()` 先沿用当前激活目录读取；历史恢复必须已有该 Runtime 的启用关联，且显式传工程与实例。它再次核对当前实例、当前正式工程、包 manifest 工程和访问权限，管理员也不能恢复从未对该目标启用的任意上传包。`new_view()` 位于既有目标锁内，再核对包实例/工程、真实 run 列表、覆盖规则和当前权限。保留 package_targets 历史关联仅恢复版本，不改变默认激活版本；删除 Runtime 会删除对应关联。

已复核 `test_selected_package_recovery.py` 与 `selected-package-after.log`：5 passed、12 subtests，包括旧包/同 Job/覆盖保持、错误目标/工程/实例、缺少恢复身份、实例变化、ACL撤销和未启用包拒绝。复核 `selected-package-browser/browser-report.json` 的 viewer/admin 两条真实 Edge + 当前构建、模拟 HTTP/WS 链：发布通知后旧版、自动恢复仍旧版/同 Job、无期间用户操作、明确刷新后新版；admin quiet 导入同步保持旧版；真实身份替换清空旧 Job。productionCommands=[]，pageErrors=[]。这两层证据分别证明真实后端约束及浏览器选择行为，不拼接成未执行的真实设备重连验收。

### 所选旧任务的真实修订

`job_metadata()` 通过既有 `_job()` 核对 GetRunState、GetJob/ListJobs 的实例、工程和 jobId，并从该 Job 的冻结 CaptureCoverage 读取 executionRevision/capturePlanRevision。对有 startRequestId 的任务，以该原请求查询 GetStartRequest，再核对实例、jobId 与 startRequestId；Runtime `_startReply()` 从同一 JobRecord 返回 projectRevision。没有用当前 GetRuntimeInfo.document_revision 代填，也没有要求页面版本、请求配置修订或任务采集修订相等。

没有旧启动请求 ID 的遗留本机任务不伪造工程修订，UI 明确显示“Runtime未提供”。真实当前 Runtime 的新/已有请求任务会从 JobRecord 返回真值；本轮未扩协议。实时预览元数据区域仅在相应预览中展示，切换目标会清空元数据。

已复核 `task-metadata-contract-after.log` 的14 passed和对应真实 loopback gRPC 测试：当前工程修订改为999后旧任务仍显示工程3及冻结执行/采集修订，无请求ID不伪造当前修订，身份不匹配拒绝。本人此前实际执行的 `runtime-sdk-qt-regression.log` 190 passed 已包含 `_startReply.project_revision == JobRecord.projectRevision`。最终 browser 报告 metadata-known/unavailable 两项通过，未显示当前999，并核对额度控件诊断及正式组件名称。

## 证据边界与保留失败

- 浏览器报告由 frontend/backend 执行者产生；本次独立审读读取其 JSON、断言脚本和日志，没有把审读冒充独立重复执行浏览器。
- 已复核 `frontend-node-final.log` 105 passed/0 fail/0 skip，`backend-full.log` 183 passed/230 subtests，及本子任务亲自执行的 Runtime 190 passed；各组范围重叠，不累加。
- `editor-after-attempt1-browser.json` 的 nested 剪贴板与名称选择器失败仍保留。最终脚本显式选择容器标题，名称检查使用包含图标前缀的可访问名称后缀，实际源码名称仍逐项与 Designer palette 比对；没有删除保存/重开/后端校验或正确布局断言。
- 前端执行者确认产品与测试冻结，最终证据为 `editor-after-attempt2-browser.json`；`frontend-frozen-source-sha256.json` 和 `frontend-tested-dist-sha256.json` 记录该被测构建。本轮最终重新打包及源/产物比对由主任务负责，本文不提前声称完成。
- 原生 Qt 人工操作、W08完整Designer→浏览器重绑定、安装后Runtime完整检测、冻结EXE、跨机LAN及真实相机/PLC仍按总记录的实际执行情况列示；本次源码审读没有补做或宣称这些层级通过。

## 本次实际审读源码指纹

HEAD 相同且有未提交修改，所以同时记录逐文件 SHA256，避免用移动分支代替固定内容。

| 文件 | SHA256 |
| --- | --- |
| `web_panel/frontend/src/editor/Editor.tsx` | `c25ce091d995eb94dc710324424e04147e9826482d737e2081bde62f8db4b91c` |
| `web_panel/frontend/src/editor/model.ts` | `ca5e435b7a3471958894f293f19028cceca5ce021a63fedca1abf4b25f1d8b5c` |
| `web_panel/frontend/src/renderer/PanelRenderer.tsx` | `204d27af8cbec0a8e626d075151b13bc3548abaa2c70091febd9e86881501aeb` |
| `web_panel/frontend/src/main.tsx` | `def04894b42e095389bba2a2b537957bd0ea809023cfc662ee7134d339998729` |
| `web_panel/frontend/src/state/catalog.ts` | `a24e2b1f0865f72c88f04a5661591e732aa38f8bd347e3b2937b8ef4316d22a7` |
| `web_panel/backend/emo_web_panel/app.py` | `8b198dd92c29d93167abfd4748276355d5c7d6ce7143c0ec5def5616dfb64fa4` |
| `web_panel/backend/emo_web_panel/packages.py` | `ea39ee17c1467c3b4e772548a753ee19da899238c5b8cb2cd8a21f118fdcc838` |
| `web_panel/backend/emo_web_panel/runtime_client.py` | `990df9685842116030c20ed766bd098332fc0c2b821b2bccb5cb5c8bd2fecabd` |
| `web_panel/backend/emo_web_panel/editor_routes.py` | `c0f503bfe6dbad98df2fea1c53d4351a678ad38e34025e6c25eb83bd86be76fd` |
| `src/emo_master/apps/runtime/grpc_server/service.py` | `43d099fcf6c34ccd89364211d9c650f1d4c71ed449a015e5af40c7cb2acb2c72` |
