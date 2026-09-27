# P5-A 执行记录

起始分支 agent/runtime-workflow-architecture-v1，HEAD `0e45d09a3c2f0dc14ddbd432d24afcfc4c01f810`，工作区干净。
四个原待推送提交已逐一核对完整SHA和父子关系：
`dce0c66edaa2ea43e5992acf283162054661a6b7` → `d727505f35ac008f750acf383a1331a73d4a0dbc` →
`f94597406e2ed743ae731fbdc8287250915da6fc` → `0e45d09a3c2f0dc14ddbd432d24afcfc4c01f810`。

仓库外备份：`D:/jsdfhasuh/documents/git-backups/emo_master-p5-before-20260927-131653-9864.bundle`。
git bundle verify确认完整历史，list-heads确认分支末端为0e45d09；SHA256：
`b66490c53a33e5cee9fb70e51c40e167f98c17e780f313c4e0680157d09176c5`。
bundle不含未跟踪/忽略文件、环境或现场数据库；源码运行依赖沿用当前Conda环境，没有发现LFS过滤配置。

Git2.55.0.windows.5，HTTPS origin。普通push及一次命令级HTTP/1.1排查都失败：
`RPC failed; curl 55 Send failure: Connection was reset`，随后sideband断开。
两次独立ls-remote均确认远端仍为 `2e45f3b70a5b317e39e855c38de3dd22f7eefe1d`。
待推对象最大两个blob约2.78/2.76MB（P3原始日志），没有据此认定网络故障原因。
没有改TLS、全局网络参数、main或历史；停止盲目重复，按用户授权持有完整备份继续本地开发。

## 基线与批次A

基线命令：`python scripts/p4_validate.py --suite ci --output docs/evidence/p5/baseline-ci --timeout 600`。
proto/Ruff/mypy通过；pytest原生访问冲突3221225477，外层4294967295，完整CI FAIL；
崩溃后的测试NOT_RUN，不把无最终统计称为通过。原始输出及干净受测HEAD/源码摘要已保存。

新增正式导出入口、允许清单、便携参数、真实release校验和编译见P5契约。
`python scripts/p5_validate.py --suite export --output docs/evidence/p5/a-first --timeout 300`：
77 passed / 2 failed；新代码Windows只读fd调用fsync报Bad file descriptor。改为r+b同步，不弱化断言。
`a-fixed`同命令：79 passed / 0 skipped，Ruff/mypy PASS。全部原始结果保留。
两轮测试均记录HEAD+dirty、逐文件摘要、环境和code_stable；不是在未来提交SHA上运行。

A提交 `c2d6eeb` 后一次正常push仍curl55，独立远端仍2e45f3b，未同步。

## 批次B

新增DeliveryStore、跨进程目录所有权、ZIP前置准入和原子active/previous指针。
导入与启用分开；失败、坏回退目标和Runtime占用都保留当前版本。上限8个不可变版本，不自动清理运行数据。
`python scripts/p5_validate.py --suite import --output docs/evidence/p5/b-first --timeout 300`：
32 passed / 0 skipped，Ruff/mypy PASS，code_stable=true。
包括坏摘要/缺文件/不兼容版本、脚本、重复/别名/越界/链接/单文件和条目超限、原资源目录改名后验证、
中文空格目录和非项目cwd、跨进程锁、回退损坏时现用版及运行数据不变。
此时只完成包/版本机制，尚不称独立运行UI闭环通过；跨机、跨盘、EXE均NOT_RUN。
