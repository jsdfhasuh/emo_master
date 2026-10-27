"""Bounded failure metadata; never retain image bytes or captured scalar values."""
import time

from pydantic import ValidationError


def errorDetail(error):
    if isinstance(error, ValidationError):
        # str(ValidationError) includes input_value; do not log those payloads.
        return {"type": type(error).__name__, "issues": [
            {"location": str(row["loc"])[:160], "type": row["type"], "message": row["msg"][:240]}
            for row in error.errors(include_input=False, include_url=False)[:8]]}
    return {"type": type(error).__name__, "message": str(error)[:240]}


def closureDiagnostic(key, item, sources, terminal, stage, error=None):
    expected = item.get("expected", [])[:16]
    rows = [source for source in sources if isinstance(source, dict)][:16]
    actual = [source.get("sourceId") for source in rows]
    result = {"stage": stage, "clock": "runtime_monotonic", "observedNs": time.monotonic_ns(),
        "path": "local-presentation-store", "workflowId": item.get("workflowId"),
        "iterationPath": item.get("iterationPath", [])[:16], "callPath": item.get("callPath", [])[:16],
        "resultKey": key, "identity": dict(item.get("identity", {})), "executionTerminal": terminal,
        "dispatch": {name: str(item.get("dispatch", {}).get(name, ""))[:128] for name in
                     ("runGroupId", "targetSlotId", "invocationId", "deliveryId")},
        "expectedSourceIds": expected, "missingSourceIds": [name for name in expected if name not in actual],
        "unavailableSourceIds": [source.get("sourceId") for source in rows if source.get("state") != "AVAILABLE"],
        "sourceOrigins": {name: item.get("sourceOrigins", {}).get(name, {}) for name in expected},
        "sourceObservations": {name: item.get("sourceObservations", {}).get(name, {}) for name in expected},
        "sources": [{name: str(source[name])[:256] for name in
            ("sourceId", "state", "reasonCode", "reason", "pendingImage", "imageSourceId") if name in source}
            for source in rows]}
    if error is not None:
        result["error"] = errorDetail(error)
    return result
