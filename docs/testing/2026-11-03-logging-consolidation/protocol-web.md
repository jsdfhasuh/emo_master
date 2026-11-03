# S4 协议 / S6 Web 实施记录

基线与当前 HEAD：`940994b3420b47ff0add8766ccce5a4f2686a02a`；分支 `codex/logging-consolidation-20261103`。本轮修改未提交，交付物 `sourceDirty=true`，没有提交、推送、发布。

实现：`proto/runtime.proto` 与唯一正式 generated 代码增加 JobEvent 三个元数据字段、StoredLogEvent 和 QueryRuntimeLogs；SDK/依赖/健康提示/CI 断言同步 1.4.0。Runtime SQL/Service 主体由主执行者记录。Web 新历史查询无当前工程或 Job 预检，HTTP 64 位 ID/sequence 为十进制字符串，业务错误与 UNIMPLEMENTED 保留 code、实例与游标。新增日志端点复用 session、Runtime view ACL 与既有取消；JSONL 下载固定筛选和上界，不增加统计行，查询失败不产生伪日志下载。

`RuntimeLogs.tsx` 复用 api 的 sessionGeneration/401 通知、AbortController；实时接入先用无业务筛选 limit=1 确定起点，按全局 eventId 合并；历史分页保留记录，筛选/目标/已知实例变化取消旧请求并重建，普通断线保留游标，RESET_REQUIRED 重建，UNIMPLEMENTED 按连接缓存，单条超限不自动重试。结构化 payload 与原始 Job sequence 可查看详情。网关 JSONL 独立于 Qt/emo_master，位于 data_dir/logs；只记录方法、路径、状态、耗时及明确关联字段，不记录请求体/token/query。网关导出固定文件集合、字节终点与文件身份，所选文件被轮转替换或范围缺失时报错；HTTP 失败最终边界一次；200 业务失败由日志路由记录一次，export 409 由 middleware 一次。管理员日志区使用有界滚动区域，审计保留原入口。

## 验证命令和结果

Python 主环境为 `D:/Conda/envs/emo_master/python.exe`；独立 Web 环境为仓库 `build/logging-web-venv/Scripts/python.exe`。前端命令在实际 D: 仓库目录运行。

- `python scripts/gen_proto.py`、`python scripts/gen_proto.py --check`：通过。
- `python scripts/build_panel_contract.py --skip-proto --output build/logging-consolidation-panel-contract-sdk-1.4.0 --archive build/logging-consolidation-panel-contract-sdk-1.4.0.zip`：通过，39 文件，SDK wheel 1.4.0；模型/保存重读/工作流编译/normalCapture 通过，生成样例 DesignerOpen=NOT_RUN。当前 vendored 合同/fixtures 更新，新 handoff 仅说明与 manifest，历史 1.3.0 交付证据保留。
- `build/logging-web-venv/Scripts/python.exe -m pytest web_panel/tests -q -ra`（PYTHONPATH=src;web_panel/backend;web_panel/tests）：150 passed、2 skipped、207 subtests passed，见 `web-pytest.txt`。两个跳过是 opt-in owning Runtime 集成，不计通过；随后由其他执行者启用并记录。
- 增加最终本地验证错误合同、固定网关范围、失败只记录一次、入口初始化失败日志和关闭断言后，`... -m pytest web_panel/tests/test_app.py web_panel/tests/test_runtime_contract.py web_panel/tests/test_contract_manifest.py -q`：31 passed、63 subtests passed，见 `web-final-focused.txt`。
- `npm ci`、`npm run build`（tsc + Vite）、`npm test`：通过，77 个前端测试，见 `web-build.txt` / `web-frontend-tests.txt`。新增用例覆盖全局 ID、超过100条历史页、错误不当作成功合并、Blob 的同源凭据/401/取消/结构化错误。
- `python web_panel/scripts/package_frontend.py`、`python -m pip wheel ./web_panel --no-deps --no-build-isolation --wheel-dir web_panel/dist`：通过。现有 `web_panel/scripts/verify_wheel.py` 执行结果见 `web-wheel.txt`，独立安装 SDK/Web wheel，检查无 Qt/主应用/视觉模型导入、SDK1.4查询与optional上界0、现有普通Job/ABORTED/Stop身份、静态资源及 CLI。
- `build/logging-web-venv/Scripts/python.exe web_panel/frontend/tests/backend_e2e.py`：真实 FastAPI HTTP/WS + Chromium + mock Runtime 通过，见 `web-browser.txt`。实际点击旧工程筛选、查看结构化详情、下载JSONL并核对一行一条、读取网关422诊断；现有登录、冻结/恢复、控制、管理员、viewer权限、A/B目标隔离及手机布局回归通过。截图 `web-history.png` 和 `web-runtime-detail.png`。这是 Web 浏览器自动化；没有声称人工验收或真实硬件验收。
- `python -m compileall -q web_panel/backend/emo_web_panel`、`git diff --check`：通过。

## 过程中出现且已处理的失败

保留初始失败，不把修复前结果记为通过：

1. 主环境 TestClient 初次收集失败：Starlette 1.7 要求 httpx2，原 test extras 只有 httpx。建立独立 Web venv，按实际要求补 test extra `httpx2>=2.14,<3`，未更改主环境。
2. 前端起初无 node_modules，tsc 不存在；npm ci 解决。C: junction 下 Vite 将 C:/D: 路径混合而失败；从实际 D: 路径构建通过，没有修改 Vite 配置。npm ci 提示现有依赖一项 high advisory，本轮未升级非日志依赖。
3. 初次新增viewer查询测试误以为角色自动授权目标，1 failed/25 passed；改为先核对未授权403再显式授予查看权限，保留权限正确断言。
4. 初次全Web回归149 passed/2 skipped/1 failed，未启动 create_app 的 GatewayLog 立即打开文件占用Windows临时目录；handler 改为 delay=True，入口/实际请求仍正常写入；目标回归10通过，再全Web150通过。
5. 首次浏览器新增日志步骤等待范围select的标签超时，补明确 aria-label 后通过。失败截图/HTML仍在 frontend/test-tmp/failure.*，不是产品成功证据。一次Windows浏览器关闭出现 asyncio WinError10054回调提示但断言通过，最终带记录的浏览器复跑无该提示。
6. 反复本机打wheel时 setuptools build/lib 留旧静态资产；只清理该构建目录中源静态目录已不存在的文件后重建，verify_wheel 追加精确静态文件集合检查。历史验证记录不受影响。
7. verify_wheel新增descriptor检查首次出现set与protobuf keys()返回list比较TypeError；改为显式set，保留失败输出 `web-wheel-check-script-failure.txt`，最终结果见 `web-wheel.txt`。

边界：没有访问真实相机/PLC、网络改址、现场工程或凭据。Web生产部署、真实浏览器人工使用和硬件验收未执行；生成SDK样例实际Designer打开未执行，manifest诚实标NOT_RUN。

最后追加检查：网关读取固定范围校验文件身份和长度的局部修改后，`pytest web_panel/tests/test_app.py -q` 15 passed；轮转文件按旧到新读取。最终合同 README 已按 owning 文档最新打包示例单行同步，manifest/ZIP 重新校验，合同组 3 passed、63 subtests passed。
