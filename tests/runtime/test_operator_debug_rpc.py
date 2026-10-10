from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
import time
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.operator_debug.contracts import trustedEntries
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_sessions import waitFor


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        yield service
    finally:
        service.close()
        assert not service.operatorDebugManager.ownsResources()


def request(runtime, **changes):
    return pb.OperatorDebugRequest(runtime_instance_id=runtime.runtimeInstanceId, **changes)


def openRequest(runtime, operatorId="vision.value.number", **changes):
    values = dict(open_request_id="open", project_json=json.dumps(project(operatorId)),
                  project_id="draft", workflow_id="main", node_id="node", operator_id=operatorId)
    values.update(changes)
    return request(runtime, **values)


def ready(runtime, caller=None, operatorId="vision.value.number"):
    caller = caller or runtime
    opened = caller.OpenOperatorDebugSession(openRequest(runtime, operatorId), None) if caller is runtime else caller.OpenOperatorDebugSession(openRequest(runtime, operatorId))
    assert opened.ok, opened.message
    identity = dict(session_id=opened.session_id, generation=opened.generation)
    waitFor(lambda: runtime.GetOperatorDebugSession(request(runtime, **identity), None).state == "READY")
    return identity


def testLoopbackGrpcNumericExecutionDoesNotLoadOrCreateJob(runtime):
    server = grpc.server(ThreadPoolExecutor(max_workers=4), options=[("grpc.max_receive_message_length", 1024*1024)])
    rpc.add_RuntimeServiceServicer_to_server(runtime, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = rpc.RuntimeServiceStub(channel)
        client = RuntimeClient(stub)
        capabilities = client.operatorDebugCall("GetOperatorDebugCapabilities", pb.OperatorDebugRequest())
        supported = [item["operatorId"] for item in json.loads(capabilities.snapshot_json)["operators"] if item["supported"]]
        assert len(supported) == 47
        assert {"vision.compare.number", "vision.value.number", "vision.image.absdiff"} <= set(supported)
        identity = ready(runtime, stub, "vision.compare.number")
        prepared = stub.PrepareOperatorDebugInputs(request(runtime, **identity, request_id="inputs",
            inputs={"left": pb.OperatorDebugValue(inline_json="3")}))
        assert prepared.ok, prepared.message
        accepted = stub.ExecuteOperatorDebugNode(request(runtime, **identity, request_id="execute",
            input_set_id=prepared.input_set_id, params_json='{"operator":"gte","rightValue":2}'))
        assert accepted.ok
        def terminal():
            reply = stub.GetOperatorDebugExecution(request(runtime, **identity, request_id="execute"))
            value = json.loads(reply.snapshot_json)
            return value if value["status"] != "RUNNING" else None
        value = waitFor(terminal)
        assert value["status"] == "SUCCEEDED" and value["outputs"] == {"result": True}
        assert value["rawParams"] == {"operator": "gte", "rightValue": 2}
        assert stub.ExecuteOperatorDebugNode(request(runtime, **identity, request_id="execute",
            input_set_id=prepared.input_set_id, params_json='{"operator":"gte","rightValue":2}')).execution_id == accepted.execution_id
        assert runtime.loadedDocument is None and runtime.loadedPayload is None
        assert runtime.jobRepository.all() == []
        assert not list(runtime.workspaceRoot.iterdir())
        # Losing the original open response is recovered by its request ID.
        lookup = stub.GetOperatorDebugSession(request(runtime, open_request_id="open"))
        assert lookup.session_id == identity["session_id"]
    finally:
        channel.close()
        server.stop(0).wait(3)


@pytest.mark.parametrize("raw,code", [('true', 'E_INPUT_TYPE'), ('null', 'E_INPUT_TYPE'),
                                     ('NaN', 'E_DEBUG_CONTEXT_INVALID'), ('1e999', 'E_DEBUG_CONTEXT_INVALID')])
def testInputRejectionIsRuntimeSide(runtime, raw, code):
    identity = ready(runtime, operatorId="vision.compare.number")
    reply = runtime.PrepareOperatorDebugInputs(request(runtime, **identity, request_id="input",
        inputs={"left": pb.OperatorDebugValue(inline_json=raw)}), None)
    assert not reply.ok and reply.code == code
    assert not runtime.operatorDebugManager.sessions[identity["session_id"]].executions


def testAssetsAndUnknownPortsAreNotSilentlyAccepted(runtime):
    identity = ready(runtime, operatorId="vision.compare.number")
    for inputs, code in [({}, "E_INPUT_MISSING"), ({"bogus": pb.OperatorDebugValue(inline_json="1")}, "E_INPUT_UNDECLARED"),
                         ({"left": pb.OperatorDebugValue(asset_ref="C:/production.db")}, "E_DEBUG_RESULT_EXPIRED")]:
        reply = runtime.PrepareOperatorDebugInputs(request(runtime, **identity, request_id="inputs", inputs=inputs), None)
        assert not reply.ok and reply.code == code


@pytest.mark.parametrize("operatorId", ["vision.io.huaray_camera", "communication.plc.slmp_write",
                                       "vision.io.sqlite_writer", "vision.inference.yolo"])
def testUnreviewedOrSideEffectingOperatorsCannotOpen(runtime, operatorId):
    reply = runtime.OpenOperatorDebugSession(openRequest(runtime, operatorId), None)
    assert not reply.ok and reply.code == "E_DEBUG_UNSUPPORTED"
    assert not runtime.operatorDebugManager.sessions


def testTrustedRegistrationRequiresOriginalClassAndOrigin(runtime, tmp_path):
    registry = runtime.pluginScanResult.activeOperators
    descriptor = registry["vision.value.number"]
    replaced = dict(registry, **{"vision.value.number": replace(descriptor, resourceRoot=tmp_path)})
    assert "vision.value.number" not in trustedEntries(replaced)
    replaced["vision.value.number"] = replace(descriptor, operatorClass=type("Forged", (), {}))
    assert "vision.value.number" not in trustedEntries(replaced)


@pytest.mark.parametrize("mutation", ["duplicate", "wrong-kind", "wrong-project", "deleted"])
def testInvalidTargetDraftRejectedBeforeSpawn(runtime, mutation):
    payload = project("vision.value.number")
    nodes = payload["workflows"]["main"]["nodes"]
    if mutation == "duplicate":
        nodes.append(deepcopy(nodes[0]))
    elif mutation == "wrong-kind":
        nodes[0]["kind"] = "loop"
    elif mutation == "wrong-project":
        payload["project"]["projectId"] = "other"
    else:
        del nodes[0]
    reply = runtime.OpenOperatorDebugSession(openRequest(runtime, project_json=json.dumps(payload)), None)
    assert not reply.ok and reply.code == "E_DEBUG_CONTEXT_INVALID"
    assert not runtime.operatorDebugManager.sessions


def testOldRuntimeIsReportedWithoutLocalOrWorkflowFallback():
    client = RuntimeClient(SimpleNamespace())
    with pytest.raises(RuntimeClientError) as error:
        client.operatorDebugCall("GetOperatorDebugCapabilities", pb.OperatorDebugRequest())
    assert error.value.code == "E_DEBUG_UNSUPPORTED"


def testRuntimeGenerationAndByteBudgetAreEnforced(runtime):
    wrong = openRequest(runtime)
    wrong.runtime_instance_id = "old-runtime"
    assert runtime.OpenOperatorDebugSession(wrong, None).code == "E_DEBUG_STALE_SESSION"
    huge = openRequest(runtime, project_json=" " * (768*1024))
    assert runtime.OpenOperatorDebugSession(huge, None).code == "E_DEBUG_LIMIT"
    assert not runtime.operatorDebugManager.sessions


def testExistingJobOrPreviewReservationPreventsOpeningWithoutStoppingIt(runtime):
    record = runtime.jobManager.createJob("other", 1, "main")
    reply = runtime.OpenOperatorDebugSession(openRequest(runtime), None)
    assert reply.code == "E_RESOURCE_BUSY"
    assert not runtime.jobRepository.get(record.jobId).isTerminal
    runtime.jobRepository.update(record.jobId, status="COMPLETED")
    sentinel = object()
    with runtime.livePreviewManager._lock:
        runtime.livePreviewManager._sessions["test-reserved"] = sentinel
    try:
        assert runtime.OpenOperatorDebugSession(openRequest(runtime), None).code == "E_RESOURCE_BUSY"
        assert runtime.livePreviewManager._sessions["test-reserved"] is sentinel
    finally:
        runtime.livePreviewManager._sessions.pop("test-reserved")
    with runtime.plcDebugManager._lock:
        runtime.plcDebugManager._sessions["test-reserved"] = sentinel
    try:
        assert runtime.OpenOperatorDebugSession(openRequest(runtime), None).code == "E_RESOURCE_BUSY"
    finally:
        runtime.plcDebugManager._sessions.pop("test-reserved")


def testDebugReservationBlocksLegacyEntryPoints(runtime):
    ready(runtime)
    assert "E_RESOURCE_BUSY" in runtime.LoadProject(pb.LoadProjectRequest(project_path="missing"), None).message
    assert runtime.OpenDraftOperatorPreviewSession(pb.OpenOperatorPreviewSessionRequest(), None).code == "E_RESOURCE_BUSY"
    assert runtime.OpenOperatorPreviewSession(pb.OpenOperatorPreviewSessionRequest(), None).code == "E_RESOURCE_BUSY"
    assert runtime.RunOperatorPreview(pb.RunOperatorPreviewRequest(), None).code == "E_RESOURCE_BUSY"
    assert runtime.jobRepository.all() == []


def testFormalPlcAndPageStartsCannotStopOrStealDebugSession(runtime, tmp_path):
    from emo_master.core.project.models import ProjectDocument
    from emo_master.apps.runtime.presentation.service import PresentationService

    payload = project("vision.value.number")
    payload["project"].update(name="Formal", createdAt="2026-10-10T00:00:00Z", updatedAt="2026-10-10T00:00:00Z")
    payload.update(entryWorkflowId="main", workflowOrder=["main"])
    payload["workflows"]["main"].update(name="Main", nodes=[
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "node", "operatorId": "vision.value.number"},
        {"nodeId": "output", "kind": "workflow_output"}])
    runtime.loadedDocument = ProjectDocument.model_validate(payload)
    runtime.loadedProjectId = "draft"
    runtime.loadedProjectPath = str(tmp_path)
    identity = ready(runtime)
    formal = runtime.StartJob(pb.StartJobRequest(project_id="draft", workflow_id="main"), None)
    assert not formal.ok and "E_RESOURCE_BUSY" in formal.message
    plc = runtime.OpenPlcDebugSession(pb.OpenPlcDebugSessionRequest(project_id="draft", workflow_id="main",
        node_id="plc", operator_id="communication.plc.slmp_read", params_json="{}", request_id="plc"), None)
    assert not plc.ok and plc.code == "E_RESOURCE_BUSY"
    page = PresentationService(runtime, tmp_path / "display")
    with pytest.raises(RuntimeError, match="E_RESOURCE_BUSY"):
        page.start("unused-prepared-id")
    assert runtime.jobRepository.all() == []
    assert runtime.GetOperatorDebugSession(request(runtime, **identity), None).state == "READY"


