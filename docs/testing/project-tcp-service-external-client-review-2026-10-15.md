# 项目 TCP 服务独立客户端复审

本轮使用独立 Python 客户端进程、真实本机 TCP、回环 gRPC、Runtime 和 spawn 工作流继续审查。正常通信路径通过了本轮测试，但确认了三个尚未修复的问题：监听异常后的直接重启、旧发送回执覆盖新代次状态、设置页旧草稿覆盖并发配置。因此不能将结果标记为功能全部验收通过。

- 日期：2026-10-15。
- 授权：用户要求“你再继续审查 你可以直接编写一个客户端程序来测试”。本轮只新增测试客户端、测试和报告，不修改生产实现。
- 工作区：`D:/jsdfhasuh/documents/my_project/emo_master`。
- HEAD：`8c3495c0f96f00b39a6741e9ca85bf574d670340`。
- Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。
- 前轮实施及修复基线的 69 个文件逐个 SHA-256 核验一致；本轮结束再核验，保留既有未提交工作。

## 已确认问题

### TCP-R2-01 监听异常后直接启动仍占用原端口

优先级 P2。`communication/manager.py` 的 `_accept()` 遇到非停止期间的 `OSError` 后将状态设为 FAILED 并退出，但未关闭或移交仍持有的 `_listener`。`start()` 只检查活线程，没有处理残留监听 socket；通信面板又允许 FAILED 状态点击启动。

复现注入一次 accept 异常后，监听线程已经退出，`ownsResources=true`。独立客户端仍能完成 TCP 握手，但没有 accept 循环接收它；直接 START 返回 `E_TCP_BIND_FAILED`，占用者实际上就是当前 Runtime。显式 STOP 再 START 可以恢复。

修正方向：为 FAILED 的监听资源明确回收边界，启动前先证明旧资源已释放；或者明确禁止直接启动并提示先停止清理，不能把自有残留资源误导成普通外部端口冲突。已有连接与线程也必须纳入同一清理流程。

复现用例：`testListenerFailureCanRestartWithoutBindingOverOwnedSocket`。这是故障注入，不声称现场已经自然出现过 accept 异常。

### TCP-R2-02 旧发送完成覆盖新连接最近发送状态

优先级 P2。`communication/manager.py` 的 `send()` 在 finally 中无条件写入 `_lastSends[channelId]`。写入时没有重新核对服务代次及当前连接所有权；记录顶层服务代次还取自写日志时的当前服务。

复现将旧连接已经发送 OLD 的 finally 暂停，随后 STOP、START、重连并发送 NEW。客户端只收到正确的 `OLDNEW`，没有旧数据重发；但放行旧 finally 后，“最近发送”的 requestId 从 `new-send` 回退为 `old-send`，新服务事件中混入旧服务回执。界面的当前连接行直接展示该 lastSend，因此可能显示旧连接的成功或失败结果。

修正方向：历史回执保留原始服务和连接身份；只有仍属于当前连接、当前服务的完成事件才更新当前通道摘要。此问题属于状态归属错误，本轮没有观察到报文串发。

复现用例：`testOldSendCompletionCannotOverwriteNewGenerationStatus`。真实 socket 传输，注入的只是 finally 调度暂停；该用例直接调用管理器，不将它计为 spawn 工作流证据。

### TCP-R2-03 设置保存绕过旧草稿的并发冲突保护

优先级 P2。`ui/communication_controller.py` 的普通 `saveSettings()` 先保存草稿，再读取最新 Runtime 状态，用最新 `desiredRevision` 提交旧草稿。服务端完整配置 CAS 本身有效，但界面丢掉了草稿的原始基线。

复现 A 基于旧端口编辑服务名称；B 经真实 gRPC 成功修改端口；A 保存。界面再次读取 B 的最新修订号，然后把 A 的旧端口连同新名称提交，最终磁盘和 Runtime 都变回旧端口，没有冲突提示。已修复的禁用保存保护不是本问题的复现路径。

修正方向：设置页保存打开/编辑时的配置修订号，保存前检查并发变更。发生冲突保留草稿，要求刷新、合并或显式覆盖；不能通过重新读取最新版本号让旧草稿看起来是最新编辑。

复现用例：`testStaleSettingsDraftDoesNotOverwriteConcurrentConfiguration`。使用 Qt offscreen Controller、真实文件保存回调及真实 gRPC，不代表原生设置窗口交互验收。

## 独立客户端与通信验证

新增 `scripts/tcp_loopback_client.py`，只依赖标准库，强制目的和源地址为字面量 IPv4 回环地址。客户端与 Runtime、工作流 Worker 是不同进程；只在本机测试，不修改防火墙。

客户端支持自动定时观察、按字节慢读、暂停读取、正常断开、RST、显式重连、原始十六进制发送和 JSON-lines 交互。记录 PID、端点、连接事件、累计接收字节数、完整流 SHA-256 和最多 4096 字节前缀。不将一次 recv 当作一条协议消息，不自动重连或重发。

