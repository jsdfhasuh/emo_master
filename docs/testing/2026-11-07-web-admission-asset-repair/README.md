# 本地修复证据

执行：**ChatGPT Web**。产品基线：`5f62eb7e2eb63e097e8e3385de34c96549eabf07`。
实施说明：[交接记录](../../handoffs/2026-11-07-pr11-admission-asset-retirement.md)。

本目录是该基线加本次改动的本地验证，不冒充固定旧版本通过。
推送后的固定 SHA、Windows/Linux CI 和实际浏览器结果见 PR #11 对应 ChatGPT Web 评论及 CI artifact。

- `admission-red-final.log`：固定旧源码 + 最终新增准入回归，10 项失败；
  `admission-red-source-final.json` 保存旧文件来源。
- `admission-green-final.log`：候选同一组 10 项通过。
- `asset-lifecycle-http-baseline.json`：真实网关旧/当前 resultKey 的 200/404 对照，最近四条边界未改变。
- `asset-lifecycle-{baseline,fixed}.log`：后端原因码与身份日志的两项红/绿反例。
- `asset-prepare-{baseline,fixed}.log`：前端将明确退休结果从 generic error 改为 unavailable 的红/绿反例，其他错误保留。
- `frontend-final.log`、`web-backend-full.log`：完整前端 183 通过；后端 221 通过、7 跳过。
- `frontend-build-final.log`、`ruff.log`、`mypy.log`：构建与静态检查。
- `product-runtime-integration.log`：软件 Runtime 集成模块 5 项通过，含 3 项真实 Runtime 场景与 2 项辅助用例。
- `windows-observation/two-job-local-final.*`：Linux Qt offscreen 的原双任务用例，1 项通过；不计为 Windows 通过。
- `windows-observation/diagnostic-aux-final.log` 及实际辅助脚本：6 项测试取证反例通过。
  脚本保留实际执行时的工作区来源，不替代仓库的原双任务用例。
- `product-browser-baseline.log`：本地 Chrome 在进入页面前因 IPC `socket()` 权限被拒绝；
  原始失败保留，不能算产品 HTTP 失败、Error 9 复现或浏览器验收通过。

常用命令（依赖已安装，仓库根目录执行）：

```bash
npm --prefix web_panel/frontend test
npm --prefix web_panel/frontend run build
python web_panel/scripts/package_frontend.py
python -m unittest discover -s web_panel/tests -v
EMO_WEB_PRODUCT_RUNTIME=1 QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m unittest discover -s web_panel/tests -p test_product_runtime.py -v
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/ui/page_designer/test_two_job_observation.py
python web_panel/frontend/tests/asset_retirement_browser.py
python web_panel/frontend/tests/product_runtime_e2e.py
```

Windows 原生 Qt 的 `QT_QPA_PLATFORM` 使用 `windows`，不复制上面的 Linux offscreen 设置。
浏览器脚本需要可启动的真实 Chromium，不能把语法检查当作其执行结果。
各集合有重叠，不相加为独立测试总数；`manifest.json` 校验本目录保留的原始文件。
