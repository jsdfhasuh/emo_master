# emo_master independent Web operator panel

Independent Python 3.10+/FastAPI + React application under `web_panel/`. The gateway consumes **Panel Contract 1.0 / SDK 1.1.0** as an ordinary gRPC client; it does not import Qt, device drivers, inference packages, or `emo_master`.

Authoritative handoff: repository commit `b9f448c18ed297652cddc3e040ca1db847b0fbfa`, implementation `d4ab96409e43d7ab6ae566820da357508d01658c`. The unchanged handoff files in `contracts/panel-contract-v1/` include their integrity manifest. Production execution and formal configuration remain owned by Runtime. Nothing here deploys software or changes networking.

## Build and run

```sh
cd web_panel
python -m venv .venv
# Windows: .venv\Scripts\activate; POSIX: . .venv/bin/activate
python -m pip install contracts/panel-contract-v1/python/emo_panel_protocol-1.1.0-py3-none-any.whl
python -m pip install -e '.[test]'
npm --prefix frontend ci
npm --prefix frontend run build
python scripts/package_frontend.py
emo-web-panel --data-dir .panel-data create-user admin
emo-web-panel --data-dir .panel-data serve --mode grpc --runtime-address 127.0.0.1:50051
```

Use the actual configured Runtime endpoint, not the example above. Cleartext gRPC is intentional for the controlled LAN; no TLS/certificate administration is added. The Web server defaults to loopback; non-loopback binding requires explicit `--allowed-origin`. No default password is installed. `admin` explicitly has view/control/admin rights; operator/viewer access also requires a target grant. The browser cannot submit arbitrary Runtime addresses, RPC methods, file paths, or SQL.

The separate `--mode mock` is explicitly synthetic and never a fallback for gRPC. Import and activate a Designer `.emoweb` package in the admin UI. Package updates and rollback change Web presentation only, never Runtime configuration or running Jobs.

## Designer preview (same built renderer)

```sh
emo-web-panel preview --package contracts/panel-contract-v1/sample-panels/demo.emoweb --context contracts/panel-contract-v1/fixtures/preview-fixtures.json --host 127.0.0.1 --port 0 --json --no-control
```

The first stdout line after successful startup is `{"event":"ready","protocolVersion":"1","url":"http://127.0.0.1:PORT/?token=..."}`. The short-lived token is exchanged once for a viewer session; do not log it. Diagnostics use stderr. Temporary users/state are isolated and cleaned up on exit.

`--context` is mandatory. `fixtures` loads only shipped synthetic examples; `runtime` pins the Designer-supplied endpoint, instance, project and history revision and lets the user select an existing Job. Both modes deny Start, Stop, configuration writes, package imports/activation and rollback. Runtime mode never silently substitutes fixtures. Closing preview releases only its own connections/subscriptions, not production Jobs.

## Verification and distribution

```sh
python -m unittest discover -s tests -v
npm --prefix frontend test
npm --prefix frontend run build
python scripts/package_frontend.py
python -m build --wheel
```

Distribute the Web wheel **and supplied SDK wheel** together; install both with `python -m pip install PATH_TO_SDK.whl PATH_TO_WEB.whl`. End users do not need Node. Use one ASGI worker for shared subscriptions and bounded byte caches. See [deployment](docs/DEPLOYMENT.md), [acceptance](docs/ACCEPTANCE-MATRIX.md), [verification](docs/VERIFICATION.md), and [remaining integration boundaries](docs/CONTRACT-GAPS.md).

Synthetic gRPC, actual software Runtime, browser automation, Windows CI, Designer UI, and physical hardware are separate evidence levels. A passing fixture is never a claim that the user's Windows Runtime or devices were accessed.
