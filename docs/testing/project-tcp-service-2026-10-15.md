# 项目 TCP 通信服务实施与验证

> 本文保留首次实施时的测试快照。后续审查发现并修复了禁用保存竞争、同 ID 不同路径隔离、配置修订号遗漏以及 IPC 写入取消四项问题；最新修正与验证见 [复审修正报告](project-tcp-service-review-fixes-2026-10-15.md)。本文中的初次 P0—P4 PASS 不代表这些边界当时已覆盖。

- 日期：2026-10-15。
- 工作区：`D:/jsdfhasuh/documents/my_project/emo_master`。
- 基线：`agent/runtime-workflow-architecture-v1`，HEAD `8c3495c0f96f00b39a6741e9ca85bf574d670340`；本次修改未提交、未推送。
- 计划：[项目级 TCP 通信服务 v1](../plans/2026-10-14-project-tcp-communication-service-v1.md)。
- 使用说明：[项目 TCP 通信服务](../guides/project-tcp-service.md)。

## 结论

P0—P4 的源码已实现，TCP 专项、操作端独立性、相机/Job/项目相关回归及旧通信测试通过。服务由 Runtime 主进程持有；项目开关、服务监听和客户端连接是独立维度。两个真实 spawn Job 与两个真实回环客户端已经验证共享一个监听器、按通道发送且一轮结束不断线。

P5 尚未完成真实 Windows 桌面、多 DPI 和手工空白工程验收。一次全量测试快照有失败，不能宣称全库通过；其失败已分为旧夹具基线与本次修复项。未连接现场相机、PLC 或机器人，未打包、发布或部署；PLC 按已确认 D800，未改 PLC 逻辑或私有工程。

## 实现范围

### 项目配置和界面

- schema 2.5 强类型 `communication` 配置，旧项目默认关闭，不因打开或保存而监听。
- 项目开关、随运行/手动启动方式、Runtime IPv4/端口、稳定服务/通道 ID、显示名称、白名单映射、毫秒/字节限制。
- Designer **运行 → 通信服务…**，底部通信 Dock 与可点击 TCP 状态栏。Dock 隐藏/浮动不停止服务。
- 设置是项目草稿；取消不改配置。保存沿用项目原子保存/备份，再轻量 SYNC；落盘和 Runtime 同步结果分开显示。初次仅打开的编辑草稿需要明确同步已保存工程，不以保存隐式启动监听。
- 已保存与当前生效参数分开：待生效时显示端口、地址、发送限制等变化；不热切断连接。运行中或未知状态不能关闭项目开关。
- 监控展示所有通道、连接代次、近期发送、HEX/UTF-8、回执和节点来源，历史断档与截断明确。RX 只是原始字节块，不是协议消息。
- 独立操作端复用公共 Qt 控制器与独立通信客户端，不导入 Designer；仅观察/显式启停，不另设可编辑现场配置。取消上下文放在 Qt-free 公共客户端层，Designer 保留兼容导入。

### Runtime 和工作流

- Runtime 持有监听器、accepted socket、通道状态、租约和有界历史。每通道一个客户端，拒绝未知 IP 与重复占用，不自动踢掉旧连接。
- 自动方式先通过相机配置/调试/准入保护，再监听，再 spawn Worker；手动方式不隐式启动。
- 正常结束、停止当前/全部流程只撤销 Job 权限，不停止公共服务。同项目路径与 ID 的重新同步保留连接；成功替换项目、明确停止服务和 Runtime 退出释放连接。
- 只给实际依赖通信的 Job 创建专用 Pipe；Worker 仅有受 Job/Runtime/项目/服务/连接代次限制的代理，没有监听器和服务控制权限。
- 新算子：`communication.tcp.session`、`communication.tcp.session_send`、`communication.coordinate_message_format`。增加类型化 `tcpSessionRef`、`tcpSendReceipt`、静态资源引用检查和可达子流程/循环依赖预检。
- 通过会话输入先取得本轮连接，再调用采集/计算子流程；发送只接受这一会话，不查找“最新连接”。重连、服务重启、Job 结束使旧权限失效。
- 发送结果区分 `NOT_SENT`、`SENT_TO_TRANSPORT`、`OUTCOME_UNKNOWN`；发送边界异常与回执丢失不自动重试。部分发送异常断开原客户端，避免后续报文接在半帧上。
- 取消与 IPC 失联竞争时仍使用实时 Job 取消令牌判定 ABORTED，同时保留可能未知的发送回执。

