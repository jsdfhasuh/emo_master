# 第三轮前端收尾

当前分支 `codex/logging-consolidation-20261103`、HEAD `d92ffd37960e8b8f527136151ab9ccb80054e78f`，叠加已有未提交实现。完整读取本轮用户要求、计划和 AGENTS.md。保留旧修复和失败证据，没有回退、提交、推送、部署或操作设备。

## 修复

1. **切页选择**：统一 `changePage()`，编辑页签、示例 / 实时预览页签和导航按钮都清理选择。属性区、属性修改与绑定通过当前页 `componentContext()` 查找对象，避免旧页被改。历史恢复继续复用原 `restoreSelection()`。
2. **剪贴板关联定义**：保存组件子树实际引用的数据源、作用域及导航动作作用域。粘贴恢复稳定来源 / 作用域身份、重建组件 ID；相同 ID 已有不同定义则拒绝，先校验后修改。保留 `tidyBindings()` 清理和目标身份变化清空剪贴板。
3. **实际父布局**：嵌套网格子组件按直接父布局显示行列 / 跨度，网格拖动修改真实 `layout` 跨度并检查边界 / 重叠；响应式继续使用此前正确的缩放逻辑尺寸换算。Editor 和 PanelRenderer 网格采用 Qt 已有的 `64px` 最小行语义，确保跨度在编辑与预览都有尺寸效果，没有新增布局格式。

非阻断细节：

- 对照 Designer `palette.py`，组件名统一为图像、文字、数值、判定指示、集合表格、导航按钮、空白块、容器、连接状态、页面标题、页面导航。内部类型和已有用户标题不改。
- 实时预览显示真实后端 `jobMetadata` 的工程 / 执行 / 采集修订；缺失时明确“Runtime未提供”，不使用当前工程值替代，不要求各类修订相等。真实元数据查询由主任务实现并验证。
- 发布状态显示额度诊断 message 和后端提供的页面 / 组件关联；额度计算、中文诊断及控件关联由其它分工处理，前端未增额度或丢来源。

## 修前

用户原脚本复制为 `user-original-counterexample.py`；`user-counterexample.py` 只改证据输出目录和被测 dist 路径。先运行再改产品，结果在 `before-user-probe/`、`before-user-probe.log`，源指纹在 `frontend-before-source-sha256.json`。未覆盖 round2 原证据。

再次真实复现：复制删除粘贴后来源为空，正式校验报“来源不存在 source-count”；预览切页后保存真的改到旧页标题；网格拖动仍 `418×91`、只写响应式首选尺寸；嵌套网格无 row / column 输入。

## 验证

本机真实 headless Edge / 正式构建 React，模拟 HTTP / WS。新增编辑案例每次实际 PUT 执行当前正式 Web 后端 `validate_source()`，并检查保存 payload 及重载持久化源。不是实际 Runtime / DisplayService 验收。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 新编辑 / 名称 / 任务修订 / 诊断 | **13/13 通过** | `editor-after-attempt2-browser.json`、PNG、`frontend-editor-attempt2.log` |
| 前一轮全部浏览器回归 | **12/12 通过** | `prior12-round3-browser.json`、PNG、`frontend-prior12.log` |
| 固定高 / 跟随 / 溢出，示例与实时，两种宿主高 | **6/6，通过12个宿主场景** | `height-round3-height-browser.json`、PNG、`frontend-height.log` |
| 全前端 Node，含7项新模型检查 | **105/105，0 跳过** | `frontend-node-final.log` |
| TypeScript / Vite | 通过 | `frontend-build-attempt1.log` |
| 受影响 diff 检查、脚本 py_compile | 通过 | 命令完成，无输出 |

新增13例：示例 / 实时的预览页签和导航按钮及普通编辑切页（5）；绑定、嵌套来源与动作作用域、共享来源、静态组件的复制删除粘贴（4）；根网格与嵌套网格（2）；任务修订有值 / 缺失及名称和发布诊断（2）。

关键结果：

- 切页后仅详情页标题变为 `Edited detail only`；检测页保持 `component-A`；保存重开源一致。
- 剪贴板恢复 `source-count` / `scope-wf`；嵌套案例也恢复 `scope-action`；正式保存和重开均通过。
- 根网格 `418×91 → 848×140`，保存 `rowSpan=2,columnSpan=2`，预览高140与跨度一致；responsive 字段未改。
- 嵌套网格约 `257×91 → 526×216`，保存 `row=1,column=1,rowSpan=3,columnSpan=2`，预览行列 / 跨度一致，高216。
- 当前工程999、任务工程3时显示3及完整 execution / capture 值，未混入999；缺失工程修订显示“Runtime未提供”。
- 发布拒绝可见“3种图片 / 上限2种 / 包含Qt需求”和具体页 / 控件 ID。

`editor-after-attempt1-browser.json` / log 保留首次10过3失败。详见 `browser-attempt1-fixture-diagnosis.txt`：嵌套测试把动作放在容器上，正式模型正确拒绝；另两例名称定位漏算图标。改合法导航按钮子组件及准确定位后13/13，产品校验和断言目标未弱化。一次辅助脚本 cwd 错误未改到文件，随后使用正确仓库目录完成。

## 精确命令

```powershell
Set-Location D:\jsdfhasuh\documents\my_project\emo_master\web_panel\frontend
npm test
npm run build
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& ..\.venv\Scripts\python.exe tests\editor_round3_browser.py --label your-editor-run
& ..\.venv\Scripts\python.exe tests\editor_round3_browser.py --label your-prior12-run --suite prior12
& ..\.venv\Scripts\python.exe tests\editor_round3_browser.py --label your-height-run --suite height
```

使用新 label；旧回归包装输出定向 round3，没有覆盖 round2 / review-fixes。

## 冻结和交付边界

产品及测试已冻结，见 `frontend-frozen-source-sha256.json`。实际被本轮编辑回归测试的 dist 指纹见 `frontend-tested-dist-sha256.json`（`Editor-M8PMPE9S.js`），只代表被测构建，不冒充主任务最终交付 hash。

其它分工随后补充 main.tsx 在途目录请求取消并独立回归。主任务负责最终统一 build / static / wheel / ZIP / 摘要；本分工冻结后不再写 dist、static、wheel，也不改共享总 README。

真实软件 Runtime、安装 wheel 完整闭环、人工 GUI、跨机 LAN、真实设备和冻结 EXE 由主任务分层记录；本子任务未执行，不用模拟 HTTP / WS 编辑检查替代。
