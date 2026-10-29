# 工作区交付整理

日期：2026-10-29。用户授权整理工作区、提交和推送。原工作区位于 `codex/release-v0.6.6`，源码基线为 `25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`。该发版分支与 v0.6.6 标签保留。

交付使用独立分支 `codex/logging-and-designer-improvements`，先快进到已获取的 `origin/main`：`f0675253631f489998056e5af618378e115efc83`。中间的 5 个提交只涉及 CI/原生资源验收测试与历史报告，没有改动产品源码，也没有覆盖本工作区的文件。整理范围分为三个提交：

1. SQLite 保存配置界面：分页、字段表格、易读文案、变量来源与小窗口布局；包含声明同步、测试和原生截图。
2. 本机工程来源迁移：显式比较/写入新目录、受影响任务与资源占用保护、SQLite 实际路径诊断及协议字段；包含合成测试和脱敏说明。
3. 展示与运行日志：细化图片预算/导出提示，补齐七类日志缺口与 Designer 本机文件，加入流程/页面分类、JSON 树及 JSON/JSONL/TXT 导出；包含验证资料和历史失败记录。

`main_window.py`、`project_configuration.py`、Runtime `service.py` 中的目录迁移与日志差异按 hunk 分开暂存；不是按整个文件混入两个用途。协议生成文件已校验，唯一新增请求字段为 `ProjectConfigurationWriteRequest.local_project_path`（编号 7，string）。

## 本次提交前检查

环境：Windows / Python 3.10 / PySide2；原生 Qt 使用 `QT_QPA_PLATFORM=windows`。所有本次测试均为临时工程、数据库及模拟设备。

| 检查 | 结果 |
| --- | --- |
| 工程目录迁移：两个 Runtime 文件与 Designer 配置 UI | **18 passed in 34.48s**，见 [relocation.xml](relocation.xml) |
| SQLite 编辑器/布局与内置参数标题 | **32 passed in 10.39s**，见 [sqlite.xml](sqlite.xml) |
| `python scripts/gen_proto.py --check` | 通过 |
| `python -m ruff check src tests` | 通过 |
| `python -m mypy --config-file mypy.ini src` | **568 个源码文件无错误**；保留默认不检查无类型函数体的边界 |
| 分组暂存区 `git diff --cached --check` | 通过 |

mypy 首次发现一处新日志队列缺少注解：`main_window.py` 的 `_localLogFailures` 为 `[var-annotated]`；补充 `deque[str]` 后全仓库 mypy 通过。修正仅为类型注解，没有变更运行逻辑。

本会话已完成的日志验证见 [七类缺口完成记录](../2026-10-29-log-gaps-completion/README.md)、[分类与 JSON 树](../2026-10-29-log-window-json-tabs/README.md) 和 [日志导出](../2026-10-29-log-export/README.md)。其中导出专项 13 项、窗口相关回归 56 项、最终日志集成与显示客户端各 41 项通过；这些批次有重叠，不合计为独立用例总数。本次仅补类型注解，未无条件重复这些已通过批次。

## 资料整理与保留

- 源码、合成测试、协议、通用使用指南和验证记录随功能提交。SQLite 记录的 6 个源码 SHA-256 与工作区逐一核对相符，仅将其路径从本机绝对路径改为仓库相对路径，Hash 未改。
- 历史失败的 XML、文本和说明保留，没有通过删除失败证据得到“全绿”。较早的页面配置确认阶段两项失败、设备描述符夹具错误及 SQLite 完整用户链路未执行等边界，仍按各原始报告保留；本次没有将它们宣称为已修复或通过。
- 暂存检查发现三份失败 JUnit 的 traceback 带行尾空白；公开副本只去除行尾空白，XML 属性、用例/失败数量和实质文本保持一致。字节原件保留在本机 `build/workspace-delivery-20261029/raw-junit/`。
- 外部现场工程的原始模型/帧/工程标识、业务统计、专用复验脚本及未脱敏 README 归档到本机 `build/workspace-delivery-20261029/private-project-evidence/`。这部分保留在本地，不上传；公开报告使用等价示例路径。原 source-hashes 记录亦备份在本机交付目录。
- 原始测试 `.log`、临时输出、既有现场工作区与模型/数据库沿用原有忽略规则，不删除、不加入代码提交。公开截图均来自合成 Qt 夹具；其中可能显示测试机临时目录，不含现场凭据或相机画面。

没有执行全量 pytest、冻结 EXE、打包、发布、真实设备或内网部署。本次交付是普通分支提交和推送，不包含合并 main 或改写远端历史。
