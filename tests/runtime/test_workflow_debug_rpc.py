from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from uuid import uuid4

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor
from tests.runtime.test_workflow_debug_control import nestedProject


class FlowClient:
    def __init__(self, runtime, target=None):
        self.runtime, self.target = runtime, target or runtime
        self.identity = dict(runtime_instance_id=runtime.runtimeInstanceId)

    def raw(self, name, **fields):
        method = getattr(self.target, name)
        request = pb.OperatorDebugRequest(**dict(self.identity, **fields))
        return method(request, None) if self.target is self.runtime else method(request, timeout=5)

    def call(self, name, **fields):
        reply = self.raw(name, **fields)
        assert reply.ok, (reply.code, reply.message)
        return json.loads(reply.snapshot_json)

    def open(self, payload=None):
        payload = payload or nestedProject()
        state = self.call("OpenWorkflowDebugSession", open_request_id=uuid4().hex,
            project_json=json.dumps(payload), project_id=payload["project"]["projectId"], workflow_id="main")
        self.identity.update(session_id=state["sessionId"], generation=state["generation"])
        def ready():
            value = self.state()
            assert value["state"] != "FAULTED", value
            return value["state"] == "READY"
        waitFor(ready)

    def state(self):
        return self.call("GetWorkflowDebugSession")

    def start(self, **values):
        prepared = self.call("PrepareWorkflowDebugInputs", request_id=uuid4().hex,
                            inputs={key: pb.OperatorDebugValue(inline_json=json.dumps(value)) for key, value in values.items()})
        self.call("StartWorkflowDebug", request_id=uuid4().hex, input_set_id=prepared["inputSetId"])
        return self.paused()

    def paused(self, after=0):
        def pause():
            state = self.state()
            assert state["state"] not in {"FAULTED", "FAILED"}, state
            return state if state["state"] == "PAUSED" and state["flow"]["pauseSequence"] > after and not state["flow"].get("pendingCommand") else None
        state = waitFor(pause)
        return self.call("GetWorkflowDebugSnapshot", execution_id=state["flow"]["current"])

    def command(self, action, paused=None, **fields):
        requestId = uuid4().hex
        control = dict(action=action, pauseSequence=(paused or {}).get("pauseSequence"), **fields)
        response = self.call("ControlWorkflowDebug", request_id=requestId, control_json=json.dumps(control))
        assert response["status"] == "ACCEPTED"
        def confirmed():
            value = self.call("GetWorkflowDebugCommand", request_id=requestId)
            return value if value["status"] != "ACCEPTED" else None
        response = waitFor(confirmed)
        assert response["status"] == "CONFIRMED", response
        return requestId, control

    def close(self):
        self.call("CloseWorkflowDebugSession", request_id=uuid4().hex)
        waitFor(lambda: not self.state()["resourcesHeld"])


def testRealWorkerNestedStepsPureTrialAndNoProductionMutation(runtime):
    client = FlowClient(runtime)
    payload = nestedProject()
    before = deepcopy(payload)
    client.open(payload)
    first = client.start()
    assert first["nodeId"] == "first"
    client.command("into", first)
    child = client.paused(first["pauseSequence"])
    assert child["nodeId"] == "number"
    client.command("trial", child, params={"value": 99})
    waitFor(lambda: client.state()["flow"].get("trial") and not client.state()["flow"].get("trialRunning"))
    trial = client.call("GetWorkflowDebugSnapshot", execution_id=client.state()["flow"]["trial"])
    assert trial["outputs"] == {"value": 99} and trial["status"] == "SUCCEEDED"
    assert client.state()["flow"]["pauseSequence"] == child["pauseSequence"]
    requestId, control = client.command("out", child)
    returned = client.paused(child["pauseSequence"])
    assert returned["phase"] == "call.return" and returned["outputs"] == {"value": 7}
    replay = client.call("ControlWorkflowDebug", request_id=requestId, control_json=json.dumps(control))
    assert replay["status"] == "CONFIRMED"
    stale = client.raw("ControlWorkflowDebug", request_id=uuid4().hex,
        control_json=json.dumps(dict(action="into", pauseSequence=child["pauseSequence"])))
    assert not stale.ok and stale.code == "E_DEBUG_STALE_PAUSE"
    client.command("continue", returned)
    waitFor(lambda: client.state()["state"] == "SUCCEEDED")
    output = client.call("GetWorkflowDebugSnapshot", execution_id=client.state()["flow"]["lastOutput"])
    assert output["outputs"] == {"value": 7}
    assert payload == before and runtime.loadedDocument is None and runtime.jobRepository.all() == []
    client.close()


