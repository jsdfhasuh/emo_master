# Public Web execution recipe

Baseline: `.github/workflows/web-panel-ci.yml`, product plan snapshot 9a8b4433.
The controller should dispatch these three independently on GitHub-hosted runners:

| Recipe | Runner | Timeout |
|---|---|---|
| web-panel | ubuntu-latest | 15 min |
| web-panel | windows-latest | 15 min |
| product-runtime | windows-latest | 20 min |

Controller installs Python 3.10, Node 22 with no package-manager cache, and makes
Git Bash available on Windows. The runner receives an already acquired **verified
execution SHA** checkout in a sandbox/isolated context, never the acquisition
credential or CI runner-control access. Validate and provide the **original**
product GitHub event, not the receiving workflow event. Dispatch Web checks only
for the exact branch/path rules in `web_recipe.json`; preserve per-source-run
manual concurrency and normal push/PR cancellation as in the original workflow.

Within the isolated context, pass an independent evidence directory not yet
created:

```text
python run_web_recipe.py --job web-panel --platform ubuntu-latest --source-event pull_request --product PRODUCT_DIR --evidence EVIDENCE_DIR
```

The recipe returns 0 only when every applicable original run command succeeds.
It fails on the first failed step. The controller needs to collect logs and the
manifest's evidence paths from the isolated context and encrypt there or using a
trusted file-only boundary. Never publish raw logs or artifacts. The original
independent preview wheel distribution is success-only. Browser and owning Runtime
diagnostics are available on failure as well. Public output is only generic step
number/status. The wrapper is **not** a secret or egress boundary, and should not
be run with an active PAT or plaintext publishing credentials, even if masked.
Do not claim this staging recipe has run on Windows or gated a production commit.
