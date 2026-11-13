"""Run the independent authoring service with an explicit workspace credential."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--target", action="append", default=[], metavar="ID=HOST:PORT")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-mcp", action="store_true")
    parser.add_argument("--allowed-origin", action="append", default=[])
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-transfer-bytes", type=int, default=256 * 1024 * 1024)
    args = parser.parse_args()
    token = os.environ.get("EMO_WORKSPACE_TOKEN")
    if not token:
        parser.error("Set EMO_WORKSPACE_TOKEN in the process environment; do not put credentials in URLs or project files")
    targets = {}
    for value in args.target:
        key, separator, address = value.partition("=")
        if not separator or not key or key in targets:
            parser.error("--target requires a unique ID=HOST:PORT")
        targets[key] = address
    from .http import create_app
    import uvicorn
    app = create_app(args.data_dir, token, targets, enable_mcp=not args.no_mcp,
                     allowed_origins=args.allowed_origin, allowed_hosts=args.allowed_host,
                     timeout=args.timeout, max_transfer_bytes=args.max_transfer_bytes)
    # Access logging is unnecessary for this local service and must not echo query inputs.
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
