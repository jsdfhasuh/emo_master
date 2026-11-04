# 独立 Web 页面编辑器：完整计划复审收尾（第三轮）

日期：2026-11-04。实施依据为用户引用附件全文及《EmoMaster 独立 Web 页面编辑器设计与实施计划》当前完整正文。实际工作目录 `D:\jsdfhasuh\documents\my_project\emo_master`；分支 `codex/logging-consolidation-20261103`；HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f`，加既有未提交实现、前两轮修复和本轮修改。未回退、提交、推送、合并或部署，未访问相机、PLC、现场工程或凭据。起始状态和diff见 `start-state.json`、`start-working-diff.patch`。

## 实际完成情况

本轮四项正常使用缺陷均已修复并通过对应浏览器/后端复验，三项非阻断细节已局部补齐。此前正确断言与历史失败保留；本轮修前直接反例也先复制执行，没有覆盖第二轮独立审查目录。四阶段代码已经具备首期两页闭环；软件验证通过不代表人工、异机或现场验收完成。

| 缺陷 | 修前反例 | 局部修复与修后结果 |
| --- | --- | --- |
| 预览切页后属性修改旧页 | 详情页画布0选中，属性仍为检测页；保存改坏检测页标题 | 统一 `changePage`；编辑/示例/实时预览页签和导航共用清选择入口，属性/绑定/布局再次按当前页解析。5种切页路径实际保存并重开，只改详情页；撤销重做选择校正保留。 |
| 复制→删除最后使用者→粘贴绑定组件不能保存 | bindings仍有source-count，sources/scopes为空；正式validate_source拒绝来源不存在 | 剪贴板保存组件子树实际引用来源、作用域及动作作用域；先核稳定ID冲突，再恢复关联定义并生成新组件ID。4种绑定/嵌套/共享/静态复制场景正式保存及重开通过；source-count/scope-wf和scope-action恢复，tidyBindings及切目标清剪贴板仍有效。 |
| 网格拖动无效、嵌套属性错误 | 根网格拖50×50后仍418×91；只写responsive；嵌套无行列输入 | `componentContext`取得直接父布局；grid拖动写已有row/column/span，属性也据父布局呈现。沿用Qt64px最小行语义，Editor/Renderer一致；根418×91→848×140，span2×2，预览高140；嵌套约257×91→526×216，保存row1/column1/rowSpan3/columnSpan2，重开及预览一致。responsive缩放换算和原边界/重叠校验保留。 |
| 普通重连静默采用新发布页面 | 通知时旧版正确；约2秒重连后换新版；旧panel/view恢复404 | 自动/quiet同步pin已选不可变版本；明确刷新/下拉切包/明确激活才采用新版，并取消在途旧目录响应。后端复用曾启用目标关联，旧panel读取及view重建仍校验目标、工程、明确实例及当前ACL，任意未启用包不能恢复。viewer/admin反例与真实软件Runtime安装闭环均证明自动恢复旧包/同Job，明确刷新后新版；新增来源在旧Job仍SOURCE_NOT_CAPTURED，无自动Start/Stop。 |

修前证据：`before-user-probe/ui-plan-probes.json`（用户原脚本/截图/源码指纹同目录），`reconnect-before/browser-report.json`、`reconnect-before/backend-report.json`。修后编辑最终报告 `final-editor-browser.json`（13/13，每次实际PUT经正式validate_source，记录完整saved/reopened源）；重连最终 `selected-package-browser-final/browser-report.json`（2/2，包ID/修订、用户动作、完整请求、6截图）。

## 三项细节

1. **任务自己的版本。** 实时预览显示所选Job的工程、执行及采集修订。执行/采集来自DisplayService冻结的CaptureCoverage；工程修订复用现有GetStartRequest回包，从对应JobRecord.projectRevision读取，并核对requestId/Job/Runtime身份，无proto或SDK变更。浏览器当前工程999、任务3时只显示任务3；真实安装闭环也断言metadata执行/采集修订等于同次结果。没有原启动请求ID的旧入口不拿当前工程值替代，明确“Runtime未提供”；未启用采集时不伪造修订。三种修订没有相等要求，覆盖规则未改。
2. **额度具体诊断。** Runtime仍在原合并及outputCaptureLimits判定处拒绝超限，补充工作流、来源/作用域地址、Qt/外部归属、计数/上限和等价图片分组；Web才关联page/component。示例3路不同图片/上限2、5个Qt/Web消费者分成2/1/2三组，明确同图共用一路。原16来源/16作用域/2路不同图/8MiB raw slab未改，不丢来源、不提交失败候选、不启动或停止任务。界面实际显示中文原因和页/控件引用。见 `quota-before.json`、`quota-after.json`、`quota-notes.md`。
3. **正式组件名称。** 按当前Designer palette统一为图像、文字、集合表格、连接状态、空白块等；内部type、绑定语义、源结构及已有用户标题未改。浏览器检查组件工具区与默认卡片/属性文案。

## 实际执行验证

| 检查及层级 | 最终结果 | 证据 |
| --- | --- | --- |
| Web后端全部，启用独立软件Runtime子进程 | 183 passed、230 subtests passed，无skip，62.51s | backend-full.log、backend-notes.md、product-runtime/等 |
| Runtime/SDK/Qt采集/统一启动必要源码回归，13文件 | 190 passed，178.08s；含正常重启、外部请求、等价图资源寿命及任务projectRevision | runtime-sdk-qt-regression.log、quota-summary.json |
| 全前端Node，最终构建源码 | 105 passed、0 fail、0 skip | frontend-node-final.log |
| 最终TypeScript/Vite、dist→static | PASS，5文件逐字节相同 | frontend-build-final.log、frontend-package.log、frontend-static-sha256.json |
| 最终实际构建React/Edge编辑+正式保存校验/重开，HTTP/WS模拟 | 13/13 | final-editor-browser.json、frontend-editor-final.log及PNG |
| 原12项工程/实例隔离、历史、缩放/拖动等浏览器回归 | 12/12 | prior12-round3-browser.json |
| 固定/跟随/溢出，示例及实时，两宿主高度 | 6/6，共12场景 | height-round3-height-browser.json |
| 新重连viewer/admin，模拟HTTP/WS但真实React/Edge | 2/2；旧包/原修订/同Job，明确操作才切新版，新增源缺覆盖 | selected-package-browser-final/ |
| 旧目录刷新实际浏览器回归 | PASS | catalog-refresh-browser.log |
| 已安装Web wheel隔离导入、源/资源/发布、CLI无Node、轻量依赖 | PASS；无emo_master/Qt/推理包导入 | installed-editor-wheel.json、web-pip-check.log |
| 已安装Web+SDK→源码软件Runtime→真实Edge两页闭环 | PASS；无浏览器错误，4个资源响应200 | installed-full-browser/report.json、installed-full-browser-loop.log |
| wheel与源码及安装文件比对 | Runtime620模块、Web45文件、SDK15文件一致；Runtime7模块解包-I导入通过 | runtime-wheel-content.json、installed-wheel-content.json、artifact-check-only-final.log |
| 源审读、语法、差异、定向ruff F | PASS；既有unused另记，不宣称整仓lint通过 | independent-source-audit.md、ruff-final-new-paths.log、diff-check-final.log、final-audit.json |

各组范围重叠，不相加。真实安装闭环使用Python -I从site-packages加载Web；软件Runtime执行仍用当前源码环境，不能称为Runtime wheel安装后完整检测。真实结果同次图片/count=2/OK，详情1600/2400、两页切换、运行中发布/回退冻结、关闭整个Web/浏览器后的恢复均通过。额外operator页：通知保持旧包→仅关闭一次WS→自动恢复收到新真实结果且旧包/Job不变→明确刷新采用新包，scoped及legacy控制请求记录均为空。原始目录 `web_panel/test-results/independent-editor/20261104-080007-faec01/`，报告、截图和进程日志已复制本目录。截图已目视核对。

## 交付、复现和备份

本轮重建Runtime和Web wheel，SDK1.5.0原样复用并重新核对源码、manifest与依赖。

| 产物 | 字节数 | SHA256 |
| --- | ---: | --- |
| Runtime 0.6.6 wheel（重建） | 1664470 | `13c6f88f658a7b63968cc361ac1367ff3881c85ace8b3138c231fb1c5a801025` |
| Web 0.1.0a1 wheel（内置前端，重建） | 191255 | `2fd3a9ee34870f3c7c3294cda0068f5383f566c79969ad9d91be5ebb4fedc686` |
| SDK 1.5.0 wheel（复用） | 40265 | `97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d` |

完整摘要见 `delivery-artifacts.json`。最终交付为 `build/independent-web-editor/independent-web-editor-delivery.zip`，证据为同目录 `verification-evidence.zip`；最终ZIP摘要、字节数、内部清单核验见外部 `delivery-manifest.json` / `archive-verification.json`，不把归档自身摘要写入证据ZIP形成循环。旧版10项产物/入口保存在 `build/independent-web-editor/previous-before-review-round3/`；生成缓存移动前确认绝对路径在仓库内且不是链接。

本机启动及两页创建步骤沿用 `../README.md` 与 `web_panel/docs/DEPLOYMENT.md`。前端/后端定向命令见本目录frontend-notes.md/backend-notes.md；Runtime13文件命令见quota-notes.md/quota-summary.json。安装后完整复验：

```powershell
Set-Location D:/jsdfhasuh/documents/my_project/emo_master
$env:PYTHONPATH='web_panel/backend'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:EMO_WEB_PRODUCT_PYTHON='D:\Conda\envs\emo_master\python.exe'
$env:EMO_WEB_USE_INSTALLED='1'
& web_panel/.venv/Scripts/python.exe web_panel/frontend/tests/independent_editor_e2e.py
```

运行前先按DEPLOYMENT安装当前Web/SDK wheel。三wheel和记录准备齐后运行 `python scripts/package_independent_web_editor_delivery.py`，或加`--check-only`只核对源码/配套，不写归档。

## 失败证据和剩余事项

- 保留4个缺陷修前反例与前两轮失败。新增编辑浏览器首次10/13：不合法容器导航动作夹具被正式模型拒绝、图标未计入名称定位，改合法子导航组件/定位后通过；产品校验及正确断言未放宽。
- 后端首轮182通过/1失败为旧包缺身份的状态码兼容，恢复既有404约定；原test_package_context断言未改。Mock来源name:null与缺省空值改为正式DataSource比较，真实Runtime路径未放宽。
- 额度定向首次产品Conda缺httpx2未收集，改既有Web环境；gRPC夹具补齐正式能力标志后通过。原失败日志均保留。
- 安装闭环attempt1停在图标按钮定位；attempt2的新探针wait_for_function被现有CSP拒绝。只修探针定位与只读条件轮询，未加入unsafe-eval或绕过CSP；attempt3完整PASS。报告/截图/日志分别在installed-browser-attempt1/、installed-browser-attempt2/、installed-full-browser/。
- 定向lint保留app.py两个HEAD既有unused、test_runtime_contract.py一个HEAD既有asyncio unused；新增浏览器探针清除一个unused import。中间错误文件导入修正的日志保留，最终相关探针编译和定向ruff通过；没有删业务断言。

**代码缺口：** 本轮明确的四项缺陷及三项细节没有剩余待实现项；未带启动请求ID的历史入口没有工程修订字段，按真实能力显示未提供，未扩展协议补造历史数据。

**验收未执行：** W08完整Designer更新流程→浏览器重绑定→发布→明确新任务恢复操作链；人工原生Qt/Designer GUI autoStart；Runtime wheel安装后完整检测；异机LAN；真实相机/PLC；冻结EXE/安装程序和现场部署。本轮未重跑Qt原生桌面自动化，Qt采集源码回归不冒充桌面操作。上述未执行项维持人工验收清单，不擅自升级为本轮代码修复的前置门槛，也不勾为通过。
