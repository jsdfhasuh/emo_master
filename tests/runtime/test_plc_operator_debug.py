"""Legacy isolated-worker compatibility against loopback, never hardware."""
from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.contracts import trustedEntries
from tests.runtime.operator_debug_fixture import project
from tests.runtime.plc_debug_server import BlockResponse, PlcDebugServer, response
from tests.core.project.test_devices import legacyPayload
from tests.runtime.test_operator_debug_rpc import runtime as runtimeService, openRequest
from tests.runtime.test_operator_debug_sessions import waitFor
from tests.runtime.test_workflow_debug_control import nestedProject


READ = "communication.plc.slmp_read"


@pytest.fixture
def runtime(runtimeService):
    # Keep the old worker/transport coverage with an explicit legacy descriptor.
    # Current production declarations are independently rejected below.
    scan = runtimeService.pluginScanResult
    descriptor = scan.activeOperators[READ]
    operators = dict(scan.activeOperators)
    operators[READ] = replace(descriptor, manifest=replace(descriptor.manifest, deviceRequirements={}))
    runtimeService.pluginScanResult = replace(scan, activeOperators=operators)
    return runtimeService


def testCurrentManagedPlcCannotOpenPrivateDebugWorker(runtimeService):
    registry = runtimeService.pluginScanResult.activeOperators
    assert registry[READ].manifest.deviceRequirements
    assert READ not in trustedEntries(registry)
    for method in (runtimeService.GetOperatorDebugCapabilities, runtimeService.GetWorkflowDebugCapabilities):
        reply = method(pb.OperatorDebugRequest(), None)
        assert reply.ok
        entry = next(row for row in json.loads(reply.snapshot_json)["operators"] if row["operatorId"] == READ)
        assert not entry["supported"] and entry["reason"] == "use managed device diagnostics"
    for method in (runtimeService.OpenOperatorDebugSession, runtimeService.OpenWorkflowDebugSession):
        reply = method(openRequest(runtimeService, READ), None)
        assert not reply.ok and reply.code == "E_DEVICE_DEBUG_REQUIRED"
    assert not runtimeService.operatorDebugManager.ownsResources()


def openRead(runtime, params):
    payload = project(READ)
    payload["workflows"]["main"]["nodes"][0]["params"] = deepcopy(params)
    connection = DebugConnection(RuntimeClient(runtime))
    try:
        connection.open(payload, EditorKey("draft", "main", "node"), READ)
        waitFor(lambda: connection.snapshot()["session"]["state"] == "READY")
    except BaseException:
        connection.close()
        raise
    return connection


def result(connection, execution):
    def terminal():
        value = connection.snapshot(execution["executionId"])["result"]
        return value if value["status"] != "RUNNING" else None
    return waitFor(terminal)


@pytest.mark.parametrize("dataType,words,expected", [
    ("uint16", [7, 65535], [7, 65535]),
    ("int16", [0xFFFF, 123], [-1, 123]),
    ("uint32", [0xFFFF, 0xFFFF, 5, 1], [4294967295, 65541]),
    ("int32", [0, 0x8000, 0xFFFF, 0xFFFF], [-2147483648, -1]),
    ("float32", [0, 0x3FC0, 0, 0xC010], [1.5, -2.25]),
])
def testReadCurrentConfigurationAndExactOperatorOutputs(runtime, dataType, words, expected):
    with PlcDebugServer() as peer:
        peer.words.update({800 + index: value for index, value in enumerate(words)})
        params = peer.params(device="D", startAddress=800, count=2, dataType=dataType,
                             outputIndex=1, retryCount=0, networkNo=2, pcNo=3,
                             moduleIoNo=1023, moduleStationNo=4, monitoringTimer=5)
        connection = openRead(runtime, params)
        try:
            assert peer.connections == 0  # Opening the window never contacts the PLC.
            record = result(connection, connection.execute(params, {}))
            assert record["status"] == "SUCCEEDED", record
            assert record["outputs"]["values"]["values"] == expected
            assert record["outputs"]["numberValue"] == expected[1]
            assert record["rawParams"] == params
            assert record["effectiveParams"]["startAddress"] == 800
            assert record["outputs"]["values"]["startAddress"] == 800
            assert record["metrics"]["outputIndex"] == 1
            request, = peer.requests
            assert (request.command, request.device, request.start, request.count) == (0x0401, "D", 800, len(words))
            assert request.raw[2:7] == bytes([2, 3, 0xFF, 3, 4])
            assert request.raw[9:11] == b"\x05\x00"
            assert runtime.loadedDocument is None and not runtime.jobRepository.all()
        finally:
            connection.close()
        assert not runtime.operatorDebugManager.ownsResources()


@pytest.mark.parametrize("dataType,expected,port", [
    ("uint16", 9, "numberValue"), ("bit", True, "booleanValue"),
])
def testMReadPreservesWorkflowReadSemantics(runtime, dataType, expected, port):
    with PlcDebugServer() as peer:
        peer.bits.update({100: True, 103: True})
        params = peer.params(device="M", startAddress=100, count=1, dataType=dataType, retryCount=0)
        connection = openRead(runtime, params)
        try:
            record = result(connection, connection.execute(params, {}))
            assert record["status"] == "SUCCEEDED", record
            assert record["outputs"][port] == expected
            assert record["outputs"]["values"]["values"] == [expected]
            assert all(request.command == 0x0401 for request in peer.requests)
        finally:
            connection.close()