@pytest.mark.parametrize("preparation", ["sqlite", "capture"])
def testFormalStartRejectsBeforePreparationAndRetainsRequestDedup(runtime, tmp_path, monkeypatch, preparation):
    from emo_master.core.project.models import ProjectDocument
    from emo_master.apps.runtime.presentation.service import PresentationService

    payload = project("vision.io.sqlite_writer" if preparation == "sqlite" else "vision.value.number")
    payload["project"].update(name="Formal", createdAt="2026-10-10T00:00:00Z", updatedAt="2026-10-10T00:00:00Z")
    payload.update(entryWorkflowId="main", workflowOrder=["main"])
    payload["workflows"]["main"].update(name="Main")
    runtime.loadedDocument = document = ProjectDocument.model_validate(payload)
    runtime.loadedProjectId = "draft"
    runtime.loadedProjectPath = str(tmp_path)
    original = document.model_dump_json()
    page = PresentationService(runtime, tmp_path / "display")
    calls = []
    def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError("production preparation must not run while debugging")
    monkeypatch.setattr(runtime.sqliteManagement, "run", unexpected)
    monkeypatch.setattr(page, "checkNormalAdmission", unexpected)
    identity = ready(runtime)
    start = pb.StartJobRequest(project_id="draft", workflow_id="main", start_request_id="formal",
        expected_runtime_instance_id=runtime.runtimeInstanceId, capture_presentation=preparation == "capture")
    rejected = runtime.StartJob(start, None)
    assert not rejected.ok and "E_RESOURCE_BUSY" in rejected.message
    assert rejected.status == "REJECTED" and not calls
    assert runtime.loadedDocument is document and document.model_dump_json() == original
    runtime.CloseOperatorDebugSession(request(runtime, **identity, request_id="close"), None)
    waitFor(lambda: not runtime.operatorDebugManager.ownsResources())
    monkeypatch.setattr(runtime, "_startJob", unexpected)
    assert runtime.StartJob(start, None) == rejected
    assert not calls and runtime.jobRepository.all() == []


