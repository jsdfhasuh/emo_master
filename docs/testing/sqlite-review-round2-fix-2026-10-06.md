# SQLite 第二轮审核修复（2026-10-06）

本轮只修复上一轮确认的四项缺陷：页面准备入口的内部库保护、表结构误判、
JSON 转换错误及节点收尾、提交后清理回执。使用正式模块、真实 SQLite、
loopback gRPC 和 multiprocessing.spawn 验证，不新增数据库功能或发布能力。

## 分支、提交与工作区

- 分支：`agent/runtime-workflow-architecture-v1`。
- 起始 HEAD：`89b2c276ba06bc710eb7dace95cbe0dcc54302ae`。
- 内部库保护：`e26917086e17fb37332c30603f224d52bc0f632c`。
- 表结构识别：`40e7bcca090883100e6148ac2ad631a89eaffc49`。
- 编码及节点收尾：`52591622a85f4b4ccd5c6e36fe9818777a3af009`。
- 提交后清理：`a33ddb424c87600f9817bde3aba1e7894662ee4f`。

四个代码提交已正常推送，独立 `git ls-remote` 查询返回完整 SHA
`a33ddb424c87600f9817bde3aba1e7894662ee4f`。报告/证据提交在代码验证之后完成，
最终同步状态以交付回复中的本地、远端完整 SHA 为准。

原有 160 个未提交文件先在仓库外逐文件备份，再核对全部 SHA-256：
`D:/jsdfhasuh/documents/emo-master-backups/sqlite-round2-fix-20261006-093415-2967493/original-dirty/`。
这些 UI 文件未修改、未暂存、未提交，工作区仍保留原有修改。
核对见 [workspace-audit.json](sqlite-review-round2-fix-2026-10-06/workspace-audit.json)。
没有 reset、stash、force push、main 修改或证书验证变更。

## 四项修复

| 问题 | 正式行为 | 回归证据 |
| --- | --- | --- |
| 页面准备漏传实际 Runtime 保护信息 | Runtime 所有者提供统一保护列表，管理、普通 StartJob、debug/release 准备及 debug 模板共用；内部库、自定义名称、硬链接、任务目录及资源目录拒绝，失败不创建 Job/准备记录 | `test_review_protection.py`：两种模式各检查多类目标；真实 gRPC 拒绝及合法业务库的真实 spawn 写入；debug 不动 release 种子 |
| 默认值或标识符中的关键词误判表类型 | 用 table_list 元数据判断类型和 WITHOUT ROWID；旧 SQLite 缺少该 PRAGMA 时用将引号内容、注释视为不透明的兼容解析；查询排除同名触发器/索引 | `test_review_schema.py`：三个关键词默认值，原生与模拟旧 PRAGMA 分支、真正 WITHOUT ROWID、虚拟表/视图和同名触发器 |
| JSON 编码错误逃出字段转换、来源节点缺终态 | 序列化/UTF-8 错误统一为 E_SQLITE_VALUE，保留映射行，由 writer stop/continue 处理；意外绑定投递在来源节点异常收尾内 | `test_review_encoding.py`：孤立代理字符值/键、整数编码限制、真实 gRPC/spawn 两种策略、完整来源事件和意外错误收尾 |
| 提交后清理错误伪报 FAILED/0 | 只有 commit 返回成功才记录已确认提交；清理错误保留 COMMITTED/真实主键/writeId，单独 cleanupError 和 ERROR 日志；stop 可使节点失败，continue 完成，取消仍传播 | `test_review_cleanup.py`：真实提交后注入清理错误、两种策略和取消、进度回调清理失败仍关闭连接、真实清理退出前持有额度、UNKNOWN/已回滚叠加清理故障 |

新增 25 项正式回归。没有搬入 prototypes，没有增加项目格式版本、隐藏端口或
自动重试 INSERT，没有改变无数据库节点的默认设备执行行为。
单独清理诊断进入既有有界回执摘要与文字展示，不增加订阅或结果缓存。

已确认提交与清理状态分开：COMMITTED 不意味着检测任务一定成功或全部资源清理成功；
UNKNOWN 仍不能等同回滚。清理故障测试是受控注入，未声称观察到标准库 close 的自然故障。
SQLite 仍保持管理并发 2、锁等待 2 秒、操作期限 5 秒、64 个映射和单记录 1 MiB。
外层 spawn watchdog 25 秒只监督测试，最终 terminate/kill/join，不放宽单操作期限。