def testReadValidationAndPlcFailureAreNotUnsupported(runtime):
    def failed(_server, request, _socket):
        return response(request, end_code=0xC051)
    with PlcDebugServer(actions=[failed]) as peer:
        params = peer.params(startAddress=800, retryCount=0)
        connection = openRead(runtime, params)
        try:
            invalid = result(connection, connection.execute(dict(params, count=1, outputIndex=1), {}))
            assert invalid["status"] == "FAILED" and invalid["code"] == "E_PARAM_INVALID"
            assert peer.connections == 0
            failedRead = result(connection, connection.execute(params, {}))
            assert failedRead["status"] == "FAILED"
            assert failedRead["code"] != "E_DEBUG_UNSUPPORTED"
            assert "C051" in failedRead["message"]
            assert len(peer.requests) == 1
        finally:
            connection.close()


@pytest.mark.parametrize("action", ["cancel", "close", "timeout", "lease"])
def testBlockedReadRetiresSocketAndDoesNotInventSuccess(runtime, action):
    blocked = BlockResponse()
    with PlcDebugServer(actions=[blocked]) as peer:
        params = peer.params(startAddress=800, responseTimeoutMs=60000, retryCount=0)
        connection = openRead(runtime, params)
        runtime.operatorDebugManager.graceSeconds = .15
        try:
            execution = connection.execute(params, {})
            assert blocked.entered.wait(5)
            if action == "cancel":
                connection.mutation("CancelOperatorDebugExecution", execution_id=execution["executionId"])
            elif action == "close":
                connection.close()
            else:
                session = runtime.operatorDebugManager.sessions[connection.identity["session_id"]]
                with runtime.operatorDebugManager.lock:
                    if action == "timeout":
                        session.deadline = 0
                    else:
                        session.expiresAt = 0
            waitFor(lambda: not runtime.operatorDebugManager.ownsResources())
            assert blocked.peer_closed.wait(3)
            record = result(connection, execution)
            assert record["status"] == "UNKNOWN"
            assert record["code"] == ("E_DEBUG_TIMEOUT" if action == "timeout" else "E_CANCELLED")
            assert not record.get("outputs")
            assert len(peer.requests) == 1
        finally:
            connection.close()


def testReadRespectsBothDirectionsOfPlcResourceExclusion(runtime):
    with PlcDebugServer() as peer:
        reserved = runtime.plcDebugManager.reserve("draft", peer.params())
        try:
            rejected = runtime.OpenOperatorDebugSession(openRequest(runtime, READ), None)
            assert not rejected.ok and rejected.code == "E_RESOURCE_BUSY"
            assert peer.connections == 0
        finally:
            runtime.plcDebugManager.close(reserved.sessionId)
        from emo_master.core.project.models import ProjectDocument
        runtime.loadedDocument = ProjectDocument.model_validate(legacyPayload())
        runtime.loadedProjectId = "draft"
        connection = openRead(runtime, peer.params())
        try:
            rejected = runtime.OpenPlcDebugSession(pb.OpenPlcDebugSessionRequest(
                project_id="draft", workflow_id="main", node_id="node", operator_id=READ,
                params_json=json.dumps(peer.params()), request_id=uuid4().hex), None)
            assert not rejected.ok and rejected.code == "E_RESOURCE_BUSY"
            assert peer.connections == 0
        finally:
            connection.close()


def testReadAdmissionRequiresTrustedCodeAndDoesNotAdmitAutomaticWorkflow(runtime):
    registry = runtime.pluginScanResult.activeOperators
    assert trustedEntries(registry)[READ]["singleNodeOnly"]
    for method, supported in ((runtime.GetOperatorDebugCapabilities, True),
                              (runtime.GetWorkflowDebugCapabilities, False)):
        reply = method(pb.OperatorDebugRequest(), None)
        assert reply.ok
        entry = next(row for row in json.loads(reply.snapshot_json)["operators"] if row["operatorId"] == READ)
        assert entry["supported"] is supported
    forged = dict(registry)
    forged[READ] = replace(registry[READ], operatorClass=type("ForgedRead", (), {}))
    assert READ not in trustedEntries(forged)
    payload = nestedProject()
    node = payload["workflows"]["child"]["nodes"][1]
    node.update(operatorId=READ, params={"host": "127.0.0.1", "startAddress": 800})
    reply = runtime.OpenWorkflowDebugSession(pb.OperatorDebugRequest(
        runtime_instance_id=runtime.runtimeInstanceId, open_request_id=uuid4().hex,
        project_id=payload["project"]["projectId"], workflow_id="main", project_json=json.dumps(payload)), None)
    assert not reply.ok and reply.code == "E_DEBUG_UNSUPPORTED"
    assert "single-node" in reply.message
    assert not runtime.operatorDebugManager.ownsResources()
