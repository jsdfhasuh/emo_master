# 日志整合：CI 修复与桌面/冻结验收边界

记录日期：2026-11-03 UTC。仓库 `jsdfhasuh/emo_master`，PR [#9](https://github.com/jsdfhasuh/emo_master/pull/9)，分支 `codex/logging-consolidation-20261103`。

## 源码身份与修复

开始 SHA：`d92ffd37960e8b8f527136151ab9ccb80054e78f`。
首个修复 SHA：`c9da22fb09c91687a5ccab6e9c11e61da358607e`，文件树 `a2429b07cf2f744daa60ac29197c806468d21ca8`。
本记录及日志分类/启动取消测试修正追加于该修复之后；其提交不更改应用源码。
所有操作位于独立云检出，保留原工作区、历史失败证据、SDK 1.4.0 来源和 `sourceDirty=true`。没有合并、强推、发布、访问真实设备或修改现场配置。

初始 [CI run 41631945192](https://github.com/jsdfhasuh/emo_master/actions/runs/41631945192) 与 [Web run 41631945197](https://github.com/jsdfhasuh/emo_master/actions/runs/41631945197) 均为 `pull_request`、attempt 1，head 为上述开始 SHA；PR synthetic merge checkout 为 `2416ec7f41380b48ffc160309a8d2b6a497d1137`。

- 主 CI Windows job `125903465601`、Ubuntu job `125903466087`：proto 与 Ruff 通过，mypy 在三个日志文件报告六项错误，pytest 尚未运行。最小修复为导出 metadata 与历史 filters 明确字典类型、读取 payload 后 `isinstance` 收窄、表格组件显示显式字符串转换。没有关闭类型检查或忽略诊断。
- Web Windows job `125903465827`、Ubuntu job `125903466226`：backend 合同精确字节测试失败。已交付合同 proto 使用 CRLF，而 root proto 的 Git 字节为 LF；Windows 又将 SDK 生成 Python 转为 CRLF，与 wheel 内 LF 字节不同。仅在 `.gitattributes` 固定 root proto 的 checkout CRLF、生成 Python LF。合同、manifest、wheel、哈希和断言未改。
- 完整源码测试继续发现 `testPageSourceAndAreaSurviveNativeSettingsRoundTrip` 仍断言旧列“页面设计”。实现计划已明确该列显示应用/模块，实际为 `designer / page_designer`。更新这一精确预期，并额外核对 `source=page_designer`、`originApp=designer`、`component=page_designer`，保留原筛选和 QSettings roundtrip 断言。
- 完整源码测试在 22% 处暴露启动取消测试的旧 `Window` mock 签名：不接受本轮新增 `localLogWriter/localLogDirectory`，TypeError 进入真实错误对话框，最终触发原有 120 秒测试超时。fixture 显式接收并验证两参数，保留 cancel/display/client 顺序断言；测试中的意外启动错误改为立即 fail，避免错误隐藏在模态等待中。没有改产品或延长超时。

## 已执行验证

解释器 Python 3.10.22 / PySide2 5.15.2.1，云 Linux，Qt `offscreen`。以下组有交叠，不将计数相加。

- mypy 2.3.1：612 个源码文件无错误；Ruff 0.15.6：`src tests` 全部通过；proto drift 检查通过。
- 新检出分别使用 `core.autocrlf=false` 与 `true`，各执行 3 个 SDK 精确字节合同测试，均通过。保留原失败，未仅用规范化比较替代断言。
- Web backend：155 tests run，153 passed，2 个既有 owning-Runtime opt-in tests skipped。实际软件 Runtime 的独立 Windows CI job 另行覆盖，不把 skip 记为通过。
- 日志 Qt 辅助组：116 passed / 33.61s，无失败或 skip。覆盖 operational integration、Runtime log view、export、usability、refresh；它不是人工可见桌面走查。
- 日志分类修正后，与设备状态一起在 `TZ=UTC` 下 43 passed / 4.98s。
- 启动取消、启动设置/进度及日志分类相关组：59 passed / 6.79s。
- 完整 `scripts/ci_check.py` 已实际启动，收集 9080 项；首轮在 22% 处因上述旧启动 mock 引发 120 秒超时而失败，未完成余下检查，不能记为完整通过。云主机默认时区为 GMT/BST；五个未启动状态测试中的禁止时长字符串碰巧命中 TCP 客户端的绝对接入时间 `01:00:00`，实际本次运行字段仍为“未开始”。这些源码和测试与 main 完全相同，`TZ=UTC` 对照通过，未修改产品逻辑。另有既存短工具箱 offscreen 布局 27px viewport < 31px button 的本机失败，需与远端平台结果区分，没有删测或扩大 skip。

修复后首轮 [CI run 41633416601](https://github.com/jsdfhasuh/emo_master/actions/runs/41633416601) 与 [Web run 41633416843](https://github.com/jsdfhasuh/emo_master/actions/runs/41633416843) 均为 attempt 1，head `c9da22fb09c91687a5ccab6e9c11e61da358607e`。截至 12:19 UTC，主 CI 两平台运行中，Web Ubuntu、Web Windows 与 owning Runtime Windows 三个 job 已全部成功（包括后端、前端、浏览器和独立 wheel 安装）。任何后续提交的 CI 必须核对其自己的完整 head SHA，不能继承本段结果。

详细增量回归与失败记录见 [dot-ci-evidence](dot-ci-evidence/)。这些记录分别标明通过、失败和未完成，不覆盖之前交接目录中的原始证据。

## 完整可见桌面：BLOCKED / NOT_RUN

本次云环境没有可见桌面输入会话：DISPLAY/WAYLAND_DISPLAY/XDG_SESSION_TYPE 为空、无 X11 socket 或桌面进程，读取根窗口失败。未操作用户电脑，没有把历史 Windows `GetCursorPos failed / 0x80070005` 写成此次新复现，也没有伪造截图。

handoff 的八组正式 Designer/Operator Runtime 鼠标键盘走查全部仍未执行，包含启动失败/目录重试、SDK 样例实际 Designer 打开、正常 workflow/mock 运行、历史筛选失败重试、重连/实例切换、JSON/JSONL/TXT 导出、取消/目标保护、缩放与长字段。`DesignerOpen=NOT_RUN` 保留。已有 offscreen/QTest 和浏览器自动化不替代此层。

后续正式运行继续使用 handoff 中无设备的最新格式临时工程、`--local-only`、独立 Runtime preferences/Designer settings 与日志目录，禁止恢复现场工程或自动连接真实设备。

## Designer / Runtime 冻结：BLOCKED / NOT_RUN

已读取当前两个 workflow：

- `.github/workflows/validate-windows-package.yml`：不发布 Designer/冻结验证，`publish_release: false`。
- `.github/workflows/runtime-windows.yml`：`source_ref` 需本轮最终完整 SHA，`packager_ref` 固定 `91766f6d18ca9a333f64cf1c6030202457ec4a8a`，`publish_release: false`。

本次没有可用的已认证 Actions dispatch 执行入口：云 `gh` 未登录，已连接 GitHub 工具可读工作流及重跑既有 run，但不提供 workflow_dispatch；没有使用用户电脑或创建新凭据、改 workflow 触发条件来绕开。因此两个冻结 workflow 均未启动、没有本轮冻结产物，也不存在可声称已验证的源码/packager 身份。需在受权可用入口手动启动这两个不发布工作流后继续核对终态及 artifact provenance。

CI/原生 Qt 自动化、源码测试、浏览器自动化、人工可见点击、冻结包和真实设备验收各自独立。真实设备、跨机器 LAN 与现场部署未执行且不在本轮范围。


## 后续发现：迁移 006 的包自检门禁

2026-11-03 12:34 UTC，继续运行源码测试发现 `testPackageGuiGateRequiresNineCustomRenderedSamples` 的总体状态为 error。单独执行实际包自检后，九个 GUI 图标样本全部通过；失败来自 `_checkMigrations()` 仍固定接受版本 `[1, 2, 3, 4, 5]`，而本轮数据库已经正确执行 `006_logging_metadata.sql` 并得到 `[1, 2, 3, 4, 5, 6]`。该遗漏同样会影响真实冻结程序的自检，但本次尚未构建冻结包，不能表述为冻结实测失败。

最小修复：包自检必需 SQL 文件列表加入 `006_logging_metadata.sql`，精确版本更新为 1–6。版本 5 仍为既有 Python 迁移，没有伪造 005 SQL。测试保留资源、ONNX、journal mode 和 Qt 隔离断言，正例明确完整五文件顺序，新增缺失/空 006 资源都必须拒绝的两个反例。没有移除门禁、容忍未知版本或修改业务迁移。

回归：`test_windows_package_entry.py`、`test_package_gui_icons.py`、`test_package_classification_selftest.py`、`test_frozen_runtime_validation.py`、`test_release_workflow_contract.py` 共 **29 passed，2 subtests passed / 9.80s**；原失败和修复后输出见 `dot-ci-evidence/package-migration-before.txt`、`package-migration-after.txt`。这是源码自检及合同测试，不是冻结 EXE 通过。Ruff `src tests` 通过。

截至此追加时，`fc2bb9afe1c7ce5c44223bc53efb6a9bd9bcc459` 的 [Web run 41634877782](https://github.com/jsdfhasuh/emo_master/actions/runs/41634877782) attempt 1 已于 12:27:57 UTC 全部成功，主 CI [41634877796](https://github.com/jsdfhasuh/emo_master/actions/runs/41634877796) 仍运行。产物名中的 `5e788bd3118b32a5ab3e64c2a00d9466252f252b` 是该 PR synthetic merge checkout；五份 Web artifact 的 metadata 均记录 `workflow_run.head_sha=fc2bb9af...`。这些结果不能冒称本次后续提交已通过。

本地第二次完整检查以 fc2bb9af 启动，后来为定位本项在 35% 后编辑了包自检文件，故仅作为诊断 sweep，不能作为单一不可变 SHA 的完整通过证据；最终完整回归须使用固定源码独立检出。远端 fc2bb9af 任务仍验证其原不可变提交。两个冻结 dispatch 和正式可见桌面阻塞保持不变。


## 后续发现：旧服务页面观察的单 Job 日志回退

2026-11-03 13:05 UTC，完整诊断到页面测试时发现：本轮 `PreviewController` 无条件移除了旧 `PreviewRuntimeLogs`，同时丢弃其 event。对于只支持 `presentation_logs/StreamJobEvents`、不支持 `QueryRuntimeLogs` 的旧 Runtime，观察外部 Job 时没有运行控制器订阅兜底，因此原页面运行日志及详情消失。计划保留旧服务原单 Job 入口，这属于正常兼容路径的产品回归，不能仅删改失败断言。

局部修复复用现有 PreviewRuntimeLogs 和关闭生命周期：在已有后台 attach 操作对**实际观察地址**、携带其 metadata runtime instance 查询一次；只有业务回复或 gRPC 明确 UNIMPLEMENTED 才启用旧单 Job 来源。支持 Query 的新服务仍使用全局查询唯一来源，RESET_REQUIRED、UNAVAILABLE 等未知/错误不误当旧服务。旧来源保留 Runtime 身份/完整 payload，页面客户端错误仍属 page_designer；同目标由运行控制器订阅的 Job 不重复订阅；同地址/实例重开保留 cursor，换地址或实例清理旧 cursor，旧流沿原关闭路径退出。

原两个失败测试及旧重开/详情断言全部保留；新增实际现代 Query 全局 eventId 与 payload、零 StreamJobEvents、未知不回退、地址/实例切换和不同主连接所属 Job 不压掉外部流的回归。隔离页面组 **43 passed / 29.52s**；整合后页面与 operational-log 组 **60 passed / 51.63s**；mypy 全 612 源码文件、Ruff `src tests` 及源码 diff whitespace 检查通过。证据见 `dot-ci-evidence/page-fallback-*.txt`。

本地诊断 sweep 已结束：9041 passed、31 skipped、8 failed、42 subtests，40 分 47 秒。它不是固定源码的最终通过：三个包自检失败来自长进程中已载入的旧 v5 验包逻辑，两个页面回退失败为本节已修问题；另有本机 offscreen 工具箱/画布几何差异和一次 750ms 图读取 deadline。后者在固定 f0d77115 单测试原样复跑通过，未改变 deadline、96 帧像素/序列断言或相关业务代码。全部旧输出保留，没有将环境/诊断失败抹去。

当前修复前的 `f0d77115536eb1b8800a7cf69c1c6a21e7a23786` [Web run 41637042466](https://github.com/jsdfhasuh/emo_master/actions/runs/41637042466) attempt 1 已全部成功（12:43:22 UTC）；该 SHA 的 [主 CI 41637042316](https://github.com/jsdfhasuh/emo_master/actions/runs/41637042316) 截至本节仍运行。后续修复提交必须单独等待其精确 head SHA 的 CI 终态，不能继承这些旧结果；冻结与可见桌面两项阻塞没有解除。
