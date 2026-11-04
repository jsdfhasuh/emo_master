> 最新：完整计划复审收尾见 [第三轮记录](../review-round3/README.md)。本文件保留当轮修复、失败及验收快照，当前交付摘要以第三轮为准。

# 独立 Web 编辑器第二轮局部修复与交付

日期：2026-11-04。分支 `codex/logging-consolidation-20261103`，HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f`，加既有未提交实现、第一轮修复及本轮两项局部修复。实际目录为D盘仓库。开始状态及源指纹见 `start-state.json`；没有回退、提交、推送、合并或部署，没有访问真实相机/PLC、现场工程或凭据。第一轮五项必修保持，旧证据保留。

## 实施清单

- [x] 产品修改前保存并运行跨目标发布等待反例，B Start/Stop/发布3项失败证据保留。
- [x] 应用目标锁与EditorStore发布锁均按Runtime目标隔离，同目标管理/确认及重复complete保护保留。
- [x] 产品修改前运行固定终端真实Edge高度反例，保留示例/实时、跟随和溢出记录。
- [x] 固定预览采用终端逻辑可用高度；跟随窗口与正式运行默认路径保持，示例/实时一致，溢出可滚动且不压缩。
- [x] Web全后端、前端全测试/构建、真实浏览器高度及上一轮12项交互回归通过。
- [x] Web wheel重建/安装/真实软件Runtime两页闭环通过，Runtime/SDK复用且逐字节核对，交付ZIP和摘要更新。

## 发布协调如何改变

旧应用全局 `target_lock` 包住发布RPC等待，也被所有目标的Start/Stop取得；`EditorStore.publish_lock` 又让不同目标发布排队。只改一层不能解决。

现在 `app.py` 的目标锁与 `editor.py` 的发布锁分别按稳定 `runtimeId` 保存，同目标切换工程仍使用同一目标锁。发布入口的锁顺序为目标锁→该目标发布锁；查询、complete、回退、结束跟踪采用相同规则，锁内重新读取权威版本记录。A的网络等待不持B的锁，故B控制及发布可以完成。

同目标改址或删除继续等待该目标发布结束；启用前仍核对Runtime实例、工程、请求配置修订和内容，不把旧客户端确认用作新目标成功。删除后保留该ID锁对象，已排队操作与后续同ID目标仍协调。取消/失败通过async上下文释放锁，未知请求保持查询/明确继续/结束出口，不自动重放写入。

共享用户目录锁只包本地用户/ACL操作，授权先取目标锁再取短用户锁，不持共享锁等待A。旧包多目标启用排序取对应锁，全部校验后仍一次 `activate_many`。没有新增事务框架、后台补执行、禁用控制或强制停任务。

本轮结论限于“发布等待不阻塞其他目标控制或发布”。原Operations控制意图锁未修改，不宣称任何长控制RPC都已实现跨目标并行。完整锁顺序和现有职责见 `backend-notes.md`。

## 固定预览高度从哪里来

Editor把所选终端的完整逻辑高度通过可选 `viewportHeight` 传给PanelRenderer，示例与实时共用该入口。保留原顶部16px间距，所以1024×768的内部弹性布局使用 `768-16=752px`，底部组件到终端逻辑bottom=768。固定外壳设置height/minHeight和overflow:auto；超高内容保留尺寸，用滚动访问底部。canvasTop扩展只加一次。

跟随窗口不传新参数，正式运行调用也不传，继续使用原 `window.innerHeight-root.top-16`、resize和ResizeObserver规则。不是把所有运行页面改成固定高度，也没有更换布局系统。

## 两个直接反例的前后结果

| 反例 | 修前 | 修后 | 证据 |
| --- | --- | --- | --- |
| A发布等待，B正常Stop | B未完成且仍running；A结束后才stopped | A仍pending时B已stopped；原脚本正常/取消两分支均成立 | `cross-target-lock.py`、`cross-target-lock{,-after}.json`、`cross-target-stop-{before,after}.log` |
| A发布等待，B Start及发布 | 两项也被排队，3项产品修前检查均失败 | B正常Start和发布在A仍pending时完成 | `backend-isolation-before.log`、`backend-before/`、`backend-after/cross-target-{start,publish}.json` |
| 1024×768+上下布局+弹性空白，宿主900/1200 | 示例content645/945，实时596/896，底部位置随宿主改变 | 示例与实时均terminal768/content752/top16/footer bottom768，正常scrollHeight=clientHeight=768 | `before-fixed-height-height-browser.json`、`after-fixed-height-attempt1-height-browser.json`及PNG |
| 超高内容 | 没有独立固定终端滚动边界 | 900px和400px组件保持尺寸，scrollHeight1364，scrollTop596后底部完整可见 | 同一高度JSON、`scroll-visibility-probe.json`及PNG |

短宿主900实际屏幕可达性另4/4通过：普通footer位于745..853，超高footer453..853，完整位于窗口内；外层backdrop和内层terminal均按需要滚动。几何检查使用真实Edge与构建React，HTTP/WebSocket明确模拟，没有声称其为真实检测验收。

## 实际检查与证据层级

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 跨目标及原目标管理/重复complete定向 | 17 passed、4 subtests passed；新增授权排队例随后纳入全套 | `backend-isolation-after.log`、`backend-after/` |
| Web后端全套，包含真实独立软件Runtime子进程 | 175 passed、218 subtests passed，无skip | `backend-full.log`、`product-runtime/` |
| 固定高度、跟随、溢出真实Edge（模拟HTTP/WS） | 6/6，共12个宿主场景 | `frontend-height-attempt1.log`、修后高度JSON |
| 真实Edge短宿主滚动可达性 | 4/4 | `scroll-visibility-probe.json` |
| 上一轮真实React交互及实际PUT/重开源 | 12/12 | `prior12-after-fixed-height-browser.json`、`frontend-prior12.log` |
| 全前端Node | 98 passed，0 fail，0 skip | `frontend-node.log` |
| TypeScript/Vite、静态资源同步 | 通过，dist/static5文件字节一致 | `frontend-build-final.log`、`frontend-package.log`、`frontend-static-sha256.json` |
| 新Web wheel独立安装、完整源/资源/发布、CLI无Node、轻量依赖 | 通过 | `web-wheel-build.log`、`web-wheel-install.log`、`installed-editor-wheel.json` |
| 已安装Web+SDK→源码软件Runtime→真实Edge两页闭环 | PASS；Web来自site-packages且Python -I | `installed-full-browser/`、`installed-full-browser-loop.log` |
| 安装包文件比对 | Web45文件、SDK15文件均与wheel字节一致 | `installed-wheel-content.json` |
| Runtime/SDK复用、当前源码及配套检查 | Runtime620个模块、SDK15个模块清单/字节一致；摘要与上轮一致 | `artifact-runtime-sdk-current.json`、`artifact-check-only-final.log` |
| 源码差异与静态审计 | diff通过；相关新增文件F检查通过；app两个HEAD既有unused保留，不记全文件lint通过 | `backend-diff-check.log`、`backend-ruff*.log/json`、`final-audit.json` |

完整安装后浏览器原始目录为 `web_panel/test-results/independent-editor/20261104-070138-dad073/`，report/截图/进程日志已复制到本目录。browserErrors=[]，静态资源4个响应200；明确Start后图片、数量2、OK及详情1600/2400属于正确结果，两页可切换，运行中发布/回退保持实际采集修订，整个Web/浏览器关闭后原任务继续，恢复后可重新订阅及读取静态图。截图已目视核对。

后端5项要求都覆盖：B Stop/Start独立；同目标改址/删除等待；取消/失败后query/complete/abandon/后续明确publish与管理可完成且未知提交只1次；原重复complete不覆盖新页面/采集配置的正确断言保留。真实Runtime测试继续验证Web不请求Qt采集、有效外部count=2与显式Qt失效绑定拒绝。

各组有范围重叠，不累加总数。本轮没有重跑第一轮188项Runtime/SDK/Qt采集检查：相关源码未改，复用既有结果并重新验证产物内容。临时模拟工程沿用既有WorkflowStore生成的2.9/1.2/devices1.0，校验、保存重读和编译流程保留，不访问设备。

## 交付及复现

- 新Web wheel：`web_panel/dist/emo_master_web_panel-0.1.0a1-py3-none-any.whl`，188804字节，SHA256 `c10957f0ee447cb3d34f443da16522d9f667e0a3d9f8f4dbf76d11c3385051ce`。
- 复用Runtime wheel：`build/independent-web-editor/runtime/emo_master-0.6.6-py3-none-any.whl`，SHA256 `aeb26acc932485cb6942b7cc0bffa10bb24118d5007913ca6da73febebd6ecd1`。
- 复用SDK1.5.0：`web_panel/contracts/panel-contract-v1/python/emo_panel_protocol-1.5.0-py3-none-any.whl`，SHA256 `97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d`。
- 完整摘要及前端5文件：`delivery-artifacts.json`、`frontend-static-sha256.json`。
- 更新归档：`build/independent-web-editor/independent-web-editor-delivery.zip`与同目录`verification-evidence.zip`。前者含三wheel、START-HERE、当前/历史验收及部署恢复说明；后者保留修前、修后、反例脚本和失败记录，不含运行SQLite/账号存储。
- 最终归档SHA256、大小、内部文件摘要校验见同目录`delivery-manifest.json`和`archive-verification.json`。归档摘要不写入证据ZIP，避免自哈希环。
- 上一版三wheel、两个ZIP、manifest和主/第一轮README备份保存在 `build/independent-web-editor/previous-before-review-round2/`，未覆盖第一轮证据。

前后端精确复现命令见 `backend-notes.md`、`frontend-notes.md`；本机启动和两页步骤仍见 `../README.md` 与 `web_panel/docs/DEPLOYMENT.md`。固定高度脚本为 `web_panel/frontend/tests/editor_preview_height_browser.py`；完整闭环脚本设置 `EMO_WEB_USE_INSTALLED=1` 使用已安装Web。产物齐备后运行 `scripts/package_independent_web_editor_delivery.py`，或加 `--check-only` 只做输入一致性检查。

## 保留的失败与未执行项

产品修前3项后端失败、固定高度/溢出失败和原用户反例全部保留。早期前端构建成功但尚未扣顶部16px的产物未充当最终几何验收；最终构建及静态指纹明确记录。app.py的两个Ruff unused是固定HEAD已有，基线比较无新增，不为无关清理改代码。未通过删断言、跳过或清除失败记录获得通过。

本轮未执行人工原生Qt、Designer GUI autoStart点击、Runtime wheel安装后完整检测、异机LAN、真实相机/PLC、冻结EXE/安装程序、现场部署；Qt无源码改动，本轮未重跑Qt桌面自动化。模拟API浏览器几何、真实软件Runtime集成、安装后Web运行、Runtime/SDK档案/源码核对分别记录，不能互相替代。本轮两项在上述实际证据范围内均已修复，无新增必修阻断。
