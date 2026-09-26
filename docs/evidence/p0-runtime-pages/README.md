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
