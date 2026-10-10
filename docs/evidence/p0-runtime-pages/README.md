# P0 原始证据

结论见 [P0 记录](../../plans/2026-09-26-qt-runtime-pages-p0-results.md)：**未满足退出条件**。

- `baseline-environment-ci.txt`：原依赖环境 proto drift 失败。
- `baseline-aligned-ci.txt` / `final-ci.txt`：依赖对齐后的前后 CI；均在既有 Designer 原生访问冲突退出。
- `runtime-regression.txt` / `preview-recheck.txt`：既有 Runtime/core/e2e 回归及失败单独复现。
- `p0-tests.txt`：最终33项P0专项通过。真实网络和故障测试有独立外层监督。
- `exploratory-measurements.json`：首窗口原始样本。未删除异常或挑选通过窗口。
- `measurements.json`：第二测量窗口，增加真实吞吐、调度迟到、连续live数据年龄；表格引用此文件。
- `final-risks.json`：修正StartJob回复/就绪观测点、增加旧四线程入口空载基线后的网络/导出复验；不覆盖性能窗口。

JSON的`execution=PASS`仅表示脚本断言/执行成功，**不是性能目标或P0通过**。
证据生成时HEAD为d96911b，P0实现尚未提交；既有src/proto保持该基线不变，原型代码见同次交付的提交。
后续小改动（OPEN超额时latest不保留旧OK、模拟消费者不隐式启动Job）由最后的33项测试覆盖；不将这些改动描述成重新做过性能测量。

所有输入、DB、Job workspace和导出文件均来自临时目录。只提交测试报告，不提交临时项目/数据库/图片。
Windows进程资源/单调时钟样本仅适用于这台开发机；Qt、冻结包、工控机和真实设备均NOT_RUN。

## 第二轮（独立归档）

最新结论见[第二轮报告](../../plans/2026-09-26-qt-runtime-pages-p0-round2-results.md)，原第一轮文件保持不变。

- [round2/acceptance.json](round2/acceptance.json)：分别列执行、正确性、性能、兼容性和P0退出，最终退出FAIL。
- [round2/raw/final-evidence.json](round2/raw/final-evidence.json)：`fa166cc`，第一套三轮，每组8预热+96测量；完整率PASS，性能FAIL。
- [round2/raw/final-code-evidence.json](round2/raw/final-code-evidence.json)：`e09e657`，修复客户端超时恢复后的最终三轮；execution PASS，correctness/performance FAIL；两个客户端第一轮90/96、91/96，后两轮各96/96。
- [round2/raw/final-gate-p0.txt](round2/raw/final-gate-p0.txt)：最后39项专项PASS；与性能/完整率门槛分列。
- [round2/raw/final-risks-after-pin.json](round2/raw/final-risks-after-pin.json)：测量后仅增加Pin/Unpin网络断言，四个风险场景再次执行。
- [round2/manifest.json](round2/manifest.json)：归档文件SHA256、日志来源和测量后源码差异；不把文档提交SHA冒充受测代码。
- `round2/raw/baseline-*.txt`、`ci-after-preview-fix.txt`：原Qt访问冲突和预览失败；`runtime-final.txt`为356 passed/1 skipped。
- `round2/raw/designer-isolated/`：49独立文件333 passed，保留组合CI失败，不称全CI通过。
- `round2/raw/final-p0.txt`：本轮曾出现33/40客户端覆盖失败的原始日志；不删除中间失败。

两套测量起止均为clean且源码摘要相同，保留全部30个窗口。连续原型是父进程真实WorkflowRunner + 两个spawn导出器 + 两个独立gRPC channel/接收线程；Supervisor真实worker spawn另由C1—C4验证。没有Qt可见率或冻结包/硬件的验收声明。
