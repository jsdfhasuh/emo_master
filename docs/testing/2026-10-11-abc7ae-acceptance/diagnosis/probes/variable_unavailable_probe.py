"""One bounded isolated probe, not UI/worker/DB acceptance or historical reproduction.

No production constructor, processes, network, SQLite, or timers are started.
Real collector/store/_consume/_finish functions run over an in-memory fixture.
A fake variable reader injects KeyError; a spy asset store records retain only.
"""
from __future__ import annotations
import hashlib
import json
import platform
from pathlib import Path
import sys
import threading
import traceback
from types import SimpleNamespace

from pydantic import ValidationError
from emo_master.core.presentation.results import ClosedSource
from emo_master.apps.runtime.presentation.collector import ResultCollector, unavailable
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.apps.runtime.presentation.service import PresentationService

HERE = Path(__file__).resolve().parent
REPORT = {"kind": "isolated model and ownership simulation; not UI; not historical NG reproduction",
          "python": sys.version, "platform": platform.platform(),
          "source_commit": "abc7ae0541e997be0bbe3a4794ad604c239de79a",
          "cases": {}, "threads_before": [t.name for t in threading.enumerate()]}


def exception_record(error):
    return {"class": type(error).__name__, "message": str(error),
            "validation_errors": error.errors(include_url=False) if isinstance(error, ValidationError) else None,
            "traceback": traceback.format_exc()}


def identity(key):
    return {"runtimeInstanceId": "probe-runtime", "jobId": "probe-job", "resultScopeId": "root",
            "invocationId": "probe-invocation", "resultKey": key, "resultOrdinal": 1,
            "executionRevision": "a" * 64, "capturePlanRevision": "b" * 64, "mode": "runtime"}


def state(store):
    return {"open_keys": sorted(store.open), "history_count": len(store.history),
            "latest_keys": [r.identity.resultKey for r in store.latest.values()],
            "closed_high": [[list(k), v] for k, v in store.closedHigh.items()], "cursor": store.cursor,
            "events": [{"cursor": c, "has_result": r is not None} for c, r in store.events]}


class Credits:
    """Real bounded semaphore plus local audit; one credit initially available."""
    def __init__(self):
        self.sem = threading.BoundedSemaphore(1)
        self.available = 1
        self.acquired = 0
        self.released = 0

    def acquire(self, blocking=True):
        ok = self.sem.acquire(blocking)
        if ok:
            self.available -= 1
            self.acquired += 1
        return ok

    def release(self):
        self.sem.release()
        self.available += 1
        self.released += 1

    def snapshot(self):
        return {"available": self.available, "acquire_count": self.acquired, "release_count": self.released}


class MissingVariables:
    def get(self, variable_id):
        raise KeyError("injected missing variable: " + variable_id)


class AssetSpy:
    def __init__(self):
        self.calls = []

    def retain(self, keys):
        self.calls.append(sorted(keys))


# Case 1: exact emitted enum value cannot instantiate the current DTO.
bad = unavailable("variable", "VARIABLE_UNAVAILABLE")
case = {"input": bad}
try:
    case["unexpected_model"] = ClosedSource.model_validate(bad).model_dump()
except Exception as error:
    case["exception"] = exception_record(error)
REPORT["cases"]["closed_source_validation"] = case

# Case 2: direct real store close removes the open item before DTO validation.
store = ResultStore()
item = {"identity": identity("direct-store-key"), "expected": ["variable"]}
case = {"begin_return": store.begin(item), "before_close": state(store)}
try:
    case["close_return"] = store.close("direct-store-key", [bad], "COMPLETED")
except Exception as error:
    case["exception"] = exception_record(error)
case["after_close_exception"] = state(store)
REPORT["cases"]["real_store_close"] = case

# Case 3: real collector.end emits the invalid enum on an injected variable failure.
credits = Credits()
emitted = []
config = {"plan": json.dumps({"scopes": {"root": {"scopeWorkflowId": "main", "callPath": []}},
          "sources": {"variable": {"kind": "global_variable", "resultScopeId": "root", "variableId": "absent"}}}),
          "identity": {k: v for k, v in identity("unused").items()
                       if k not in {"resultKey", "resultOrdinal", "resultScopeId", "invocationId"}},
          "scopeIds": ["root"], "ordinals": [0], "credits": credits, "slots": []}
