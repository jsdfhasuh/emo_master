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
