# Dot：完整可见桌面走查与 CI / 编译交接

日期：2026-11-03。分支：`codex/logging-consolidation-20261103`。

S1—S6 与 F01—F08 已完成并通过独立复审。本次接手目标是补齐真实可见桌面鼠标/键盘验收，
修复走查发现的正常使用问题，并处理当前提交的 CI、构建和包验证失败。不要重新实施已完成的计划。
开始时记录实际完整 SHA；用户提示词提供首次推送的固定提交，后续提交须先核对 diff。

## 阅读入口与范围

- 仓库根 `AGENTS.md`。
- [实施计划及完成状态](../../plans/2026-11-03-logging-consolidation-implementation-plan.md)。
- [交付记录](README.md)、[F01—F08 修复](review-fixes.md)、[桌面记录](desktop.md)、
  [桌面修复验证](review-desktop.md)、[设备/导出](devices-export.md)、[协议/Web](protocol-web.md)。
- [SDK 1.4.0](../../handoffs/runtime-web-panel-v1/sdk-1.4.0/README.md)。

保留已有工作区及原失败证据。使用 Python 3.10、PySide2 / Qt QWidget；使用临时数据目录、
临时工程和 mock/loopback 设备。新生成工程按当前工程规范，当前工程 2.9、页面 1.2。
不得访问真实相机/PLC、改网络地址、现场工程或凭据；无需扩展为真实设备、跨机器 LAN 或现场验收。
已授权完成本轮必要局部修复、测试、提交及推送至同一分支；不合并 main、不创建发布或推送发布 tag。

## 可见桌面走查

使用真实可见的 Designer 和 Operator Runtime 正式入口，实际鼠标/键盘操作控件并留下截图与步骤。
确认工具有权向当前 Windows 桌面输入；上一轮两次 `GetCursorPos failed / 0x80070005` 是未完成原因。
若再失败，诊断会话与输入访问条件，记录真实阻塞，不用截图、QTest、offscreen 或探针替代人工点击通过。
自动化可辅助准备数据和回归；mock 探针只覆盖部分窗口，不能替代正式应用启动与工作流操作。

按正常使用顺序覆盖以下范围，可合并相邻场景，无需穷举异常组合：

1. Designer、Operator Runtime 的本次数据目录与应用日志；普通初始化失败后能找到应用记录，
   返回设置、更换临时目录并重试后使用新目录，关闭/重开正常。正式无界面 Runtime 入口用日志核对。
2. 实际在 Designer 打开 SDK 生成的当前格式样例，完成正常打开、保存重读、编译及 mock 运行。
   原 `DesignerOpen=NOT_RUN` 仅在实际打开成功后更新；避免默认配置连接现场设备。
3. 运行一条临时 mock 工作流，查看 Runtime 实时日志、应用/模块/级别、Job/设备/节点/调度关联及 JSON 详情。
   从工作流、设备、页面相关入口进入日志，核对显示与来源范围，正常运行/停止及关闭窗口不中断业务。
4. 查询跨 Job/旧工程历史，应用筛选、翻页、改筛选与重试，确认范围和详情一致。
   通过可控 mock 使一次新筛选失败，旧成功结果的翻页/导出仍属旧范围；重试成功再切换。
5. 同目标一次断连、重连、关闭并重开日志；实际 Runtime 实例切换时清理旧数据和晚返回，
   不混用实例/游标；新 Runtime 使用统一来源，支持范围内用 mock 核对旧服务 UNIMPLEMENTED 单 Job 回退。
6. 分清“当前显示”“Runtime 历史”“本机文件”“设备诊断缓存”。分别实际点击 JSON、JSONL、TXT 导出，
   抽读文件与实际计数；JSONL 每行只有一条日志。完整历史超过界面缓存仍完整，活动文件范围固定。
7. 实际点击导出取消，检查既有目标文件不被替换、界面不报成功；运行中导出、切源及再打开均有正常出口。
8. Designer 离线/本机日志、Runtime 应用与服务文件、设备缓存及结构化详情均可定位并阅读；
   检查窗口缩放、列宽、长字段、滚动、按钮状态和提示是否便于操作，不新增无关 UI 重构。

已有辅助入口：`python docs/testing/2026-11-03-logging-consolidation/desktop_mock_probe.py --seconds 600`。
它创建临时 RuntimeService/mock 日志与日志窗口，没有真实硬件，但不是完整产品验收入口。
本机解释器原为 `D:\Conda\envs\emo_master\python.exe`；先核对本机实际环境，不假设其他机器路径相同。

