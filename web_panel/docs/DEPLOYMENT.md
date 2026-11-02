# Official SDK1.1.1 install update (2026-10-31)

Install the supplied SDK wheel together with the Web wheel. For a source checkout,
first run `python -m pip install contracts/panel-contract-v1/python/*.whl`, then
install the Web project. For distributions: `python -m pip install SDK.whl WEB.whl`.
The SDK is not assumed available from a public package index. Preview now requires
`--context CONTEXT_JSON --host 127.0.0.1 --port 0 --json --no-control`; the first
stdout line provides a one-use token URL, not a password. See the current README.
Real `--mode grpc` now uses official RPC; old missing-contract statements below
are historical draft notes, not current behavior. Do not deploy without the
explicit host/operator acceptance appropriate for your environment.

---

# Independent installation and upgrade

Development distribution only. Software Runtime/browser acceptance does not replace native Designer, cross-physical-machine or field acceptance. No scripts change firewall, services, Runtime configuration or user machines.

## Build machine

Python 3.10+ and Node 22:

```sh
cd web_panel
python -m pip install -e '.[test]' build
npm --prefix frontend ci
npm --prefix frontend run build
python scripts/package_frontend.py
python -m build --wheel
```

The wheel contains frontend static assets. The target host needs Python and the wheel dependencies, **not Node, Qt, drivers, model inference or an emo_master installation**. Offline hosts require a separately prepared dependency wheelhouse for their Python/OS architecture; the development wheel is not a self-contained interpreter bundle.

## Windows host (commands for the operator, not automatically executed)

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\emo_master_web_panel-0.1.0a1-py3-none-any.whl
.\.venv\Scripts\emo-web-panel.exe --data-dir .\panel-data create-user admin
.\.venv\Scripts\emo-web-panel.exe --data-dir .\panel-data serve --mode mock
```

## Linux host

```sh
python3 -m venv .venv
.venv/bin/pip install ./emo_master_web_panel-0.1.0a1-py3-none-any.whl
.venv/bin/emo-web-panel --data-dir ./panel-data create-user admin
.venv/bin/emo-web-panel --data-dir ./panel-data serve --mode mock
```

Default loopback URL: `http://127.0.0.1:8780`. Interactive password entry avoids shell-history secrets. The administrator can register Runtime aliases and grant each operator/viewer explicit target view/control rights. Admin explicitly includes production control in this preview's permission table. Password resets revoke sessions; new same-name accounts receive a new internal identity.

For an intentionally authorized LAN deployment, the operator may set `--host 0.0.0.0 --allowed-origin http://HOST:8780`. Do not expose the development service or ordinary gRPC to the public Internet. `--secure-cookie` is only for an actual HTTPS reverse-proxy setup; plain LAN HTTP must leave it off. No TLS/mTLS or OS service is automatically configured.

`--mode grpc --runtime-address HOST:PORT` sets the initial configured entry only. Persisted administrator edits are authoritative after restart. Real RPC uses the supplied SDK and has no mock fallback. CONTRACT_UNAVAILABLE was a retired pre-handoff limitation.

## Activation migration

Back up the entire private Web data directory before upgrading. Activation mappings
are added to its existing `web.sqlite`; users, grants, history and version files are
retained. The old active package is migrated only to existing Runtime IDs with an
explicit association. No association means no automatic publication: select the
intended target and activate a matching-project package explicitly. Rollback changes
only that target/project mapping, not other Runtime grants or pinned old views.

## Preview and daily workflow

`emo-web-panel preview --package FILE.emoweb --context CONTEXT.json --host 127.0.0.1 --port 0 --json --no-control` starts an isolated loopback temporary port, emits a JSON URL and short-lived viewer login, and cleans temporary state on exit. Treat stdout as credential material; do not log or publish it. The official launcher I/O is implemented; native Designer process start/close acceptance remains separate.

Log in → select an authorized Runtime → choose a workflow or an existing viewing run → view results/history. The stop target is selected separately from historical viewing. A history-only view is allowed without an active run. Upload and activate a package as admin; existing views keep their pinned version until they explicitly switch. Activation is an explicit SQLite mapping for the selected Runtime and its formal project; it never changes another target or the Runtime project. Existing package files and grants remain shared.

Stop the Web process before upgrading. Back up its private data directory (contains credential hashes, sessions, ACLs and audit), install the new wheel in the independent environment, then restart. Do not treat this backup as a project/production backup. Retain the prior wheel for software rollback; page-package rollback is a different operation and does not roll back running jobs or production configuration.
