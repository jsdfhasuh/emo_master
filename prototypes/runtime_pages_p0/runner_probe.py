"""Actual WorkflowRunner with its existing pre-route preview capture seam.

This adapter replaces the preview writer ONLY inside the experiment. Production
worker_main remains unchanged. Stable roles come from the compiled call graph.
"""
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from .contracts import BUDGET, Ledger, Results, Unavailable, freeze, freeze_image


class Capture:
    def __init__(self, compiled, job="job", generation=1):
        self.compiled = compiled
        self.session = ("p0-runtime", generation, compiled.projectId, job)
        self.results = Results()
        self.runs = {}
        self.paths = {}
        self.frames = {}
        self.ledger = Ledger(BUDGET.job_memory)
        self.charges = []
        self.events = []
        self.bytes = {}

    def publish(self, eventType, context, **_):
        self.events.append((eventType, context))
        run = context.workflowRunId
        if eventType == "workflow.started":
            parent = context.parentWorkflowRunId
            path = ()
            if parent:
                parent_context, _key = self.runs[parent]
                node = self.compiled.workflows[parent_context.workflowId].nodeById[context.callerNodeId]
                role = "subflow"
                if node.kind == "loop":
                    role = "body" if node.loop.get("bodyWorkflowId") == context.workflowId else "condition"
                path = self.paths[parent] + ((parent_context.workflowId, node.nodeId, role),)
            self.paths[run] = path
            expected = tuple(f"{n.nodeId}.{port}"
                             for n in self.compiled.workflows[context.workflowId].nodes
                             if n.kind == "operator" for port in n.outputPorts)
            try:
                key = self.results.begin(self.session + (context.workflowId, path), expected)
            except Unavailable:
                key = None
            self.runs[run] = (context, key)
            self.bytes[run] = [0, 0]
        elif eventType in ("workflow.completed", "workflow.failed"):
            self.results.seal(self.runs[run][1], time.perf_counter_ns(),
                              failed=eventType.endswith("failed"))
            if eventType.endswith("failed"):
                pending = self.results.open.get(self.runs[run][1])
                if pending:
                    for source in pending.expected:
                        self.results.value(self.runs[run][1], source, ("FAILED", "execution"))
        elif eventType == "node.skipped":
            key = self.runs[run][1]
            node = self.compiled.workflows[context.workflowId].nodeById[context.callerNodeId]
            for port in node.outputPorts:
                self.results.value(key, f"{node.nodeId}.{port}", ("SKIPPED", None))

    def capture(self, node, outputs, context):
        key = self.runs[context.workflowRunId][1]
        if key is None:
            return
        totals = self.bytes[context.workflowRunId]
        for port in node.outputPorts:
            source = f"{node.nodeId}.{port}"
            if port not in outputs:
                self.results.value(key, source, ("MISSING", None))
                continue
            value = outputs[port]
            try:
                if type(value) is np.ndarray:
                    if totals[1] + value.nbytes > BUDGET.result_image_bytes:
                        raise Unavailable("result image budget")
                    frozen, charge = freeze_image(value, self.ledger)
                    self.charges.append(charge)
                    totals[1] += value.nbytes
                    token = str(uuid4()) if node.operatorId == "vision.io.image_loader" else None
                    self.frames[(key, source)] = (token, context.nodeRunId, port)
                else:
                    # Reserve worst case before normalization/allocation.
                    charge = BUDGET.source_bytes * 2
                    self.ledger.reserve(charge)
                    try:
                        frozen, size = freeze(value)
                        if totals[0] + size > BUDGET.result_bytes:
                            raise Unavailable("result value budget")
                        totals[0] += size
                        self.charges.append(charge)
                    except BaseException:
                        self.ledger.release(charge)
                        raise
                self.results.value(key, source, ("VALID", frozen))
            except Unavailable as error:
                self.results.value(key, source, ("UNAVAILABLE", str(error)))

    def close(self):
        self.results.terminate(self.session)
        self.results.history.clear()
        self.results.latest.clear()
        for charge in self.charges:
            self.ledger.release(charge)
        self.charges.clear()


def image_project(image_path, repeated=True, delay=0.0, fail=False):
    """Local ImageLoader -> controlled nested mutable output -> mutator."""
    leaf = {
        "name": "Local image", "inputs": {}, "outputs": {},
        "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {"nodeId": "load", "kind": "operator", "operatorId": "vision.io.image_loader",
             "params": {"imagePath": str(image_path)}, "outputPorts": {"image": "image", "frame": "bbox2d"}},
            {"nodeId": "source", "kind": "operator", "operatorId": "p0.source",
             "inputPorts": {"image": "image"}, "outputPorts": {"image": "image", "items": "json"}},
            {"nodeId": "mutate", "kind": "operator", "operatorId": "p0.mutate",
             "inputPorts": {"image": "image", "items": "json"}, "outputPorts": {},
             "params": {"delay": delay, "fail": fail}},
            {"nodeId": "output", "kind": "workflow_output"}],
        "edges": [
            {"fromNode": "load", "fromPort": "image", "toNode": "source", "toPort": "image"},
            {"fromNode": "source", "fromPort": "image", "toNode": "mutate", "toPort": "image"},
            {"fromNode": "source", "fromPort": "items", "toNode": "mutate", "toPort": "items"}]}
    workflows = {"main": leaf}
    if repeated:
        workflows = {"leaf": leaf, "main": {
            "name": "Repeated calls", "inputs": {}, "outputs": {}, "edges": [],
            "nodes": [{"nodeId": "input", "kind": "workflow_input"},
                      {"nodeId": "a", "kind": "subflow", "targetWorkflowId": "leaf"},
                      {"nodeId": "b", "kind": "subflow", "targetWorkflowId": "leaf"},
                      {"nodeId": "loop", "kind": "loop", "inputPorts": {}, "outputPorts": {},
                       "loop": {"mode": "repeat", "bodyWorkflowId": "leaf", "repeatCount": 2,
                                "maxIterations": 2, "timeoutMs": 0}},
                      {"nodeId": "output", "kind": "workflow_output"}]}}
    return {"schemaVersion": "2.1", "project": {
        "projectId": "p0-project", "name": "P0 temporary", "revision": 1,
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"},
        "entryWorkflowId": "main", "workflowOrder": list(workflows), "workflows": workflows,
        "runtime": {"maxConcurrentJobs": 2}}


class Source:
    meta = SimpleNamespace(operatorId="p0.source", displayName="source", version="1.0.0",
                           inputPorts={"image": "image"}, outputPorts={"image": "image", "items": "json"},
                           paramSchema={"type": "object"})

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        return {"status": "ok", "outputs": {"image": inputs["image"], "items": [{"count": 7}]}}


class Mutator:
    meta = SimpleNamespace(operatorId="p0.mutate", displayName="mutate", version="1.0.0",
                           inputPorts={"image": "image", "items": "json"}, outputPorts={},
                           paramSchema={"type": "object"})

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        inputs["items"][0]["count"] = 999
        inputs["image"][:] = 0
        time.sleep(params.get("delay", 0))
        if params.get("fail"):
            raise RuntimeError("controlled failure")
        return {"status": "ok", "outputs": {}}
