# 6a5fd21 复验后局部改进计划

日期：2026-10-12。基线：6a5fd216180ebdec3159a7e9808147d953e32f69。

用户已授权继续改进；使用现有独立修复 worktree，原目录本地改动不纳入。

1. 保留 dot 原始分包、Windows push 首次失败及 P0 15 个窗口。先执行一次本机首结果原用例和完整 P0 96/8/3/5 基线，不刷绿。
2. Windows 首结果：不把 StopJob 或一次 commit 栈当根因。补充可选、有界、独立于正式事件/展示队列的首结果阶段诊断，贯穿同一 runtime/job/scope/invocation/result/source；记录 seal、export/adopt、finish 等待原因、close 和失败观察时刻。用故障注入证明缺 seal、待导出、模型失败可区别，不改 10 秒 deadline 或资源所有权。
3. P0：先区分原型双网络路径与产品本机路径，测量复制/编码等开销。只有证据支持才作局部性能修改；保留像素、身份、资源配额、500ms 预算及 5% 门槛。不得改变负载或基线配置来取得通过。
4. 回归首结果/闭合/资源归还、mailbox 压力、PNG 保真、双消费者和故障路径；再跑原完整 P0 与软件全量、静态检查。性能测量不与 pytest 并发。用户已有 Designer 进程不擅自终止，记录测量环境限制。
5. 区分产品修复、诊断增强、性能测量和未关闭历史问题。Windows 首失败、NG→INCOMPLETE、ordinal20、While busy 无直接根因证据不得关闭。
6. 完成必要修改、测试与报告后正常推送现有 agent/runtime-workflow-architecture-v1 分支，核对远端 SHA，给 dot 固定 SHA 复验提示。不得合并、发布、部署或连接生产设备。