| 检查 | 本轮结果 |
| --- | --- |
| 自动策略加载不监听，正式 StartJob 才启动 | 通过 |
| 两个逻辑通道跨 8 个实际 spawn Job 共用长连接 | 通过，两个获准客户端各保持一个连接 |
| 坐标算子、IPC、真实 TCP、独立客户端全链路 | 实际收到 `'12.345,-6.78EY'`，16 字节，包含单引号，无换行 |
| EN、两种 EX 原始样本 | 字节流与期望完全一致，没有统一引号或自动生成异常业务动作 |
| 未知 IP、重复通道连接 | 被关闭，接收字节数为 0；原连接不受影响 |
| 正常断开与 RST 后重连 | 旧引用 NOT_SENT，新 Job 正常发送，没有旧结果重发 |
| 同路径同 ID 重载 | 服务代次和连接代次保持 |
| 客户端发送入站原始字节 | RX 观察记录保留 NUL、0xff，不解析为业务 ACK |
| 慢客户端真实背压 | 1000 ms 配置触发 E_TCP_SEND_TIMEOUT；本次为 NOT_SENT、0 字节；另一通道正常收发 |
| 正在背压等待时取消或停止服务 | graceful 约 0.796 秒，force 约 0.235 秒，停止服务约 0.469 秒，均未等待 10 秒网络超时 |
| 取消等待连接的 Job 后客户端才接入 | graceful/force 均不补发旧数据，新 Job 正常发送 |
| 停启、禁用保存保护、完整配置旧版本拒绝 | 通过真实 gRPC 验证 |
| 同 ID 不同文件替换 | 原连接关闭，新项目保持 STOPPED，旧上下文控制请求被拒绝 |
| 退出清理 | 每项夹具检查通信线程、Worker 桥、客户端进程释放，并用新 socket 重新 bind/listen 验证端口释放 |

背压夹具在独立客户端不读的情况下，将真实 accepted socket 的 SO_SNDBUF 请求设为 4096，并以真实 socket 写入填充至持续不可写。最终 Windows 实测预填充 131072 字节。这是测试专用的内核缓冲准备，不修改生产代码，不 mock send/select，也不把预填充字节算作工作流发送。当前实测证明的是发送边界前超时和取消；不据此新增“真实部分写入 OUTCOME_UNKNOWN 已验证”的结论。

## 自动化结果与过程记录

证据根目录：`manual_test_workspace/tcp-service-20261015-external-client-review/`。

| 测试集 | 结果 | 日志 |
| --- | --- | --- |
| 新增独立客户端及故障复审测试 | **11 passed，3 xfailed**，65.90 秒 | `final-run.txt`、`final-run.xml` |
| 原有 TCP 管理器、四项修复、集成、项目配置/交付、算子、离屏通信 UI | **73 passed**，18.09 秒 | `existing-regression.txt`、`existing-regression.xml` |
| 新增 Python 文件 Ruff | PASS | `ruff.txt` |
| git diff --check | PASS，既有 CRLF 转换提示保留 | `diff-check.txt` |

三个 xfail 对应上面的已确认缺陷，**不是通过项**。首次没有 xfail 标记的复现结果为 `3 failed`，保留于 `fault-run-1.txt/xml`。最终使用 `strict=True` 和仅用于最后缺陷断言的专用异常；前置条件或清理失败仍是普通失败，未来缺陷修复后会 XPASS(strict) 提醒移除标记。需要直接观察失败时可加 `--runxfail`。

慢客户端夹具的前几次尝试没有正确制造背压：Windows 回环会把首个甚至第二个 2 MiB 直接接受到内核缓冲，返回 SENT_TO_TRANSPORT，不代表客户端已读取。这些失败属于测试前提错误，不是发送超时失效。保留 `wire-run-1.txt`、`pressure-run-2.txt`、`pressure-run-3.txt` 及对应 JSON；没有删除失败证据。`pressure-run-4.txt` 是两次大消息预填充的通过记录；最终夹具进一步改为以持续不可写作为压力建立的证据。

本轮测试集与历史报告不相加。没有重跑全量库或 Mypy，也没有将历史全量失败改写为通过。原生 Windows 菜单、Dock、模态框、多 DPI、Linux 分支、现场设备及部署验收仍不在本轮结果内；前轮原生 QMessageBox access violation 也未由这些网络测试证明已解决。

## 使用方法

先将本机测试项目的 TCP 监听配置为回环地址及所选端口，显式启动服务。以下示例端口 10000 必须与测试项目一致。客户端自身不启动 Runtime 或工作流。

```powershell
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  'D:/jsdfhasuh/documents/my_project/emo_master/scripts/tcp_loopback_client.py' `
  --host 127.0.0.1 --source 127.0.0.1 --port 10000 --duration 20 `
  --expect-hex 2731322e3334352c2d362e3738455927 `
  --output 'D:/jsdfhasuh/documents/my_project/emo_master/manual_test_workspace/tcp-probe-result.json'
```

在 20 秒内运行一次对应坐标工作流。客户端用整个观察周期的累计字节和摘要比对，不要求单次 recv 收齐。若不需要精确匹配，省略 `--expect-hex`。期望不匹配会返回非零退出码。

添加 `--interactive` 可在标准输入逐行输入：

```json
{"action":"snapshot"}
{"action":"pause"}
{"action":"resume"}
{"action":"close","rst":true}
{"action":"connect"}
{"action":"quit"}
```

`--no-read` 模拟完全不读；`--read-chunk 1 --read-delay-ms 10` 模拟逐字节慢读；`--source 127.0.0.2` 测试第二通道，但项目白名单必须显式包含该地址。

完整自动化测试会自行创建临时项目、数据库和回环服务：

```powershell
Set-Location 'D:/jsdfhasuh/documents/my_project/emo_master'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' -m pytest `
  tests/runtime/test_tcp_external_client.py tests/runtime/test_tcp_review_fault_probes.py `
  -q -rx -W error::pytest.PytestUnhandledThreadExceptionWarning
```

`TCP_REVIEW_EVIDENCE_DIR` 可指定新的证据目录以保留每个客户端的 JSON/JSONL、标准错误和服务状态；重跑时使用新的目录，避免覆盖已有记录。未设置时使用 pytest 临时目录。

本轮没有连接相机、PLC、机器人或旧 LN5 工程，没有修改 D800、业务策略、依赖或现场项目，没有提交、推送、打包或发布。
