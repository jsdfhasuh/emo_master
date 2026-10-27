# Desktop workflow U01–U07 remediation

Completed on visible Linux desktop. Prior acceptance files are preserved, not rewritten. Final tested source: `e7ceb5135f2f5dcc158110fb23da29c049bbe2be`; delivery consists only of subsequent documentation/evidence commits. The exact delivery SHA is recorded in the handoff and verified remote branch, avoiding a self-referential commit hash.

## Scope and environment

Baseline remote `agent/runtime-workflow-architecture-v1`: `d39169251c9bedc339c440a29a56df5eafba3be5`. Plan committed first as `b1bf28a`. Independent sparse worktree, cloud Debian13.6/Xfce1364×1024, Python3.10.22 / PySide2 5.15.2.1 / QtWidgets. No user computer, physical production devices, Windows, forced pushes or other-branch merges.

## Changes

- `456a9d0`, `1360cc5`, `598d4a8`: U01 standard current blank skeleton and local draft entry, startup/menu/reopen/error contract and current title; U07 native body double-click ends before touching the item destroyed by synchronous save/rebuild. Title rename/cancel preserved, no broad exception suppression.
- `f8d7fb3`: U02 framework expected cancellation enters existing CancellationRequested/ABORTED, guards first beginCycle, STOPPING never regresses from late start; real faults and retirement preserved.
- `324b194`: U03 structured compiler diagnostics with friendly flow/node/device/channel location, U04 responsive splitters and actual inner/outer error reveal, U05 current-controller no-page summary and device names, U06 undo/redo notice refresh.
- `9fa5745`: test fixture corrections for current named files and publication guards; no product code change.
- `73194ec`: actual desktop found first-publication selected symlink versus canonical source mismatch. Canonical same-copy matching fixes it; another same-UUID copy still rejected.
- `e7ceb51`: actual replacement B uses a Runtime-managed online source reference. Designer run, Validate, inspection and image selection now use the committed reference for the matching current structured project, preserving local file/session and copy guards. Native QAction embedded/loopback regression failed before and passes after. Do not label73194ec routeA passed.

## Earlier automatic verification (preserved history)

- `9fa574597961d5e86097361417a4f70de79e3809`:70 deduplicated modules,766passed,0failed,0skipped,2historical migration cases deselected,255.38s. Ruff30 changedPython files, mypy19 changedproduct modules, protocol check and git diff checks passed.1299 trackedsource/test/tool blobs unchanged before/after.
- `73194eceef2819b2207129eeb55311efb4be8f79`: expanded aggregate883executed,881passed,2failed,0skipped,2historicalmigrationdeselected,341.21s. The failures are parallel-workflow[4-4]image45sdeadline and[None-5]partialstartAll. Exact original failures retained. Same entire module independent rerun11passed25.20s, not presented as an all-green original aggregate. Static checks passed,1299blobs unchanged.
- U02 deterministic actual spawned framework barriers:4scenarios/8Jobs, cancellation then successful restart with real image artifacts. No sleeps/random retries to trigger boundary and no manually injected terminal event substitute.
- U07 exact baseline native Qt deleted-item failure retained, then fixed real body-save path passes. Separate unrelated callback problems not credited.
- Historical migration cases excluded by task scope: `testUpgradedDraftResourceFailureCanBeFixedAndRevalidated` both variants. Exclusion and original failures retained, not weakened into passes.

## Earlier desktop verification (preserved history)

See independent evidence timeline and screenshots. Mouse/keyboard/menu/dialog/filechooser only for project construction, device binding, import, save, publish and run. Shell only official entry/package commands, localhost peer and read-only evidence. No JSON editing or internal Qt/businessAPI substitutes.

A3 actual image320×480 and16byte TCP, then same Designer newB3 preserved onlineA exactly through save/reopen and imports. Duplicate device import undo/redo and mapping verified. Explicit B3 replaceCOMMITTED R2, but F5 failed before Job due online-copy reference mismatch. This is a normal-route blocker, not a pass, and was subsequently fixed by e7ceb51, verified below.

