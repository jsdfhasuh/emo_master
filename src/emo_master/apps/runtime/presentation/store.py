"""Bounded, monotonic latest pointers independent of notification ordering."""
from collections import deque
import threading
import time

from emo_master.core.presentation.results import ClosedResult
from emo_master.apps.runtime.presentation.collector import unavailable


class ResultStore:
    metadataLimit = 8 * 1024 * 1024
    latestLimit = 32  # Two owned Jobs, at most sixteen scopes each.

    def __init__(self):
        self.lock = threading.RLock()
        self.open = {}
        self.history = deque()
        self.latest = {}
        self.high = {}
        self.closedHigh = {}
        self.expired = {}
        self.expiryResets = {}
        self._sizes = {}
        self.cursor = 0
        self.events = deque(maxlen=32)

    def _notify(self, result=None):
        self.cursor += 1
        self.events.append((self.cursor, result))

    def metadataBytes(self):
        # Shared immutable results count once across latest/history/replay.
        with self.lock:
            retained = {result.identity.resultKey: result for result in self.history}
            retained.update({result.identity.resultKey: result for result in self.latest.values()})
            retained.update({result.identity.resultKey: result for _, result in self.events if result is not None})
            # Sizes are immutable and pruned with ownership. Repeated trimming
            # must not repeatedly serialize megabyte-scale scalar payloads.
            self._sizes = {key: self._sizes[key] if key in self._sizes
                           else len(result.model_dump_json().encode()) for key, result in retained.items()}
            return sum(self._sizes.values())

    def _expireOldestLatest(self):
        address = next(iter(self.latest))
        result = self.latest.pop(address)
        self.expired[address] = max(result.identity.resultOrdinal, self.expired.get(address, 0))
        # Explicit compatibility fence: pre-extension clients ignore expiry
        # metadata, but already understand reset_required and generation fences.
        self.expiryResets[address[0]] = self.cursor

    def _trim(self):
        while len(self.latest) > self.latestLimit:
            self._expireOldestLatest()
        # Preserve authoritative per-scope values ahead of optional replay.
        # No extra latest cache reservation is added to the original8MiB cap.
        while self.metadataBytes() > self.metadataLimit:
            if self.history:
                self.history.popleft()
            elif self.events:
                self.events.popleft()
            elif self.latest:
                self._expireOldestLatest()
            else:
                break

    def begin(self, item):
        with self.lock:
            identity = item["identity"]
            key = identity["resultKey"]
            address = (identity["jobId"], identity["resultScopeId"])
            if (key in self.open or identity["resultOrdinal"] <= self.closedHigh.get(address, 0)
                    or any(r.identity.resultKey == key for r in self.history)
                    or any(r.identity.resultKey == key for r in self.latest.values())
                    or any(r is not None and r.identity.resultKey == key for _, r in self.events)):
                return False
            self.open[key] = item
            if identity["resultOrdinal"] > self.high.get(address, 0):
                self.high[address] = identity["resultOrdinal"]
            self._notify()
            return True

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
            while len(self.history) > 32:
                self.history.popleft()
            address = (result.identity.jobId, result.identity.resultScopeId)
            if result.identity.resultOrdinal > self.closedHigh.get(address, 0):
                self.closedHigh[address] = result.identity.resultOrdinal
                self.latest.pop(address, None)
                self.latest[address] = result
                self.expired.pop(address, None)
            self._notify(result)
            self._trim()
            return True

    def fence(self, jobId, terminal):
        with self.lock:
            for key, item in list(self.open.items()):
                if item["identity"]["jobId"] == jobId:
                    code = "EXECUTION_CANCELLED" if terminal == "CANCELLED" else "IPC_ERROR"
                    self.close(key, [unavailable(s, code) for s in item["expected"]], terminal)

    def snapshot(self, jobId, cursor=0, incremental=False):
        with self.lock:
            reset = bool(cursor and (cursor > self.cursor or cursor < self.cursor - len(self.events)
                                     or cursor < self.expiryResets.get(jobId, 0)))
            results = [r for (job, _), r in self.latest.items() if job == jobId]
            if incremental and cursor and not reset:
                changes = [r for offset, r in self.events if offset > cursor and r is not None
                           and r.identity.jobId == jobId
                           and r.identity.resultOrdinal > self.expired.get((jobId, r.identity.resultScopeId), 0)]
                # Include authoritative latest even if a consumer saw a cursor
                # but lost its final result payload and no newer event follows.
                results = list({r.identity.resultKey: r for r in [*changes, *results]}.values())
            return {"cursor": self.cursor, "reset": reset,
                    "results": results,
                    "high": {scope: value for (job, scope), value in self.high.items() if job == jobId},
                    "expired": {scope: value for (job, scope), value in self.expired.items() if job == jobId}}
