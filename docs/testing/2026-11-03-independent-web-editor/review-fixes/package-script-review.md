# 最终交付脚本审计

2026-11-03，基线 `d92ffd37960e8b8f527136151ab9ccb80054e78f` 加当前未提交实现。本子任务只审计和局部修复交付脚本，没有构建 wheel、创建交付 ZIP、提交或修改共享总 README。Runtime wheel、完整浏览器闭环和最终总记录由主执行者完成。

## 发现与局部修正

审计对象是 `scripts/package_independent_web_editor_delivery.py`，同时核对 Web/SDK pyproject、`package_frontend.py`、`verify_wheel.py`、`check_installed_editor.py` 及启动/恢复说明。

- 既有 Runtime wheel 检查会核对全部 Python 模块清单和源码字节，能发现 setuptools 旧 `build/lib` 残留。既有 Web 检查会核对当前后端 Python 和静态文件。保留这些检查。
- 原脚本仅选择 SDK wheel，没有核对源码与契约摘要。现补充 `emo_panel_protocol` Python 清单/字节、契约指定 wheel 文件名及摘要、Web 精确 SDK 依赖版本的核对；当前契约是 1.5.0。
- 原脚本只把 Web wheel 对比 `backend/emo_web_panel/static`，没有检查 `frontend/dist` 是否已同步。现核对两目录完整清单及字节，再检查 wheel，避免遗漏 `package_frontend.py`。
- 现有证据有 349 字符的 Windows 路径。归档证据时使用局部扩展绝对 IO 路径，ZIP 内仍使用正常相对路径。只读检查实际读取该最长文件成功。
- 证据 ZIP 先生成，其 SHA256/大小再写入交付 manifest。manifest 位于交付 ZIP，不位于证据目录，脚本没有自身哈希环。不要把当前证据 ZIP 的摘要另写入将被同一 ZIP 收录的证据文件后反复重打；交付 manifest 已提供外部对应关系。
- 交付 ZIP 增加 `web/contract-manifest.json`；生成的 START-HERE 明确证据 ZIP 是并列交付文件，解释解压安装路径。Runtime 使用 Python 3.10 和原 Runtime 依赖，Web 使用独立 Python 3.10+ 环境；不把 Runtime 装进轻量 Web 环境。
- 增加 `--check-only`，只读核对现有 wheel/源码/静态产物并打印摘要，不写 START-HERE、manifest 或 ZIP。
- 经主执行者明确授权，把 `web_panel/scripts/verify_wheel.py` 两处 SDK 1.4.0 字样同步为 1.5.0；所有既有普通任务、ABORTED、空 runGroupId、Stop presence 和独立环境断言保留。没有为此重新建环境或安装依赖。

运行路径均由脚本文件位置解析，仓库 C: junction 会解析为实际 D: 路径；不依赖调用者当前目录。旧 `verify_wheel.py` 则按其既有约定在 `web_panel` 目录运行。

## 五项修复进入最终产物的检查对应

| 修复 | 行为证据 | 产物包含关系 |
| --- | --- | --- |
| R1 工程/实例变化清空上下文，同身份刷新保留编辑 | `after-frontend-fixes-final-browser.json` 中身份切换、实例切换、旧预览关闭、保存实际源与重开断言 | 前端正式构建；`frontend/dist` = 后端 static = Web wheel；最终静态摘要与 `frontend-static-sha256.json` 一致 |
| R2 Web Start 不请求 Qt 采集 | `web-start-before.log` → `web-start-after-verified.log`；真实 gRPC 外部 count=2，Qt 显式 true 仍拒绝失效绑定 | Web wheel 的 `runtime_client.py` 与当前源码逐字节一致；Runtime wheel 同时匹配统一外部请求实现 |
| R3 撤销/重做恢复有效页面和组件选择 | 同一前端浏览器记录中新建、复制、删除页后的历史恢复、编辑、保存重开；Node 97 项 | 与 R1 共用最终 Editor JS，检查 `Editor.tsx` / `model.ts` 对应的已构建文件清单及摘要 |
| R4 缩放后尺寸采用逻辑坐标 | 同一前端浏览器记录中缩放拖动、100% 拖动、无位移、取消、保存重开 | 与 R1 共用最终 Editor JS/CSS；Web wheel 不得残留旧 Editor 静态文件 |
| R5 两路等价 Qt/Web 图片共享实际采集预算 | `issue-5-public-rpc-recheck.log`、`issue-5-summary.json`，包括双结果身份、图片像素/摘要、两 raw lanes、第三路拒绝、独立 lease 释放 | Runtime wheel 全部 `.py` 清单及字节等于当前 src，涵盖 capture_limits / collector / service / assets；SDK wheel 与当前协议源码一致 |

