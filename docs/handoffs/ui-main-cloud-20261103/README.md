# emo_master 界面分支：纯 GitHub 合并交接

交接日期：2026-11-03（Asia/Shanghai）。用户已授权将当前有效的界面分支及原先未提交的界面改动检查、修复并合并到 `main`，随后明确要求接手方仅使用 GitHub 和自己的云端环境，不访问用户电脑。

## 远端入口

- 仓库：<https://github.com/jsdfhasuh/emo_master>
- 待完成的产品 PR：<https://github.com/jsdfhasuh/emo_master/pull/7>
- 产品分支：`codex/integrate-reviewed-branches-20261102`
- 原始快照及完整失败证据对应的产品提交：`61e2717456d5fc17d1202709bcd61f7997fa25eb`
- 推送交接时最新观察到的产品提交：`e6342e32d2716f61fcfe884310ccfc85960e3f1c`
- 交接时目标 `main`：`f0675253631f489998056e5af618378e115efc83`
- 交接资料分支：`codex/ui-main-handoff-20261103`

资料分支基于 `61e27174` 完整产品提交，只增加本目录的说明、原始快照和验证证据。产品整合及随后修复已推送到 PR #7；该 PR 继续承载实际合并。接手时先查询实时分支及 CI 状态，文档记录不是实时状态。

## 推送期间的远端新进展

最终远端核验发现产品分支已在 `61e27174` 之后新增 `e6342e32d2716f61fcfe884310ccfc85960e3f1c`（`test(runtime): fence asynchronous readiness in integration checks`）。该提交更新两个失败测试的异步准备条件及整合报告：每帧发布前等待现有 asset 响应退休，工程源迁移测试则在原 15 秒期限内同时等待输出文件和 `RUNNING`。原有完整像素序列、审计记录和运行状态断言仍保留。

新提交的完整 CI、Web 和冻结包检查正在运行，尚不能算通过。交接者只读核对了这次差异，未独立重跑该提交声称的云端定点验证。接手应先查看新提交与其检查，不要根据下文 `61e27174` 的历史失败重复实现已有修复。