固定上限：每通道排队 32 条/16 MiB，单条最多 16 MiB，Worker 桥 128 个，租约 4096 个，记录 512 条/1 MiB，单记录原始数据 1024 字节，IPC 24 MiB/128 条去重回执，管理请求去重 2048 条。

### LN5 字节边界

正常样本为 UTF-8 的 `'12.345,-6.78EY'`，含两端单引号共 **16 字节**，无换行；遵循 Python `round(value, 3)` 后 `str`，不补齐三位小数。测试保留 `'0,0EN'`、`0,0EX`、`'0,0EX'` 原始差别，不添加自动 NG/EX 动作。

这证明报文与传输契约，不证明机器人动作完成、旧视觉算法或现场节拍。回执始终 `deviceConfirmed=false`。托管生产连接的孤立单算子调试不支持，纯格式化算子支持调试；未引入手工发送/历史重发控制台。

## 最终定向验证

Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，3.10.21；PySide2 5.15.2.1、pydantic 2.13.5、grpc 1.78.0。

| 层级 | 命令范围 | 结果 |
| --- | --- | --- |
| TCP 专项及修复回归 | 6 个新测试文件 + SQLite 注册门槛 + 操作端窗口 + 独立 self-test | **86 passed**，36.88 秒；未处理线程异常按错误处理 |
| 相机/Job/项目/客户端回归 | 相机配置 RPC、JobSupervisor、continuous、operator_debug、心跳终态、core/project、运行包、全局变量包、RuntimeClient | **186 passed**，69.31 秒 |
| 旧能力回归 | TCP Client/Receive Once、通信 payload、通信流程、算子生命周期、注册与中文参数标题 | **63 passed**，2.35 秒；线程异常按错误处理 |
| Ruff | `ruff check src tests examples/project_tcp_service.py` | PASS |
| Mypy | `mypy src/emo_master --no-incremental` | PASS，432 个源文件；按现有 mypy 配置执行 |
| Protobuf | `scripts/gen_proto.py --check` | PASS |
| Designer 启动检查 | `scripts/dev.py run-designer --local --check` | PASS；不创建窗口、不启动 Job，不算桌面验收 |
| Qt 离屏图像 | 显式载入 Windows 字体、文字栅格探针、真实本机回环 TX 后渲染实际 Qt 控件 | PASS，662 个文字探针像素，16 字节逐字节一致，结束后连接/线程/Worker 全释放 |

各套件有重叠，**不累计测试数量**。专项覆盖真实 embedded/gRPC 管理路径、两个 spawn Job、同项目重载、有效/无效项目替换、背压/部分发送确定性注入、重连隔离、请求去重/过期/摘要不一致、回执丢失、强制与平缓停止、旧 Worker 不能访问服务新代次、设置保存失败/待生效/失联及迟到响应隔离。

主要复核命令：

```powershell
python -B -m pytest tests/runtime/test_managed_tcp.py tests/runtime/test_tcp_service_integration.py tests/designer/test_project_communication_ui.py tests/core/project/test_project_communication.py tests/core/project/test_tcp_delivery.py tests/plugins/test_managed_tcp_operators.py tests/sqlite_writer/test_dependencies.py tests/ui/operator_view/test_production_window.py tests/test_operator_runtime_validation.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning

python -B -m pytest tests/runtime/test_camera_configuration_rpc.py tests/runtime/test_job_supervisor_contracts.py tests/runtime/test_production_continuous.py tests/runtime/test_operator_debug_resources.py tests/runtime/test_operator_debug_rpc.py tests/runtime/test_worker_heartbeat_finalization.py tests/core/project tests/core/test_runtime_package.py tests/core/test_global_variable_packages.py tests/designer/test_runtime_client.py -q

python -B -m pytest tests/plugins/test_communication_operators.py tests/runtime/test_builtin_communication_operator_workflows.py tests/core/contracts/test_communication.py tests/runtime/test_operator_lifecycle.py tests/runtime/test_operator_registration.py tests/core/plugin/test_builtin_parameter_titles.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning
```