@pytest.mark.parametrize("clearBeforeStart", [False, True])
def testReadyBreakpointsAreConfirmedIdempotentAndReplaceable(runtime, clearBeforeStart):
    client = FlowClient(runtime)
    client.open()
    try:
        requestId, control = client.command("breakpoints", breakpoints=[
            dict(workflowId="main", nodeId="first"),
            dict(workflowId="child", nodeId="number", condition="hits == 2", hitCount=2)])
        replay = client.call("ControlWorkflowDebug", request_id=requestId, control_json=json.dumps(control))
        assert replay["status"] == "CONFIRMED"
        state = client.state()
        assert state["state"] == "READY" and not state["flow"].get("started")
        assert not state["snapshots"]
        client.command("breakpoints", breakpoints=[] if clearBeforeStart else [
            dict(workflowId="child", nodeId="number", condition="hits == 2", hitCount=2)])
        initial = client.start()
        assert initial["reason"] == "step" and "main/first" not in initial["hits"]
        client.command("continue", initial)
        if clearBeforeStart:
            assert initial["hits"] == {}
            waitFor(lambda: client.state()["state"] == "SUCCEEDED")
        else:
            paused = client.paused(initial["pauseSequence"])
            assert paused["nodeId"] == "number" and paused["hits"]["child/number"] == 2
        assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    finally:
        client.close()


def testReadyStillRejectsExecutionControlsAndTerminalRejectsBreakpoints(runtime):
    client = FlowClient(runtime)
    client.open()
    try:
        for action in ("pause", "continue", "into", "over", "out", "runTo", "trial"):
            reply = client.raw("ControlWorkflowDebug", request_id=uuid4().hex,
                control_json=json.dumps(dict(action=action, pauseSequence=0)))
            assert not reply.ok and reply.code == "E_DEBUG_STALE_SESSION", action
        assert client.state()["state"] == "READY"
        initial = client.start()
        client.command("continue", initial)
        waitFor(lambda: client.state()["state"] == "SUCCEEDED")
        reply = client.raw("ControlWorkflowDebug", request_id=uuid4().hex,
            control_json=json.dumps(dict(action="breakpoints", breakpoints=[])))
        assert not reply.ok and reply.code == "E_DEBUG_STALE_SESSION"
    finally:
        client.close()


