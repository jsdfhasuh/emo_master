# Reviewed branch integration

Date: 2026-11-02. User requested a second review followed by consolidation into main.

## Latest consolidation follow-up

The user subsequently requested inclusion of the current uncommitted UI work.
The Designer `8df8599fc026c424a49e87cc53fb45f6b371f16f` and Web
`b89826b0841c6b4ef7f9c5f11ac938c6008934c7` tips were merged, followed by their
captured SDK 1.3.0 / expanded canvas workspaces. The complete source inventory,
integration repairs, and validation boundaries are recorded in
[the consolidation report](../testing/2026-11-02-ui-main-integration/README.md).
The original source worktrees and historical branch dispositions below remain
preserved; the older fixed-tip table describes the first PR revision.

## Fixed source inventory

Main starts at `f0675253631f489998056e5af618378e115efc83`.

| Branch | Fixed tip | Disposition |
|---|---|---|
| codex/designer-resize-zoom-fix | `46af63de553f0cf60f64ce0b2f37440362a55da2` | Integrate Designer resize and preceding canvas repairs |
| codex/panel-protocol-ungrouped-fix | `0e58440a0a6bea9b368d07b37e2d0b9cf8f0d377` | Already an ancestor of resize tip; retain its official protocol delivery |
| codex/logging-and-designer-improvements | `b1ca6d386e8bb5cf4d21609e52f09acd0803126b` | Normally merge latest official Web/SDK integration and preceding logging/Designer work |
| feat/web-operator-panel-20261030 | `402113075b2c5014b4bff8370d31f7c59fe1c59a` | Superseded draft; current Web verification explicitly retains its history and replaces its missing-contract adapter. Preserve ref, do not overwrite current implementation |
| codex/camera-draft-preview | `3f6a256ad97273a47bba2f95f05898e7c379b2e1` | Functionality and evidence integrated by later main history, including `0e45d09a`; preserve original worktree/ref |
| codex/flow-edge-readability | `f448c88240ea824d2bbfd6a1757e59c8873927e9` | Ancestor of main; preserve dirty historical evidence worktree |
| codex/release-v0.6.5 / codex/release-v0.6.6 | `443f1d55` / `25c82042` | Ancestors of main; retain release records |
| docs/agents-guide-20261028 | `b679092057b7d3ffd4c09f65e3ef891f0c534abe` | Add/revert pair with no net changes; retain history |
| pr-5-merge / pr-5-e943-merge | `4aae3160` / `a6335c3d` | Historical merge refs with no changes beyond ancestors already in main |
| codex/ci-threadpool-provider-smoke | `e3dee3cada48eb571eed42bf93358b37d3e9baea` | Previously retained diagnostic-only work; no product repair to merge |

No branch, tag, historical worktree, or uncommitted file is deleted. No release, installer, hardware operation, or deployment is included.

## Review and integration checks

- Resize review: original fixed commit independently passes 105 focused tests; same final 33-case matrix against the parent reproduces 31 failures and 2 passes. These are offscreen Qt window-level tests, not manual desktop acceptance.
- The two current feature tips merge without conflicts. Their unique changes are in separate Designer and Web files. The merged tree is `8d4d98ed0de8bf66d558871b5629d120254a24da` before integration repairs below.
- Full protobuf drift, source/test Ruff and source mypy checks pass on that tree (605 source files for mypy).
- Whole-repository collection exposes a duplicate `test_page_publication.py` module and accidental collection of independently installed Web tests. Give the new operator UI test a distinct filename, declare desktop/example test roots, and keep the Web suite in its dedicated workflow.
- Web CI must follow main and main-targeted pull requests, including owning Runtime/SDK changes; its old branch-only trigger would otherwise stop validation after consolidation.
- The previously recorded legacy 2.1 save failure is a normal supported save path. Refresh a page observer only when the document has a presentation; preserve legacy format and normal saved-state handling. Extend the existing legacy entry regression without changing its assertions or fixture format.
- Preserve original failing collection and prior resize evidence. Run affected regressions and the existing CI jobs on the integration revision. Merge/push main only after resolving attributable blocking failures; record skips and any platform limits separately.

## Acceptance boundaries

Real mouse resize at 50/100/150%, frozen EXE, actual camera/PLC, deployment, and unresolved historical Qt log anomalies remain separate from source/CI verification. This consolidation does not convert them to passed acceptance.
