# 日志诊断 V3 完成记录

## 本地交付

- 从已抓取的 `origin/main` 创建独立工作树 `D:/emo-log-diagnostics-v3`，分支为 `codex/upgrade-log-diagnostics-v3`。开工时远端主线与评审基线均为 `ea5aefa9cf62f0d7ee3ac0fb6fa4a14661dc663b`。实施期间主线前进到 `348a39f42a5f8d43d3319a894857db9e9e6ce059`，差异限于两份 Operator R4 验证文档。
- 原工作区 HEAD 为 `78a547270324553b8e88dea5a05258e562fcfb08`。复核原工作区状态清单与开工时一致。实施、提交和测试均在独立工作树或临时测试目录进行。
- M1 定向验收通过后提交 `588e8a2d742b75e551a8bb5294f7b90ffe87d564`，随后实施 M2。最终产品代码候选为 `8e3c33c04f694b57ceb3a1bfc4d4cb37ee8f9d1d`，Source 与 Web 验证启动时独立工作树干净。
- 定稿包的两份规格作为行为要求，13 个任务以 [tasks.md](tasks.md) 为验收清单。原版 43 个场景的对照见 [scenario-map.md](scenario-map.md)。两轮对比留在后续选项。

## M1 定向验证

- 软件 Runtime 定向回归 181 项通过。覆盖小配额下超过配额的周期心跳、判活、原错误保留、业务序号和游标、单条和批量事件及终态、旧库读取与重放，以及普通 SQLite 起止、后台最终回执和子工作流 JSONL。
- 原生 Windows Qt 定向回归 30 项通过。覆盖任务生命周期归属、While 循环、实时与暂停耗时、Job 终态、失败提示及同次执行原始记录。Designer 和 Operator 入口使用同一共享任务页。

## M2 定向验证

- 软件 Runtime 定向回归 129 项通过，Designer 名称回退验证 3 项通过。模拟派发运行后改名并重启 Runtime，由另一 gRPC 客户端核对历史工程名、实际目标工作流及槽位。在线已记录名称和离线已受理、已完成事件都保留当次身份。Proto 生成检查通过，Panel SDK 隔离安装检查 2 项通过，随附的 42 项文件摘要一致。
- 原生 Windows Qt 定向回归 71 项通过，折叠定位后续验证 14 项通过。覆盖节点行、异常计数、按代码及名称和载荷搜索、历史毫秒和应用筛选、设备缓存可用与不可用、回执和报文、业务 0 和 false，以及原始复制。
- 导出、本机日志、失败文案和 Operator 宿主合并定向回归 120 项通过。Designer 与 Operator 分别验证所选 Job 的 Markdown 入口及完整范围。Designer 用跨页历史核对事件顺序、未加载记录和条数，Operator 验证切换任务后仍按原 Job 和固定上界写出。取消及写入失败保留目标文件。
- Web 定向逻辑 14 项通过。真实 Chromium 与本机 FastAPI 和模拟 Runtime 验证毫秒及可空范围、倒置时保留列表和详情、框架文案与自定义文案、带样式的等级、对应原始记录、游标和导出。
- 预检发现 Designer 首屏消息列和设备详情首行报文预览回归，已修正。现有 Qt 验证覆盖首屏布局、TCP 报文预览和工作台下钻，原生 Windows 15 项通过。补齐中文模块及工程修订的两处测试断言后，Windows 与 Linux 的两份定向测试各 48 项通过。

## Source CI

- 最终候选在 Linux 容器（Python 3.10.22、Qt offscreen）运行 `python scripts/ci_check.py --shard runtime`，在原生 Windows（Python 3.10.21、1920 × 1080）运行 `python scripts/ci_check.py --shard other`。两端源码选择检查各 9 项通过，Proto 漂移检查、Ruff 和 mypy 均通过。
- Linux `runtime` 分片完成，3,487 项通过、1 项失败、10 项跳过。唯一失败是启动取消用例的旧断言，原断言未包含计划要求的冻结工程名和工作流名。补上对两项名称的精确断言后，`tests/runtime/test_startup_cancellation.py` 在 Linux 和 Windows 各 13 项通过。产品代码与全量候选一致，保留首次分片结果。
- 原生 Windows `other` 分片 完成，6,397 项通过、9 项失败、4 项跳过，另有 66 个子测试通过。5 项 Qt 剪贴板或设备提示框失败在评审基线与候选上均复现。3 项旧 SQLite 探针因本机 Git 进程环境配置报错，清除该次子进程的相关覆盖后，原候选定向复跑 3 项通过。最后 1 项页面缩放撤销检查只在全量中失败，候选和当前主线（相对评审基线仅变更文档）各复跑 6 项通过，诱因未确定。保留首次分片结果。
- 分片清单核对了全部 9,908 个收集项。两个分片无交集，每个选中项均进入测试。两份首次分片记录均有失败。Linux 跳过 8 个 Windows 专属项及 2 个显式压力验收。Windows 跳过真实相机、外部客户端样本、本地示例包及非 Windows 拒绝路径。
- 本阶段原始日志、JUnit 和分片清单在 `build/local-ci/source-final-8e3c33c04f69/`。评审基线的 Windows 剪贴板和设备提示框复现单独归档在 `build/local-ci/windows/`。较早候选验证发现桌面兼容性问题后停止，早期日志仍保留。Linux 早期归档方式缺少 `.git`，已更换为能核对 SHA 的浅克隆。

## Web CI

- 最终候选在本地 Windows 使用 Python 3.10.21、Node 22.23.5 和 Chromium。23 个工作流步骤通过，Node 依赖和 Chromium 安装复用预检已验证的环境。前端 207 项通过，独立后端 225 项通过、8 项跳过。10 个 Chromium 脚本、3 个浏览器测试框架用例和独立 Wheel 安装通过。
- 单独启用软件 Runtime 的回环步骤 6 项通过，其中 4 项覆盖了独立后端中的 opt-in 跳过。软件 Runtime 与 Chromium 还验证了同一实例的两个任务、图像和自动重启。
- 最终候选的 Chromium 首次启动在进入页面前超时，Windows 恢复后浏览器步骤全部通过。日志和证据在 `build/local-ci/web-final-8e3c33c0/`，保留首次启动日志和恢复后的浏览器日志。较早候选的完整 Web 步骤也有独立记录。

## OpenSpec

- `openspec validate upgrade-log-diagnostics-v3 --strict` 通过。

## 未验证项

- 使用软件 Runtime、模拟设备和临时工程。真实相机、PLC、现场工程、真实设备人工验收，以及冻结版桌面安装包未验证。
- 本地运行对应 CI 命令，GitHub 托管矩阵未触发。本轮未执行 Linux Web 浏览器步骤、Web 的 3 个 POSIX 专属用例，以及工作流未选中的独立 `test_web_start_capture`。
- 两轮对比留待后续需求。交付仅在本地，未推送、合并或发布。
