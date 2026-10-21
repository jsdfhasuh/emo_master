# 设备易用性实施原始证据

报告：[2026-10-21-device-usability-improvement.md](../../testing/2026-10-21-device-usability-improvement.md)。
计划：[设备易用性 v1](../../plans/2026-10-21-device-usability-improvement-plan-v1.md)。

最终源码：`e2a9d867dc3a7069c7e91d2e68da5dfa23ee1fbf`。
最终 tree：`38a0f6252ddb7acf3019c4766d1af59f98614e55`。

| 位置 | 含义 |
| --- | --- |
| `final-snapshot-delivery/` | 最终 git archive 快照：61 passed CHAIN、348 passed／2 deselected REGRESSION；environment、Ruff、mypy、proto、diff 均 exit 0 |
| `final-snapshot-delivery/validation.json` | 实际 SHA/tree、导入路径、每个文件 blob、完整命令 argv、cwd、时间和退出码 |
| `final-snapshot-delivery/acceptance/` | A1—A6 工程、三类包、三次导入工程、实际 Job／诊断结果及原生 Qt 截图 |
| `final-snapshot-delivery/usability-chain/` | 新 C1—C6 连续工程、待绑定模板、实际图像／字节／诊断与发布截图 |
| `final-snapshot-delivery/dispatch/`、`byte-logs/`、`once-byte-logs/` | 周期身份、单次持久化及有界字节预览的原始事件材料 |
| `final-snapshot/` | 较早源码 848ad46，61+345；保留过程证据 |
| `final-snapshot-verified/` | 较早源码 ae74080，61+347；保留过程证据 |
| 根目录 `*.log`／`*.xml` | 首次反例、夹具修正和定向回归；文件名 fixed 不保证内部全部通过，按原始输出与 evidence-index 判断 |
| `preexisting-*` | 在未改基线 756e526c 复现的旧测试问题，未冒充本轮回归 |
| `desktop/` | 848ad46 真实桌面关键路径；恢复、发布、待绑定、明确运行在线版本、COMPLETED／8×6 实际输出 |
| `desktop-final/` | e2a9d86 实际桌面局部复查：树／表滚动保留、70 算子加载后无需重编节点即可显示缺 camera |
| `desktop*-launch.json`、`desktop*-stderr.log` | 桌面夹具启动、导入路径、失败尝试与进程身份；失败尝试未删除 |
| `initial-status.txt`、`parallel-work-*` | 并行工作区保护；10 个无关已跟踪文件 SHA-256 核对 |
| `source-candidate.json`、`final-source-candidate.json` | 明确暂存源码候选与最终源码提交树 |
| `evidence-index.json` | 每个 JUnit 文件实际计数及失败用例索引；保留所有原始材料 |
| `delivery-checks.json` | 本轮文档／证据脚本及交付前检查命令与结果 |
| `SHA256SUMS.json` | 本目录所有交付文件的 SHA-256（自身除外）；不包含 Python 缓存 |

`run_validation.py` 接收明确的 `--source-sha` 和全新 `--output-dir`，复用未修改的前轮 closure runner。
`desktop_demo.py` 仅准备隔离模拟工程；桌面输入使用 computer-use / sky。
图像／业务结果以正式 Job 和诊断断言为证，夹具初始化完成的后台 Job 不冒充 UI 操作证据。
最终桌面 `02-publication` 是一次未命中的索引点击后快照；`03-publication-ready` 才是实际发布窗口。

所有结果为本轮实际 Windows 软件证据。硬件、人工完整 UI、冻结产品、Linux 和跨机器为 NOT_RUN。
历史目录、历史 runner 和历史结果未覆盖。