## 全量快照与基线分类

实施过程的一次 `python -B -m pytest tests -q` 为：**5696 passed、133 failed、11 skipped，另有 41 subtests passed**，1921.77 秒。运行期间仍有源代码修正，因此它是过程快照，不是最终固定源码全量验收。

失败已逐个按测试身份对照：

1. **129 个私有工程历史夹具失败已在 TCP 修改前的源码基线复现。** 隔离探针使用 HEAD 的源码，加本轮开始前已有的 numeric/blur/truncate 改动和同一套本地测试/私有工程文件；它不是原始干净 HEAD 的发布验收。独立复制私有工程而非通过 junction 扫描当前插件，避免路径回指污染比较。七个文件的基线运行为 128 failed、82 passed、1 skipped；补齐只读复制的历史 `before.emoproj` 后，唯一跳过用例单独复现同一失败，合计 129 个不同失败身份与全量对应。
2. 这些失败涉及已变化的 workflow/node ID、旧流程数/模型名、历史初始生成图与已确认 revision 10 工程的差异。未为通过旧断言而恢复工程、改 D800、运行生成器落盘或覆盖确认配方。
3. **另 4 个失败已修复并在最终 86 项定向验证通过**：新增算子的严格版本拒绝集合预期、两个操作端 self-test 因误导入 Designer 失败、窗口按钮集合因新增通信入口需要精确更新。版本门槛与操作端禁止导入 Designer 的检查均保留，未弱化。
4. 修复后没有再做 32 分钟完整全量重跑，不能据此给全量标 PASS；最终源码依据上面的定向结果。裸 `pytest -q` 还会收集既有 `manual_test_workspace` 历史源码快照并发生 import mismatch，未删除这些快照或修改收集规则，正式范围明确使用 `tests`。

完整失败身份与分类保存在本地证据 JSON，原始日志保留，不把基线失败称为 TCP 新回归，也不把全量失败隐去。

## 本地证据和工作保护

本轮证据目录：`D:/jsdfhasuh/documents/my_project/emo_master/manual_test_workspace/tcp-service-20261015-implementation/`，处于既有忽略目录，不改其他人工验收资料。

- `logs/.tmp-tcp-final-integration.txt`、`logs/.tmp-tcp-final-regression.txt`、`logs/.tmp-tcp-final-legacy.txt`：最终定向日志。
- `logs/.tmp-tcp-final-full.txt`：过程全量原始日志。
- `logs/.tmp-tcp-isolated-baseline-result.txt`、`logs/.tmp-tcp-isolated-baseline-backup.txt`：隔离旧源码基线。
- `implementation-evidence.json`：测试范围、失败分类、工作区保护和源码文件摘要。
- `preview.py`、`preview-result.txt`、`communication-panel-offscreen.png`、`communication-settings-offscreen.png`：可复核 Qt 离屏预览，**不是原生 Windows 屏幕截图**。

本轮开始前的 8 个无需接入的 numeric/blur/truncate/配方测试文件 SHA-256 均保持不变。另 6 个已有 dirty 文件只在保留并行内容基础上接入计划/版本/算子注册预期；新增修改未提交、未推送，未更改依赖。

## 后续验收门槛

| 项目 | 状态 |
| --- | --- |
| P0—P4 源码及定向软件验证 | PASS |
| 全量库验收 | 非 PASS；过程快照含已分类失败，待最终固定源码重跑及私有夹具基线整理 |
| 真实 Windows 菜单、Dock 浮动/隐藏恢复、长文案、节点定位 | NOT_RUN |
| 100%、125%、150%、200% 原生 DPI | NOT_RUN |
| 手工从空白项目构建启用/禁用项目、两条流程、保存重开 | NOT_RUN |
| EXE 打包、发布、部署 | NOT_RUN |
| 现场相机、PLC D800 只读、隔离机器人通信、节拍稳定性 | NOT_RUN，另行授权 |

管理界面查询/启停在独立观察线程执行；已有项目替换路径仍使用受保护的同步 RPC，不能宣称全部窗口网络入口都已异步化。现有 Runtime 项目锁内的有界控制释放沿用保护链，正常发送/等待连接不持有项目总锁。
