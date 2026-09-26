"""Bounded, monotonic latest pointers independent of notification ordering."""
from collections import deque
import threading
import time

from emo_master.core.presentation.results import ClosedResult
from emo_master.apps.runtime.presentation.collector import unavailable


class ResultStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.open = {}
        self.history = deque()
        self.latest = {}
        self.high = {}
        self.closedHigh = {}
        self.cursor = 0
        self.events = deque(maxlen=32)

    def _notify(self, result=None):
        self.cursor += 1
        self.events.append((self.cursor, result))
        while sum(len(r.model_dump_json().encode()) for _, r in self.events if r is not None) > 8 * 1024 * 1024:
            self.events.popleft()

    def begin(self, item):
        with self.lock:
            identity = item["identity"]
            key = identity["resultKey"]
            if key in self.open or any(r.identity.resultKey == key for r in self.history):
                return
            self.open[key] = item
            address = (identity["jobId"], identity["resultScopeId"])
            if identity["resultOrdinal"] > self.high.get(address, 0):
                self.high[address] = identity["resultOrdinal"]
            self._notify()

    def close(self, key, sources, terminal):
        with self.lock:
            item = self.open.pop(key, None)
            if item is None:
                return False
            complete = all(s["state"] == "AVAILABLE" for s in sources)
            status = ("COMPLETE" if complete else "INCOMPLETE") if terminal == "COMPLETED" else terminal
            if terminal == "UNKNOWN":
                status = "INCOMPLETE"
            timing = None
            if "captureStartedNs" in item:
                timing = {"captureStartedNs": item["captureStartedNs"], "scopeEndedNs": item.get("scopeEndedNs", time.perf_counter_ns()),
                          "closedNs": time.perf_counter_ns()}
            result = ClosedResult.model_validate_json(__import__("json").dumps(dict(
                identity=item["identity"], expectedSourceIds=item["expected"], sources=sources,
                status=status, executionTerminal=terminal, timing=timing)))
            self.history.append(result)
            # 32 records and 8 MiB serialized metadata, included in Job reservation.
            while len(self.history) > 32 or sum(len(r.model_dump_json().encode()) for r in self.history) > 8 * 1024 * 1024:
                old = self.history.popleft()
                address = (old.identity.jobId, old.identity.resultScopeId)
                if self.latest.get(address) is old:
                    del self.latest[address]
            address = (result.identity.jobId, result.identity.resultScopeId)
            if result.identity.resultOrdinal > self.closedHigh.get(address, 0):
                self.closedHigh[address] = result.identity.resultOrdinal
                self.latest[address] = result
            self._notify(result)
            return True

    def fence(self, jobId, terminal):
        with self.lock:
            for key, item in list(self.open.items()):
                if item["identity"]["jobId"] == jobId:
                    code = "EXECUTION_CANCELLED" if terminal == "CANCELLED" else "IPC_ERROR"
                    self.close(key, [unavailable(s, code) for s in item["expected"]], terminal)

    def snapshot(self, jobId, cursor=0, incremental=False):
        with self.lock:
            reset = bool(cursor and (cursor > self.cursor or cursor < self.cursor - len(self.events)))
            results = [r for (job, _), r in self.latest.items() if job == jobId]
            if incremental and cursor and not reset:
                changes = [r for offset, r in self.events if offset > cursor and r is not None and r.identity.jobId == jobId]
                # Include authoritative latest even if a consumer saw a cursor
                # but lost its final result payload and no newer event follows.
                results = list({r.identity.resultKey: r for r in [*changes, *results]}.values())
            return {"cursor": self.cursor, "reset": reset,
                    "results": results,
                    "high": {scope: value for (job, scope), value in self.high.items() if job == jobId}}
