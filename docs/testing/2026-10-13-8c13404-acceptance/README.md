# 8c13404 Debian desktop acceptance archive

Tested source: `8c1340461d9675ab3a5f078135f17407fa027cc7`.
This directory is a documentation-only archive. Its commit and any later branch HEAD have not thereby been re-tested.

Read the Chinese PDF or DOCX report first. The five original evidence ZIP files are byte-identical to the delivered, sanitized attachments. Verify `SHA256SUMS.txt`, then extract all five ZIPs into the same directory and check their internal SHA lists. They contain raw test evidence, projects, scripts, audits, 119 unchanged product screenshots and 10 lossless video excerpts. Original full recording and excluded private-host screenshots are not published; their hashes and exclusion/provenance records are retained in the archive.

Open `CODEX_HANDOFF_PROMPT.md` for the complete repair and regression task. W01/P1 and A02/P2 remain open under their documented scope. This publication adds no product, test or CI changes and supplies no new Windows CI claim.

GitHub transfer note: three ZIP files exceed the connector request-body limit after base64 encoding. Their unchanged bytes are stored as .part01/.part02 files. Run `python reassemble_evidence.py` first; it verifies part and original SHA256 and refuses to overwrite different bytes. ZIP-PARTS.json records the mapping. The other two ZIPs are stored whole.
