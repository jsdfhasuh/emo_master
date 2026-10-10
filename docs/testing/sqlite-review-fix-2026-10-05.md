# SQLite 写入算子审核修复（2026-10-05）

本轮只修复审核确认的五类缺陷，使用正式编译器、Runner、SQLite 后端及既有原生
Designer 入口验证。不新增数据库类型、自动迁移、异步写入队列、历史查询页或发布能力。
原算子实施报告及 A18、运行页面性能、资源稳态和 Qt 组合崩溃结论保留。

## 代码与工作区

- 分支：`agent/runtime-workflow-architecture-v1`。
- 起始 HEAD：`3fe04720f3702f9ffb923e3c7f9abb181ac145c6`。
- 回执修复：`e5e30a7f6791bce8c91d339680561c8cb8e26b14`。
- 调试隔离修复：`736889d53404472ea008b7a9fc35e0b9b526541a`。
- 路径与标识符修复：`933c424ebfa07a56c71a90c438656fb39b5d1af9`。
- 三批正常推送，每次另用 `git ls-remote` 查询完整远端 SHA，未 force push/reset/stash。

起始 160 个修改/未跟踪文件已在仓库外逐文件备份：
`D:/jsdfhasuh/documents/emo-master-backups/sqlite-review-fix-20261005-124811-0992366/`。
本轮不修改这些 UI 文件，也不把它们一起提交；因此工作区仍有用户原有修改。
最终逐文件及受测源码核对见
[workspace-audit.json](sqlite-review-fix-2026-10-05/workspace-audit.json)：160 个文件全部字节一致。

## 五项修复及证据

| 审核问题 | 修复行为 | 可重复回归 |
| --- | --- | --- |
| 触发器/约束忽略 INSERT 却返回 COMMITTED/1 行 | 提交前检查实际影响行数；非一行返回 E_SQLITE_NO_INSERT 并回滚，包括触发器副作用；保留 stop/continue | `test_review_receipts.py`：RAISE(IGNORE)、UNIQUE ON CONFLICT IGNORE，各两种策略 |
| 已提交后取消，节点面板丢失回执 | Runner 保留当前调用返回且身份校验成功的真实回执；仍发布 E_CANCELLED/node.failed，不伪称回滚、不复用旧执行回执 | 同文件：真实 COMMITTED 事件后取消，两种策略，数据库记录及 NodeRunInspection 一致 |
| 同库不同表被拆成独立 debug 文件 | 按物理数据库分组，路径大小写/硬链接别名共用隔离库；全组选择唯一测试模板、校验全部目标表并一次复制；禁止其它业务目标充当模板 | `test_review_isolation.py`：父子外键、相对/大小写/硬链接、节点顺序、无模板、模板冲突/缺表、重复准备 |
| Windows 缓存路径大小写可绕过图片引用限制 | 按本机路径规则检查原始及实际解析位置，保存规范化持久路径；目录联接不能隐藏缓存 | `test_review_paths.py`：真实 Image Saver → Runner → SQLite，缓存/持久图片、stop/continue、NTFS junction |
| Unicode casefold 合并 SQLite 的不同列 | 配置去重、列元数据、建表和保留列统一使用 SQLite ASCII 名称规则；类型按真实列校验 | 同文件：Straße/STRASSE、K/K、İ/i̇ 的真实表、错误类型拒绝、正确双列写入、ASCII 重复仍拒绝 |

新增 32 项边界用例，不删除或放宽旧断言，不改变既有无数据库流程的执行顺序。
SQLite 仍为锁等待 2 秒、操作期限 5 秒、管理并发 2、64 个映射、每条 1 MiB。
未改变 Job 自动启动、展示订阅或默认设备执行行为。

修复前七个反例的原始 JSON 已按字节复制到
[review-baseline](sqlite-review-fix-2026-10-05/review-baseline/result.json)，另保留
[真实图片反例](sqlite-review-fix-2026-10-05/review-baseline/real-image-result.json) 和
[原审核审计](sqlite-review-fix-2026-10-05/review-baseline/review-audit.json)。
其中 `jobResult.status` 是复现脚本对 Runner 异常的包装，不是 Supervisor Job 终态证据。
没有将这些旧缺陷记录改为 PASS。

## 实际环境与结果

Windows 11 build 26200；已有 Conda Python 3.10.21、PySide2 5.15.2.1、SQLite 3.53.4，
grpcio 1.78.0、protobuf 6.33.6、NumPy 1.26.4、OpenCV 4.10.0.84。没有安装/升级依赖。