collector = ResultCollector(config, emitted.append)
collector.globalVariables = MissingVariables()
context = SimpleNamespace(callPath=[], workflowId="main", workflowRunId="probe-invocation")
collector.begin(context)
collector.end(context, "COMPLETED")
case = {"emitted": emitted, "collector_open": len(collector.open),
        "collector_pending_seals": len(collector.pendingSeals), "credits_after_capture": credits.snapshot()}
REPORT["cases"]["real_collector_injected_key_error"] = case

# No __init__: no Runtime owner, monitor thread, worker, or process is constructed.
service = object.__new__(PresentationService)
service.store = ResultStore()
service.pending = {}
service.jobs = {"probe-job": {"credits": credits}}
service.assets = AssetSpy()
service._consume("probe-job", emitted[0])
key = emitted[0]["item"]["identity"]["resultKey"]
case = {"before_seal": {"store": state(service.store), "pending_keys": sorted(service.pending), "credits": credits.snapshot()}}
try:
    service._consume("probe-job", emitted[1])
    case["unexpected_no_exception"] = True
except Exception as error:
    case["first_exception"] = exception_record(error)
case["after_first_exception"] = {"store": state(service.store), "pending_keys": sorted(service.pending),
                                  "has_seal": "seal" in service.pending[key], "credits": credits.snapshot(),
                                  "retain_calls": service.assets.calls[:]}
# One explicit next _finish call models a later monitor/finish boundary. This is
# not a rerun of the failed case, nor a retry-to-pass; the first failure stays recorded.
try:
    case["next_finish_return"] = service._finish(key)
except Exception as error:
    case["next_finish_exception"] = exception_record(error)
case["after_one_next_finish"] = {"store": state(service.store), "pending_keys": sorted(service.pending),
                                  "credits": credits.snapshot(), "retain_calls": service.assets.calls[:]}
REPORT["cases"]["isolated_real_service_finish"] = case

# Separate positive control: a valid UNAVAILABLE reason closes INCOMPLETE and releases.
control_credit = Credits()
control_credit.acquire(False)
control = object.__new__(PresentationService)
control.store = ResultStore()
control.pending = {}
control.jobs = {"probe-job": {"credits": control_credit}}
control.assets = AssetSpy()
control.store.begin({"identity": identity("control-key"), "expected": ["variable"]})
control.pending["control-key"] = {"job": "probe-job", "exports": {}, "seal": {"terminal": "COMPLETED",
    "sources": [unavailable("variable", "INVALID_VALUE")]}, "deadline": 0}
control._finish("control-key")
REPORT["cases"]["valid_reason_control"] = {"store": state(control.store), "credits": control_credit.snapshot(),
    "pending_keys": sorted(control.pending), "result": control.store.history[-1].model_dump()}
REPORT["threads_after"] = [t.name for t in threading.enumerate()]
REPORT["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
checks = {
    "invalid_reason_is_validation_error": REPORT["cases"]["closed_source_validation"].get("exception", {}).get("class") == "ValidationError",
    "invalid_store_close_popped_open": case["after_first_exception"]["store"]["open_keys"] == [],
    "invalid_store_close_published_no_result": case["after_first_exception"]["store"]["history_count"] == 0,
    "pending_remains_after_exception": case["after_first_exception"]["pending_keys"] == [key],
    "next_finish_drops_pending_without_credit_release": case["after_one_next_finish"]["pending_keys"] == [] and credits.released == 0,
    "control_closes_incomplete_and_releases_credit": control.store.history[-1].status == "INCOMPLETE" and control_credit.released == 1,
    "no_background_threads_started": REPORT["threads_before"] == REPORT["threads_after"],
}
REPORT["observation_checks"] = checks
with (HERE / "variable-unavailable-first-run.json").open("x", encoding="utf-8") as stream:
    json.dump(REPORT, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
print(json.dumps(REPORT, ensure_ascii=False, indent=2), flush=True)
raise SystemExit(0 if all(checks.values()) else 1)
