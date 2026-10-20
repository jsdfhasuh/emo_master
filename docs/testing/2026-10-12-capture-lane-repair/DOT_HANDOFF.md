# dot 复验：raw 通道提前归还与图像完整性

请使用 Codex 交付的完整提交 SHA 建立独立工作区，保留旧工作区与首次失败。
先阅读同目录 REPORT.md、VALIDATION.json 和证据包。验收范围是捕获通道生命周期，
不能由这一轮替代 SQLite、P0 性能或全产品验收。

关键词：**编码完成后归还 raw lane / 资产接管慢 / 同调用图像身份 / 后继图不覆盖前图 /
单双 lane / 双网络消费者 96 输入、8 预热 / NG 同调用图像 / 额度恰好归还一次 /
超时与未知回复隔离 / 资源真实退出**。

1. 先运行 `tests/runtime/presentation/test_capture_lane_lifetime.py` 以及相邻 exporter、
   multi_capture、closure/mailbox 回归，分别记录自动化与故障注入。它用事件控制先后次序，
   不靠增加 sleep 或超时复现。实际编码进程和两张不同像素的 PNG 必须均验证。
2. 在新进程运行原 `scripts/r3_export_credit_control.py` 的固定 ABBA（off/on/on/off），
   仍为 1080p、5Hz、每组 96 输入含 8 预热、两个网络消费者。保留每次运行的完整结果，
   不排除失败组或漏图序号。观察组的成功编码应满足 reply→raw release→callback 顺序。
3. 如再次出现 `BUDGET_EXCEEDED`，按相同 runtime/job/source/slot/lane 串起前一次接受、
   本次拒绝、前一次编码回复与归还时间。明确区分“回复前仍被占用”“回复后资产接管仍占用”
   和“证据不足”。不要将前一类自动归因于本轮已修正的回调问题。
4. 真实 Designer/Runtime 验证连续图像、NG 对应调用、双窗口的结果及图像身份；分别记录
   业务终态与图像可用性。停止/重启/释放后确认 exporter、worker、读线程真实结束。
5. 所有读图必须验证字节完整性/像素及来源身份。前一个 PNG 不可被后继覆盖；回调异常后
   不可串图、重复归还信用或使 Job/slab 被提前重新分配。

本轮尚未关闭历史 CI 中“编码回复前”六次拒绝。若本轮通过，应写本轮固定 SHA 场景通过，
不得据此宣称所有历史缺图根因均已修复。未运行项、首次失败、故障注入及人工操作分别列出。