def testLoopbackGrpcWorkflowAndOperatorNamespacesCannotCross(runtime):
    server = grpc.server(ThreadPoolExecutor(max_workers=4), options=[("grpc.max_receive_message_length", 1024*1024)])
    rpc.add_RuntimeServiceServicer_to_server(runtime, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        client = FlowClient(runtime, rpc.RuntimeServiceStub(channel))
        capability = client.call("GetWorkflowDebugCapabilities")
        assert "trial" in capability["commands"]
        assert capability["preStartBreakpoints"] is True
        client.open()
        client.command("breakpoints", breakpoints=[dict(workflowId="main", nodeId="first")])
        paused = client.start()
        assert paused["reason"] == "breakpoint" and paused["hits"]["main/first"] == 1
        rejected = client.raw("GetOperatorDebugSession")
        assert not rejected.ok and rejected.code == "E_DEBUG_CONTEXT_INVALID"
        client.command("over", paused)
        after = client.paused(paused["pauseSequence"])
        assert after["phase"] == "call.return"
        client.close()
    finally:
        channel.close()
        server.stop(0).wait(3)


@pytest.mark.parametrize("operatorId", ["vision.io.huaray_camera", "vision.io.sqlite_writer", "third.party"])
def testWorkflowRejectsUnreviewedPluginBeforeWorkerConstruction(runtime, operatorId):
    payload = nestedProject()
    payload["workflows"]["child"]["nodes"][1]["operatorId"] = operatorId
    client = FlowClient(runtime)
    reply = client.raw("OpenWorkflowDebugSession", open_request_id=uuid4().hex,
        project_json=json.dumps(payload), project_id=payload["project"]["projectId"], workflow_id="main")
    assert not reply.ok and reply.code == "E_DEBUG_UNSUPPORTED"
    assert not runtime.operatorDebugManager.sessions


def emptyLoopProject():
    from tests.runtime.test_workflow_while_boolean import booleanPayload
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"].update(unlimited=True, timeoutMs=0)
    body = payload["workflows"]["body"]
    body["nodes"] = [dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output")]
    body["edges"] = [dict(fromNode="input", fromPort=key, toNode="output", toPort=key) for key in ("count", "hasNext")]
    return payload


def testEmptyInfiniteLoopCanPauseAndStopInsideStepOver(runtime):
    client = FlowClient(runtime)
    client.open(emptyLoopProject())
    first = client.start(count=0, hasNext=True)
    client.command("over", first)
    client.command("pause")
    paused = client.paused(first["pauseSequence"])
    assert paused["phase"].startswith("loop.") and paused["identity"]["iterationPath"]
    assert paused["reason"] == "pause"
    assert not runtime.jobRepository.all()
    client.close()


def testPausedWorkflowLeaseExpiryRetiresWorkerAndAssets(runtime):
    import time
    from pathlib import Path
    client = FlowClient(runtime)
    client.open()
    client.start()
    with runtime.operatorDebugManager.lock:
        session = runtime.operatorDebugManager.sessions[client.identity["session_id"]]
        workspace = Path(session.worker.workspace.name)
        session.expiresAt = time.monotonic() + .15
    waitFor(lambda: not client.state()["resourcesHeld"])
    assert client.state()["code"] == "E_DEBUG_SESSION_EXPIRED"
    assert not workspace.exists()


def testWorkflowImageInputsAndOutputsUseCompleteAssets(runtime):
    import hashlib
    import numpy as np
    from emo_master.apps.runtime.operator_debug.assets import imageBytes, decode
    payload = nestedProject()
    payload["workflowOrder"] = ["main"]
    payload["workflows"] = {"main": dict(name="Image", inputs={"image": "image"}, outputs={"image": "image"}, nodes=[
        dict(nodeId="input", kind="workflow_input"),
        dict(nodeId="blur", kind="operator", operatorId="vision.preprocess.blur", params={}),
        dict(nodeId="output", kind="workflow_output")], edges=[
            dict(fromNode="input", fromPort="image", toNode="blur", toPort="image"),
            dict(fromNode="blur", fromPort="image", toNode="output", toPort="image")])}
    client = FlowClient(runtime)
    client.open(payload)
    raw = imageBytes(np.full((24, 32, 3), 41, np.uint8))
    asset = client.call("WriteWorkflowDebugAsset", request_id=uuid4().hex, content=raw, total_bytes=len(raw),
        mime_type="image/png", sha256=hashlib.sha256(raw).hexdigest())["assetId"]
    prepared = client.call("PrepareWorkflowDebugInputs", request_id=uuid4().hex, inputs={"image": pb.OperatorDebugValue(asset_ref=asset)})
    client.call("StartWorkflowDebug", request_id=uuid4().hex, input_set_id=prepared["inputSetId"])
    paused = client.paused()
    assert paused["inputsAssets"]["image"]["shape"] == [24, 32, 3]
    client.command("continue", paused)
    waitFor(lambda: client.state()["state"] == "SUCCEEDED")
    output = client.call("GetWorkflowDebugSnapshot", execution_id=client.state()["flow"]["lastOutput"])
    reply = client.raw("ReadWorkflowDebugAsset", asset_id=output["outputsAssets"]["image"]["assetId"])
    assert reply.ok
    value, _ = decode(reply.content, "image/png")
    assert np.all(value == 41)
    client.close()


def testConditionErrorsPauseBeforeCallingAndFailureKeepsExactInputs(runtime):
    payload = nestedProject()
    node = payload["workflows"]["child"]["nodes"][1]
    node["operatorId"] = "vision.flow.error"
    node["params"] = {"message": "test failure"}
    payload["workflows"]["child"]["outputs"] = {}
    payload["workflows"]["child"]["edges"] = []
    payload["workflows"]["main"]["outputs"] = {}
    payload["workflows"]["main"]["edges"] = []
    client = FlowClient(runtime)
    client.open(payload)
    first = client.start()
    client.command("breakpoints", breakpoints=[dict(workflowId="child", nodeId="number", condition='inputs["missing"] == 1')])
    client.command("over", first)
    paused = client.paused(first["pauseSequence"])
    assert paused["reason"] == "condition-error"
    client.command("continue", paused)
    waitFor(lambda: client.state()["state"] == "FAILED")
    failure = client.call("GetWorkflowDebugSnapshot", execution_id=client.state()["flow"]["lastOutput"])
    assert failure["phase"] == "node.failed" and failure["nodeId"] == "number"
    assert failure["identity"]["nodeRunId"] == paused["identity"]["nodeRunId"]
    assert failure["outputs"] == {}
    rejected = client.raw("ControlWorkflowDebug", request_id=uuid4().hex, control_json='{"action":"continue","pauseSequence":2}')
    assert not rejected.ok
    client.close()


def testLostControlAcknowledgementReconcilesOriginalRequestWithoutReplay(runtime):
    from emo_master.apps.designer.services.workflow_debug import WorkflowDebugConnection
    from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
    class LoseOnce:
        def __init__(self):
            self.commands = []
        def __getattr__(self, name):
            return getattr(runtime, name)
        def ControlWorkflowDebug(self, request, context):
            self.commands.append(request.request_id)
            result = runtime.ControlWorkflowDebug(request, context)
            assert result.ok
            raise RuntimeClientError("UNAVAILABLE", "test lost response")
    transport = LoseOnce()
    connection = WorkflowDebugConnection(RuntimeClient(transport))
    try:
        connection.openWorkflow(nestedProject(), "main")
        waitFor(lambda: connection.call("GetWorkflowDebugSession")["state"] == "READY")
        connection.start({})
        state = waitFor(lambda: (row if (row := connection.call("GetWorkflowDebugSession"))["state"] == "PAUSED" else None))
        connection.control("into", state["flow"]["pauseSequence"])
        waitFor(lambda: connection.call("GetWorkflowDebugSession")["flow"].get("pauseSequence") == 2)
        assert len(transport.commands) == 1 and not connection.uncertain
    finally:
        connection.close()


def testMalformedPauseSequenceAndSuccessfulTrialClearOldError(runtime):
    client = FlowClient(runtime)
    client.open()
    root = client.start()
    for sequence in (True, 1.0, "1", None):
        reply = client.raw("ControlWorkflowDebug", request_id=uuid4().hex,
            control_json=json.dumps(dict(action="into", pauseSequence=sequence)))
        assert not reply.ok and reply.code == "E_DEBUG_STALE_PAUSE"
    client.command("into", root)
    paused = client.paused(root["pauseSequence"])
    client.command("trial", paused, params={"value": "not-a-number"})
    waitFor(lambda: client.state()["flow"].get("trialError") and not client.state()["flow"].get("trialRunning"))
    client.command("trial", paused, params={"value": 99})
    waitFor(lambda: client.state()["flow"].get("trial") and not client.state()["flow"].get("trialRunning"))
    assert not client.state()["flow"].get("trialError")
    assert client.state()["state"] == "PAUSED"
    client.close()