启动隔离不能只靠更换数据目录：Operator Runtime 不带工程时会从
`QSettings("EmoMaster", "Runtime").projectPath` 恢复工程/在线状态，工程的 `production.autoStart`
还可能自动执行。必须显式指定经过核对的无真实设备临时工程，并使用 `--local-only`。
Designer 使用 `EMO_DESIGNER_SETTINGS_PATH` 指向临时设置。Runtime 没有等价环境开关；
使用隔离 Windows 用户配置，或在临时启动 harness 中给正式 `OperatorWindow` 传入独立 INI
`preferences`，实际使用正式窗口并明确记录这种启动方式；不要为走查修改真实 Runtime 偏好。

正式源码入口分别为 `python scripts/dev.py run-designer` 和
`python scripts/run_operator.py <临时工程.emoproj> --data-root <临时目录> --local-only`。
Designer 的 `--local` 会预填已有 `manual_test_workspace/runtime-embedded`，完整隔离时不使用该参数；
在本次 shell 给 `EMO_RUNTIME_DATA_DIR`、`EMO_RUNTIME_DB_PATH`、`EMO_RUNTIME_LOG_DIR`、
`EMO_DESIGNER_LOG_DIR` 设置独立临时路径，清除本次 shell 的旧 `EMO_RUNTIME_TARGET` 与
`EMO_MASTER_RUNTIME_DB_PATH`，避免历史配置覆盖。所有设置只用于当前进程环境，不改用户永久环境。
`scripts/create_designer_space_acceptance.py <临时工程.emoproj>` 可生成最新格式无设备基础工程，
日志场景仍须补 mock 运行数据；它不是现成的完整日志业务样例。
`scripts/dev.py run-runtime` 是无窗口服务，不能作为操作员窗口走查。

Web 已有实际 FastAPI/Chromium 自动化及 F03/F08 反例；受桌面或 CI 修复影响时回归相关正常浏览器路径。
未经实际可见点击，不把既有浏览器自动化改记为人工浏览器验收。

## CI 与构建

对 PR 当前 head SHA 检查 CI，记录运行 URL、workflow、job、attempt 和失败日志；区分旧 SHA 的结果。
现有 PR 的主 CI 包括 Linux/Windows Python 3.10、proto 生成检查、`scripts/ci_check.py`，
Web workflow 包括后端、前端测试/构建、浏览器、实际软件 Runtime、wheel 独立安装。

- `.github/workflows/ci.yml` 和 `web-panel-ci.yml` 的 push 分支过滤不包含本分支；指向 main 的 PR 可触发。
- `.github/workflows/validate-windows-package.yml` 是不发布的 Designer/冻结包验证；本轮普通源码改动
  不匹配它的 PR path 过滤，需要手动对本分支 dispatch。确认 `publish_release: false`，不要改用发布 workflow。
- `.github/workflows/runtime-windows.yml` 是不发布的 Operator Runtime 构建与包验证；dispatch 时
  `source_ref` 传本轮完整 SHA，`packager_ref` 沿用该 workflow 当前固定版本。
  两个冻结构建都应核对最终 artifact 的源码身份与验证结果。
- 不为触发工作流随意改 branch/path 过滤，不为变绿删测试、弱化断言、扩大 skip、隐藏失败或跳过打包校验。
- 从真实失败定位最小修复；依赖/环境、源码问题、已有非阻断诊断分别说明。相关修复完成后运行必要回归，
  提交推送同一分支，并对最新 SHA 重新确认受影响 CI/编译及产物。
- 当前产物的基线 SHA 与 `sourceDirty=true` 是原验证事实，不可改成新 SHA 伪装重建。
  新构建/包验证记录实际来源；协议或 SDK 未变时不无故重建 SDK。

本次明确包括相关冻结构建与验包，但不发布。CI/offscreen 冻结检查不替代上面的完整可见桌面点击。
既有 65 秒 opt-in 租约专项和 Web 文件级 Ruff 164 项已有诊断不自动扩大为全仓清理任务；
若相关 CI 实际失败，基于失败与此次变更关系处理并保留证据。

## 完成交付

在本目录追加 `dot-desktop-ci.md` 或独立子目录，保留原报告，记录：

- 起始与最终 SHA、实际环境及启动方式、PR/CI URL。
- 可见桌面逐项步骤、预期/实际、截图、是否通过；未执行/受阻原因明确标记。
- 发现问题的触发、后果、最小修复、失败证据和修复后回归。
- CI 与 Designer/Runtime 冻结包验证结果、产物来源及尚未完成项。
- 分开陈述源码测试、原生 Qt 自动化、可见人工点击、浏览器、冻结包、真实设备/现场验收。

持续推进到本次桌面与 CI/编译范围完成；若存在确实无法继续的外部阻塞，交代具体原因和已完成部分，
不把尚未运行的检查记为通过，也不要求先清理无关非阻断风险才能进行正常走查。
