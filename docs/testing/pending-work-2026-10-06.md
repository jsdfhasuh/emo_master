# 原有 233 个文件的整理提交记录

## 范围与起点

2026-10-06，用户授权整理提交当前 `emo_master` 工作区的全部 233 个原有未提交文件。
起始分支为 `agent/runtime-workflow-architecture-v1`，起始 HEAD 和独立查询的远端引用均为
`dce541532909e18ec8113abbe643deba87b67d64`。本次整理没有新增执行或页面能力。

原有文件包括 163 个文档及证据、37 个源码、24 个测试、6 个脚本、1 个 Actions 工作流、
`start_runtime.cmd` 和 `.gitignore`。原 index 中有 `.gitignore` 及计划文件；计划文件工作副本
还包含后续修改，提交采用其完整最新内容。[incoming.json](pending-work-2026-10-06/incoming.json)
记录了原始状态、文件大小、SHA-256 和 Git 规范化后的 blob 身份。

仓库外备份位于
`D:/jsdfhasuh/documents/emo-master-backups/organize-existing-20261006-215103-113405/`，
包括起始分支 Git bundle、原暂存与工作副本 binary patch、233 个文件的 ZIP 副本和摘要清单。
`git bundle verify` 成功，bundle 末端与起始 HEAD 相同；ZIP 每个文件按 SHA-256 校验成功。
备份信息及内容核对见 [content-audit.json](pending-work-2026-10-06/content-audit.json)。

## 可审阅批次

| 批次 | 提交 | 文件数及内容 |
| --- | --- | --- |
| Designer 工作区 | `e631a6a151ab950f060ecfcc5609e70e36888528` | 32；统一工具区、动画工作区切换、页面编辑与独立预览、相应测试和验证脚本 |
| 工程配置与执行 | `65687bca8c270af494c1f97614eea6d0b85b0ffe` | 21；显式 2.3 生产设置、路径准备、连续 worker、背压与心跳、旧接口保护及测试 |
| 独立运行与工程交付 | `79c5166089e0f6e4ab6b69ab156f8384d16f9ce5` | 16；操作员入口、工程包与停机更新、BuildOnly 工作流、启动/验证脚本及测试 |
| 文档与证据 | 本记录所在提交 | 原有 164 个文件，另加本次整理记录、摘要核对及原始专项输出 |

前三批的完整文件清单见 [code-commits.json](pending-work-2026-10-06/code-commits.json)。
每批仅提交列出的文件。前三批后，233 个原有文件的工作副本内容逐项核对一致。
文档审查随后仅将开发指南中的旧工作区下拉入口说明改为当前的两个切换按钮，保留其它原有修改；
这项明确的文档更新单列在 [document-update.json](pending-work-2026-10-06/document-update.json)。
最终核对 232 个文件保持原始内容，开发指南只包含上述一处增量修正。
文档批次中的 `docs/evidence/p4/a-first/evidence.json` 仅有 LF/CRLF 字节差异；解析后的 JSON
和归一化字节与起始 HEAD 相同，历史测试结果没有改变。旧提交中的原始字节仍保留在历史中。

`docs/testing/.gitattributes` 为本批证据目录设置 `-text -whitespace`，沿用仓库既有证据规则，
保留日志、JSON、XML 的原始字节和诊断空白。组件工作区与动画工作区的两个历史 manifest 共
122 个文件均与记录的 SHA-256 一致；提交时再次检查 Git blob。`.log` 仅按本次专项的确切路径
强制加入，不改变全局日志忽略规则。历史截图、失败记录和阶段报告均保留。

## 实际验证与复用口径

解释器为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10.21，
PySide2 5.15.2.1；Windows 平台标识为 `Windows-10-10.0.26200-SP0`。依赖包括 grpcio 1.78.0、
protobuf 6.33.6、numpy 1.26.4、opencv-python 4.10.0.84、SQLite 3.53.4；没有重新安装依赖。

本轮实际运行以下专项，使用 `QT_QPA_PLATFORM=offscreen`、`HUARAY_CAMERA_SMOKE=0`，
以及仓库外临时 Runtime 数据目录。受测 HEAD 为
`79c5166089e0f6e4ab6b69ab156f8384d16f9ce5`，当时文档/证据仍 dirty；完整候选身份见
[focused/candidate.json](pending-work-2026-10-06/focused/candidate.json)。

