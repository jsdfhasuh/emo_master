> 最新：完整计划复审收尾见 [第三轮记录](../review-round3/README.md)。本文件保留当轮修复、失败及验收快照，当前交付摘要以第三轮为准。

> 本文件保留第一轮修复及其当时的验收和产物摘要。第二轮补充修正了全局发布锁跨目标排队、固定预览内部纵向高度；最新实现、交付和实际复验见 [review-round2/README.md](../review-round2/README.md)。第一轮五项必修保留，原证据未改写。

# 独立审查后的五项修复与最终交付

分支 `codex/logging-consolidation-20261103`；HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f`。功能及本轮修复位于未提交工作区；C盘用户入口与实际D盘仓库为同一工作区。开始状态见 `start-state.json`。没有回退、提交、推送、合并或部署，已有修改与失败证据保留。

## 完成清单

- [x] R1 工程/实例切换完整清理上下文，同目标刷新保留未保存编辑。
- [x] R2 Web启动不请求Qt采集；Runtime自动应用外部请求，Qt显式意图及校验保留。
- [x] R3 撤销/重做恢复有效页面和组件选择；新建、复制、删除后可继续编辑保存。
- [x] R4 缩放拖动统一逻辑坐标；无位移及取消保持原尺寸。
- [x] R5 等价图片只占实际两路；两端来源、结果归属、资源寿命保留，第三路仍拒绝。
- [x] 前端、后端、Runtime/SDK、Qt采集和Designer入口回归；两页真实闭环及Web关闭恢复。
- [x] 重建前端及Web/Runtime wheel，核对三wheel与当前源码，更新交付、摘要和验收记录。

## 五项问题的根因、修复与证据

| 项目 | 局部修复 | 修前与修后证明 |
| --- | --- | --- |
| R1 旧工程历史进入新草稿 | `clearTargetContext()`统一清理历史/重做、剪贴板、任务、预览/结果资源、选择、草稿及发布显示。工程或实例改变、首次没有文档时完整装入；同身份正常刷新保留编辑。 | `before-frontend-fixes-browser.json`保存A→B后实际PUT携带B身份与A源的反例；最终JSON检查实际保存请求及重开源，覆盖工程变化、实例变化、普通刷新、关闭旧预览及首次连接失败后恢复。 |
| R2 Web错误请求Qt采集 | Web Start明确`capture_presentation=False`，沿用Runtime统一外部请求；未改变Designer/本机Qt显式路径。 | `web-start-before.log`保留Qt失效绑定拒绝；`web-start-after-verified.log`真实gRPC下有效Web请求得到count=2，显式Qt true仍拒绝该失效绑定。详见`backend-review.md`。 |
| R3 撤销后页面悬空 | 历史恢复复用`restoreSelection()`，原页不存在时选首页/首张有效页；清除失效或跨页组件选择，不新增历史系统。 | 修前新建/复制撤销空画布截图保留；修后真实React覆盖新建/复制/删除后的undo/redo、继续编辑、保存及重开。模型检查嵌套与跨页选择。 |
| R4 缩放拖动尺寸错误 | bbox屏幕初值和鼠标增量均除以zoom；仅有实际位移的正常pointerup写源，结束时还原临时DOM样式。 | 修前zoom=0.464583，屏幕拖20×10保存918×507；修后保存/重开1927×1066。100%拖动848×524→868×534；无位移和取消保持原响应式源。 |
| R5 同两路图片重复计费/争用 | `imageCaptureIdentity()`用真实输出及完整scope定义识别等价需求；collector同次观察只copy/export一次；assembler先建立各结果独立拥有的编码资产，再关闭结果，复用原保留/读取/租约规则。 | `issue-5-before.log`保留合并被拒反例；公开RPC复验四来源、两scope、同invocation、独立resultKey与正确ownerResultKey/像素/摘要，只编码两次、两lane/8MiB，第三路仍拒绝。ReleaseJob后Qt资源NOT_FOUND、Web租约资源可读，ReleaseLease后资产归零。见`issue-5-public-rpc-recheck.log`、`issue-5-summary.json`。 |

修前检查在对应产品修正前执行；相邻首次连接失败恢复也先建立`before-load-recovery-browser.json`再修正。没有弱化既有正确断言。

## 两项非阻断缺口

两项均用直接相关的小改动完成：

- 固定终端尺寸应用到示例/实时预览共用容器，真实尺寸且可滚动。模拟API真实浏览器示例实测1366×768；真实Runtime完整闭环又检查实时容器width=1366px、min-height=768px。
- 发布与修改Runtime地址复用应用现有`target_lock`，覆盖publish/query/complete/rollback/abandon。在途发布目标一致，之后管理修改正常完成。`target-before/`保留延迟MockRuntime的HTTP并发反例，`target-after/`记录`changedDuringPublish=false`及随后两操作完成。未改变实际网络配置。

## 实际检查及层级

| 检查 | 最终结果 | 证据 |
| --- | --- | --- |
| 构建React+真实headless Edge，模拟API，检查实际PUT/保存后重开 | 12/12通过 | `after-frontend-fixes-final-browser.json`及12张对应PNG、`frontend-browser-final.log` |
| 全前端Node测试 | 97 passed，0 fail，0 skip | `frontend-node-final.log` |
| TypeScript/Vite、静态资源复制 | 通过，dist/static五文件字节和SHA一致 | `frontend-build-final.log`、`frontend-package-final.log`、`frontend-static-sha256.json` |
| Web全后端源码及真实独立软件Runtime子进程集成 | 169 passed，214 subtests passed，无skip | `backend-full.log`、`backend-review.md`、`product-runtime/` |
| Runtime/SDK/Qt采集、图片通道及资源寿命 | 188 passed | `issue-5-runtime-sdk-qt-regression-final-2.log` |
| R5公开RPC及迟到回调定向复验 | 4 passed | `issue-5-public-rpc-recheck.log` |
| Designer入口/终端属性原生QWidget offscreen自动化 | 20 passed | `qt-entry-regression.log` |
| 源码Web→软件Runtime→真实Edge两页闭环 | PASS，browserErrors=[]，4个静态资源200 | `source-full-browser/`；原始`web_panel/test-results/independent-editor/20261103-232514-6ce9be/` |
| 已安装Web+SDK wheel→源码软件Runtime→真实Edge闭环 | PASS，gateway Python -I，Web来自site-packages | `installed-full-browser/`、`installed-full-browser-loop.log`；原始`web_panel/test-results/independent-editor/20261103-233028-4c7c90/` |
| Web wheel独立安装、源roundtrip、资源、发布、CLI无Node/轻量导入 | PASS | `web-wheel-install.log`、`installed-editor-wheel.json` |
| Runtime wheel完整inventory/字节、ZIP、语法及解包-I导入 | PASS，620个Python模块与src一致，旧Web launcher不存在 | `runtime-wheel-content.json`、`runtime-wheel-verification.log` |
| Web源码轻量导入、pip check、ruff F、diff --check | PASS | `backend-independent-source.log`、`backend-pip-check.log`、`final-audit.json` |

各组范围重叠，不相加为总数。两次完整浏览器闭环均检查正式运行页明确Start，同次图片/数量2/OK、详情1600/2400、两页导航，运行中发布与回退保持非空实际采集修订，整个Web和浏览器关闭后原Job继续出结果，恢复后重订阅与静态图正常。检测/正式详情/恢复详情截图已目视核对。

临时工程由WorkflowStore生成完整2.9 / Presentation1.2 / devices1.0、空设备，经ProjectDocument校验、saveProject保存重读、工作流编译。未使用真实相机/PLC、现场工程或凭据。

## 交付、摘要与复现

三wheel路径、大小、完整SHA256见`delivery-artifacts.json`，前端五文件见`frontend-static-sha256.json`。主验收`../README.md`已同步。

- Runtime wheel SHA256：`aeb26acc932485cb6942b7cc0bffa10bb24118d5007913ca6da73febebd6ecd1`。
- Web wheel SHA256：`d098b3c99700276d2587c9013579575007a7f4d19dc024d723b964709a3719eb`。
- SDK1.5.0未改源码，SHA256：`97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d`。
- `build/independent-web-editor/independent-web-editor-delivery.zip`含三wheel、START-HERE、部署恢复和验收说明；同目录`verification-evidence.zip`保留本轮/历史证据。
- 同目录`delivery-manifest.json`记录归档内部文件及外部证据ZIP摘要；`archive-verification.json`记录两ZIP最终摘要和核验，不放回证据ZIP形成自哈希环。
- `scripts/package_independent_web_editor_delivery.py`先验证Runtime/Web/SDK Python、SDK manifest和dist/static，再打包；`--check-only`只核对输入。见`package-script-review.md`。

本机启动、创建两页、发布、运行步骤见`../README.md`、`web_panel/README.md`、`web_panel/docs/DEPLOYMENT.md`。E2E设置`EMO_WEB_USE_INSTALLED=1`验证已安装Web wheel；Runtime仍指定产品Python和源码，省略时使用源码Web。wheel不含解释器或离线依赖wheelhouse，运行已构建前端不需Node。

上一版三wheel、两个ZIP、manifest保存在`build/independent-web-editor/previous-before-review-fixes/`；首轮验收及摘要副本为`first-delivery-record.md`、`first-delivery-artifacts.json`。修前和中间失败均保留。

## 失败记录及未执行层级

保留前端首次nullable类型构建失败、修前浏览器反例、首次连接恢复失败；R2首次修后业务检查过但证据归档MAX_PATH失败，修归档路径后通过。R5首次错误测试路径无测试，随后大回归发现迟到orphan回调访问已不存在字段，修产品代码且保留正确旧断言后188项通过。产品Python没有build包，首次`python -m build`失败保留`runtime-wheel-build.log`，改用已有pip wheel后`runtime-wheel-build-final.log`通过。旧setuptools缓存先核对路径在工作区内后移动备份，没有删除工作区源或历史交付。

未执行人工Qt桌面走查、Designer GUI autoStart点击、Runtime wheel安装后完整任务执行、异机LAN、真实相机/PLC、冻结EXE/安装程序或现场部署。Qt offscreen自动化、真实headless浏览器、源码Runtime执行、Web wheel安装运行、Runtime归档/导入分开记录，不能互相替代。当前没有在这些检查证据范围内未解决的五项必修问题。
