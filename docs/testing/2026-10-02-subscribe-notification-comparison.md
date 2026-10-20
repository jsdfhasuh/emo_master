# Subscribe notification: reviewed cloud comparison

One prespecified, equally instrumented, serial A1/B1/B2/A2 comparison completed
once on 2026-10-02, 02:13:41–02:15:13 UTC. The candidate showed a consistent
observed reduction in closed-timestamp → metadata-enqueue p95 for these inputs.
This is descriptive evidence, not general latency, physical-screen, Windows, or
formal acceptance evidence. Performance acceptance is **NOT_ASSESSED**; every
arm still violates the unchanged all-window 500 ms model/UI age gate.

## Sources and workload

- A: clean frozen baseline `655eeac84d0602aa670c76af67679509782bbd7f`
- B: clean frozen candidate `7d8c9e9af0901253fce15d7bd65efa66f14e12a8`
- Integration base: diagnostic-only `ecf8bcfcf193e05807556340d670f05c0f3d9e15`,
  whose parent is A. The two production files and 23-case regression file remain
  byte-identical to B; their Git blobs and SHA-256 hashes are indexed below
- Separate reviewed no-capture follow-up:
  `4db426f24dd4cc8466a7a1eb7d14c93860aa66fa`. Only its exact fixture correction
  and new preflight-only workflow are added to this integration. Five of the six
  original diagnostic files and all nine pre-existing workflows remain
  byte-identical to the base, including the original capture workflow. The
  fixture explicitly rejects every real host before native probes. The new
  workflow permits offline fixtures, compilation, and metadata preflight only;
  it has no capture/controller/stop invocation. This is not a Windows run result
- Existing `none_qt` LoadProject/StartJob path, NONE policy, capture enabled,
  two independent DisplaySession consumers, two actual PySide2 Qt windows
  sharing consumer 0's hub, cloud offscreen platform
- Seed 20260926, 1920×1080×3 inputs, scheduled 5 Hz, 96 inputs, 8 warmup;
  ordinals 9–96 give exactly **88 per consumer, window, and Subscribe stream**
  in every arm. Common input SHA-256:
  `da18583fb8e3118e7eee799451516a06de92d3c5f9d584becc890393644bd210`

No arm was repeated or replaced. Both trees and reviewed support files stayed
unchanged; all jobs/exporters/samplers/owners retired, before/after owner
censuses were empty, and bounded evidence had no overflow, drops, read failures,
resets, expired scopes, or consumer errors. Every job executed all 96 inputs,
export completed 96 images per arm, and both consumers/windows retained all 88
measured results. A few metadata receipts came through health snapshots rather
than Subscribe (including one B1 C0 result); they remain in the denominator.

## Observed direction and limits

The primary is the median of the two consumer closed→metadata p95 values. P95
uses sorted index `floor(0.95 × n)`, n=88; the two consumers/windows are not
independent experimental replications. Candidate-minus-adjacent-original values:

| Pair | Primary p95, A → B (ms) | Primary change (ms) | Closed→model p95, C0 / C1 (ms) | Closed→GUI p95, W0 / W1 (ms) | Closed→paint p95, W0 / W1 (ms) |
|---|---:|---:|---:|---:|---:|
| A1/B1 | 19.900 → 3.614 | -16.285 | -14.059 / -11.410 | -9.635 / -9.694 | -9.237 / -9.117 |
| A2/B2 | 20.693 → 4.125 | -16.568 | -12.957 / -9.544 | -12.205 / -12.274 | -11.596 / -11.526 |

Scope-end→model/GUI/paint p95 also fell in both pairs, but execution and tails
did not uniformly improve:

- Execution p95 was 21.72% lower for B1/A1 and **4.26% higher** for B2/A2
- B2 scope-end→paint maxima were **168.107/166.562 ms**, versus A2
  **164.127/162.644 ms**
- Candidate B2 retained a **19.798 ms** closed→qualifying-snapshot-ready maximum;
  its consumer closed→metadata maxima were **19.139/21.362 ms**
- Total qualifying replies increased: B1 **198/195** versus A1 **193/193**;
  B2 **196/196** versus A2 **192/192**. Every measured stream still had 88
  started-only messages and all 88 closures, with at most one newly observed
  closed result per reply. Zero-new-result and cursor-jump patterns changed
- Raw all-window maximum model/UI ages were A1 **553.309**, B1 **506.125**,
  B2 **529.242**, A2 **534.891 ms**. The post-terminal observation tail stays
  included; retrospective phase separation does not erase the failed age gate

`closedNs` is stamped inside ResultStore.close under the store lock, before
remaining publication/trim/unlock work. It is not publication-after-lock.
Metadata `received_ns` is sampled after client conversion/validation immediately
before enqueue, not at transport arrival. Snapshot-ready is recorded after the
original snapshot returns, before yielding/serialization/transport. These
intervals do not isolate pure wake latency. GUI is widget commit; first paint
is a callback, not physical-screen presentation. Repeated commits of the same
key are retained in raw data; first commit/paint gives each 88-key measurement.

The original full 0.02-second post-yield sleep is preserved in source. Candidate
ready gaps of roughly 21.2–21.6 ms at minimum are observational snapshot gaps,
not direct sleep/yield timing. Early started-only notification can still place
closure behind that cooldown. This comparison does not establish unchanged
temporal coalescing, a causal explanation of individual tails, a 200 ms target,
or a pass of the original formal nine-trial acceptance workload.

## Audit trail and validation scope

The independent post-run review approved the interpretation after recomputing
raw p95 values, checking every 88-result join and the support-byte, validity,
and retirement gates. No additional workload was run for that review.

The compact [source evidence index](../evidence/r3-subscribe-notification/source-evidence.json)
records full source identities, byte counts, hashes, and the retained external
evidence location. The repository also retains the complete
[comparison checksum list](../evidence/r3-subscribe-notification/comparison-SHA256SUMS.txt);
the approximately 15.7 MB raw comparison bundle remains outside the repository.
Principal SHA-256 identities, relative to `evidence-subscribe-abba-20261002/`:

| File | SHA-256 |
|---|---|
| `report.md` | `e7df718be5f7df655851b27cb0b2399fd63d5ab3dfaba259facd651c267812de` |
| `REVIEW-AFTER.md` | `051d44fde2b9508c2debfab1f8f9b9dc6b841924813af8b328e3afd697a8aa97` |
| `run/manifest.json` | `a510670d403b514d79f212b230541d8315ed2a69a5ba1d72da73953b6cfdaabe` |
| `run/report.json` | `36368473352851e956522e315924b301162c21db7aba57ffc3073ea80483582b` |

Frozen candidate B separately passed the complete original `scripts/ci_check.py`
once: proto drift, Ruff, mypy, **2488 passed, 4 skipped, 27 subtests passed**.
Its [full CI log](../evidence/r3-subscribe-notification/candidate-full-ci.txt) and
indexed supervisor result establish 1371 stable tracked-file hashes, clean Git
status, and empty owner censuses before and after. The final integration needs
its own frozen-commit CI record, retained separately under the integration
evidence location in the index. Neither CI result changes performance status.

The historical [poll gate](2026-10-02-subscribe-poll-gate.md) remains a baseline
record and is not collected as a candidate passing test. Candidate publication
and Windows/system tracing are separate work; this comparison performed neither.
