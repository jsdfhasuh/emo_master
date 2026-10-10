"""Trusted, process-importable fault fixtures; never admitted by production RPC."""
import os
import time


class Probe:
    def __init__(self):
        self.count = 0
        self.disposeMode = ""

    def initOperator(self, runtime):
        runtime["logger"].info("initialized")

    def executeNode(self, inputs, params, runtime):
        self.count += 1
        mode = params.get("mode", "ok")
        self.disposeMode = mode
        runtime["logger"].info("invoked")
        if mode == "crash":
            os._exit(17)
        if mode in {"wait", "ignore"}:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                if mode == "wait":
                    runtime["raiseIfCancellationRequested"]()
                time.sleep(.01)
        if mode == "error":
            raise ValueError("test operator failed")
        if mode == "large":
            return {"status": "ok", "outputs": {"value": self.count}, "diagnostics": {"data": "x"*70000}}
        return {"status": "ok", "outputs": {"value": self.count}}

    def disposeOperator(self):
        if self.disposeMode == "dispose_error":
            raise RuntimeError("test cleanup failed")
        if self.disposeMode == "dispose_hang":
            time.sleep(10)


def admitted():
    return {"test.probe": dict(entry="tests.runtime.operator_debug_fixture:Probe", version="1",
        inputPorts={}, outputPorts={"value": "integer"},
        paramSchema={"type": "object", "properties": {"mode": {"type": "string"}}})}


def project(operatorId="test.probe"):
    return {"schemaVersion": "2.1", "project": {"projectId": "draft"},
            "workflows": {"main": {"nodes": [{"nodeId": "node", "operatorId": operatorId},
                {"nodeId": "unfinished", "operatorId": "unknown", "params": {}}], "edges": []}}}


class MutatingImage:
    def executeNode(self, inputs, params, runtime):
        before = int(inputs["image"][0, 0])
        inputs["image"][:] = 0
        return {"status": "ok", "outputs": {"image": inputs["image"], "before": before},
                "diagnostics": {"large": "x"*70000} if params.get("large") else {}}


def orphanParent(pipe, mode):
    import json
    from emo_master.apps.runtime.operator_debug.manager import OperatorDebugManager
    from tests.runtime.test_operator_debug_sessions import ready, prepare, execute, waitFor
    manager = OperatorDebugManager("runtime", admitted())
    identity = ready(manager)
    execute(manager, identity, prepare(manager, identity), mode=mode)
    session = manager.sessions[identity["sessionId"]]
    waitFor(lambda: any(event["type"] == "node.log" and event["event"]["message"] == "invoked"
                       for event in manager.call("events", identity)["events"]))
    (session.assets.root / "orphan-fixture.bin").write_bytes(b"owned-test-data")
    pipe.send_bytes(json.dumps(dict(pid=session.worker.process.pid, workspace=session.worker.workspace.name)).encode())
    time.sleep(60)
