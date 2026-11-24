# Mainline CI migration: implementation evidence

Baseline verified on 2026-11-24 UTC. This is a record of the new implementation, not an assertion that any hosted suite has passed.

- Product main: `9f3ac8550e7fbfcdd02510d5a1ad7395c82bef11`; new product draft PR #26 first implementation `5b08d7455b665ba1ac83f46890c83194bc92014f`. The workflow inventory and local exact-blob verification covered all 12 active files. Five local event/provenance tests passed.
- Public CI main: `481016dcbc96ed5a80fce53b02d8fa3cb105d33c`; [new public draft #17](https://github.com/jsdfhasuh/emo_master_ci/pull/17) first implementation `385ce8455dd9d7ba70336b2cb4fe52a0d97d7520`. Three local Web recipe tests passed; reviewed step profile sizes are 22/19/22/7, not counts of test cases.
- Packager default branch is named `master`, actual SHA `e0204fe6fe83b0f31818d88e4f296c70e230d172`. Fresh BuildOnly/V2 candidate local only, no new Windows run.
- The established V2 recipient fingerprint was rechecked from the Actions Variable. V2 activation is not a product verification result. Do not reconfigure keys or attempt to open V1 ciphertext with V2.
- All 12 product suites are **not yet accepted on the same candidate**. No existing PR was edited, merged, closed, or used as the branch base. No default trigger, main push, required gate, product Release, or private visibility change was made.
