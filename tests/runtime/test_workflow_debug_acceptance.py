import json
from pathlib import Path
import time
from uuid import uuid4

import pytest

from emo_master.apps.runtime.operator_debug.manager import OperatorDebugManager
from tests.runtime.operator_debug_fixture import admitted
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor
from tests.runtime.test_workflow_debug_control import nestedProject
from tests.runtime.test_workflow_debug_rpc import FlowClient


def repeatProject(count=70):
    payload = nestedProject()
    main = payload["workflows"]["main"]
    main["nodes"] = [main["nodes"][0], dict(nodeId="repeat", kind="loop", inputPorts={}, outputPorts={"value": "number"}, loop=dict(
        contractVersion=2, mode="repeat", bodyWorkflowId="child", repeatCount=count, maxIterations=count, timeoutMs=0)),
        main["nodes"][-1]]
    main["edges"][0]["fromNode"] = "repeat"
    return payload


def testSnapshotRetentionIsBoundedAndOldInputsDoNotBecomeAnotherInvocation(runtime):
    client = FlowClient(runtime)
    client.open(repeatProject())
    first = client.start()
    client.command("into", first)
    iteration = client.paused(first["pauseSequence"])
    client.command("continue", iteration)
    waitFor(lambda: client.state()["state"] == "SUCCEEDED")
    state = client.state()
    assert len(state["snapshots"]) == 32
    expired = client.raw("GetWorkflowDebugSnapshot", execution_id=first["snapshotId"])
    assert not expired.ok and expired.code == "E_DEBUG_RESULT_EXPIRED"
    retained = client.call("GetWorkflowDebugSnapshot", execution_id=state["flow"]["lastOutput"])
    assert retained["outputs"] == {"value": 7}
    session = runtime.operatorDebugManager.sessions[client.identity["session_id"]]
    path = Path(session.worker.workspace.name)
    client.close()
    assert not path.exists()


@pytest.mark.parametrize("mode", ["wait", "ignore"])
def testWorkflowStopRetiresCooperativeAndUncooperativeLiveOperator(mode):
    payload = nestedProject()
    node = payload["workflows"]["child"]["nodes"][1]
    node.update(operatorId="test.probe", params={"mode": mode}, inputPorts={}, outputPorts={"value": "integer"})
    manager = OperatorDebugManager("runtime", admitted(), graceSeconds=.2)
    identity = dict(runtimeInstanceId="runtime", debugKind="workflow")
    try:
        opened = manager.call("open", dict(identity, openRequestId="open", projectJson=json.dumps(payload),
            projectId=payload["project"]["projectId"], workflowId="main"))
        identity.update(sessionId=opened["sessionId"], generation=1)
        waitFor(lambda: manager.call("get", identity)["state"] == "READY")
        prepared = manager.call("prepare", dict(identity, requestId="inputs", inputs={}))
        manager.call("flow_start", dict(identity, requestId="start", inputSetId=prepared["inputSetId"]))
        waitFor(lambda: manager.call("get", identity)["state"] == "PAUSED")
        manager.call("flow_control", dict(identity, requestId="continue", controlJson='{"action":"continue","pauseSequence":1}'))
        waitFor(lambda: any(event["type"] == "node.log" and event["event"].get("message") == "invoked"
                           for event in manager.call("events", identity)["events"]))
        session = manager.sessions[identity["sessionId"]]
        path = Path(session.worker.workspace.name)
        manager.call("close", dict(identity, requestId="close"))
        assert manager.ownsResources()
        waitFor(lambda: not manager.ownsResources())
        assert not path.exists()
        assert session.forced == (mode == "ignore")
    finally:
        manager.close()


def testStatefulTrialIsRejectedAndVariablesAreIsolated(runtime):
    from emo_master.core.project.migration import migrateProjectPayload
    payload = migrateProjectPayload(nestedProject(), enableGlobalVariables=True)
    payload["globalVariables"] = {"v": dict(name="Value", type="integer", kind="variable", lifetime="persistent", initialValue=4)}
    child = payload["workflows"]["child"]
    child["nodes"][1].update(operatorId="vision.state.variable_read", params={"variableId": "v"})
    client = FlowClient(runtime)
    client.open(payload)
    root = client.start()
    client.command("into", root)
    paused = client.paused(root["pauseSequence"])
    assert paused["variables"] == {"v": 4}
    requestId = uuid4().hex
    client.call("ControlWorkflowDebug", request_id=requestId,
        control_json=json.dumps(dict(action="trial", pauseSequence=paused["pauseSequence"])))
    def rejected():
        row = client.call("GetWorkflowDebugCommand", request_id=requestId)
        return row if row["status"] != "ACCEPTED" else None
    assert waitFor(rejected)["code"] == "E_DEBUG_UNSUPPORTED"
    client.command("continue", paused)
    waitFor(lambda: client.state()["state"] == "SUCCEEDED")
    assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    client.close()


def testDesignerConnectionRenewsWhilePausedBeyondLease(runtime):
    import os
    if os.environ.get("EMO_DEBUG_STRESS") != "1":
        pytest.skip("65-second live lease acceptance; set EMO_DEBUG_STRESS=1")
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.designer.services.workflow_debug import WorkflowDebugConnection
    connection = WorkflowDebugConnection(RuntimeClient(runtime))
    try:
        connection.openWorkflow(nestedProject(), "main")
        waitFor(lambda: connection.call("GetWorkflowDebugSession")["state"] == "READY")
        connection.start({})
        waitFor(lambda: connection.call("GetWorkflowDebugSession")["state"] == "PAUSED")
        time.sleep(65)
        state = connection.call("GetWorkflowDebugSession")
        assert state["state"] == "PAUSED" and state["ttlMs"] > 45000 and not connection.leaseError
        connection.control("continue", state["flow"]["pauseSequence"])
        waitFor(lambda: connection.call("GetWorkflowDebugSession")["state"] == "SUCCEEDED")
    finally:
        connection.close()
