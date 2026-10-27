# Workspace Delivery Evidence, 2026-10-23

See [the delivery and review report](../../reports/2026-10-23-workspace-delivery.md)
for source identities, results, repairs and acceptance boundaries.

These files preserve actual outputs, including failed investigations:

- `merge-focused-initial.txt`: six old online-copy-reference assertions failed.
- `affected-regression.txt` / `.xml`: first affected regression, four failures.
- `upstream-resource-feedback.txt`: resource feedback failures reproduced on upstream `3c8a56ff`.
- `image-prefix-final.txt`: despite its original filename, this is a FAILED
  intermediate run. Per-test settings alone did not restore first paint.
- `image-prefix-geometry.txt`: failed run with an empty image visible region.
- `image-prefix-scroll.txt` / `.xml`: ordered-prefix rerun after exposing the
  image through the ordinary nested scroll areas; actual paint remains required.
- `feedback-review-final.txt`: intermediate test parsing failed on trailing
  traceback text; the diagnostic JSON parser was subsequently corrected.
- `feedback-review-verified.txt` / `.xml`: successful diagnostic feedback rerun.
- `affected-remaining-final.txt` / `.xml`: final remaining affected tests.
- `native-node-final.txt` / `.xml`, `small-*.png`, `4k-*.png`: Windows QPA
  automation with synthetic images, not camera or field evidence.
- `snapshot-review-integrity.json`: Git-normalized source/test/proto comparison
  between the tested snapshot and staged tree.
- `protected-final.json`: aggregate preservation result and three already tracked
  upstream example paths only; the private baseline manifest is not published.
- Static-check outputs and the exact affected-test file list are also retained.

Raw files use `-text -whitespace` attributes so Git does not rewrite their bytes.
No skipped, deselected, pending or initially failed check is recorded as passed.