| 检查 | 状态 | 实际结果/证据目录 |
| --- | --- | --- |
| 回执及检查器/Runner 专项 | PASS | receipts-first：19 passed，1.14 秒 |
| debug 隔离首轮 | FAIL（新增夹具） | isolation-first：8 failed、34 passed，24.82 秒；夹具漏传正式必需输入 value，保留原日志 |
| debug 隔离与原事务/RPC/故障专项 | PASS | isolation-second：42 passed，23.85 秒；补传输入，保留原断言 |
| 路径、标识符与依赖/后端专项 | PASS | paths-first：61 passed，3.86 秒 |
| 全部 SQLite 与 Runtime 集成 | PASS | integration-final：114 passed，34.40 秒，包含 106 项 SQLite 用例 |
| 受影响画布、结果面板、编辑器、项目与 Runtime 回归 | PASS | regression-final：71 passed，7.78 秒 |
| Windows 可见 Qt 完整路径 | PASS（功能） | native-final：1 passed，4.45 秒，DPR 1.0，两次真实写入及保存重开 |
| 完整 CI | FAIL（原基线） | ci-final：2843 passed、1 failed、8 skipped、28 subtests passed，745.20 秒；完整命令墙钟 748.079 秒 |
| 协议生成/Ruff/mypy | PASS | ci-final：proto drift 与 Ruff 通过，mypy 检查 324 个源码文件 |
| 跨机、冻结 EXE、现场发布 | NOT_RUN | 本轮没有执行或扩展 |
| 125/150/200% 原生 DPI 与原 1080p/5Hz 性能 | NOT_RUN（本轮） | 不将 100% 功能路径替代原性能、DPI 或稳定性验收 |

首次静态检查发现新增路径测试有一个未使用的 Path 导入，已移除；随后 Ruff 全仓通过。
未变更静态规则或测试断言。
完整 CI 唯一失败为 `tests/designer/test_visual_layout.py::testNarrowToolbarKeepsRunActionAndHasOverflow`
第 314 行 `window.startButton.isVisible()`，与原实施报告的基线失败一致；本轮未修改该
测试或其 UI 实现，没有新增最终 CI 失败。8 个 SKIP 保持跳过，不算 PASS。
本次完整 CI 与原生路径未出现进程崩溃，不代表旧 Qt 间歇崩溃已解决。

证据目录 [sqlite-review-fix-2026-10-05](sqlite-review-fix-2026-10-05/) 的每个测试目录都保存
candidate.json、实际命令/退出码 result.json 和未经编辑的 raw.log。原始失败堆栈带有
pytest 输出的行尾空格，按原样保存；代码、文档和 JSON 单独执行 diff whitespace 检查。

| 最终受测候选 | HEAD / dirty | src/tests/proto/scripts 摘要 |
| --- | --- | --- |
| integration-final / regression-final | 736889d + dirty（167 个路径） | 3c470da76a690f8ad7b561d8b0faac4cbdbd3056015fc7976d29480729ddeb77 |
| native-final | 933c424 + 原 dirty（160 个路径） | 同上 |
| ci-final | 933c424 + dirty（172 个路径，含新证据） | 同上 |

paths-first 与最终候选仅相差测试中的未使用导入；integration-final、原生路径和完整 CI
使用相同源码摘要。后续证据/报告提交不改受测生产、协议、测试或脚本源码。

原生 Qt 使用 QTest、正式控件、loopback gRPC 和 spawn Job，执行拖入、双击、来源选择、
初始化、应用、明确运行、查看回执、查询数据库及保存重开。数据库两条记录值为 7/13，
writeId 分别匹配真实回执，见 [ui-results.json](sqlite-review-fix-2026-10-05/native-final/ui-results.json)。
截图窗口请求 1280×720、1600×900、1920×1080；实际主窗口分别为 1280×720、1600×900、
1920×1061，最后一个受本机可用屏幕高度限制，不能记录为完成原生 1080 高度验收。
一次成功不证明原有 Qt 间歇崩溃已修复。

## 复现命令

在仓库根目录使用已有解释器，输出目录必须是新的，脚本拒绝覆盖旧证据：

```powershell
$py = "$env:USERPROFILE\.conda\envs\emo_master\python.exe"
$stamp = Get-Date -Format yyyyMMdd-HHmmss-fffffff
& $py scripts/sqlite_writer_validate.py --phase review-receipts --output "manual_test_workspace/sqlite-fix-$stamp-receipts"
& $py scripts/sqlite_writer_validate.py --phase review-isolation --output "manual_test_workspace/sqlite-fix-$stamp-isolation"
& $py scripts/sqlite_writer_validate.py --phase review-paths --output "manual_test_workspace/sqlite-fix-$stamp-paths"
& $py scripts/sqlite_writer_validate.py --phase b --output "manual_test_workspace/sqlite-fix-$stamp-integration"
& $py scripts/sqlite_writer_validate.py --phase c-regression --output "manual_test_workspace/sqlite-fix-$stamp-regression"
& $py scripts/sqlite_writer_ui_validate.py --scale 1 --output "manual_test_workspace/sqlite-fix-$stamp-native"
& $py scripts/sqlite_writer_validate.py --phase ci --output "manual_test_workspace/sqlite-fix-$stamp-ci"
```

上述验证只使用临时工程、图片、SQLite 文件及 Runtime 数据目录，不操作真实设备或现场库。
强杀/超时专项仍使用独立 spawn 外层监督（25 秒）；可见 Qt 外层 watchdog 180 秒，
均不放宽 SQLite 的 5 秒实际操作期限，最终回收测试自有进程与连接。

## 收口结论

五项已确认缺陷的正式修复、32 项新增边界用例、114 项集成、71 项回归与原生路径通过。
完整 CI 保持一项原基线失败；不改判原 A18、性能、资源稳态或 Qt 稳定性，也不允许现场发布。
本轮结束，不扩展数据库功能或 P6。更新源码后需保存项目并重启已有 Designer 才加载修复。
