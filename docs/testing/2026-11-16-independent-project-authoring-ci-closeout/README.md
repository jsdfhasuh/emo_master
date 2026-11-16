# V7.1 CI 集成收尾 — 2026-11-16

执行者：Sol。ChatGPT Web 计划来源：`emo_master_v7_1_ci_closeout_plan_20261114.zip`。本轮仅处理原活动变更 6.7 的 R1–R3 与集中验证，不重新实施 A–D/F1–F6，不归档活动变更。

## 实际基线与计划合并

- 开始分支 `main`，HEAD `9dce7543232042c6840d9cc20c72a4a903556154`；fetch 后 `origin/main` 相同，ahead/behind 为 0/0，没有后续提交。
- 开始工作区仅存在用户原有未跟踪 `.tmp/`；本轮未清理或提交它，也未变更分支。
- 收尾包 SHA-256：`c3284f65dc96327c13ee7252c0e6d769abba1f0c8c03281a2d6024c4bd4d0a4c`。提取到独立私有目录 `.tmp/v7_1-ci-closeout-20261116-source/`，清单 10 文件摘要全部匹配。
- 阅读实际根 AGENTS.md、包 README、原 tasks.md 与 ci-closeout.md；仓库未发现子目录 AGENTS.md。`git apply --check` 成功后只应用一次计划增量，修改原活动 `tasks.md`、`acceptance.md`，新增 `ci-closeout.md`。
- 比对 HEAD：原 25 项与 6.0–6.6 共 32 条完成行及原证据映射保留。历史 V6/V7、SDK 快照、首轮和修复证据未覆盖；六份私有 CI 诊断未纳入公开提交。
- 本地验证针对上述 HEAD **加本轮未提交差异**，不记作 9dce7543 干净版本的结果。旧 CI 主 run 43402182752 失败、Web run 43402182710 成功，仅为修前依据。

## 修复及直接回归

| 项目 | 确认与最小改动 | 本轮验证 |
| --- | --- | --- |
| R1 | 两个 Runtime 测试绕过构造，缺少 `runtimeInstanceId`；补稳定 fixture 身份并断言回复身份相等。只新增 5 行测试代码，保留端口、图标字节/摘要、真实 gRPC、重连与 channel 断言。 | 修前 7 failed / 9 passed；修后三文件 23 passed，见 `r1-before/after.txt` 和 XML。 |
| R2 | 测试访问已不存在的 `RoiCanvas._image` / `outputLabels`；改用实际 `pixmap()` / `outputViews`，核对源图 24×16、梯度像素、四个输出及参数修改后的实际 9×5 裁图。产品代码未改，保留八个 ROI/histogram × 2.1–2.4 分支和全部草稿/Job/关闭清理断言。 | 修前完整文件 4 failed / 10 passed；修后完整文件 14 passed，见 `r2-before/after.txt` 和 XML。 |
| R3 | 460×720 原样复现：policy rect 275×34，仅 275×25 可见。Apply 的状态提示延迟布局将 viewport 从 632 降为 615、滚动上限从 40 增为 57；提前 focus 停在旧值 40，后续四轮事件也不重新定位。测试在 focus 前派发已投递的 LayoutRequest，再按原逻辑定位，完全保留尺寸及可见性/数值/来源/动作断言，没有修改产品代码或加入 sleep。 | 修前 1 failed / 4 passed（460 失败，620 通过）；修后同文件 5 passed，见 `r3-before/after.txt`、XML、geometry JSON 和原生 460 截图。 |

## 集中验证与交付

Python 3.10.21，PySide2 / 原生 Windows Qt；`PYTHONPATH=src;.`、`PYTHONIOENCODING=utf-8`、`QT_QPA_PLATFORM=windows`。以下针对基线加最终四个测试文件差异；本轮没有 src/proto/SDK/生产配置变动。

| 实际命令 | 本轮结果/证据 |
| --- | --- |
| `python -m pytest tests/runtime/test_operator_icon_assets.py tests/runtime/test_operator_port_specs.py tests/designer/test_operator_icon_integration.py tests/designer/test_draft_pure_preview_ui.py tests/designer/test_yolo_parameter_presentation.py tests/authoring -q --tb=short -o junit_family=legacy --junitxml=<本目录>/consolidated.xml` | **101 passed** / 56.51s，9 个现有依赖弃用警告，0 skipped；其中包含完整 59 项 authoring，不能再相加为 160。`consolidated.txt/xml`。 |
| `python -m ruff check src tests` | Ruff 0.15.6；最终四文件修改后再验，exit 0。`static-ruff-final.txt`。 |
| `python -m mypy --config-file mypy.ini src` | mypy 2.3.1；633 source files 无 issues，exit 0，保留 annotation-unchecked notes。`static-mypy.txt`。 |
| `python scripts/gen_proto.py --check` | libprotoc 31.1；exit 0，无生成漂移。`static-proto.txt`。 |
| `openspec validate add-independent-project-authoring --strict` | 官方 CLI 2.0.0 的本机缓存入口；exit 0。PATH 初次缺失记录保留，随后实际执行成功。`static-openspec.txt` / `static-openspec-final.txt`。 |
| `python scripts/authoring_acceptance.py --inspector <实际缓存Inspector入口> --output .tmp/independent-authoring-ci-closeout-20261116-inspector` | exit 0；脚本为已有入口，未修改。`inspector-console.txt` / `inspector-report.json`。 |
| `python -m pytest tests/authoring/test_designer_handoff.py::test_external_inspector_download_sample_continues_in_native_designer -q --tb=short -o junit_family=legacy --junitxml=<本目录>/designer-native.xml` | 设置 `EMO_AUTHORING_HANDOFF_PROJECT_DIR` 为本轮实际 download 目录；**1 passed** / 2.36s，2 个依赖弃用警告。`designer-native.txt/xml/png`。 |

