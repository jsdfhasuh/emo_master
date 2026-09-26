# Qt 运行页面 P2 执行记录

起始 HEAD：`adb7b8ec1c12cddff0998323287b6cf4ae9e3a11`，分支
`agent/runtime-workflow-architecture-v1`，起始工作区 clean。按用户新授权进入 P2；
P0 原始 FAIL、200 ms / 5% / 500 ms 目标、读图完整率和 Qt 间歇崩溃继续保留。

环境：Windows、Python 3.10.21、PySide2 5.15.2.1、grpcio 1.78.0、protobuf 6.33.6。
完整依赖及系统版本见 `docs/evidence/p2/baseline/evidence.json`。

## 第一批

正式值契约、资源物化与完整编译、隔离 SQLite、Runner 路由前采集、Supervisor 接收、
业务顺序与游标分离、typed DisplayService 协议和薄适配。
真实 spawn、本地固定图像、内置 ImageLoader→Blob→Count 已读取 count=2；
准备后删除原图不影响任务，debug 增加和清零不改变同 projectId release=100。

验证命令：`python scripts/p2_validate.py --suite focused --output docs/evidence/p2/batch1-focused`。
75 passed；proto-drift、Ruff、mypy 均通过。原始日志和 dirty/受测代码摘要已记录。

基线：独立 git archive 执行 `python scripts/ci_check.py`，前三项通过，pytest 在
`test_visual_layout.py::testSidebarStaysCollapsedAcrossShowResizeAndRestore` 原生访问冲突，
退出码 3221225477。这是本轮编辑前重现的基线失败，不改断言或 skip。

开发中曾遇到测试 fixture 名冲突、长参数 ID、未设 PYTHONPATH 和新增 mypy 变量类型错误；
已修复并重新验证。后续验证全部由统一监督脚本记录原始日志，skip 不计 PASS。

首批提交 `6bf7235` 已正常推送。相关 core/runtime/e2e：431 passed、1 skipped（符号链接权限）。

## 第二批

正式模块新增共享内存冻结、两个 spawn 导出单元、资产库/有限租约、异步封闭与强杀栅栏。
专项逐步执行 85、87、89 项通过（包含重叠 P1 用例，不相加），proto/Ruff/mypy 通过。
实际验证图像 decode/摘要、同结果 count=2、可选 overlay 不自动打开、六种完成排列、
重复/跨作用域/换 Job、真实循环及重复子流程、强杀、封闭超时与迟到栅栏；
永久阻塞及损坏 IPC 各重复三次，确认导出 PID 替换和退出清理。
外层监督 300 s，单任务导出和封闭仍为 500 ms，回收确认 1 s。

第二批证据位于 `docs/evidence/p2/batch2-*`。首批证据 `.log` 被仓库通用忽略规则影响，
本批显式强制加入原日志，不修改日志内容或 JSON 内摘要。
图像 provenance 暂为逐帧独立 unknown；不猜测几何叠加。可信链适配与整进程资源稳态仍待完成。

第三批与最终阶段结论待实际执行后追加；不把专项通过宣称 P2 全部退出。
