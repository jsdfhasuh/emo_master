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

第二/三批与最终阶段结论待实际执行后追加；不宣称首批已满足 P2 退出条件。
