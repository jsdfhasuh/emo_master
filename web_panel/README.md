# emo_master independent Web operator panel

**Development preview, not yet connected to a real Runtime.** This directory is an independent application in the emo_master repository. It does not import Qt, drivers, inference packages, or `emo_master` and does not modify their startup paths.

The supplied plan's official `emo_panel_protocol` SDK, schemas and companion Runtime plan have not been handed over. See [contract gaps](docs/CONTRACT-GAPS.md). The UI labels mock data. `--mode grpc` fails closed with 503; it never displays mock results as production results. Do not deploy this preview as a production control surface.

## Local development (independent environment)

Python 3.10+ and Node 20+ are build tools. End users do not need Node after frontend is built.

```sh
cd web_panel
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX: . .venv/bin/activate
python -m pip install '.[test]'
npm --prefix frontend ci
npm --prefix frontend run build
python scripts/package_frontend.py
emo-web-panel --data-dir .panel-data create-user admin
emo-web-panel --data-dir .panel-data serve --mode mock
```

Choose a unique password interactively (minimum 12 characters); no default admin account/password is installed. Open http://127.0.0.1:8780. `admin` explicitly includes view/control/admin permissions; `operator` has view/control and `viewer` read-only. New accounts can be created with `create-user NAME --role viewer`. Network binding and origins must be configured explicitly. No firewall, TLS, OS startup, Runtime configuration or devices are modified.

## Preview launch

```sh
emo-web-panel preview --package dev/fixtures/demo.emoweb
```

This uses an isolated temporary state directory and read-only viewer account; stdout returns a local URL and short-lived login credentials for the launching Designer. Never collect this stdout in ordinary logs. This draft invocation must be aligned with the official launcher contract. Package imports cannot grant access to Runtime paths. Preview is mock-only and cannot execute production controls.

## Tests and build

```sh
PYTHONPATH=backend python -m unittest discover -s tests -v
npm --prefix frontend run build
```

Frontend builds to static assets served by FastAPI on the same origin. A separate Vite process is development-only. A single ASGI worker shares upstream subscriptions. Browser tests use the explicit demo, not a real Runtime. Production image/lease/cursor/counter/Dispatch functionality must wait for the official SDK.

## Implemented development slice

- Password hashing, HttpOnly sessions, viewer/operator/admin permissions, CSRF/Origin checks, expiry/logout, local audit.
- Immutable bounded `.emoweb` ZIP import, draft schema validation, version activation/rollback and static images by ID.
- React renderer for 13 component types, mobile layout, per-browser views, draft staged results, freeze and history demo.
- Stable-id demo start/stop operations with persisted outcomes; no automatic replay of uncertain requests.
- Bounded shared mock subscriptions and per-view queues; closing browser cancels Web subscription, never calls ReleaseJob.

Package switching changes Web state only. It does not roll back a production project. Removing/closing this app leaves Runtime and Qt unchanged. No branch merge, formal publication or deployment is part of this delivery.
