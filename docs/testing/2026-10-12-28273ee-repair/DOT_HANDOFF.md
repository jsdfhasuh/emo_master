# dot 固定 SHA 复验关键词

以 Codex 最终交付消息给出的完整 SHA 为产品基线，不只按分支名运行。先检查本目录 REPORT.md / PLAN.md 和 evidence.zip；不覆盖旧工作区，不混用旧 Designer/Runtime 进程。本轮不要改产品代码、提交推送、合并、发布或部署；不连生产设备。

1. **ROI：manager 关闭 + 旧 producer 未退休 + 最新 Future 已完成**。复用“真实成功 RPC、仅延迟客户端返回”的反例，比较 manager.closeAll、窗口关闭、主窗口/工程切换、刷新来源；在所有 producer 退出前资源仍在，退出后原 session/assets/临时文件清零且只 close 一次。晚返回异常、重复收尾、新会话、重连、同 ID 两窗口分别检查。
2. **ROI：Tab 聚焦后滚轮只滚页面**。宽/高、坐标、padValue、roiType、interpolation；滚动不改参数、不改 generation、不清当前 snapshot/ZIP；键盘和明确步进仍能编辑。宽高最小 0.001，坐标允许负数；正值之间也不能被滚轮误改。
3. **四输出 / 同次身份**。bbox/rotatedBox/polygon，croppedImage/croppedMask/fullMask/maskedImage 的 1:1、PNG、JSON、ZIP；原图逐像素；不得使用另一 executionId 或把 requestedParams 当 resolvedParams；关闭中导出、过期、重连、写盘失败不覆盖旧目标。
4. **SQLite 精确 SHA Windows push CI / 4路5路 While**。原 28273ee busy 失败根因尚 OPEN。新诊断命令见 REPORT.md，固定次数，保留全部首轮结果。明确父与 worker 的数据库、连接、Job、事务阶段；无复现不写根因已修复；不加 timeout、不删断言、不重放 COMMIT/body。
5. **P0 96×3×5 完整基准**。96 测量、8 预热、3 轮5组、原门槛；Windows 本轮 correctness PASS / performance FAIL。不能只看 CLI exit=0；双方每次完整覆盖、序号、同资产、最终零资源；model_p95 和 max_live_age 分开，保留 producer 间隔和消费者传输/解码时间。
6. **回归 D01–D08 / 80秒暂停继续 / O20 [7,13] 两 Job、先退休再保存重开 / NG 同 invocation 图像计数 / 闭合错误与 credit 恰一次**。软件自动化、故障注入、真实 CUA 桌面、设备与 NOT_RUN 分账；不重复相加用例数量。
7. **历史 NG 与 ordinal20 仍 OPEN**。本机 store.latest 与双网络消费者不混因；start_wait、start_identity、completion_wait、completion_identity 不统一吞成 TimeoutError。

交付固定源码身份、首次日志/截图/数据库证据和 PASS/FAIL/SKIP/NOT_RUN/OPEN，旧失败日志不覆盖。附新 push CI 与 PR merge CI 各自 commit/tree/event 身份，不用后一条通过替代前一条失败。