## 环境与测试

使用已有 Windows 11 build 26200 环境：Python 3.10.21、PySide2 5.15.2.1、
SQLite 3.53.4、grpcio 1.78.0、protobuf 6.33.6、NumPy 1.26.4、OpenCV 4.10.0.84。
没有重装或升级依赖。测试始终使用临时项目与数据库，不操作现场数据或设备。
自动化使用 `QT_QPA_PLATFORM=offscreen`、`HUARAY_CAMERA_SMOKE=0`；
Qt 自动化不代表可见窗口或真实设备验收。以下测试耗时为 pytest 自报，
`result.json` 另记录从启动命令到子进程退出的 monotonic 墙钟耗时。

| 检查 | 状态 | 实际结果及原始证据 |
| --- | --- | --- |
| 内部库保护、RPC 及调试隔离 | PASS | [protection-verified](sqlite-review-round2-fix-2026-10-06/protection-verified/result.json)：16 passed，11.87 秒 |
| 表结构与原后端 | PASS | [schema](sqlite-review-round2-fix-2026-10-06/schema/result.json)：33 passed，3.36 秒 |
| 编码、依赖及 Runner 摘要 | PASS | [encoding](sqlite-review-round2-fix-2026-10-06/encoding/result.json)：30 passed，5.87 秒 |
| 清理、回执、后端及检查器 | PASS | [cleanup-final](sqlite-review-round2-fix-2026-10-06/cleanup-final/result.json)：44 passed，3.64 秒 |
| 全 SQLite 与 Runtime 集成 | PASS | [integration](sqlite-review-round2-fix-2026-10-06/integration/result.json)：139 passed，45.10 秒；包含 131 项 SQLite |
| 画布、结果面板、编辑器、项目及 Runtime 回归 | PASS | [regression](sqlite-review-round2-fix-2026-10-06/regression/result.json)：71 passed，7.87 秒 |
| 上轮原始复现脚本复验 | PASS | [original-probes.log](sqlite-review-round2-fix-2026-10-06/original-probes.log)：8 passed，10.18 秒；脚本未放宽原断言 |
| 协议生成/Ruff/mypy | PASS | 完整 CI 的 proto-drift、全 src/tests Ruff 通过；mypy 检查 325 个源码文件，无错误 |
| 进入本轮前的完整 CI | FAIL（基线） | [上轮报告](sqlite-review-fix-2026-10-05.md)：2843 passed、1 failed、8 skipped、28 subtests passed；原日志和结论保持不变 |
| 完整 CI | FAIL（原基线） | [ci/raw.log](sqlite-review-round2-fix-2026-10-06/ci/raw.log)：2868 passed、1 failed、8 skipped、28 subtests passed，769.06 秒 pytest；[完整命令](sqlite-review-round2-fix-2026-10-06/ci/result.json) 墙钟 771.531 秒，退出 1 |
| 全量跳过项 | SKIP | 8 项保持原跳过，本轮未增加 skip；`pytest -q` 原始日志未逐项输出原因，不能把跳过计为通过 |
| 本轮可见 Qt、DPI 矩阵及原 1080p/5Hz 性能 | NOT_RUN | 本轮为后端边界修复，文字回执由专项/回归验证；不替代原生视觉、性能或稳定性验收 |
| 原生旧版本 SQLite、跨机、冻结 EXE、现场发布 | NOT_RUN | 旧 PRAGMA 分支在本机真实 SQLite 连接上模拟缺失，不冒充另一版本库或另一台机器验收 |

完整 CI 唯一失败为
`tests/designer/test_visual_layout.py::testNarrowToolbarKeepsRunActionAndHasOverflow`，
第 314 行 `window.startButton.isVisible()`。它与进入本轮前的失败一致，
相关 UI 实现及测试属于保留的原未提交修改，逐文件哈希未变。
本轮新增最终失败为 0；没有删除断言或放宽 CI。
这次完整 CI 没有发生进程崩溃，也不能据此宣布已修复既有 Qt 间歇崩溃。

## 原始失败与受测代码

