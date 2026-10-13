# b35e2af Debian NG acceptance archive

Tested source: `b35e2af8641683f366393e02388a0ce5bcb7680d`. This is a documentation-only archive, not validation of its newer archive commit.

Read the6-page Chinese report and CODEX_HANDOFF_PROMPT.md first. The current scoped checks passed; historic capture root causes remain OPEN and untested scope is not extended.

Verify SHA256SUMS.txt, then run `python reassemble_evidence.py`. The original evidence ZIP is split into lossless byte parts no larger than8MB to respect the transfer request limit. The script verifies each part and original SHA, and refuses to overwrite different bytes. ORIGINAL-ARTIFACTS.json records original PDF/DOCX/ZIP hashes and byte counts. Extract the recovered ZIP and verify its internal SHA256SUMS.txt.

11 unchanged product screenshots,40 sampled native PNGs,340 observed raw task/proof records,55 automated/injection results, original logs/projects/scripts and2 lossless video excerpts are retained. Full454.166-second recording remains only in the task workspace with original hash/provenance. READY is not retired; Stop32 historical assets follow the disclosed contract. No Windows CI, performance or full-product claim is added.