同下载物验收经实际 `MainWindow.loadProjectSelection` 正式打开，在测试自己的临时副本修改节点与流程名、保存、重开、编译，校验资源/页面/设备/变量/生产配置均保留，未创建 Job。已实际查看窗口截图。该单项额外运行专门证明本轮外部下载物，没有把集中测试中默认示例的结果借用给新下载物。

Inspector 2.13.0 + 官方 Python MCP SDK 1.30.0 / 协议 `2025-11-25` 的现有脚本本轮已 exit 0：真实 PNG 字节上传、新修订绑定、INCOMPLETE → 修复 → PASSED、撤销重做、声明采用及本地校验、同修订导出 `not_attached/performedByExport=false`、鉴权下载/摘要匹配/匿名拒绝通过；隔离软件 Runtime 显式 LoadProject + StartJob 为 `COMPLETED`。详情见 `inspector-report.json` / `inspector-console.txt`。该脚本不运行 Designer，报告中的 `designer: not_run` 专指脚本本身，后续同产物 Qt 验收另记。

本轮实际下载物：`.tmp/independent-authoring-ci-closeout-20261116-inspector/download.zip`（3022 字节，SHA-256 `c1485c40d9aaf98ee81f9bd9a2c700f3ae663570db160bc6d200661c318d1248`），工程在相邻 `download/project.emoproj`。这是合成无硬件工程，未纳入私有工作区提交。

修复提交 `f17e8a912ce482fe3c81a2a35ad8c38eb51ec586` 已在原 `main` 分支推送；推送后 local/remote 相同、ahead/behind 0/0，工作区只剩私有 `.tmp/`。对应 [执行评论](https://github.com/jsdfhasuh/emo_master/commit/f17e8a912ce482fe3c81a2a35ad8c38eb51ec586#commitcomment-210177783) 已标明 Sol / ChatGPT Web 计划来源及验证边界。

该修复提交的远端结果均已实际完成：

| 工作流 | run / attempt | 固定 head_sha | 实际结论 |
| --- | --- | --- | --- |
| 原主 CI | [43782967435](https://github.com/jsdfhasuh/emo_master/actions/runs/43782967435) / 1 | `f17e8a912ce482fe3c81a2a35ad8c38eb51ec586` | **completed / success**；Windows/Linux × runtime/other 四分片、选择与双平台汇总，共 7 个作业全部 success。 |
| 原 Web panel isolated checks（workflow_dispatch，完整双平台） | [43782988301](https://github.com/jsdfhasuh/emo_master/actions/runs/43782988301) / 1 | 同上 | **completed / success**；Windows、Linux 和 Owning Runtime integration 三作业全部 success，含既有构建、wheel 和软件 Runtime 验收。 |

主 CI 本轮实际 pytest 统计如下，各平台相同用例不相加为唯一用例；分片 deselected 是另一个分片的选择，不记作通过或跳过。本轮没有新增 skip/xfail。

| 平台 / 分片 | passed | skipped | failed / error | 额外 subtests passed |
| --- | ---: | ---: | --- | ---: |
| Linux runtime | 3364 | 7 | 0 / 0 | 0 |
| Windows runtime | 3369 | 2 | 0 / 0 | 0 |
| Linux other | 6176 | 24 | 0 / 0 | 66 |
| Windows other | 6197 | 3 | 0 / 0 | 66 |

双平台 other 的本轮 JUnit 原件分别核实 **59 authoring / 59 passed / 0 failed / 0 errors / 0 skipped**；它们已包含在对应 other 统计内。原件只下载到 `.tmp/ci-closeout-20261116-f17e8a9-main/`，公开摘要记录 artifact 来源/摘要与聚合计数，不提交原 XML 或完整日志。

公开 run/jobs 摘要见 `ci-implementation-main.json`、`ci-implementation-web.json`；不提交完整原始 CI 日志或私有诊断。依据以上实际结果关闭 6.7；原 25 项与 6.0–6.6 保留，活动 change 不归档。收尾记录提交只修改计划/报告，其代码和测试与修复提交相同；推送后继续核对它自身的原主 CI 与 Web，最终固定 SHA/run 及完成结论写入该提交下的 Sol / ChatGPT Web 计划来源评论及交付回复，不用旧 SHA 的成功替代最终检查。

人工桌面操作、Designer 冻结产物验收和现场硬件本轮未执行；原生 Qt 自动化不记作人工操作，Web CI 的构建/wheel 自测也不替代 Designer 打包验收。没有生产发布、启停或相机/PLC 访问。
