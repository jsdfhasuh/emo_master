"""Worker-side bounded capture, after output validation and before routing."""
import json
from uuid import uuid4

from emo_master.core.presentation.values import freezeValue


def unavailable(sourceId, code):
    return {"sourceId": sourceId, "state": "UNAVAILABLE", "reason": code, "reasonCode": code}


def projectField(value, path):
    if not path:
        return value
    key, *tail = path
    if key == "*" and isinstance(value, list):
        return [projectField(item, tail) for item in value]
    return projectField(value[key], tail)


class ResultCollector:
    def __init__(self, config, emit):
        self.config = config
        self.plan = json.loads(config["plan"])
        self.emit = emit
        self.open = {}
        self.ordinals = {}

    def begin(self, context):
        path = [{"nodeId": n, "relation": r} for n, r in context.callPath]
        for scopeId, scope in self.plan["scopes"].items():
            if scope["scopeWorkflowId"] != context.workflowId or scope["callPath"] != path:
                continue
            ordinal = self.ordinals.get(scopeId, 0) + 1
            self.ordinals[scopeId] = ordinal
            if not self.config["credits"].acquire(False):
                # Shared counter is bounded state; no unbounded rejected-event queue.
                with self.config["rejected"].get_lock():
                    self.config["rejected"].value += 1
                continue
            identity = dict(self.config["identity"], resultScopeId=scopeId,
                            invocationId=context.workflowRunId, resultKey=str(uuid4()), resultOrdinal=ordinal)
            sources = {key: source for key, source in self.plan["sources"].items()
                       if source["resultScopeId"] == scopeId}
            item = {"identity": identity, "expected": list(sources), "values": {}, "bytes": 0,
                    "sources": sources}
            self.open[(context.workflowRunId, scopeId)] = item
            self.emit({"eventType": "display.open", "item": {"identity": identity, "expected": list(sources)}})

    def output(self, context, outputs, workflow=False):
        for (runId, _scopeId), item in self.open.items():
            if runId != context.workflowRunId:
                continue
            for key, source in item["sources"].items():
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
                        item["values"][key] = self.image(key, value, item)
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
        return unavailable(key, "EXPORT_FAILED")

    def event(self, eventType, context):
        if eventType not in {"node.skipped", "node.failed"}:
            return
        for (runId, _scopeId), item in self.open.items():
            if runId == context.workflowRunId:
                for key, source in item["sources"].items():
                    if source["nodeId"] == context.callerNodeId:
                        item["values"][key] = unavailable(key, "BRANCH_SKIPPED" if eventType == "node.skipped" else "NODE_FAILED")

    def end(self, context, terminal):
        for address in list(self.open):
            if address[0] != context.workflowRunId:
                continue
            item = self.open.pop(address)
            code = {"COMPLETED": "SOURCE_MISSING", "FAILED": "NODE_FAILED", "CANCELLED": "EXECUTION_CANCELLED"}[terminal]
            values = [item["values"].get(key, unavailable(key, code)) for key in item["expected"]]
            self.emit({"eventType": "display.seal", "key": item["identity"]["resultKey"],
                       "sources": values, "terminal": terminal})
