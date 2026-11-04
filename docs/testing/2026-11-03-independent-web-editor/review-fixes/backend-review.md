# Web 后端复核修复记录

2026-11-03，源码基线 `d92ffd37960e8b8f527136151ab9ccb80054e78f`，分支 `codex/logging-consolidation-20261103`，加当前未提交的独立 Web 编辑器实现。保留已有修改和修前失败日志。本轮未提交、推送、合并、部署，也未重新构建 wheel。

## 独立 Web 启动的采集意图

触发：工程有指向已删除节点的 Qt 页面来源，同时 Runtime 已保存有效的 Web 外部 `count.count` 请求。原 Web 客户端向 `StartJob` 发送 `capture_presentation=True`，使独立 Web 启动误请求 Qt 页面采集，最终被 Qt 失效来源拒绝。

修改：`GrpcRuntimeClient.control('start')` 明确发送 `capture_presentation=False`；Runtime 继续统一读取已保存外部请求。Designer / 本机 Qt 的显式采集意图和原有校验没有修改。

真实 gRPC 回归 `test_web_start_uses_valid_external_request_despite_deleted_qt_source` 使用独立 Runtime 进程及本机软件算子，测试解释器只安装轻量 Web/SDK 依赖。工程经 `WorkflowStore.toPayload()` 创建，使用完整 2.9 / 页面 1.2 结构，经过 `ProjectDocument` 校验、保存重读一致检查和工作流编译，无真实设备。修后 Web 启动被接受，实际 DisplayService 返回数量 2；同工程显式 Qt `true` 仍被 `stale-qt: output node/port missing` 拒绝。

证据：

- `web-start-before.log`：修前失败及拒绝原因。
- `web-start-before/`：Runtime 日志、工程生成校验记录、gRPC 网关观察记录。
- `web-start-after.log`：首次修后业务断言通过，但归档嵌套路径超过 Windows MAX_PATH，测试在 teardown 失败；保留该失败。
- `web-start-after-verified.log`：归档改用 Windows 扩展绝对路径后，真实 gRPC 回归与协议测试 14 passed。
- `web-start-after/` / `product-runtime/`：修后 Runtime 与网关结构化观察记录。

原 sample 工程只含 Qt 全局变量来源，无外部请求。其起停回归改为检查 Web 不隐式启用 Qt 采集：`captureEnabled=False`，读取快照明确返回 `CAPTURE_UNAVAILABLE`；原起停请求关联、日志、停止和不重放检查保留。有效外部来源与真实结果由上述新增回归验证。

## 发布与修改目标地址的协调

触发：提交外部请求的正常 RPC 尚在等待时，另一个管理请求修改相同 Runtime 标识的地址；旧客户端之后仍可启用页面并报告发布完成。HTTP 并发反例使用延迟 MockRuntime 提交，不涉及真实设备或网络地址变更。

修改：编辑器发布、发布状态查询、继续发布、回退和结束发布复用应用现有 `target_lock`，从解析目标到本次协调结束保持目标一致。锁顺序为现有目标锁 → 发布锁，不新增第二套目标状态或重试机制。并发地址编辑在发布完成后正常继续。

证据：

- `target-before.log` 与 `target-before/publish-target-address.json`：修前修改先完成，旧发布仍 PUBLISHED。
- `target-after.log`：编辑器、并发及目标管理检查 20 passed。
- `target-after/publish-target-address.json`：发布在途 `changedDuringPublish=false`，地址仍为原目标，随后发布与管理更新均完成。

## 最终源码检查

执行：轻量 `web_panel/.venv/Scripts/python.exe`，设置 `PYTHONPATH=src;web_panel/backend;web_panel/tests`、`EMO_WEB_PRODUCT_RUNTIME=1`、`EMO_WEB_PRODUCT_PYTHON=D:\Conda\envs\emo_master\python.exe`，运行 `python -m pytest web_panel/tests -q`。

- `backend-full.log`：169 passed、214 subtests passed，42.25 秒，无跳过。
- `backend-independent-source.log`：仅导入当前 Web 后端源码，环境中没有 `emo_master`、PySide2、cv2、onnxruntime，导入后也未加载这些模块。
- `backend-pip-check.log`：No broken requirements found。
- `backend-diff-check.log`：无空白错误，仅已有 Git LF/CRLF 提示。

这些是源码自动检查、真实本机软件 Runtime/gRPC 和 HTTP 并发反例结果，不代替浏览器、原生 Qt、最终 wheel 或现场设备验收。最终前端构建和 wheel 由主执行者统一完成。