非阻断小改同样进入对应产物：固定终端预览位于最终前端静态文件；发布与管理改地址协调位于 Web `app.py` / `editor_routes.py`，其实际后端字节由同一 wheel 检查覆盖。

逐字节一致证明“测试过的实现被收录”，不单独证明每项行为。前端行为依赖最终构建的真实 React/Edge 针对性检查，Runtime 行为依赖真实 gRPC 和结果资源回归，安装行为另外记录。

## 已执行只读检查

```powershell
& web_panel/.venv/Scripts/python.exe scripts/package_independent_web_editor_delivery.py --check-only
```

结果 PASS，详见 `package-script-readonly-check.log`。当前观察值如下；主执行者最后生成的 manifest 是最终交付摘要的依据。

| 产物 | SHA256 | 字节数 |
| --- | --- | ---: |
| Runtime `emo_master-0.6.6-py3-none-any.whl` | `aeb26acc932485cb6942b7cc0bffa10bb24118d5007913ca6da73febebd6ecd1` | 1662929 |
| Web `emo_master_web_panel-0.1.0a1-py3-none-any.whl` | `d098b3c99700276d2587c9013579575007a7f4d19dc024d723b964709a3719eb` | 188352 |
| SDK `emo_panel_protocol-1.5.0-py3-none-any.whl` | `97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d` | 40265 |

最终静态文件是 `Editor-Qa0TYqL3.js`、`Editor-Dou073XM.css`、`index-DH1kqud4.js`、`index-BPJhQw1Z.css` 和 index.html，五个摘要均与前端子任务最终记录一致。脚本语法解析和相关 diff 空白检查通过。`package-script-long-evidence.log` 记录只读枚举 183 个当时存在的证据文件，并成功读取最长 349 字符文件；没有为此生成 ZIP。

## wheel 后证据的充分性与界限

- 后端最终源码回归仍是 `backend-full.log`：**169 passed、214 subtests passed，42.25 秒，无跳过**。本次仅修改交付脚本和说明，没有修改后端运行代码，不重复执行后端全套。
- `installed-editor-wheel.json` 覆盖 site-packages 中 Web/SDK 的独立安装、两页源、静态资源、发布不隐式启动、无 Runtime/Qt/推理依赖、无 Node 的实际 CLI。它使用明确的 mock Runtime，不能独自证明真实检测闭环或五项针对性行为。
- 已核对主执行者新生成的 `web_panel/test-results/independent-editor/20261103-233028-4c7c90/report.json`：PASS；`webPackage.mode=installed-wheel`，导入来自 site-packages，`isolated=true`。该记录覆盖安装后 Web wheel 与真实软件 Runtime 的同次图片/数量/判定、两页、发布/回退、Web 进程关闭恢复。当前 E2E 的 Runtime 仍从 `ROOT/src` 启动，故准确名称是“安装后 Web wheel → 软件 Runtime 闭环”，不扩大为 Runtime wheel 安装后执行。
- Runtime wheel 的源码匹配、内容、解包/安装/导入及实际执行层级，以主执行者 `runtime-wheel-content.json` 和最终说明为准。即使源码逐字节一致，也不能把没有执行的安装、冻结 EXE、跨机 LAN、原生人工 Qt 或真实设备检查记为完成。

当前组合证据足以判断五项代码修复已进入被核对的 wheel、最终前端行为回归通过且安装后 Web 可完成真实软件 Runtime 闭环。主执行者更新总说明并最后运行正常打包命令时，需同时交付两个 ZIP，并记录它们实际生成后的摘要；本子任务不代做该步骤。
