# 设备等待与父桥关闭的诊断保留

执行身份：ChatGPT Web。基线：`cdf1250ccc41b78f79b25b87e6db197809875384`。

## 解决的证据缺口

原实现中，设备调用者等待超时仅返回 `E_DEVICE_OPERATION_TIMEOUT / UNKNOWN`，无法区分驱动执行、原始图像发布、图像观察回调和清理阶段。设备父桥最外层的 `OSError / EOFError / ValueError` 则被静默丢弃；工作进程只能报告连接中断，无法还原父桥为何关闭。

这组改动补齐失败记录，不把历史 93 秒停顿归因于硬件，也不声称已经消除该停顿。

## 改动

- `deviceWait` 保存请求、执行状态、入队/开始时间、原有等待/执行 deadline 和观察时间。无限等待使用 `null` 表示没有有限执行 deadline。记录不重新获取执行器锁，不释放原生操作的所有权。
- `deviceAction` 保存发生错误或调用者放弃等待时的操作阶段，并在驱动已返回、图像已发布时保留这些边界时间。阶段只是观测位置，不自动证明是该阶段的代码或硬件导致调度延迟。
- 父桥在实际关闭连接后发布 `device.ipc.failed / E_DEVICE_IPC_CLOSED`，含阶段、具体传输异常类型及短消息、请求/周期/任务身份和原定 deadline。日志冻结创建桥时的项目 scope，不记录请求参数或图像内容，不重放请求。
- 回复的 `encode_reply` 与 `send` 分开记录；保持原实现先完成序列化、再计算 2 秒发送 deadline 的顺序，实际发送阶段仍受原预算限制。
- 空闲对端退出不制造设备错误。覆盖 POSIX EOF 和 Windows `ERROR_BROKEN_PIPE` 分支；已有请求的发送故障仍记录。
- 上述固定大小的诊断在已有日志预览截断时保留，每个诊断映射上限 768 字节；原有日志总量边界继续生效。
- 原有错误信息中的“driver still owns device”改为“operation has not retired”，与覆盖发布、观察和清理的实际所有权范围一致。错误码、UNKNOWN 判定、取消回执优先及超时数值保持原有语义。

## 验证

`red.txt` 是实现前的 5 个新增契约用例：均失败，包括缺少诊断字段和缺少回调接口；这不代表 5 个独立产品根因。之后新增真实日志投影、Windows 分支和发送计时控制用例，共 11 项。

最终定向命令：

```bash
python -m pytest -q \
  tests/runtime/test_device_failure_observability.py \
  tests/runtime/test_device_executor.py \
  tests/runtime/test_device_manager.py \
  tests/runtime/test_device_ipc_cancellation.py \
  tests/runtime/test_device_ipc_retirement.py \
  tests/runtime/test_device_operational_logs.py \
  tests/runtime/test_startup_cancellation.py \
  tests/runtime/test_cycle_plan_scope_cleanup.py
```

最终受影响套件结果：**76 passed in 11.32s**，完整输出见 `green-focused.txt` 和 `final-focused-receipt.json`。其中包含真实 Pipe、真实模拟相机管理器、真实全局 EventStore 和 JSONL 写入路径，以及启动/停止取消与 cycle scope 的兼容性控制。观察回调暂停的反例证明：驱动已经返回时，超时诊断明确为 `frame_observer`，UNKNOWN 和占用仍保留，操作真正退场后同一管理器可再次采集。初版设备定向 51 项结果单独保留在 `green-focused-initial.txt`。

最终复核发现初版诊断把回复序列化计入发送预算。`reply-deadline-review.json` 保留该失败对照，修正后的 `reply-deadline-review-restored.json` 验证：序列化处推进假时钟 2.25 秒后，基线和最终版本进入实际发送时都仍有原 2 秒；把暂停放在实际发送阶段则两版都超时。真实 Pipe/sendBytes 全程只执行一次请求。该控制已加入正式参数化回归，没有放大超时。

独立复核和额外 5 项控制验证见 `review.md`。Linux 上的 Windows 错误分支验证不等于原生 Windows 验收。

## 边界

这组记录不会追溯生成历史上没有保存的原始调度数据。历史 93 秒停顿、双任务启动无心跳、图像首次编码变慢仍需复验时结合新记录定位，不能宣布根因已全部闭环。

父桥先关闭连接再同步发布诊断，因此连接关闭不等待日志；桥线程退出仍可能受同步日志发布延迟影响。独立复核中，人为长时间持有 EventStore 顺序锁可使 `bridge.close()` 的既有 3 秒门槛报 `E_DEVICE_RELEASE_PENDING`，解锁后正常退出。未找到新增生产锁环，也没有证明该叠加条件在现场自然发生，按仓库要求记录为非阻断限制。

本组未执行人工桌面、原生 Windows 或真实相机/PLC 验收。
