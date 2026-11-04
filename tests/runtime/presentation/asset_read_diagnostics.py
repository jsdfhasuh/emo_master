"""Failure-only snapshots for the two-consumer ReadAsset regression.

No RPC, file read, collection, retry, or wait is performed. These observations
are non-atomic and occur after the failed RPC, not at its unknown stall point.
"""
import json
import sys


PREFIX = "ASSET_READ_FAILURE "


def _locked(lock, read):
    if not lock.acquire(blocking=False):
        return {"unknown": "lock_unavailable"}
    try:
        return read()
    except Exception:
        return {"unknown": "unavailable_or_changed"}
    finally:
        lock.release()


def assetReadFailureDetails(server, clients, consumer, result):
    assets = server.presentation.assets
    details = {
        "diagnostics": "best-effort, non-atomic, after RPC failure; not stall attribution",
        "consumer": consumer,
        "result_key": result.identity.resultKey,
        "ordinal": result.identity.resultOrdinal,
        "server": {"active": dict(server.active), "peak": dict(server.peak),
                   "errors": [str(error)[:400] for error in list(server.errors)[-8:]]},
        # Do not call assets.stats(): it also expires leases and collects files.
        "assets": _locked(assets.lock, lambda: {
            "readers": dict(assets.readers), "items": len(assets.items),
            "retained": len(assets.retained), "leases": len(assets.leases)}),
        "consumers": [],
    }
    for client in clients:
        details["consumers"].append(_locked(client.lock, lambda: {
            "connection": client.connection, "generation": client.generation,
            "cursor": client.cursor, "high": dict(client.high),
            "stats": dict(client.stats), "scheduled": len(client._scheduled),
            "queued": len(client.pending.queue), "records": len(client.records)}))
    # Inspect only the loop and asset threads already owned by this server. Never
    # capture locals, image payloads, unrelated tests, or OS/native thread state.
    threads = [server.thread, *tuple(server.pools["asset"]._threads)]
    frames = sys._current_frames()
    details["threads"] = []
    for thread in threads:
        frame = frames.get(thread.ident)
        stack = []
        while frame is not None and len(stack) < 8:
            stack.append({"file": frame.f_code.co_filename.replace("\\", "/").rsplit("/", 1)[-1],
                          "line": frame.f_lineno, "function": frame.f_code.co_name})
            frame = frame.f_back
        details["threads"].append({"name": thread.name, "stack": list(reversed(stack))})
    return details


def emitAssetReadFailure(server, clients, consumer, result):
    try:
        print(PREFIX + json.dumps(assetReadFailureDetails(server, clients, consumer, result)))
    except Exception:
        pass  # Diagnostic failure must not replace the original RPC error.
