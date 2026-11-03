# 独立审查 F03 / F08 修复记录

基线与当前 HEAD：`940994b3420b47ff0add8766ccce5a4f2686a02a`；分支 `codex/logging-consolidation-20261103`。在原 S1—S6 未提交实现上继续修改，没有重置、清理、提交或发布。原报告/日志保持，当前源码与构建仍为 `sourceDirty=true`。

## F03 — 已完成

根因：缓存 key 包含独立异步 `/status` 返回的 prop 实例 ID。日志查询已确认实例时，prop 从空变同一实例也会更换 key，触发 fresh cursor 与第二次 baseline，丢掉已经显示/尚未消费的新增记录。

修改 `web_panel/frontend/src/components/RuntimeLogs.tsx`：目标+筛选决定查询范围 key；独立核对 prop 与既有 cursor.instance。同实例仅确认，保留事件数组、after、当轮 upper 及正在读取的请求。只有实际实例不同才关闭旧请求/下载、清除旧实例显示并以新实例重建。baseline 保留预期实例，RESET_REQUIRED 使用回复的实例。接受响应前检查当前 Cursor 对象仍归属该请求，同时保留原 alive/AbortController/sessionGeneration/401 机制，不等待所有请求串行执行。

正式回归位于原 `frontend/tests/logging.test.mjs`：执行生产组件及 effect 的状态测试使用显式 React hook doubles，没有新增测试依赖；日志先返回、upper=12 的下一页待返回时 status 确认同实例，断言不 abort、不新增 baseline，11 与12都保留，下一轮 after=12；status 先返回只有一次baseline；实际实例变化 abort 旧请求，旧返回晚到不再写入，新实例从新上界接续。

原 `frontend/tests/backend_e2e.py` 新增三个真实 FastAPI HTTP+Chromium+mock Runtime 场景：logs-first、status-first、instance-change。前两种均只执行一次baseline；日志先返回时11、12均保留；实际实例变化恰执行第二个baseline，旧行清除，仅显示新实例21。正式请求轨迹见 `review-frontend-browser-observations.json`。临时独立审查脚本只作为复现依据，回归已经进入既有测试体系。

## F08 — 已完成

根因：实时组件与 liveCursors 均保存 merge 后全部事件。现在仅实时显示及缓存数组保留最新 **1000** 条，界面明确范围。after/upper/instance 不参与裁剪，历史 merge、历史分页和完整导出继续保存完整范围。没有新增通用缓存机制或性能专项。

组件/effect 回归连续读取6页×200条，显示最新1000条（201—1200）；卸载再挂载仍1000条并从 after=1200 接续，无额外baseline。切换历史后6页完整保留1200条。真实浏览器进一步验证：实时DOM在连续多页后1000条（213—1212），隐藏/重开保持1000；切换历史分页得到1202条，完整JSONL下载也有1202行。

## 实际执行

所有 npm 命令在实际 D: 仓库的 `web_panel/frontend` 运行。Python 独立 Web 环境为仓库 `build/logging-web-venv/Scripts/python.exe`，主 Python 为 `D:/Conda/envs/emo_master/python.exe`，没有更改主环境依赖。

- `node --test tests/logging.test.mjs`：**6 passed**，无skip；`review-frontend-focused.txt`。
- `npm run build`：tsc/Vite通过；`review-frontend-build.txt`。
- `npm test`：**81 passed**，无skip；`review-frontend-tests.txt`。
- `build/logging-web-venv/Scripts/python.exe web_panel/scripts/package_frontend.py`：同步实际构建到既有Web静态目录。
- `build/logging-web-venv/Scripts/python.exe web_panel/frontend/tests/backend_e2e.py`：新增三种实例顺序、实时DOM1000/隐藏重开、历史及JSONL1202通过，原登录/HTTP+WS/权限/控制/冻结恢复/目标隔离/手机布局回归也通过；`review-frontend-browser.txt`。这是浏览器自动化，不是人工或硬件验收。
- F07 后端修改落入后运行 `python -m pip wheel ./web_panel --no-deps --no-build-isolation --wheel-dir web_panel/dist`：通过，`review-frontend-wheel-build.txt`。只删除 `web_panel/build/lib/emo_web_panel/static` 中已不在源静态目录的构建残留文件，未触碰历史验证记录。
- 在 `web_panel` 执行既有 `python scripts/verify_wheel.py`：独立安装结果见 `review-frontend-wheel-verify.txt`，检查精确静态文件集合、SDK1.4元数据/optional上界/普通Job与Stop、无Qt/主应用依赖及CLI。最终构建hash见 `review-frontend-artifacts.json`。
- 针对三个修改文件运行 `git diff --check -- web_panel/frontend/src/components/RuntimeLogs.tsx web_panel/frontend/tests/backend_e2e.py web_panel/frontend/tests/logging.test.mjs`：exit0。

首次effect测试曾因跨vm数组原型的测试收集问题3通过3失败，保留 `review-frontend-initial-harness-failure.txt`；修正为宿主Array.from后保留全部原值断言并6通过。未删除原产品复现或历史失败证据。

本轮不改变协议/SDK，因此没有重建SDK或覆盖原sourceDirty manifest。没有访问相机、PLC、现场工程、凭据或网络改址；无冻结EXE、部署、真实设备、跨机器LAN或人工验收。本分工无剩余实现缺口。
