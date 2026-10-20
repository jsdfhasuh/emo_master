"""Opt-in first-result evidence, independent of event and display backpressure.

No payloads, file IO, RPC, waiting locks or changes to execution ownership.
Shared slots are append-only: a killed producer cannot expose a partial record as
a valid observation. Missing/omitted observations never prove a stage absent.
"""
import json
import os
import threading
import time
from itertools import islice


IDENTITY_FIELDS = ("runtimeInstanceId", "jobId", "resultScopeId", "invocationId",
                   "resultKey", "resultOrdinal", "workflowId", "workflowRunId")
DETAIL_FIELDS = ("sourceId", "nodeRunId", "nodeId", "errorType", "terminal", "waitNs", "reason", "waitingSources")


def _scalar(value):
    if type(value) is str:
        return value[:96]
    if type(value) in (int, bool) and abs(value) < 2**64:
        return value
    return None


def _encode(sequence, phase, identity, details, capacity):
    row = {"sequence": sequence, "phase": phase[:64], "pid": os.getpid(),
           "threadId": threading.get_ident(), "observedNs": time.monotonic_ns()}
    row.update({key: _scalar(identity[key]) for key in IDENTITY_FIELDS if key in identity})
    if "invocationId" in row:
        row.setdefault("workflowRunId", row["invocationId"])
    for key in DETAIL_FIELDS:
        if key in details:
            value = details[key]
            row[key] = ([_scalar(item) for item in value[:8]]
                        if key == "waitingSources" and type(value) is list else _scalar(value))
    data = json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(data) > capacity:
        # Preserve the envelope/identity if optional source lists exceed budget.
        for key in DETAIL_FIELDS:
            row.pop(key, None)
        row["unknown"] = "detail_budget_exceeded"
        data = json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(data) > capacity:
        raise ValueError("identity budget exceeded")
    return data


class SharedPhaseTrace:
    limit = 64
    capacity = 2048

    def __init__(self, context):
        self.data = context.Array("B", self.limit * self.capacity, lock=False)
        self.lengths = context.Array("I", self.limit, lock=False)
        self.omitted = context.Value("b", False, lock=False)
        self.index = 0  # private to the single producer, never a parent cursor

    def record(self, phase, identity=None, **details):
        try:
            identity = identity or {}
            if identity.get("resultOrdinal", 1) != 1:
                return
            if self.index >= self.limit:
                self.omitted.value = True
                return
            data = _encode(self.index + 1, phase, identity, details, self.capacity)
            offset = self.index * self.capacity
            memoryview(self.data).cast("B")[offset:offset + len(data)] = data
            self.lengths[self.index] = len(data)  # publish only the complete record
            self.index += 1
        except Exception:
            self.omitted.value = True  # diagnostic failure cannot fail the producer

    def snapshot(self):
        rows, unknown = [], []
        for index in range(self.limit):
            size = self.lengths[index]
            if not size:
                continue
            try:
                if size > self.capacity:
                    raise ValueError("record size")
                offset = index * self.capacity
                data = bytes(memoryview(self.data).cast("B")[offset:offset + size])
                row = json.loads(data)
                if row["sequence"] != index + 1:
                    raise ValueError("record sequence")
                rows.append(row)
            except Exception:
                unknown.append(index)
        return {"records": rows, "truncated": bool(self.omitted.value), "unknownSlots": unknown}


class LocalPhaseTrace:
    limit = SharedPhaseTrace.limit
    capacity = SharedPhaseTrace.capacity

    def __init__(self):
        self.lock = threading.Lock()
        self.records: list[bytes] = []
        self.omitted = False

    def record(self, phase, identity=None, **details):
        if not self.lock.acquire(False):
            self.omitted = True
            return
        try:
            identity = identity or {}
            if identity.get("resultOrdinal", 1) != 1:
                return
            if len(self.records) >= self.limit:
                self.omitted = True
                return
            self.records.append(_encode(len(self.records) + 1, phase, identity, details, self.capacity))
        except Exception:
            self.omitted = True
        finally:
            self.lock.release()

    def snapshot(self):
        if not self.lock.acquire(False):
            return {"unknown": "trace_lock_unavailable"}
        try:
            return {"records": [json.loads(row) for row in self.records], "truncated": self.omitted}
        finally:
            self.lock.release()


def captureFailureSnapshot(owner, jobId):
    """Sections have separate observation times; this is NOT an atomic view."""
    snapshot = {"jobId": jobId, "observedNs": time.monotonic_ns(),
                "clock": "monotonic_ns with PID/TID; compare phases within their process",
                "semantics": "best-effort, non-atomic; missing/omitted is unknown, not absence"}
    try:
        config = owner.jobs.get(jobId, {})
        producer = config.get("phaseTrace")
        parent = getattr(owner, "phaseTraces", {}).get(jobId)
        snapshot["producer"] = (producer.snapshot() if producer is not None
                                else {"unknown": config.get("phaseTraceUnavailable", "trace_disabled")})
        snapshot["parent"] = parent.snapshot() if parent is not None else {"unknown": "trace_disabled"}
        if not owner.store.lock.acquire(False):
            snapshot["state"] = {"unknown": "store_lock_unavailable"}
            return snapshot
        try:
            rows = []
            for key, item in islice(owner.store.open.items(), 16):
                identity = item["identity"]
                if identity.get("jobId") != jobId:
                    continue
                pending = owner.pending.get(key, {})
                exports = pending.get("exports", {})
                waiting = [source["sourceId"] for source in pending.get("seal", {}).get("sources", [])
                           if source.get("pendingImage") and source.get("imageSourceId", source["sourceId"]) not in exports]
                rows.append({"identity": {name: _scalar(identity[name]) for name in IDENTITY_FIELDS if name in identity},
                             "hasSeal": "seal" in pending, "exported": list(exports)[:16], "waitingSources": waiting[:16]})
            snapshot["state"] = {"observedNs": time.monotonic_ns(), "open": rows,
                "closed": [{"key": r.identity.resultKey, "ordinal": r.identity.resultOrdinal, "status": r.status}
                           for r in islice(owner.store.latest.values(), 32) if r.identity.jobId == jobId]}
        finally:
            owner.store.lock.release()
    except Exception as error:
        snapshot["unknown"] = type(error).__name__
    return snapshot
