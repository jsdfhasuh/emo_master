# 独立 Web 编辑器前端审查修复

本记录对应 `codex/logging-consolidation-20261103` / HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f` 上现有未提交实现的局部修复。未提交、推送、合并或部署。既有实现和历史失败证据均保留。

## 修复与直接证据

1. **工程 / Runtime 实例切换**：`loadTarget()` 在身份改变时统一清理撤销与重做、剪贴板、当前页与选择、草稿修订、任务选择、预览、结果资源、来源诊断及发布显示状态，再装入新目标草稿。仅当同身份已有文档时保留未保存编辑。首次连接失败后手动刷新也会完整装入文档。
   - 修前 A 编辑 → B 刷新 → 撤销 → 保存，实际请求是 `projectId=B`、`pageOrder=[page-A]`，保存后重开仍为 A 源。完整请求保存在 `before-frontend-fixes-browser.json`。
   - 修后检查实际保存请求和保存后重开，并验证同身份刷新保留未保存编辑及历史；工程切换和仅 Runtime 实例变化都清空历史、旧任务及旧剪贴板。单独检查身份变化时关闭旧预览。
   - 首次 context 返回 503 → 手动刷新成功但没有装入文档的直接反例保存在 `before-load-recovery-browser.json`；最终复验可继续编辑、保存并重开。
2. **撤销 / 重做的当前页与组件选择**：恢复原有历史文档时重新选取有效当前页；原页不存在时使用恢复文档首页或第一张有效页。仅保留属于当前页的有效组件选择，包括容器内组件；复用现有历史结构。
   - 修前新建与复制页后撤销都显示空编辑区，失败截图保留。
   - 修后新建、复制、删除页面分别执行撤销 → 重做 → 撤销 → 编辑 → 保存 → 重开，检查源内容；模型直接检查跨页选择清理、缺失页回退及嵌套选择保留。
3. **缩放后拖动尺寸**：起始屏幕宽高和鼠标增量均除以当前缩放，统一使用逻辑坐标。结束时恢复临时 DOM 样式，只有有实际位移的正常 pointerup 才写入历史；无位移、pointercancel 和丢失捕获均保持原尺寸。
   - 修前缩放 `0.464583`，拖动屏幕 `20×10px`，预期逻辑 `1927×1066`，实际保存 `918×507`；无位移、取消也改变源尺寸。
   - 修后缩放拖动实际保存并重开为 `1927×1066`；100% 拖动由 `848×524` 正常变成 `868×534`；无位移和取消后响应式源字段与原始对象完全一致。
4. **非阻断固定终端预览尺寸**：示例 / 实时预览共用的渲染器外增加终端尺寸容器和滚动区域，应用编辑器已选择的终端宽高，保留真实尺寸，不使用编辑画布缩放。真实浏览器示例预览实测宽 `1366`、最小高 `768`。本项没有单独宣称真实 DisplayService 实时预览的终端尺寸专项验收；两种预览共用容器代码。

## 已执行检查

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 修前真实 React / Edge 交互，模拟 API，原 8 项 | 7 项失败、删除页回归原已通过，失败均保留 | `before-frontend-fixes-browser.json` 和同前缀 PNG |
| 首次加载失败的修前恢复反例 | 失败已保留 | `before-load-recovery-browser.json` / PNG |
| 修后真实 React / Edge，模拟 API，实际保存请求及重开 | **12/12 通过** | `after-frontend-fixes-final-browser.json`、同前缀 PNG、`frontend-browser-final.log` |
| 全部前端 Node 测试（含 3 项新选择恢复检查） | **97/97 通过，0 跳过** | `frontend-node-final.log` |
| TypeScript + Vite 正式构建 | 通过 | `frontend-build-final.log` |
| 首次构建的可空默认页类型错误 | 失败保留，已通过显式可空处理修正 | `frontend-build-after.log`；后续 `frontend-build-after-attempt2.log` / final 均通过 |
| 受影响前端源与测试 `git diff --check` | 通过 | 本轮命令完成，无输出 |
| `package_frontend.py` | 完成，先核对目标真实路径位于仓库且非链接 | `frontend-package-final.log`、`frontend-static-sha256.json` |

浏览器脚本：`web_panel/frontend/tests/editor_review_browser.py`。使用实际构建的 React、真实本机 Edge，无 Runtime、设备或 Qt。模拟 API 持有完整 Presentation 源，记录 PUT 保存请求，并在浏览器重载后重新供给持久化源；断言保存后的内容而非只检查页面标题。

复现命令（从实际 D: 前端目录运行，避免本机 C:/D: junction 对 Vite 路径的影响）：

```powershell
Set-Location D:\jsdfhasuh\documents\my_project\emo_master\web_panel\frontend
npm test
npm run build
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& ..\.venv\Scripts\python.exe tests\editor_review_browser.py --label your-run
```

`--case identity-switch` 等参数可选择单个案例。每次使用新 `--label` 保留已有记录。固定 Edge 路径为 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`。

## 最终静态产物

已复制到 `web_panel/backend/emo_web_panel/static/`，完整 SHA256 在 `frontend-static-sha256.json`：

| 文件 | SHA256 |
| --- | --- |
| `index.html` | `EAE3AB0088AB482BC0337E3505213D221CE286BE18E4F7FFB215DE6F00D3F1DF` |
| `assets/Editor-Qa0TYqL3.js` | `40BC25F34D659509E2083A7BBA8BA3950388710FBCA8117ADD534AA8CF776B81` |
| `assets/Editor-Dou073XM.css` | `0815B9C139E7A613556F968524F18A003ECFF7A4FBFB70D5531F769CCCA3740A` |
| `assets/index-DH1kqud4.js` | `696613282A883B1365410F8F4BC95714C2B2E5B455B1E164539740B198D70D27` |
| `assets/index-BPJhQw1Z.css` | `6E99AE16E325407A452BF0ABEDD5403EA9E330189515D4C06D2247AF1DB9E8E5` |

主任务负责在其它修复合入当前工作区后重跑真实本机软件 Runtime 闭环、构建最终 wheel 和更新总交付哈希。本前端子任务没有构建 wheel，没有人工 GUI、另一台电脑 / LAN、真实设备或冻结 EXE 验收。