- [最新产品完整 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/41486738201)
- [最新产品 Web 检查](https://github.com/jsdfhasuh/emo_master/actions/runs/41486738208)
- [最新产品冻结包检查](https://github.com/jsdfhasuh/emo_master/actions/runs/41486739418)
- [latest-remote-state.json](latest-remote-state.json) 保存了本次远端查询结果；实时状态以 GitHub 为准。

## 已完成的整合

Designer `8df8599fc026c424a49e87cc53fb45f6b371f16f` 与 Web `b89826b0841c6b4ef7f9c5f11ac938c6008934c7` 已通过正常 merge 保留历史。原工作区未提交的改动已在 `c2be5767cd796ff26821638b1863488ebaa115b8` 捕获整合，之后的修复也已提交至当前产品 HEAD。两条源分支提交均为该 HEAD 的祖先。

内容包括响应式 Designer 编辑、终端视口、整页适配、浮动工具和属性面板、父分组导航、边角及等宽布局手动缩放、共享 spacer、持久化向上画布扩展、性能探针，以及配套 Web 渲染和正式 SDK 1.3.0。工程格式为 2.9，Presentation 为 1.2。

协议分支已包含在 Designer 历史中；flow-edge 已是原 main 的祖先；相机预览成果已有后续 main 实现。`feat/web-operator-panel-20261030` 为被正式实现替代的原型，保留引用。诊断分支和历史发布引用也保留。无需把这些旧实现再次覆盖到已整合的代码。

近期修复包含旧网格追加行、配置错误提示高度、原生 Qt 测试窗口收尾，以及 Stop 后立即远端 Start 被显示资源尾部回收误拒绝的问题。最新普通 Start 预检对已终止、Supervisor 已交还的显示资源共用最多 2 秒等待预算，保持原释放校验、取消语义和请求去重，后续 attach 检查保持零等待。具体改动、失败触发条件和测试边界见产品提交中的整合报告。

## 所有交接输入都可从 Git 获取

- [file-manifest.json](file-manifest.json)：本目录证据文件的相对路径、字节数和 SHA-256，可在任意云端 checkout 中核对。
- [snapshots/manifest.json](snapshots/manifest.json)：Designer/Web 捕获来源、原始文件哈希及改动集合。
- [snapshots/designer.patch](snapshots/designer.patch) 与 [snapshots/web.patch](snapshots/web.patch)：原工作区已跟踪内容的二进制 patch。
- `snapshots/designer/` 与 `snapshots/web/`：全部捕获文件副本，含原先未跟踪的文件；这些是整合前的历史输入，后续修复以产品 HEAD 为准。
- [evidence/build/](evidence/build/)：历次云端失败/通过日志、JUnit XML、原生 Qt 收尾验证及本轮定点回归。
- [evidence/build/ui-main-integration/](evidence/build/ui-main-integration/)：Designer、Web、快速重启、视口像素单位等验证原始结果。
- [cloud-handoff-ci-status.json](cloud-handoff-ci-status.json)：推送前查询的固定产品提交 CI 状态和证据索引；其中 `build/...` 路径对应本目录的 `evidence/build/...`。

148 条来源路径记录涉及 121 条唯一路径，其中 27 条共享。Designer 有 101 个文件，Web 有 46 个文件和 1 个删除项（旧 SDK 1.2.0 wheel）。交接前已核对原 HEAD、文件字节和哈希、tracked/untracked 改动集合及二进制 patch，均与捕获状态一致。原始本地路径仅为历史来源字段；接手不需要这些路径存在，也不需要再次访问原工作区。

本目录保留成功、失败、命令错误和未执行状态。不能将历史失败日志当作当前 HEAD 的结果，也不能把失败、跳过、收集数量或未完成状态记为通过。Python 环境、依赖缓存和可重新构建的临时目录不属于交接输入；安装要求、源码、测试和 CI 工作流均在产品树中。

## 固定产品提交的检查入口

- [完整 Windows/Linux CI](https://github.com/jsdfhasuh/emo_master/actions/runs/41474013539)
- [Web、SDK、浏览器及软件 Runtime](https://github.com/jsdfhasuh/emo_master/actions/runs/41474013532)
- [Windows 冻结包构建与验证](https://github.com/jsdfhasuh/emo_master/actions/runs/41474015001)

完整源码 CI 已结束：Ubuntu 为 SUCCESS，9001 passed、31 skipped、0 failed，另有 42 passing subtests。Windows 为 FAILURE，9025 passed、5 skipped、2 failed，另有 42 passing subtests，pytest 耗时 3964.19 秒。两平台主测试收集数均为 9032，两个启动目录别名用例在两平台均通过。

`61e27174` 的两个 Windows 失败现象如下；远端后续 `e6342e32` 已加入上述修正，需查看其实际检查结果：

- `testTwoNetworkConsumersAll96ChangingImagesAndExplicitSupersession`：两个网络消费者的结果序列均缺少第 88 帧，其余 1–96 序号存在。
- `testSourceOnlyChangeRetiresActiveJobWithoutDevices[stopAffected]`：第 201 行等待检查预期 `RUNNING`，观察到的状态仍是 `STARTING`。

这些是 `61e27174` 实际失败现象，不代表历史失败的唯一根因已确定。完整原始日志、JUnit XML 和失败摘要在 `evidence/build/ci-61e27174-windows*`，状态 JSON 包含具体测试路径与诊断信息。它们按原样保留，不能作为后续新提交的通过或失败结论。

Web 和冻结包运行均已成功。Web 两个平台各有后端 148 passed / 2 opt-in skipped、前端 63 passed、生产构建通过；浏览器验证、独立软件 Runtime 和隔离 Web wheel 安装通过。冻结包 `vision_train` 为预设跳过。已有本地 Ruff、protobuf drift 和 mypy（609 源码文件）检查通过。

本地较早 Web `summary.json` 中 `cloudCi: NOT_RUN` 是其生成时的历史记录；当前云端结果由固定提交对应的 GitHub Actions 运行提供。原始报告保留原样以保持证据含义。

产品树中已有详细记录：

- [整合过程与历史失败](../../testing/2026-11-02-ui-main-integration/README.md)
- [捕获输入与文档冲突处理](../../testing/2026-11-02-ui-main-integration/source-snapshots.json)
- [分支处置与原整合计划](../../plans/2026-11-02-reviewed-branch-integration.md)

## 接手执行步骤

1. 在自己的云端环境 clone 仓库，读取 `AGENTS.md`，fetch 产品分支和资料分支。通过 GitHub 查询 PR #7 的真实 head、base、合并状态及所有检查。用户已授权在该仓库修复、提交、推送和完成合并。
2. 先审阅已存在的 `e6342e32` 及其检查结果。如果最新提交仍有失败，读取日志并做与本次整合有关的最小修复；保留正确断言，不通过 skip、弱化断言或盲目重跑掩盖失败。将修复推到现有 PR 的产品分支，按新 HEAD 验证。没有新改动、失败或未解决的问题时，无需重跑已通过的检查。
3. 所有适用检查通过后，更新 PR 描述，记录最终固定提交及实际结果。若 HEAD 仍为新观察到的 `e6342e32` 且其检查通过，可执行：

   ```sh
   gh pr merge 7 --repo jsdfhasuh/emo_master --merge --match-head-commit e6342e32d2716f61fcfe884310ccfc85960e3f1c
   ```

   若已有必要的新修复，使用经检查的新 SHA。采用正常 merge，保留历史和现有分支，不 force-push，不使用 admin bypass。
4. 合并后 fetch 并验证 `origin/main` 的实际合并提交、父提交、代码树及两条源分支的祖先关系。合并树若与已完整验证的 PR 树相同，新触发的重复 main CI 不要求再空等一整轮；报告应明确“合并前 PR 检查通过”。
5. 用中文报告 PR 已合并状态、最终 main SHA、纳入的主要成果、准确的 Windows/Linux 测试计数、Web/冻结包结果及验收范围。任务在 GitHub 上完成，不操作或声称同步用户本机分支。

沿用 Python 3.10、PySide2 / QWidget，遵守正常使用优先和局部修复原则。本任务不操作实际设备、现场工程、地址或凭据，不发布版本、不部署。源码与原生 Qt 自动化、冻结包检查不等于人工桌面和真实设备验收。
