# 第二轮：固定终端预览内部高度

基于 `codex/logging-consolidation-20261103`、HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f` 及已有未提交工作区。未回退、提交、推送、合并或部署。未改共享总 README、打包脚本或 wheel。产品源码完成后已冻结，指纹见 `frontend-source-sha256.json`。

## 局部修复

上一轮固定尺寸只约束预览外壳的 `minHeight`，内部弹性空白仍按 `window.innerHeight - 页面起点 - 16` 分配空间。固定终端因此受宿主浏览器高度影响；溢出内容也没有独立可滚动的终端边界。

- `PanelRenderer` 新增可选 `viewportHeight`，表示**固定终端的完整逻辑高度**。只有编辑器固定尺寸预览传入，示例与实时共用同一入口。
- 保留原顶部 `16px` 间距，内部弹性布局高为 `viewportHeight - 16`。`1024×768` 对应 terminal 高 `768`、content 高 `752`、顶部偏移 `16`，底部组件到终端逻辑底部 `768`。
- 固定 terminal 同时约束 `height` / `minHeight`，使用 `overflow:auto`。正常适配内容没有额外滚动；超高组件保留尺寸，通过滚动访问底部。
- 参数默认 `undefined`，跟随窗口与正式运行仍使用原窗口测量、ResizeObserver 和 resize 规则。
- `canvasTop` 向上扩展只加一次：inner 使用 `layoutHeight`，outer 在该高度上增加 extension，并保留原 `marginTop:16`。新增 renderer 测试验证 viewport `768`、extension `120` 时 inner 高 `752`、outer 最小高 `872`。本项是源码/Node 验证，未单列浏览器 canvasTop 专项。

## 修前证据

先运行真实本机 Edge + 已构建 React，再修改产品代码。HTTP 及实时预览的 WebSocket 明确模拟，没有启动 Runtime 或检测。

`before-fixed-height-height-browser.json` 与同前缀 12 张 PNG 保留：

| 场景 | 宿主高 900 | 宿主高 1200 | 修前结果 |
| --- | --- | --- | --- |
| 固定 1024×768，示例 | content 高 645，底部组件 top 相对 content 为 537 | content 高 945，底部组件相对 top 为 837 | 失败 |
| 固定 1024×768，实时 | content 高 596 | content 高 896 | 失败 |
| 跟随窗口，示例 / 实时 | 原窗口测量 | 原窗口测量 | 两项原已通过 |
| 超高内容，示例 / 实时 | 内容高 1348，但 terminal scrollTop 为 0 | 同样无法滚动终端 | 两项失败 |

结果与 `review-fixes/independent-editor-rereview-20261103-6f9bd7/fixed-height-adjacent-probe.json` 一致。旧证据未改写。

## 修后检查

`after-fixed-height-attempt1-height-browser.json`、同前缀 PNG、`frontend-height-attempt1.log`：**6/6 项通过，共 12 个宿主高度场景**。

| 场景 | 宿主高 900 / 1200 的实际结果 |
| --- | --- |
| 固定尺寸，示例与实时 | 均为 terminal 高 `768`、content 高 `752`、顶部偏移 `16`、底部组件 bottom 相对 terminal 为 `768`。普通组件各高 `108`，未压缩；`scrollHeight == clientHeight == 768`，无意外纵向滚动。 |
| 弹性空白 | 不同宿主高度下，空白高度与底部组件相对位置一致。 |
| 跟随窗口，示例 | content 高仍为 `645 / 945`。 |
| 跟随窗口，实时 | content 高仍为 `596 / 896`，保留实时预览提示区的原窗口分配。 |
| 溢出，示例与实时 | 顶部组件 `900`、底部组件 `400`，保持尺寸；terminal 高 `768`、scrollHeight `1364`；scrollTop `596` 后底部组件完整落入终端可见区。 |

| 其它检查 | 结果 | 证据 |
| --- | --- | --- |
| 上一轮真实 React / Edge 交互、保存请求及重开 | **12/12 通过** | `prior12-after-fixed-height-browser.json`、同前缀 PNG、`frontend-prior12.log` |
| 全前端 Node | **98/98 通过，0 跳过** | `frontend-node.log` |
| TypeScript + Vite | 通过 | `frontend-build-final.log` |
| 受影响源 / 测试 `git diff --check` | 通过 | 命令无输出 |
| 新浏览器脚本 `py_compile` | 通过 | 命令无输出 |
| `package_frontend.py` | 完成；先确认 static 真路径在仓库内且不是链接 | `frontend-package.log` |
| dist 与后端 static | 5 文件逐字节一致 | `frontend-static-sha256.json` |

补充 `scroll-visibility-probe.py` / JSON 与 `scroll-visible-*.png`：在宿主高 `900` 时，固定与溢出两场景、示例与实时两模式共 **4/4 通过**。实际滚动终端及现有外层 backdrop 后，底部组件完整位于浏览器视口内，避免仅靠“相对终端底部正确”判断可访问。此补证没有改动已冻结的产品源码、前端测试或静态产物。

`frontend-build-attempt1.log` 是处理顶部 16px 间距前的早期构建，最终产物对应 `frontend-build-final.log`。早期构建未充当最终几何验收。

## 复现命令

```powershell
Set-Location D:\jsdfhasuh\documents\my_project\emo_master\web_panel\frontend
npm test
npm run build
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& ..\.venv\Scripts\python.exe tests\editor_preview_height_browser.py --label your-height-run
& ..\.venv\Scripts\python.exe tests\editor_preview_height_browser.py --label your-prior12-run --prior-regressions
```

新脚本记录 content、弹性空白、顶部 / 底部组件、terminal/client/scrollHeight 与滚动后位置。每次使用新 label 保留历史证据。

## 最终静态文件

已复制到 `web_panel/backend/emo_web_panel/static/`，SHA256 见 `frontend-static-sha256.json`：

| 文件 | SHA256 |
| --- | --- |
| `index.html` | `a2fe603d88e1a7090ad2f126d0bf5b55981745a20e5fa17594e1b3cd36d45465` |
| `assets/Editor-Dzy_zun9.js` | `44afe9049b9c6e552b9702835631304469bb35b073c48c5f4b82556432cc7dcd` |
| `assets/index-cIlQGe0x.js` | `fc7df47378e120509be06cca0a1dd9754ea0aae4632087a9a39658898c19e015` |
| `assets/Editor-Dou073XM.css` | `0815b9c139e7a613556f968524f18a003ecff7a4fbfb70d5531f769ccca3740a` |
| `assets/index-BPJhQw1Z.css` | `6e99ae16e325407a452bf0abedd5403ea9e330189515d4c06d2247af1db9e8e5` |

本子任务实时预览几何使用模拟 HTTP / WebSocket，不能充当真实 DisplayService 结果或安装 wheel 验收。主任务继续真实本机软件 Runtime 闭环及最终交付包验证。人工 GUI、跨机 LAN、真实设备、冻结 EXE 未执行。
