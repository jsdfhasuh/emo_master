# 5410737 Debian display acceptance archive

Tested source: `5410737a561e1f192ee9a081b3f208c9b27d9886`.
This is a documentation-only archive, not a product change or validation of its newer archive commit.

Read the 7-page Chinese PDF/DOCX and `CODEX_HANDOFF_PROMPT.md` first. The scoped five-session native GUI lifecycle and full1–96 ABBA checks passed. Historical failures/root-cause questions and untested scope remain explicitly bounded in the report.

Verify `SHA256SUMS.txt`. Two larger ZIPs are stored as lossless byte parts to respect the transfer request limit; run `python reassemble_evidence.py` to recover them. `ORIGINAL-ARTIFACTS.json` lists the original PDF/DOCX and three ZIP byte counts and SHA256. Extract the three independent ZIPs into one folder and verify each internal SHA list. No original file is changed by the reassembly script if different bytes already exist.

21 unchanged screenshots,35 sampled native PNGs, raw traces/logs, projects, scripts, independent audit and7 lossless video excerpts are included. The full1202-second original recording is retained only in the task workspace with a hash/provenance index; it is not the delivered video. No Windows CI or performance/full-product claim is added.