Operator B3 official package export/install/check successful. Visible no-page feedback and named devices correct. Start/Stop Job420d5feb-1828-4451-ac38-8744dd3c384a: accepted→stopping→process.started→aborted/E_CANCELLED, UI resourcesreleased. Restart Job3be628eb-d92f-4f0e-899c-6e20ee480082 COMPLETED, actual16bytesreceived, realcamera480×320×3frame. No persisted PNG because production snapshotNONE; final image artifact check was subsequently completed on e7ceb51 below. Normal close, no related exception.

## Unverified boundaries

Windows, frozen executable, physical cameras/PLC/robot, productionnetwork and unexecuted packagecombinations are NOT passed. No new stress/forcedkill/complexfailure matrix. Offscreen automated Qt tests are not desktop evidence.

## Final verification and status

Final fixed source `e7ceb5135f2f5dcc158110fb23da29c049bbe2be`: **156 passed, 0 failed, 0 errors, 0 skipped, 0 deselected**, 15 affected modules. Ruff33 changed Python files, mypy22 changed source files, protocol and diff checks pass. All1,299 tracked source/test/support blobs unchanged. Exact commands, durations, XML, logs and checksums: `../evidence/2026-10-22-desktop-workflow-remediation/automated/final-e7ceb51/FINAL_MANIFEST.json`. A prior harness-only attempt had156setup errors from a missing temporary parent directory; preserved, corrected in the harness only, not represented as product failures or silently omitted.

| Item | Code | Automated | Visible desktop |
|---|---|---|---|
| U01 | Fixed current skeleton/local open/title/errors | Actual startup/menu/skeleton/load/isolation tests pass | Startup new A3; final-source same-session new B4, save/reopen, online A unchanged |
| U02 | Fixed expected framework cancellation/STOPPING | Deterministic real-worker boundaries, genuine faults and retirement pass | Final-source accepted Stop → ABORTED, retired → restart COMPLETED |
| U03 | Structured diagnostic mapping/navigation | Real compiler/dialog navigation passes | Missing channel → friendly node/location → corrected → validation passes (73194ec; unchanged relevant code) |
| U04 | Responsive splitters/error reveal | Affected device tests pass | Normal and smaller window actual error cell visible; values retained (73194ec; unchanged relevant code) |
| U05 | Current-controller fallback/names | Feedback/device tests pass | Final-source no-page, correct Job, connected state and readable names |
| U06 | Undo/redo notice refresh | Affected import/workspace tests pass | Final-source double import → undo → redo, distinct disabled copy retained |
| U07 | End handled event before destroyed item reuse | Exact original native Qt failure, fixed save/cancel/rename tests pass | Body save/cancel, title rename, move/select/re-edit (73194ec; unchanged event code) |

Route A normal chain is repaired and verified through staged UI evidence: actual new A/device/node/U03/4/7 creation on73194ec, then after fixing discovered source-reference defect, final e7ceb51 **single Designer session** reran A publish/run/export → File New B → save/reopen/import/undo/redo/map → explicit replacement → B actual image/TCP. No second Designer substitutes the critical A→newB step. This is deliberately not described as an uninterrupted fresh-A-creation full route on final SHA. Final A Job `2edc4530-f110-4e70-ab90-ee9851211080`, B Job `c2db0e56-c5d2-4dbb-b1b7-f8a5ab39179a`; both completed with actual320×480PNG and localhost16-byte receipt.

Route B fully rerun on final SHA with official B4 package export/install/check and visible Operator. Cancellation Job `e4d25e74-adfd-442a-82d1-3dd88fb7e229`: accepted08:22:15.208Z → stopping.359 → process.started.674 → aborted/E_CANCELLED.679, no business node/FAILED/late RUNNING. UI resources released. Restart Job `46007ce6-6137-479b-b58b-08ab2dd4ff6a` completed08:22:29.777Z; actual TCP16bytes and persisted77,614byte PNG. Normal close without related exceptions. This desktop cancellation is distinct from deterministic race coverage.

