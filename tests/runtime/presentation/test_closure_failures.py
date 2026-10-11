"""Fault injection for closed-result validation and exactly-once scope ownership."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from emo_master.apps.runtime.presentation.collector import ResultCollector, unavailable
from emo_master.apps.runtime.presentation.rpc import wireResult
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.clients.runtime.display_session import decodeResult
from emo_master.core.presentation.results import ClosedSource
from tests.runtime.presentation.test_images import item


class Credits:
    def __init__(self):
        self.semaphore = threading.BoundedSemaphore(1)
        self.released = 0

    def acquire(self, blocking=False):
        return self.semaphore.acquire(blocking)

    def release(self):
        self.semaphore.release()
        self.released += 1


def assembler():
    service = object.__new__(PresentationService)
    service.store = ResultStore()
    service.pending = {}
    credit = Credits()
    service.jobs = {"job": {"credits": credit}}
    service.diagnostics = deque(maxlen=64)
    service.assets = SimpleNamespace(retain=lambda _: None)
    return service, credit


def opened(service, credit, ordinal=1):
    assert credit.acquire()
    event = {"eventType": "display.open", "item": item(ordinal)}
    service._consume("job", event)
    return event["item"]["identity"]["resultKey"]


def seal(key, source):
    return {"eventType": "display.seal", "key": key, "sources": [source],
            "terminal": "COMPLETED", "sealedAt": 0}


@pytest.mark.parametrize("kind", ["global_variable", "global_counter"])
@pytest.mark.parametrize("error", [KeyError("missing"), ValueError("invalid"), TypeError("wrong type"),
    sqlite3.OperationalError("injected read busy"), OSError("injected read I/O")])
def testVariableCaptureFailureUsesExistingWireReasonAndReleasesOnce(kind, error):
    service, credits = assembler()
    config = {"plan": json.dumps({"scopes": {"scope": {"scopeWorkflowId": "main", "callPath": []}},
        "sources": {"n": {"kind": kind, "resultScopeId": "scope", "variableId": "v", "name": "parts"}}}),
        "identity": {k: v for k, v in item(1)["identity"].items()
                     if k not in {"resultScopeId", "resultKey", "resultOrdinal", "invocationId"}},
        "scopeIds": ["scope"], "ordinals": [0], "credits": credits, "slots": []}

    def read(_):
        raise error

    collector = ResultCollector(config, lambda event: service._consume("job", event))
    collector.globalVariables = SimpleNamespace(get=read)
    collector.globalCounters = SimpleNamespace(apply=read)
    context = SimpleNamespace(callPath=[], workflowId="main", workflowRunId="invocation")
    collector.begin(context)
    collector.end(context, "COMPLETED")
    result = service.store.history[-1]
    assert result.status == "INCOMPLETE" and result.executionTerminal == "COMPLETED"
    source = result.sources[0]
    # Existing strict clients accept INVALID_VALUE; no new wire enum is required.
    assert source.reasonCode == "INVALID_VALUE" and "VARIABLE_UNAVAILABLE" in source.reason
    assert type(error).__name__ in source.reason
    assert ClosedSource.model_validate_json(source.model_dump_json()) == source
    assert decodeResult(wireResult(result)) == result
    service._finish(result.identity.resultKey)
    assert credits.released == 1 and not service.pending and not service.store.open


@pytest.mark.parametrize("source", [unavailable("n", "UNKNOWN_REASON"),
    {"sourceId": "n", "state": "AVAILABLE", "valueJson": "NaN"},
    unavailable("unexpected", "SOURCE_MISSING")])
def testStoreValidationFailureIsAtomicAndCanBeClosedExplicitly(source):
    store = ResultStore()
    original = item(1)
    assert store.begin(original)
    cursor = store.cursor
    with pytest.raises((ValidationError, ValueError)):
        store.close("job-scope-1", [source], "COMPLETED")
    assert store.open["job-scope-1"] is original
    assert store.cursor == cursor and not store.history and not store.latest and not store.closedHigh
    assert store.close("job-scope-1", [unavailable("n", "INVALID_VALUE", "explicit failure")], "COMPLETED")
    assert store.history[-1].status == "INCOMPLETE"


@pytest.mark.parametrize("source", [unavailable("n", "UNKNOWN_REASON"),
    {"sourceId": "n", "state": "AVAILABLE", "valueJson": "NaN"},
    unavailable("unexpected", "SOURCE_MISSING")])
def testInvalidClosureProducesObservableFailureWithoutStoppingLaterResults(source):
    service, credit = assembler()
    key = opened(service, credit)
    service._consume("job", seal(key, source))
    result = service.store.history[-1]
    assert result.identity.resultKey == key and result.status == "INCOMPLETE"
    assert result.sources[0].reasonCode == "INVALID_VALUE"
    assert "closure" in result.sources[0].reason.lower()
    assert service.diagnostics and service.diagnostics[-1]["stage"] == "result_validation"
    assert credit.released == 1 and not service.store.open and not service.pending
    service._consume("job", seal(key, source))
    service._finish(key)
    service._exported({"key": key}, None, "EXPORT_FAILED")
    assert credit.released == 1 and len(service.store.history) == 1
    nextKey = opened(service, credit, 2)
    service._consume("job", seal(nextKey, {"sourceId": "n", "state": "AVAILABLE", "valueJson": "false"}))
    assert service.store.history[-1].status == "COMPLETE"
    assert service.store.history[-1].sources[0].valueJson == "false" and credit.released == 2


def testPendingOwnsOneCreditEvenIfTheStoreWasAlreadyClosed():
    service, credit = assembler()
    key = opened(service, credit)
    source = {"sourceId": "n", "state": "AVAILABLE", "valueJson": "1"}
    assert service.store.close(key, [source], "COMPLETED")
    service._consume("job", seal(key, source))
    service._finish(key)
    assert credit.released == 1 and not service.pending and len(service.store.history) == 1


def testConcurrentFinishAndLateCallbacksCannotReleaseScopeTwice():
    service, credit = assembler()
    key = opened(service, credit)
    service.pending[key].update(seal=seal(key, unavailable("n", "UNKNOWN_REASON")), deadline=0)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(service._finish, [key] * 32))
        list(pool.map(lambda _: service._exported(dict(key=key), None, "EXPORT_FAILED"), range(16)))
    assert credit.released == 1 and not service.pending and not service.store.open
    assert len(service.store.history) == 1 and service.store.history[-1].status == "INCOMPLETE"
