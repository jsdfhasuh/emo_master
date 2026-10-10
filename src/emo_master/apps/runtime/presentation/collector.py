"""Worker-side bounded capture, after output validation and before routing."""
import json
from multiprocessing.shared_memory import SharedMemory
import queue
import time
from uuid import uuid4

from emo_master.core.presentation.values import freezeValue
from emo_master.apps.runtime.presentation.provenance import FrameTracker


def unavailable(sourceId, code, detail=""):
    return {"sourceId": sourceId, "state": "UNAVAILABLE", "reason": detail or code, "reasonCode": code}


def projectField(value, path):
    if not path:
        return value
    key, *tail = path
    if key == "*" and isinstance(value, list):
        if len(value) > 4096:
            raise ValueError("projection collection budget exceeded")
        return [projectField(item, tail) for item in value]
    return projectField(value[key], tail)


class ResultCollector:
    def __init__(self, config, emit):
        self.config = config
        self.plan = json.loads(config["plan"])
        self.emit = emit
        self.open = {}
        # These retain their original scope credits until the parent closes them.
        # Admission also bounds local ownership if an IPC-error fence returns
        # parent credits before this producer has retired its local items.
        self.pendingSeals = {}
        self.ordinals = {}
        self.frames = FrameTracker(config.get("versions", {}))
        self.telemetry = {}

    def observe(self, node, inputs, outputs):
        if self.config.get("capture", True):
            self.frames.observe(node, inputs, outputs)

    def begin(self, context):
        self._flushSeals()
        path = [{"nodeId": n, "relation": r} for n, r in context.callPath]
        for scopeId, scope in self.plan["scopes"].items():
            if scope["scopeWorkflowId"] != context.workflowId or scope["callPath"] != path:
                continue
            if self.config.get("measure"):
                self.telemetry[(context.workflowRunId, scopeId)] = time.perf_counter_ns()
            if not self.config.get("capture", True):
                continue
            ordinal = self.ordinals.get(scopeId, 0) + 1
            self.ordinals[scopeId] = ordinal
            self.config["ordinals"][self.config["scopeIds"].index(scopeId)] = ordinal
            if (len(self.open) + len(self.pendingSeals) >= 8
                    or not self.config["credits"].acquire(False)):
                # Shared counter is bounded state; no unbounded rejected-event queue.
                with self.config["rejected"].get_lock():
                    self.config["rejected"].value += 1
                continue
            identity = dict(self.config["identity"], resultScopeId=scopeId,
                            invocationId=context.workflowRunId, resultKey=str(uuid4()), resultOrdinal=ordinal)
            sources = {key: source for key, source in self.plan["sources"].items()
                       if source["resultScopeId"] == scopeId}
            item = {"identity": identity, "expected": list(sources), "values": {}, "bytes": 0,
                    "sources": sources, "rawBytes": 0}
            self.open[(context.workflowRunId, scopeId)] = item
            try:
                self.emit({"eventType": "display.open", "item": {"identity": identity, "expected": list(sources),
                                                                  "captureStartedNs": time.perf_counter_ns()}})
            except Exception:
                self.open.pop((context.workflowRunId, scopeId))
                self.config["credits"].release()
                raise

    def output(self, context, outputs, workflow=False):
        for (runId, _scopeId), item in self.open.items():
            if runId != context.workflowRunId:
                continue
            frozenSources = {}
            for key, source in item["sources"].items():
                if source["kind"] not in {"node_output", "workflow_output"}:
                    continue
                if (source["kind"] == "workflow_output") != workflow:
                    continue
                if not workflow and source["nodeId"] != context.callerNodeId:
                    continue
                if source["port"] not in outputs:
                    item["values"][key] = unavailable(key, "OPTIONAL_ABSENT")
                    continue
                try:
                    value = projectField(outputs[source["port"]], source["fieldPath"])
                    if source["expectedType"] == "image":
                        signature = json.dumps(source, sort_keys=True)
                        if signature in frozenSources:
                            frozen = frozenSources[signature]
                            item["values"][key] = dict(frozen, sourceId=key)
                            if frozen.get("pendingImage"):
                                item["values"][key]["imageSourceId"] = frozen["sourceId"]
                        else:
                            item["values"][key] = frozenSources[signature] = self.image(key, value, item)
                        continue
                    frozen = freezeValue(value)
                    if item["bytes"] + len(frozen.encode()) > 1024 * 1024:
                        item["values"][key] = unavailable(key, "BUDGET_EXCEEDED")
                    else:
                        item["bytes"] += len(frozen.encode())
                        item["values"][key] = {"sourceId": key, "state": "AVAILABLE", "valueJson": frozen}
                except (ValueError, TypeError, KeyError, IndexError):
                    item["values"][key] = unavailable(key, "INVALID_VALUE")

    def image(self, key, value, item):
        import numpy as np
        if (not isinstance(value, np.ndarray) or value.dtype != np.uint8 or value.ndim not in (2, 3)
                or (value.ndim == 3 and value.shape[2] not in (1, 3, 4)) or not value.size):
            return unavailable(key, "INVALID_VALUE")
        if value.nbytes > 8 * 1024 * 1024 or item["rawBytes"] + value.nbytes > 16 * 1024 * 1024:
            return unavailable(key, "BUDGET_EXCEEDED")
        for slot in self.config.get("slots", []):
            lane = self.config.get("imageLaneBySource", {}).get(key, 0)
            if slot.get("lane", 0) != lane:
                continue
            capacity = slot.get("capacity", 8 * 1024 * 1024)
            if value.nbytes > capacity:
                return unavailable(key, "BUDGET_EXCEEDED",
                    f"raw image {value.nbytes} bytes exceeds source lane limit {capacity}; no implicit resize")
            quarantined = slot.get("quarantined")
            if quarantined is not None and quarantined.value:
                return unavailable(key, "BUDGET_EXCEEDED", "image slab quarantined until actual owner retirement")
            if not slot["free"].acquire(False):
                continue
            transferred = False
            try:
                if quarantined is not None and quarantined.value:
                    return unavailable(key, "BUDGET_EXCEEDED", "image slab quarantined until actual owner retirement")
                memory = SharedMemory(name=slot["name"])
                try:
                    target = np.ndarray(value.shape, value.dtype, buffer=memory.buf, offset=slot.get("offset", 0))
                    np.copyto(target, value)
                    del target
                finally:
                    memory.close()
                descriptor = {"slot": slot["index"], "lane": slot.get("lane", 0),
                              "offset": slot.get("offset", 0), "shape": list(value.shape), "sourceId": key,
                              "key": item["identity"]["resultKey"], "jobId": item["identity"]["jobId"],
                              "frozenAt": time.monotonic(), "rawBytes": value.nbytes,
                              "provenance": self.frames.lookup(value) or {"frameIdentity": str(uuid4()),
                                  "coordinateSpaceId": str(uuid4()), "trust": "unknown"}}
                self.emit({"eventType": "display.image", "descriptor": descriptor})
                transferred = True
                item["rawBytes"] += value.nbytes
                return {"sourceId": key, "pendingImage": True}
            finally:
                if not transferred and not (quarantined is not None and quarantined.value):
                    slot["free"].release()
        return unavailable(key, "BUDGET_EXCEEDED", "source image lane is busy; capture never waits")

    def event(self, eventType, context):
        if eventType not in {"node.skipped", "node.failed"}:
            return
        for (runId, _scopeId), item in self.open.items():
            if runId == context.workflowRunId:
                for key, source in item["sources"].items():
                    if source["nodeId"] == context.callerNodeId:
                        item["values"][key] = unavailable(key, "BRANCH_SKIPPED" if eventType == "node.skipped" else "NODE_FAILED")

    def _flushSeals(self):
        for key in list(self.pendingSeals):
            try:
                self.emit(self.pendingSeals[key])
            except queue.Full:
                # Full occurs before mailbox publication. Retry at a later
                # capture boundary without waiting or counting a new rejection.
                return False
            except Exception:
                # Other IPC failures may have partially published the packet;
                # never retry ambiguous delivery. The original parent fence owns it.
                del self.pendingSeals[key]
                raise
            del self.pendingSeals[key]
        return True

    def end(self, context, terminal):
        sealedAt, scopeEndedNs = time.monotonic(), time.perf_counter_ns()
        # Remove every completed-invocation timing before an optional send can
        # fail. Timing is created even for rejected captures and cannot backlog.
        timings = [{"eventType": "display.timing", "startNs": self.telemetry.pop(address),
                    "endNs": scopeEndedNs, "scope": address[1], "invocation": address[0]}
                   for address in list(self.telemetry) if address[0] == context.workflowRunId]
        for address in list(self.open):
            if address[0] != context.workflowRunId:
                continue
            item = self.open.pop(address)
            for sourceId, source in item["sources"].items():
                if source["kind"] not in {"global_variable", "global_counter"}:
                    continue
                try:
                    if source["kind"] == "global_variable":
                        value = self.globalVariables.get(source["variableId"])
                    else:
                        value = self.globalCounters.apply(source["name"])
                        value = getattr(value, "value", value)
                    frozen = freezeValue(value)
                    if item["bytes"] + len(frozen.encode()) > 1024 * 1024:
                        raise ValueError("variable capture budget exceeded")
                    item["bytes"] += len(frozen.encode())
                    item["values"][sourceId] = {"sourceId": sourceId, "state": "AVAILABLE", "valueJson": frozen}
                except (ValueError, AttributeError, KeyError):
                    item["values"][sourceId] = unavailable(sourceId, "VARIABLE_UNAVAILABLE")
            code = {"COMPLETED": "SOURCE_MISSING", "FAILED": "NODE_FAILED", "CANCELLED": "EXECUTION_CANCELLED"}[terminal]
            values = [dict(item["values"].get(key, unavailable(key, code))) for key in item["expected"]]
            key = item["identity"]["resultKey"]
            self.pendingSeals[key] = {"eventType": "display.seal", "key": key,
                "sources": values, "terminal": terminal, "sealedAt": sealedAt, "scopeEndedNs": scopeEndedNs}
        if not self._flushSeals():
            if timings:
                # Seals retain ownership; optional timings are actually lost.
                # Preserve the existing bounded capture-error diagnostic path.
                raise queue.Full
            return
        for timing in timings:
            self.emit(timing)