def testConcurrentOpensUseSingleAtomicReservation(runtime):
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda n: runtime.OpenOperatorDebugSession(
            openRequest(runtime, open_request_id=f"open-{n}"), None), range(8)))
    assert sum(reply.ok for reply in results) == 1
    assert all(reply.ok or reply.code == "E_RESOURCE_BUSY" for reply in results)


def testRegistrationReplacedAfterRuntimeStartIsRejected(runtime, tmp_path):
    registry = runtime.pluginScanResult.activeOperators
    registry["vision.value.number"] = replace(registry["vision.value.number"], resourceRoot=tmp_path)
    reply = runtime.OpenOperatorDebugSession(openRequest(runtime), None)
    assert not reply.ok and reply.code == "E_DEBUG_UNSUPPORTED"


def testOldGrpcServerUnimplementedIsReportedExplicitly():
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    rpc.add_RuntimeServiceServicer_to_server(rpc.RuntimeServiceServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        client = RuntimeClient(rpc.RuntimeServiceStub(channel))
        with pytest.raises(RuntimeClientError) as error:
            client.operatorDebugCall("GetOperatorDebugCapabilities", pb.OperatorDebugRequest())
        assert error.value.code == "E_DEBUG_UNSUPPORTED"
    finally:
        channel.close()
        server.stop(0).wait(3)


def testAioServerDisconnectExpiresSessionWithoutAnotherRpc(runtime, tmp_path):
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.presentation.service import PresentationService

    page = PresentationService(runtime, tmp_path / "display")
    server = AioRuntimeServer(runtime, page)
    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        stub = rpc.RuntimeServiceStub(channel)
        identity = ready(runtime, stub)
        reply = stub.RenewOperatorDebugSession(request(runtime, **identity))
        assert reply.ok
        with runtime.operatorDebugManager.lock:
            runtime.operatorDebugManager.sessions[identity["session_id"]].expiresAt = time.monotonic() + .15
        channel.close()
        waitFor(lambda: not runtime.operatorDebugManager.ownsResources())
        assert runtime.GetOperatorDebugSession(request(runtime, **identity), None).state == "FAULTED"
    finally:
        channel.close()
        server.close()