```powershell
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' -m pytest `
  tests/ui/page_designer/test_workspace_modes.py `
  tests/ui/page_designer/test_workspace_switch.py `
  tests/ui/page_designer/test_palette.py `
  tests/core/test_runtime_directory.py tests/core/test_runtime_package.py `
  tests/runtime/test_production_continuous.py tests/runtime/test_production_boundaries.py `
  tests/ui/operator_view/test_production_window.py tests/runtime/test_operator_lifecycle.py `
  tests/runtime/test_worker_heartbeat_finalization.py tests/runtime/test_heartbeat_liveness.py `
  tests/test_windows_package_entry.py tests/test_package_bootstrap.py `
  tests/test_operator_runtime_validation.py -q
```

**PASS：133 passed in 96.13s**，进程退出码 0，监督脚本总耗时 97.375 秒。
原始命令、环境和结果见 [focused/result.json](pending-work-2026-10-06/focused/result.json)，
原始输出见 [focused/raw.log](pending-work-2026-10-06/focused/raw.log)。外层 watchdog 为 300 秒，
没有修改任何单任务故障期限。没有操作真实相机、PLC 或现场数据。

上一轮已经实际执行 `python scripts/ci_check.py`，验证对象包含这 233 个 dirty 文件。
本轮逐项核对其候选清单中的 760 个源码、测试、协议和脚本文件，全部 SHA-256 一致；
候选摘要仍为 `5d1117d685956d45e79f2a1d6b8cd8c2453a113e17e1b864884faeb19f6675f7`。
本次整理没有再修改这些代码，故复用这份完整 CI；不宣称本轮又执行了一次完整 CI。

| 检查 | 结果 | 证据和口径 |
| --- | --- | --- |
| 本轮相关专项 | PASS，133 passed | 本轮实际执行；离屏 Qt、真实 spawn 与临时工程/数据库 |
| 完整 CI 的 Proto 漂移、Ruff、Mypy | PASS，Mypy 334 源文件 | 复用上一轮摘要一致的受测候选 |
| 完整 CI 的 pytest | FAIL，2992 passed / 1 failed / 8 skipped / 28 subtests passed | 复用上一轮原始结果，992.77 秒；SKIP 不计为 PASS |
| 提交 diff 空白检查 | PASS | 每批执行 `git diff --cached --check`；原始日志按字节保留 |
| 233 个工作副本内容、历史证据 manifest | PASS | 内容摘要核对；不存在新增数据库、EXE、ZIP 或项目包待提交内容 |
| 本轮重跑完整 CI、重新冻结构建、云端 Action、干净机器、真实设备和现场验收 | NOT_RUN | 本轮为整理提交；既有历史验证与限制继续保留 |

完整 CI 的真实候选、失败输出和退出码分别保留在
[上一轮候选](designer-shortcuts-2026-10-06/ci/candidate.json)、
[原始 CI 日志](designer-shortcuts-2026-10-06/ci/raw.log)和
[CI 结果](designer-shortcuts-2026-10-06/ci/result.json)。

## 保留的问题与交付边界

- 完整 CI 中 `tests/designer/test_visual_layout.py::testNarrowToolbarKeepsRunActionAndHasOverflow`
  仍失败：640×500 窗口中 `startButton.isVisible()` 为 false。关键可见性断言仍在，未放宽或删除。
  前次 Runtime 记录的计数器全量时序失败及其隔离重跑结果也保留，不因本次专项通过宣称已修复。
- 既有 A18、Qt 间歇/组合崩溃、运行页面原性能 FAIL 和资源稳态缺口保持各自原始结论。
  原生截图和合成短测不能替代原 1080p/5 Hz 或长期现场验收。
- 独立 Runtime 的源码功能、本地冻结历史证据、工程包短测与现场发布资格分别报告。
  云端构建、干净 Windows 机器、真实工程/模型/设备、更新中断/断电和目标工况长测仍未完成。
- 本次仅整理 `emo_master`。外部 `python_build_script` 仓库的提交状态不在本次整理中，
  不把应用工作流入库等同于两仓代码已同步或云端构建已经通过。

工作区和推送末端在提交后以 `git status --porcelain=v1 -uall`、
`git ls-remote origin refs/heads/agent/runtime-workflow-architecture-v1` 独立核对；
完整最终 SHA 以本记录所属提交及最终交付报告为准。
