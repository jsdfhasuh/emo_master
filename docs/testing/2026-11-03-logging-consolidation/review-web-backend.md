# F07：Web 日志级别参数空格修复

日期：2026-11-03（Asia/Shanghai）。已阅读独立审查附件、AGENTS.md、v3.4 实施计划及原 README 交付记录，沿用当前未提交实现。分支 `codex/logging-consolidation-20261103`；实现基线及本轮 HEAD 均为 `940994b3420b47ff0add8766ccce5a4f2686a02a`。未重建工作区、回退、提交、推送或发布。

## 根因与最小修改

`web_panel/backend/emo_web_panel/app.py::log_query()` 原先只按逗号拆分并删除长度为零的字符串，没有去掉每项空白，因此 `ERROR, INFO` 传给 Runtime 的级别是 `['ERROR', ' INFO']`；INFO 记录被错误筛掉。该解析函数由查询及 JSONL 导出共用，两个正常入口都受影响。

本轮仅将该列表推导改为逐项 `strip()` 后去除空项。没有额外调整级别大小写、请求权限、会话、错误或分页合同，也没有新增依赖。

## 新增回归与修复前反例

在现有 `web_panel/tests/test_app.py` 的 TestClient/mock Runtime 夹具中增加 `test_runtime_log_level_spacing_matches_query_and_export`：

- `ERROR,INFO` 查询与 JSONL 导出都必须有实际记录，避免空结果等价造成假通过。
- 对比 `ERROR, INFO`、前后空格、重复逗号/空项，要求与紧凑输入的整个 HTTP 回复内容一致。
- 覆盖重复 `levels` 参数，验证每个参数中各项均已归一处理。

先增加回归、执行反例，再修改生产代码。`review-web-backend-before.txt` 保留修复前实际失败：**4 failed、15 deselected**，表现为原本两条 INFO 记录被筛成空列表。保留原失败输出，没有修改正确断言以获取通过。

## 实际命令和结果

全部使用已有独立 Web 执行器 `build/logging-web-venv/Scripts/python.exe`，SDK 为已有正式 1.4.0 wheel，没有改主环境依赖。

```powershell
$env:PYTHONPATH='web_panel/backend'
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_app.py -k runtime_log_level_spacing -q
```

修复后 `review-web-backend-after.txt`：**1 passed、15 deselected、6 subtests passed**。

```powershell
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_app.py web_panel/tests/test_runtime_contract.py -q
& build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests/test_app.py web_panel/tests/test_runtime_contract.py web_panel/tests/test_contract_manifest.py -q
```

- `review-web-backend-regression-final.txt`：**29 passed、6 subtests passed**。
- `review-web-backend-integration.txt`：**32 passed、69 subtests passed**。覆盖现有权限、401/CSRF、日志查询/错误、JSONL 下载、Runtime RPC 与合同 manifest。
- 首次集成命令误写不存在的 `test_manifest.py`，未执行用例；`review-web-backend-regression.txt` 保留该命令错误，随后定位真实 `test_contract_manifest.py` 并使用上述正确命令重跑，不记误命令为通过。

```powershell
& D:\Conda\envs\emo_master\python.exe -m compileall -q web_panel/backend/emo_web_panel/app.py web_panel/tests/test_app.py
& D:\Conda\envs\emo_master\python.exe -m ruff check --select F821,F822,F823 web_panel/backend/emo_web_panel/app.py web_panel/tests/test_app.py
git diff --check -- web_panel/backend/emo_web_panel/app.py web_panel/tests/test_app.py
```

语法、名字检查及 diff 空白检查通过，名字检查日志为 `review-web-backend-name-check.txt`。
另外实际执行全规则 Ruff，`review-web-backend-ruff.txt` 记录 **164 个现有文件风格/未使用导入诊断**，主要是原文件已有的单行复合语句；本轮未扩成整文件格式重写。没有将该全规则检查写成通过，新增 F07 测试和两行解析修改未产生该类新诊断。

## 边界与剩余项

F07 已完成，没有未处理的相关功能失败。此处是实际 FastAPI TestClient 和 loopback gRPC 源码集成测试；不记作浏览器、人工界面、冻结 EXE、真实设备或 LAN 验收。

已向负责协议/Web 交付物的分工通知本轮 backend 源码完成，可以重新生成受影响 Web wheel。此局部修改不改变 proto/SDK 合同。
