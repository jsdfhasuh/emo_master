# 合并后体验修复集成记录

执行与记录：dot。基线 main 为 `7919f9a5b5335f55f6eeb0fb1e77109653e4f32c`。本次在独立云端副本完成，保留各项首次失败与回归记录，未操作用户电脑或真实设备。

- 日志导出：最终扩展名目标存在时确认覆盖，取消不启动写入，详见 `2026-11-04-log-export-overwrite-fix.md`。
- Dispatch：无根捕获时保留 Job 真实分组身份，捕获不可用只清理对应 Job，详见 `2026-11-04-consumer-only-dispatch-dot-fix.md`。
- Web 包：仅对会超过导入压缩率限制的自产条目使用 STORED，保留外部导入防护与普通包身份；网格空白块与可用布局一致，详见 `2026-11-04-web-editor-package-repair.md`。
- 编辑器草稿：保留会话中断及目标切换时的本地编辑，按用户/目标/工程隔离；用显式选择与 CAS 处理并发冲突，详见 `2026-11-04-editor-draft-repair.md`。

集成后 Python 3.10：Web 后端 187 项，180 通过、7 跳过；相关原生 Qt offscreen 与 Runtime 测试 89 通过。前端 123 项通过，TypeScript/Vite 构建通过。独立复审确认三个 Editor 修改共同保留。Qt 自动化不是可见桌面验收。

本地 Chromium 受云环境 IPC socket 权限限制，启动前失败，未声称浏览器通过。将三组新增浏览器脚本加入 Web CI，验证草稿恢复、分组捕获隔离及网格/BMP 正常操作；最终状态以对应提交的 CI 为准。未执行 Windows 原生 UI、现场设备或部署验收。
