# Mainline CI migration: implementation evidence

Baseline verified on 2026-11-24 UTC. This is a record of the new implementation, not an assertion that any hosted suite has passed.

- Product main: `9f3ac8550e7fbfcdd02510d5a1ad7395c82bef11`; new product draft PR #26 first implementation `5b08d7455b665ba1ac83f46890c83194bc92014f`. The workflow inventory and local exact-blob verification covered all 12 active files. Five local event/provenance tests passed.
- Public CI main: `481016dcbc96ed5a80fce53b02d8fa3cb105d33c`; [new public draft #17](https://github.com/jsdfhasuh/emo_master_ci/pull/17) first implementation `385ce8455dd9d7ba70336b2cb4fe52a0d97d7520`. Three local Web recipe tests passed; reviewed step profile sizes are 22/19/22/7, not counts of test cases.
- Packager default branch is named `master`, actual SHA `e0204fe6fe83b0f31818d88e4f296c70e230d172`. Fresh BuildOnly/V2 candidate local only, no new Windows run.
- The established V2 recipient fingerprint was rechecked from the Actions Variable. V2 activation is not a product verification result. Do not reconfigure keys or attempt to open V1 ciphertext with V2.
- All 12 product suites are **not yet accepted on the same candidate**. No existing PR was edited, merged, closed, or used as the branch base. No default trigger, main push, required gate, product Release, or private visibility change was made.

## First gated hosted jobs

- Product under test is `5b08d7455b665ba1ac83f46890c83194bc92014f`; subsequent documentation-only commits do not change runtime. [Capture Windows run 45428035361](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45428035361), public `1ed9b65e92113b97ea998640f5de93bd28dd6380`, job `138565304248`: actual public hosted run started, with 80-minute controlled test timeout, 120-minute job, V2 preflight checkpoint, per-case phase and JUnit evidence. No acceptance or recovery yet recorded here.
- [Web / owning Runtime run 45428340586](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45428340586), public `d3c61aef1b437321a040d307dd67edda357cee19`, four jobs on hosted Linux/Windows; only bounded V2 ciphertext should be uploaded. Actual dispatch linkage, test outcomes and independent private evidence verification remain open. These are separate public controller SHAs and not a final unified verdict.
- Fresh [packager draft PR #5](https://github.com/jsdfhasuh/python_build_scripts/pull/5), head `29e32308a368aa7f7537fae47a6a8c305bb96d3e`, adds Windows BuildOnly and V2 handling on a new branch from `e0204fe6`. Static checks passed. Public caller/real build and receiving side still pending. No release or old PR changes.

## Main synchronization and focused validation (2026-11-25 UTC)

- Product main `ed98a0212820efd0e6436029b69e82dc6e313fc7` includes PR #25. Product PR #26 was synced via merge commit `e1c19fcdbbee523b51c34ece905d45152033dfda` while preserving all five existing migration blobs. Product main, existing workflows, and earlier evidence were not modified.
- Historical Web/owning Runtime four jobs succeeded and private V2 recovery verified all four archives. The historical Capture execution failed on the old candidate; its two V2 archives were recovered and integrity checked. These six envelopes and their historical conclusions were preserved.
- [Public focused run 45566403989](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45566403989), controller `67ef6321aa30be591cb44d4b10de96ffe9a05bb6`, executes four original Designer cases plus one observer witness on the synced product SHA, Windows hosted/Python 3.10.11/Qt offscreen. The independent JUnit gate requires exactly five passes; only V2 ciphertext uploads. Hosted JUnit checked five passes with no failures or skips; private job 139043992424 in receipt run 45566960696 independently verified the two encrypted archives, hashes, five passing calls and valid observed Capture payload. This is not full Capture acceptance.
- [Public Windows BuildOnly run 45566601554](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45566601554), controller `ea69fc563abdbf1f82108d1c23bd5811a4f08e72`, reusable workflow `c2d39e76cad28ebc153aed0f15055b39a2290af1`, packager source `e0204fe6fe83b0f31818d88e4f296c70e230d172`, same product SHA. Designer and Runtime, no release credentials or publish. Real builds and V2 recovery pending.
- Source decision/receipt and non-required shadow writeback checks were added as a local software boundary. 12 local unit tests pass (5 envelope and 7 receipt/writeback). There is no deployed reporter, Source job dispatch or formal gate in this commit. No publish, Private switch or workflow retirement.

- [Original-scope Capture 45567025964](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45567025964) started after focused hosted success on the same product SHA `e1c19fcdbbee523b51c34ece905d45152033dfda`, controller `7e8a3bdb7a8508bbf05fa6556987889a5faa927f`. Original collection/order/assertions retained; full hosted result and new V2 recovery pending.

### Hosted results for the synchronized product candidate

- Run 45567025964: full Capture pytest failed with a later Windows native termination in the device usability chain. The Capture witness payload was present and its independent validator passed. No completed JUnit report; 6,259 passing call phase records and no failed call-phase records were recorded before termination. These are partial phase counts, not a full suite verdict. Both V2 archives were downloaded and verified inside the existing private boundary (receipt run 45578764771, job 139083197068). No rerun.
- Run 45566601554: Designer and Runtime BuildOnly jobs passed the build and their target-specific validation; two package archives and two diagnostic archives were uploaded encrypted. GitHub outer SHA-256 metadata recorded; independent package recovery pending. No publish.

## Parallel migration results

| Scope | Product / workflow revision | Public run | Verified outcome |
|---|---|---|---|
| Native Display contract | e1c19fc / 4750950 | [45592225981](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45592225981) | 72 passed, no failures or skips; V2 private receipt verified. |
| Device wizard focused | ca57a56 / 2fa1c47 | [45592495954](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45592495954) | Two passed (C1–C6 and Capture witness), no failures or skips; V2 verified; not full Capture. |
| Windows BuildOnly | e1c19fc / ea69fc5 / c2d39e7 packager workflow / e0204fe tool source | [45566601554](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45566601554) | Two jobs passed. Private receipt 45592091858 matched four outer archive digests and four internal binary hashes to build manifests. |
| Linux and Windows Source | ca57a56 / 46445fc | [45593167042](https://github.com/jsdfhasuh/emo_master_ci/actions/runs/45593167042) | Four original hosted shards started; private combined shard-scope validation pending. |

- Private receipt 45593075267 verified 3 ciphertexts and JUnit for Display and the wizard. Different tested product revisions are deliberately distinct.
- The wizard test-only patch relocates the existing narrowly scoped Windows offscreen fixture; it does not change production code or native dialog behavior. A new full Capture run must be selected only after the combined baseline, not as repeated ad hoc diagnostics.
- On tested product commit e1c19fc, actual non-required `dot/windows-buildonly-v2-shadow` status ID 59858221698 was written and independently read back, then confirmed by idempotent private workflow 45593296845. The official push/PR/manual dispatcher and required gate remain disabled and not proven. GitHub's creator-bearing status history endpoint is used for readback; 17 local Source broker/decision tests passed.
- Remaining: formal event dispatch and pinned worker registration, Source V2 cross-shard verification, unified acceptance of all 12 workflows, exact evidence/failure semantics, Release/rollback/visibility handoff. No default gate or Release opened.