## Evidence index and delivery

Repository evidence: `docs/evidence/2026-10-22-desktop-workflow-remediation/`:
- `desktop-timeline.md`: exact staged chronology, failures retained, continuity limits, screenshot numbers.
- `final-route/`: official command lines, job events, raw Runtime/Designer logs, TCP receiver records, before/after online snapshots, actual PNGs.
- `automated/`: exact per-SHA commands/counts/static checks/manifests, baseline failure and fixed tests; original881pass/2fail aggregate retained.
- `projects/`: original GUI-created drafts, exported packages and final B4 Runtime package. These are simulation samples, not production configurations.
- `SHA256SUMS`: evidence integrity.

External full working evidence remains `/workspace/shared/emomaster-remediation-evidence-20261022`; prior read-only acceptance remains `/workspace/shared/emomaster-desktop-accept-20261022`. No prior records overwritten. Changed-file list is `changed-files.txt` in evidence. Only target branch is pushed, no force and no merge to another branch. No further source/test changes after tested e7ceb51.

## 中文交付摘要

| 项目 | 代码修复 | 自动测试 | 真实桌面 |
|---|---|---|---|
| U01 新建/打开 | 已修复 | 通过真实入口、构造器和在线隔离测试 | 启动新建 A3；最终源码同会话新建、重开 B4，在线 A 未变 |
| U02 启动取消 | 已修复 | 确定性屏障、真实错误、资源退休通过 | 最终源码接受 Stop 后 ABORTED，再次开始完成 |
| U03 错误定位 | 已修复 | 编译器诊断与定位通过 | 缺通道→定位节点→补齐→校验通过 |
| U04 布局/错误可见 | 已修复 | 受影响模块通过 | 正常/缩小窗口错误单元格可见 |
| U05 Runtime 反馈 | 已修复 | 当前任务摘要、设备名称通过 | 最终源码无页面反馈和名称已验证 |
| U06 撤销提示 | 已修复 | 受影响模块通过 | 最终源码双批导入、撤销、重做已验证 |
| U07 双击生命周期 | 已修复 | 原异常精确复现后修复通过 | 正文保存/取消、标题改名、移动和再编辑已验证 |

路线 A：分阶段走通。最终源码同一 Designer 会话的 A 发布/运行→新建 B→重开/导入/映射→显式替换→B 图像/TCP 已完成；A 的最初创建及 U03/U04/U07 桌面检查沿用73194ec的真实记录，不冒称全部从零在最终 SHA 重做。`git diff 73194ec e7ceb51 --` 对 project_store.py、project_controller.py、flow_scene.py、device_workspace.py、device_wizard.py、tcp_device_form.py、project_configuration.py 的输出为空；main_window.py 后续仅涉及在线工程引用解析/执行检查，不改新建入口。

路线 B：最终源码完整走通，正式包导出/安装/校验、可见 Operator 开始/启动期停止/退休/再次开始均有日志和图像产物。

最终源码156通过；旧扩大回归881通过/2失败和同模块重跑11通过分别保留，不宣称旧整组全绿。Windows、冻结程序和实体相机/PLC/机器人未验证。报告与原始证据经独立只读复核，未发现交付阻断项。

## GitHub 发布说明

本地被测源码 e7ceb5135f2f5dcc158110fb23da29c049bbe2be 经已连接 GitHub API 按原顺序重建提交，发布源码 SHA 为 a1d6b24f671afb458cb405ceb5c2e73fb96f9424，完整 Git tree 均为 a4ca2b2a5ef8fea6d5a593cca6de01d7bf0884e6。身份/时间元数据改变 SHA，但全部源码/测试字节相同。目标分支的后继交付仅新增文档证据。映射见 ../evidence/2026-10-22-desktop-workflow-remediation/publication-map.json。关键原始证据现打包为该目录单个 key-raw-evidence.zip；上述目录路径均指解压内容，完整扩展证据另有单个整包。