旧审核证据仍在仓库外
`D:/jsdfhasuh/documents/emo-master-backups/sqlite-review-round2-20261005-231441-0683317/`，
其中原始最终有效探针为 1 PASS/7 FAIL，未修改成 PASS。本轮复验日志独立保存。
本轮证据同时保留原审核结果、原始日志及三份复现脚本的逐字节副本
（[original-review](sqlite-review-round2-fix-2026-10-06/original-review/source-manifest.json)）。
脚本副本以 `.py.txt` 保存，避免成为全量测试的额外发现入口；
清单记录原路径及 SHA-256，复制不改变上轮结果。
原 P0/P2/P3、A18、性能、资源稳态和 Qt 组合崩溃记录未改判。

本轮开发过程的失败全部保留：

- `protection-red`：2 failed/14 passed；未修复时准备未拒绝目标，初始父子进程夹具未传回失败堆栈，父进程见 EOF。
- `protection`：2 failed/14 passed；新夹具用后端 URI 初始化已规范化的 Windows 扩展路径，出现 `%3F` authority 错误。夹具改用真实 sqlite3 直接建表，不改变拒绝断言。
- `protection-final`：2 failed/14 passed；新夹具报告路径的 relative_to 不接受扩展前缀与短路径混合。只修正报告标签，并以 samefile 核对合法目标身份。
- `schema-red`：9 failed/24 passed，复现表结构识别缺陷。
- `encoding-red`：pytest 自动生成巨整数用例名称触发编码限制，收集失败；只增加显式用例 ID。
- `encoding-red-verified`：6 failed/24 passed，复现正式编码/收尾缺陷。
- `cleanup-red`：5 failed/37 passed，复现回执覆盖、取消丢回执及连接清理问题。
- `cleanup`：42 passed；随后新增双故障回归，`cleanup-final` 44 passed。

每个验证目录保存 candidate.json（真实 HEAD、dirty、环境和源码哈希）、result.json
（实际命令、退出码及 monotonic 墙钟秒数）、未经编辑的 raw.log。原失败堆栈中的
空格及行尾格式按原样保留，不能把原始日志的空格当作修改代码或删除失败证据的理由。
证据目录的局部 `.gitattributes` 禁止换行归一化，保证 Windows 的 `core.autocrlf`
不改变清单中的逐字节摘要；该设置只作用于本轮证据，不改变源码检查。

最终集成、回归、完整 CI 使用同一受测源码摘要：
`669d710ea375b16d3db8819ee0897a83bd59449c85f01e9152a11e6cfb9784d3`，
受测 HEAD 为 `a33ddb424c87600f9817bde3aba1e7894662ee4f`，dirty 包含原 UI 修改及新增证据。
后续报告/证据提交不改变 src/tests/proto/scripts 源码。原 UI 文件字节核对和证据摘要
分别见 workspace-audit.json 与 evidence-manifest.json。

## 可复现命令

在仓库根目录用已有解释器执行；每次必须使用新的输出目录，脚本拒绝覆盖旧证据：

```powershell
$py = "$env:USERPROFILE\.conda\envs\emo_master\python.exe"
$stamp = Get-Date -Format yyyyMMdd-HHmmss-fffffff
& $py scripts/sqlite_writer_validate.py --phase review-protection --output "manual_test_workspace/sqlite-r2-$stamp-protection"
& $py scripts/sqlite_writer_validate.py --phase review-schema --output "manual_test_workspace/sqlite-r2-$stamp-schema"
& $py scripts/sqlite_writer_validate.py --phase review-encoding --output "manual_test_workspace/sqlite-r2-$stamp-encoding"
& $py scripts/sqlite_writer_validate.py --phase review-cleanup --output "manual_test_workspace/sqlite-r2-$stamp-cleanup"
& $py scripts/sqlite_writer_validate.py --phase b --output "manual_test_workspace/sqlite-r2-$stamp-integration"
& $py scripts/sqlite_writer_validate.py --phase c-regression --output "manual_test_workspace/sqlite-r2-$stamp-regression"
& $py scripts/sqlite_writer_validate.py --phase ci --output "manual_test_workspace/sqlite-r2-$stamp-ci"
```

本轮四项修复和功能回归通过；完整 CI 仍为 FAIL（同一项原工具栏失败），
SKIP 与 NOT_RUN 不计通过，不据此批准现场发布。
源码变更需保存项目并重启现有 Designer 才生效；本轮停止于修复，不进入下一开发阶段。
