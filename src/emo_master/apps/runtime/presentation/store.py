"""Bounded, monotonic latest pointers independent of notification ordering."""
from collections import deque
import threading

from emo_master.core.presentation.results import ClosedResult
from emo_master.apps.runtime.presentation.collector import unavailable


class ResultStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.open = {}
        self.history = deque()
        self.latest = {}
        self.high = {}
        self.cursor = 0
        self.events = deque(maxlen=32)

    def _notify(self):
        self.cursor += 1
        self.events.append(self.cursor)

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
                self.latest.pop(address, None)
            self._notify()

    def close(self, key, sources, terminal):
        with self.lock:
            item = self.open.pop(key, None)
            if item is None:
                return False
            complete = all(s["state"] == "AVAILABLE" for s in sources)
            status = ("COMPLETE" if complete else "INCOMPLETE") if terminal == "COMPLETED" else terminal
            result = ClosedResult.model_validate_json(__import__("json").dumps(dict(
                identity=item["identity"], expectedSourceIds=item["expected"], sources=sources,
                status=status, executionTerminal=terminal)))
            self.history.append(result)
            # 32 records and 8 MiB serialized metadata, included in Job reservation.
            while len(self.history) > 32 or sum(len(r.model_dump_json().encode()) for r in self.history) > 8 * 1024 * 1024:
                old = self.history.popleft()
                self.latest.pop((old.identity.jobId, old.identity.resultScopeId), None)
            address = (result.identity.jobId, result.identity.resultScopeId)
            if result.identity.resultOrdinal == self.high[address]:
                self.latest[address] = result
            self._notify()
            return True

    def fence(self, jobId, terminal):
        with self.lock:
            for key, item in list(self.open.items()):
                if item["identity"]["jobId"] == jobId:
                    code = "EXECUTION_CANCELLED" if terminal == "CANCELLED" else "IPC_ERROR"
                    self.close(key, [unavailable(s, code) for s in item["expected"]], terminal)

    def snapshot(self, jobId, cursor=0):
        with self.lock:
            reset = bool(cursor and (cursor > self.cursor or cursor < self.cursor - len(self.events)))
            return {"cursor": self.cursor, "reset": reset,
                    "results": [r for (job, _), r in self.latest.items() if job == jobId],
                    "high": {scope: value for (job, scope), value in self.high.items() if job == jobId}}
