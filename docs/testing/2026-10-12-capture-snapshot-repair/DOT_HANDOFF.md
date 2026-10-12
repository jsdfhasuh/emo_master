# dot 固定 SHA 复验：编码期间捕获完整性

请用 Codex 交付的完整 SHA 创建独立工作区，保留旧工作区和首次失败。先读本目录
REPORT.md、VALIDATION.json 及 evidence.zip；不得把基线或 PR 合并提交的通过结果替代交付 SHA。

关键词：**编码前独立副本 / snapshot 确认后归还 raw / 慢编码时下一张可捕获 /
两图逐像素与调用身份 / 单双 lane / 两段等待共用 500ms / 重复确认拒绝 /
失败不释放后继额度 / 真正回收与隔离 / 双消费者 96 输入含 8 预热 /
源图、解码、live、paint 分层完整性**。

1. 在新进程先跑 capture_lane_lifetime、export_diagnostics、images、multi_capture、
   closure、mailbox 及 export-credit 诊断回归。真实进程故障注入、伪连接状态测试与
   普通自动化分别记录。必须验证旧图编码暂停时可提交新图，两张 PNG 像素和身份正确；
   第三张仍按有界容量拒绝，不能放大共享内存或移除安全检查。
2. 单独执行 `python scripts/r3_export_credit_control.py --output <新目录> --qt-platform <本机平台>`。
   原样保持 off/on/on/off、1080p、5Hz、96 次含 8 预热和两个消费者，不并行跑 pytest。
   观察组成功链路为 snapshot_recv→credit_release→最终 pipe_recv→callback，
   各阶段一一对应同一个 job/result/source/slot/lane。保留首轮所有 raw evidence。
3. 再出现 BUDGET_EXCEEDED 时，先关联当前拒绝与同 lane 前一个接受任务，区分
   尚未取得独立副本、已有后继排队、接管阻塞、子进程失败/隔离与证据缺失。
   不能把缺图统一归为几毫秒超时，也不能把接收/绘制漏号写成源图未导出。
4. 真实 Designer/Runtime 运行连续图像、NG 同调用图像、双消费者和 5 路 While 图像。
   保留 D01–D08、保存重开、SQLite 并发/保存重载原验收；未运行就标记 NOT_RUN。
   验证暂停/继续、停止/重启后 worker/exporter/读线程真实退出和额度恰好归还。
5. 分别列出 96/96 源图与两个消费者各 88/88 正式接收、解码、live、paint 的数量及缺号。
   核对精确交付 SHA 的 Windows Actions；Debian 或本机通过不能覆盖 Windows 失败。

本轮的本地通过只能说明明确场景通过，不得推断所有历史缺图、NG→INCOMPLETE 或
性能问题已关闭。不修改生产设备，不重跑到绿色，不放大服务端 500ms 期限；
客户端接收/解码仍沿用用户允许的 550ms。
