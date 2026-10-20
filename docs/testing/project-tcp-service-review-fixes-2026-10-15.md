# 项目 TCP 通信服务：复审修正与验证

- 日期：2026-10-15。
- 授权：用户在审查四项问题后要求“改正吧”。
- 工作区：`D:/jsdfhasuh/documents/my_project/emo_master`。
- HEAD：`8c3495c0f96f00b39a6741e9ca85bf574d670340`；保留既有未提交修改，未提交、未推送。
- 原计划：[项目级 TCP 通信服务](../plans/2026-10-14-project-tcp-communication-service-v1.md)。
- 原实施证据：[首次实施快照](project-tcp-service-2026-10-15.md)。原快照与摘要不覆盖本轮新增修正，未覆盖或改写原证据。

## 修正范围

### 1. 禁用保存与启动竞争

- 新增 `PREPARE_DISABLE / ABORT_DISABLE` 控制动作与单个有界保存保护凭据。
- Runtime 在原子检查 STOPPED/无监听资源后建立保护，期间拒绝其他启动、正式运行自动启动、配置覆盖、重载与项目替换。
- Designer 异步申请保护；确认后在 GUI 线程调用原项目保存通道；成功后携凭据 SYNC 禁用配置并解除保护。
- 保存失败恢复已应用项目配置，保留对话框草稿，撤销保护；不自动停止服务，不为了恢复状态自动启动。
- 准备回执丢失可撤销未落盘操作；提交回执丢失可通过“同步已保存配置”确认，不自动重复动作。关闭窗口时若尚未落盘，先撤销准备。
- 保护不会因计时自动失效。如果持有窗口丢失且结果无法确认，保持禁止启动，面板要求显式重启 Runtime，再加载磁盘上的真实项目；这是保守恢复边界，不是自动恢复承诺。

### 2. 项目路径与身份隔离

- 查询绑定项目路径；控制同时校验规范路径、projectId、Runtime 身份及项目加载代次。
- Controller 在刷新前也检查当前文件与缓存状态是否一致，避免另存为后的旧权限窗口。
- 同 ID 不同文件必须经过正常加载/替换；不能轻量 SYNC 到旧上下文。
- 打开其他项目不再根据旧本地草稿的 enabled 值决定是否释放资源。
- 另存为在副本成功写入后、切换当前文件身份前释放旧运行上下文。释放失败保留原上下文，并通过日志/错误提示明确“副本已写入，但尚未切换”。
- 管理协议提升为 **1.1**。旧版协议拒绝与“API 根本不存在”分开处理，不能因协议不匹配推断资源不存在。会话/回执 1.0 与项目 schema 2.5 不变。

### 3. 完整配置修订号

- `desiredRevision` 对完整配置求摘要，包含 enabled 和 startupPolicy，供管理操作并发校验。
- 会话的 `configRevision` 继续仅绑定传输参数，策略变化不会无故使已有连接引用失效。
- 旧版本覆盖启用开关或启动方式返回 `E_TCP_CONFIG_STALE`；本地与 Runtime 配置不一致时禁止启动旧配置。

### 4. IPC 有界取消与 deadline

- 新增 `pipe_io.py`；保留 multiprocessing Pipe 的消息/帧格式。
- Windows 使用 overlapped ReadFile/WriteFile、20 毫秒检查与显式取消；不启动可能遗留的后台写线程。
- POSIX 分支使用非阻塞 fd 与 select，**本轮在 Windows 执行验证，未声称实测 Linux 分支**。
- 请求携带同机 monotonic 绝对时限，覆盖 IPC、派发、排队和网络发送；过期请求在进入发送边界前拒绝。
- 代理获知当前服务的发送超时；250 毫秒仅用于接收终态回执，不延长网络发送时限。
- 管道写入取消/超时仍保守报告 OUTCOME_UNKNOWN；不自动重试。桥关闭先取消并等待 I/O 退出，再关闭句柄。

## 自动化验证

Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。

| 范围 | 本轮结果 | 证据 |
| --- | --- | --- |
| TCP 综合专项、操作端独立性与注册门槛，含新增 21 项复审用例 | **107 passed**，42.19 秒 | `integration.txt` |
| 相机配置、Job、连续运行、调试、心跳、项目/包/客户端相关回归 | **186 passed**，72.66 秒 | `regression.txt` |
| 旧 TCP 算子、通信契约、算子生命周期、注册与参数标题 | **63 passed**，2.69 秒 | `legacy.txt` |
| Designer 保存/加载、另存为、窗口生命周期、项目存储、启动反馈与菜单 | **34 passed**，7.96 秒 | `designer-regression.txt` |
| Ruff | PASS | `ruff.txt` |
| Mypy（仓库既有配置，未开启所有未注解函数体检查） | PASS，433 个源文件 | `mypy.txt` |
| Protobuf 生成一致性 | PASS | `scripts/gen_proto.py --check` |

上述套件存在重叠，不能累计成独立用例总数。测试包含实际 Windows Pipe、4 MiB 消息、无父端读取时取消/超时、超时派发拒绝、真实 spawn Job 和回环客户端；故障使用明确注入，不以重复碰时序代替断言。

新增用例集中在 `tests/runtime/test_tcp_review_fixes.py`，覆盖跨客户端启动竞争、磁盘失败、回执丢失、关闭期间撤销、旧版本拒绝、同 ID 不同路径、释放失败仍保留旧身份等。

### 过程异常与验收边界

增加“另存为释放失败”用例时，一次离屏测试在 `QMessageBox.warning` 原生调用处出现 Windows access violation，进程退出码 `0xC0000005`。原始日志保存在 `integration-modal-access-violation.txt`。没有证据将其归因于 TCP I/O，也没有宣称修好了该原生 Qt 异常。

该故障注入用例随后改为截获警告呈现接口，严格断言错误文本包含“副本已写入”、文件存在且 Designer/Runtime 均仍保持旧项目。没有取消失败路径或放宽资源/身份断言；因此后续通过证明的是状态与错误内容，而不是原生模态框渲染/交互。

真实 Windows 菜单/Dock/模态框、多 DPI、手工空白工程构建仍 **NOT_RUN**。未重跑整个测试库，不将本轮定向通过改写成全量 PASS。首次实施的私有夹具基线分类仍只作为历史证据。

## 文件保护与证据

日志与本轮文件摘要：`manual_test_workspace/tcp-service-20261015-review-fixes/`。

仅调整通信链路、必要项目入口、对应回归及文档。原有 numeric/blur/truncate、配方及其他未涉及文件对照原 66 文件摘要核验；未更改现场工程或依赖。

未连接相机、PLC 或机器人；D800 与 PLC 逻辑不变。未打包、发布、部署或执行生产动作。
