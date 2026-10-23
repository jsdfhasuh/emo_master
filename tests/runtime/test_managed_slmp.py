"""Managed PLC acceptance against loopback only. No physical equipment."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
import time

import pytest
import grpc

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import revisionOf
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins._communication_operators import PlcSlmpReadOperator, PlcSlmpWriteOperator
from tests.runtime.plc_debug_server import BlockResponse, PlcDebugServer, response
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_runtime import simulatedProject


ROOT = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"


def plcProject(port, encoding="binary", *, write=False):
    payload = simulatedProject()
    payload["devices"]["definitions"] = {"plc": dict(name="Loopback PLC", deviceTypeId="device.plc.mitsubishi_slmp",
        driverVersion="1.0.0", connection=dict(host="127.0.0.1", port=port, frameEncoding=encoding,
        responseTimeoutMs=500), defaults={})}
    payload["workflows"]["main"].update(outputs={}, edges=[], nodes=[dict(nodeId="plc-node",
        operatorId="communication.plc.slmp_write" if write else "communication.plc.slmp_read",
        params=dict(startAddress=800, **({"values": [123]} if write else {})), deviceBindings={"plc": {"deviceId": "plc"}}),
        dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output")])
    return payload


def managerFor(payload):
    types, rejected = PluginRegistry("0.6.1").scanDeviceTypes([ROOT])
    assert not rejected
    manager = DeviceManager(types)
    manager.load(ProjectDocument.model_validate(payload))
    manager.authorize("a", ["plc"])
    manager.authorize("b", ["plc"])
    return manager


def execute(manager, action="read", arguments=None, owner="a", cancelled=lambda: False):
    return manager.execute(owner, "plc", action, {}, arguments=arguments or dict(device="D", startAddress=800, count=1),
                           cycleId="cycle-" + owner, cancelled=cancelled)


@pytest.mark.parametrize("encoding", ["binary", "ascii"])
def testTwoOwnersReuseOneSessionAndTypedOperatorPorts(encoding):
    with PlcDebugServer(encoding=encoding, fragment=True) as peer:
        manager = managerFor(plcProject(peer.port, encoding))
        try:
            assert peer.connections == 0
            def context(owner):
                return {"executeDevice": lambda slot, action, arguments: execute(manager, action, arguments, owner)}
            write = PlcSlmpWriteOperator().executeNode({}, dict(startAddress=800, dataType="int32", values=[-17]), context("a"))
            read = PlcSlmpReadOperator().executeNode({}, dict(startAddress=800, dataType="int32"), context("b"))
            assert write["status"] == "ok", write
            assert read["status"] == "ok", read
            assert read["outputs"]["numberValue"] == -17
            assert write["outputs"]["receipt"]["attempts"] == 1
            assert read["diagnostics"]["deviceOperation"]["connectionGeneration"] == write["diagnostics"]["deviceOperation"]["connectionGeneration"]
            manager.retire("a")
            execute(manager, owner="b")
            assert peer.connections == 1 and len(peer.requests) == 3
        finally:
            manager.close()


@pytest.mark.parametrize("action,args", [
    ("read", dict(device="D", startAddress=0, count=0)),
    ("read", dict(device="D", startAddress=999999, count=2)),
    ("read", dict(device="M", startAddress=999990, count=1)),
    ("read", dict(device="D", startAddress=0, count=1, host="other")),
    ("write", dict(device="D", startAddress=0, words=[])),
    ("write", dict(device="D", startAddress=0, words=[65536])),
    ("write", dict(device="D", startAddress=0, words=[True])),
])
def testInvalidRequestsRejectedBeforeConnect(action, args):
    with PlcDebugServer(encoding="ascii") as peer:
        manager = managerFor(plcProject(peer.port, "ascii"))
        try:
            with pytest.raises((ValueError, DeviceError)):
                execute(manager, action, args)
            assert peer.connections == 0 and not peer.requests
            assert not manager.executors["plc"].quarantined
        finally:
            manager.close()


@pytest.mark.parametrize('retries', [0, 3])
def testLostWriteReceiptQuarantinesWithoutReplay(retries):
    def applyThenDisconnect(server, request, sock):
        server.memory_response(request)
        return None
    with PlcDebugServer([applyThenDisconnect]) as peer:
        payload = plcProject(peer.port)
        payload['devices']['definitions']['plc']['policies'] = dict(connectionRetryCount=retries)
        manager = managerFor(payload)
        try:
            with pytest.raises(DeviceError) as failure:
                execute(manager, "write", dict(device="D", startAddress=800, words=[321]))
            assert failure.value.outcome == "UNKNOWN"
            assert peer.words[800] == 321
            assert manager.executors["plc"].quarantined
            with pytest.raises(DeviceError, match="quarantined"):
                execute(manager, owner="b")
            assert len(peer.requests) == peer.connections == 1
        finally:
            manager.close()


def testPlcRejectIsNotTransportSuccess():
    with PlcDebugServer([lambda server, request, sock: response(request, end_code=0xC051)]) as peer:
        manager = managerFor(plcProject(peer.port))
        try:
            with pytest.raises(DeviceError) as failure:
                execute(manager, "write", dict(device="D", startAddress=800, words=[321]))
            assert failure.value.outcome == "REJECTED"
            assert failure.value.code == "E_DEVICE_SLMP_RESPONSE"
            assert len(peer.requests) == 1
        finally:
            manager.close()


def testCancellationRetiresSocketBeforeNextOwnerCanUseIt():
    held = BlockResponse()
    cancelled = Event()
    with PlcDebugServer([held]) as peer, ThreadPoolExecutor(1) as pool:
        manager = managerFor(plcProject(peer.port))
        try:
            pending = pool.submit(execute, manager, cancelled=cancelled.is_set)
            assert held.entered.wait(2)
            cancelled.set()
            with pytest.raises(DeviceError):
                pending.result(2)
            assert held.peer_closed.wait(2)
            executor = manager.executors["plc"]
            with executor.condition:
                assert executor.condition.wait_for(lambda: executor.active is None, timeout=2)
            assert executor.quarantined
            with pytest.raises(DeviceError):
                execute(manager, owner="b")
            assert len(peer.requests) == 1
        finally:
            manager.close()


def testDuplicateEndpointCannotBypassLockThroughRoutingOrFormat():
    payload = plcProject(12345)
    other = deepcopy(payload["devices"]["definitions"]["plc"])
    other["connection"].update(frameEncoding="ascii", networkNo=2)
    payload["devices"]["definitions"]["other"] = other
    with pytest.raises(DeviceError, match="conflicts with plc"):
        managerFor(payload)


def testDnsAliasesAreRejectedAndMappedIpv4CannotCreateAnotherOwner():
    payload = plcProject(12345)
    payload["devices"]["definitions"]["plc"]["connection"]["host"] = "localhost"
    with pytest.raises(ValueError, match="IP literal"):
        managerFor(payload)
    payload = plcProject(12345)
    other = deepcopy(payload["devices"]["definitions"]["plc"])
    other["connection"]["host"] = "::ffff:127.0.0.1"
    payload["devices"]["definitions"]["mapped"] = other
    with pytest.raises(DeviceError, match="conflicts with plc"):
        managerFor(payload)


def testManagedCompilerRequiresBindingAndRejectsHiddenConnectionOverrides():
    scan = PluginRegistry("0.6.1").scan(ROOT)
    compiler = WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes)
    payload = plcProject(12345)
    assert compiler.compile(payload).workflows["main"].nodes[0].devicePlans
    node = payload["workflows"]["main"]["nodes"][0]
    node["params"]["retryCount"] = 3
    with pytest.raises(WorkflowCompileError, match="connection/retry"):
        compiler.compile(payload)
    node["params"].pop("retryCount")
    node.pop("deviceBindings")
    with pytest.raises(WorkflowCompileError, match="device slots"):
        compiler.compile(payload)
    payload["schemaVersion"] = "2.5"
    payload["devices"] = {"bindings": {}}
    from emo_master.core.project.communication import CommunicationSettings
    payload["communication"] = CommunicationSettings().model_dump()
    with pytest.raises(WorkflowCompileError, match="device slots"):
        compiler.compile(payload)


@pytest.mark.parametrize("encoding", ["binary", "ascii"])
@pytest.mark.parametrize("write", [False, True])
def testSpawnedJobsUseRuntimeSocketNotPrivateWorkerConnections(tmp_path, encoding, write):
    with PlcDebugServer(encoding=encoding) as peer:
        peer.words[800] = 7
        path = tmp_path / "project.json"
        path.write_text(json.dumps(plcProject(peer.port, encoding, write=write)), encoding="utf-8")
        service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
        try:
            loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
            assert loaded.ok, loaded.message
            assert peer.connections == 0
            replies = [service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
                       for _ in range(2)]
            for reply in replies:
                assert reply.ok, reply
                terminal = waitForTerminal(service, reply.job_id, timeoutSeconds=30)
                assert terminal.status == "COMPLETED", terminal
            deadline = time.monotonic() + 5
            while service.deviceManager.workers and time.monotonic() < deadline:
                service.jobSupervisor.finishRetirements()
                time.sleep(.02)
            assert not service.deviceManager.workers
            assert peer.connections == 1 and len(peer.requests) == 2
            assert peer.words[800] == (123 if write else 7)
        finally:
            service.close()


def openManagedDebug(service, **overrides):
    selector = dict(deviceId="plc", definitionRevision=revisionOf(service.deviceManager.definitions["plc"].model_dump()))
    selector.update(overrides)
    return service.OpenPlcDebugSession(pb.OpenPlcDebugSessionRequest(project_id=service.loadedProjectId,
        workflow_id="devices", node_id="plc", operator_id="communication.plc.slmp_read", request_id="open",
        params_json=json.dumps(selector)), None)


def debugCommand(service, session, command, requestId, **params):
    return service.ExecutePlcDebugCommand(pb.ExecutePlcDebugCommandRequest(session_id=session.session_id,
        runtime_instance_id=service.runtimeInstanceId, command=command, request_id=requestId, params_json=json.dumps(params)), None)


@pytest.mark.parametrize("encoding", ["binary", "ascii"])
def testManagedDiagnosticsAndJobShareSocketWithConfirmedIdempotentBitWrites(tmp_path, encoding):
    with PlcDebugServer(encoding=encoding) as peer:
        path = tmp_path / "project.json"
        path.write_text(json.dumps(plcProject(peer.port, encoding)), encoding="utf-8")
        service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
        try:
            assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
            stale = openManagedDebug(service, definitionRevision="stale")
            assert not stale.ok and peer.connections == 0
            override = openManagedDebug(service, host="127.0.0.1")
            assert not override.ok and peer.connections == 0
            session = openManagedDebug(service)
            assert session.ok, session
            assert service.deviceManager.maintenanceOwners
            read = debugCommand(service, session, "read", "r", device="M", dataType="bit", startAddress=100, count=3)
            assert read.ok, read
            assert json.loads(read.result_json)["values"] == [False, False, False]
            values = dict(device="M", dataType="bit", startAddress=100, values=[True, False, True], lockGeneration=0)
            locked = debugCommand(service, session, "write", "locked", confirmed=True, **values)
            assert locked.code == "E_PLC_WRITE_LOCKED"
            assert debugCommand(service, session, "set_write_enabled", "unlock", enabled=True, lockGeneration=0).ok
            unconfirmed = debugCommand(service, session, "write", "unconfirmed", **values)
            assert not unconfirmed.ok and len(peer.requests) == 1
            written = debugCommand(service, session, "write", "w", confirmed=True, **values)
            assert written.ok, written
            assert json.loads(written.result_json)["receipt"]["outcome"] == "confirmed"
            assert json.loads(written.result_json)["readback"]["values"] == [True, False, True]
            replay = debugCommand(service, session, "write", "w", confirmed=True, **values)
            assert replay.result_json == written.result_json and len(peer.requests) == 3
            started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
            assert started.ok, started
            assert waitForTerminal(service, started.job_id, timeoutSeconds=30).status == "COMPLETED"
            assert debugCommand(service, session, "renew", "renew").ok
            closed = service.ClosePlcDebugSession(pb.ClosePlcDebugSessionRequest(session_id=session.session_id,
                runtime_instance_id=service.runtimeInstanceId), None)
            assert closed.ok, closed
            assert not service.deviceManager.maintenanceOwners
            assert peer.connections == 1
            assert service.deviceManager.drivers["plc"].connected
        finally:
            service.close()


def testManagedDiagnosticLostReceiptDoesNotReplayOrReleaseAnotherOwner(tmp_path):
    def lost(server, request, sock):
        server.memory_response(request)
        return None
    with PlcDebugServer([lost]) as peer:
        path = tmp_path / "project.json"
        path.write_text(json.dumps(plcProject(peer.port)), encoding="utf-8")
        service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
        try:
            assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
            session = openManagedDebug(service)
            assert session.ok
            assert debugCommand(service, session, "set_write_enabled", "unlock", enabled=True, lockGeneration=0).ok
            written = debugCommand(service, session, "write", "write", device="D", startAddress=800, dataType="uint16",
                                   values=[777], lockGeneration=0, confirmed=True)
            assert written.code == "E_PLC_WRITE_UNKNOWN", written
            assert peer.words[800] == 777 and len(peer.requests) == 1
            assert service.deviceManager.executors["plc"].quarantined
            assert not service.deviceManager.maintenanceOwners
            assert not service.projectConfiguration.cycles
            assert not openManagedDebug(service).ok
            assert peer.connections == 1
            state = json.loads(service.GetDeviceStates(pb.GetDeviceStatesRequest(project_id=service.loadedProjectId), None).states_json)["plc"]
            recover = pb.RecoverDeviceRequest(project_id=service.loadedProjectId, runtime_instance_id=service.runtimeInstanceId,
                device_id="plc", expected_generation=state["generation"], expected_definition_revision=state["definitionRevision"])
            wrong = pb.RecoverDeviceRequest()
            wrong.CopyFrom(recover)
            wrong.runtime_instance_id = "old-runtime"
            assert not service.RecoverDevice(wrong, None).ok
            recovered = service.RecoverDevice(recover, None)
            assert recovered.ok, recovered
            new = json.loads(recovered.states_json)["plc"]
            assert new["generation"] != state["generation"] and not new["lifecycleConnected"]
            assert peer.connections == len(peer.requests) == 1
            assert not service.RecoverDevice(recover, None).ok
            session = openManagedDebug(service)
            assert session.ok, session
            newRead = debugCommand(service, session, "read", "verify", device="D", startAddress=800, count=1)
            assert json.loads(newRead.result_json)["values"] == [777]
            assert peer.connections == len(peer.requests) == 2
            newRecovery = pb.RecoverDeviceRequest(project_id=service.loadedProjectId, runtime_instance_id=service.runtimeInstanceId,
                device_id="plc", expected_generation=new["generation"], expected_definition_revision=new["definitionRevision"])
            assert not service.RecoverDevice(newRecovery, None).ok
        finally:
            service.close()


def testQueuedWriteCancellationDoesNotSendOrRetireOtherOwner():
    held = BlockResponse()
    cancelled = Event()
    with PlcDebugServer([held]) as peer, ThreadPoolExecutor(2) as pool:
        manager = managerFor(plcProject(peer.port))
        try:
            read = pool.submit(execute, manager)
            assert held.entered.wait(2)
            write = pool.submit(execute, manager, "write", dict(device="D", startAddress=800, words=[42]), "b", cancelled.is_set)
            executor = manager.executors["plc"]
            with executor.condition:
                assert executor.condition.wait_for(lambda: len(executor.queue) == 1, timeout=2)
            cancelled.set()
            with pytest.raises(DeviceError) as failure:
                write.result(2)
            assert failure.value.outcome == "NOT_EXECUTED"
            held.release.set()
            assert read.result(2)["words"] == [0]
            assert len(peer.requests) == 1 and peer.connections == 1
            assert not executor.quarantined
        finally:
            held.release.set()
            manager.close()


def testManagedActionParametersCannotConstructPrivateClient(monkeypatch):
    from emo_master.plugins.builtins import _communication_operators as operators
    monkeypatch.setattr(operators, "Slmp3EClient", lambda *args: pytest.fail("private connection"))
    calls = []
    context = {"executeDevice": lambda *args: calls.append(args)}
    result = PlcSlmpReadOperator().executeNode({}, dict(host="localhost"), context)
    assert result["status"] == "error" and not calls


def testConfirmedWriteSurvivesLocalReceiptFormattingFailure(monkeypatch):
    from emo_master.plugins.builtins import _communication_operators as operators
    def fail(**kwargs):
        raise ValueError("receipt formatting failed")
    monkeypatch.setattr(operators, "PlcWriteReceipt", fail)
    context = {"executeDevice": lambda *args: dict(outcome="COMPLETED", connectionGeneration="g")}
    result = PlcSlmpWriteOperator().executeNode({}, dict(values=[123]), context)
    assert result["status"] == "error"
    assert result["diagnostics"]["deviceOperation"]["outcome"] == "COMPLETED"


def testRemoteRecoveryFencesAndLegacyDebugGateWithoutConnecting(tmp_path):
    with PlcDebugServer() as peer:
        path = tmp_path / "project.json"
        path.write_text(json.dumps(plcProject(peer.port)), encoding="utf-8")
        service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        try:
            assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
            client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
            runtimeId, states = client.getDeviceStates(service.loadedProjectId)
            _, recovered = client.recoverDevice(service.loadedProjectId, runtimeId, "plc", states["plc"])
            assert recovered["plc"]["generation"] != states["plc"]["generation"]
            with pytest.raises(RuntimeClientError, match="generation"):
                client.recoverDevice(service.loadedProjectId, runtimeId, "plc", states["plc"])
            with pytest.raises(RuntimeClientError, match="Runtime"):
                client.recoverDevice(service.loadedProjectId, "old", "plc", recovered["plc"])
            request = pb.OperatorDebugRequest(project_id=service.loadedProjectId, project_json="{}",
                operator_id="communication.plc.slmp_read")
            assert service.OpenOperatorDebugSession(request, None).code == "E_DEVICE_DEBUG_REQUIRED"
            request.operator_id = ""
            request.project_json = json.dumps(plcProject(peer.port))
            assert service.OpenWorkflowDebugSession(request, None).code == "E_DEVICE_DEBUG_REQUIRED"
            request.project_json = "[]"
            assert service.OpenWorkflowDebugSession(request, None).code == "E_DEBUG_CONTEXT_INVALID"
            assert peer.connections == 0
        finally:
            channel.close()
            server.stop(0).wait()
            service.close()


@pytest.mark.parametrize("blocked,code", [
    ("unauthorized", "E_DEVICE_OWNER"), ("not-maintenance", "E_DEVICE_OWNER"),
    ("disabled", "E_DEVICE_DISABLED"), ("cancelled", "E_DEVICE_CANCELLED"),
    ("paused", "E_DEVICE_PAUSED"), ("pending", "E_DEVICE_PAUSED"),
    ("quarantined", "E_DEVICE_UNAVAILABLE"),
])
def testDiagnosticConnectChecksAuthorityBeforeSocketIo(blocked, code):
    with PlcDebugServer() as peer:
        manager = managerFor(plcProject(peer.port))
        owner = "diagnostic-test"
        try:
            if blocked != "unauthorized":
                manager.authorize(owner, ["plc"])
                if blocked != "not-maintenance":
                    manager.authorizeMaintenance(owner, "plc")
            if blocked == "disabled":
                manager.definitions["plc"].enabled = False
            if blocked == "paused":
                manager.lifecycles["plc"].paused = True
            if blocked == "pending":
                manager.lifecycles["plc"].controlPending = True
            if blocked == "quarantined":
                manager.executors["plc"].quarantined = True
            with pytest.raises(DeviceError) as failure:
                manager.connectDiagnostic(owner, "plc", lambda: blocked == "cancelled")
            assert failure.value.code == code
            assert peer.connections == 0 and not peer.requests
        finally:
            manager.retire("diagnostic-test")
            manager.close()


def testDiagnosticConnectKeepsLeaseAndFirstReadReusesSocket():
    with PlcDebugServer() as peer:
        manager = managerFor(plcProject(peer.port))
        manager.definitions["plc"].policies.update(connect="manual", release="when_unused")
        manager.authorizeMaintenance("diagnostic-test", "plc")
        try:
            assert manager.connectDiagnostic("diagnostic-test", "plc") == "CONNECTED"
            assert manager.lifecycles["plc"].connected
            assert "diagnostic-test" in manager.lifecycles["plc"].keepAlive
            assert not peer.requests
            result = execute(manager, owner="diagnostic-test")
            assert result["outcome"] == "COMPLETED"
            assert peer.connections == 1 and len(peer.requests) == 1
            manager.retire("diagnostic-test")
            assert "diagnostic-test" not in manager.lifecycles["plc"].keepAlive
        finally:
            manager.retire("diagnostic-test")
            manager.close()


def testCancelledDiagnosticDoesNotCancelAnotherOwnersConnection(monkeypatch):
    entered, release, cancelled = Event(), Event(), Event()
    with PlcDebugServer() as peer, ThreadPoolExecutor(1) as pool:
        manager = managerFor(plcProject(peer.port))
        driver = manager.drivers["plc"]
        original = driver.connect
        def blocked(params, cancel, deadline):
            entered.set()
            assert release.wait(3)
            return original(params, cancel, deadline)
        monkeypatch.setattr(driver, "connect", blocked)
        manager.authorizeMaintenance("diagnostic-test", "plc")
        try:
            production = manager._connectEvent("plc", "a")
            assert entered.wait(2)
            diagnostic = pool.submit(manager.connectDiagnostic, "diagnostic-test", "plc", cancelled.is_set)
            end = time.monotonic() + 2
            while not manager.executors["plc"].queue and time.monotonic() < end:
                time.sleep(.01)
            assert manager.executors["plc"].queue
            cancelled.set()
            with pytest.raises(DeviceError) as failure:
                diagnostic.result(2)
            assert failure.value.code == "E_DEVICE_CANCELLED"
            assert not production.cancellation.is_set()
            release.set()
            assert manager.executors["plc"].wait(production) == "CONNECTED"
            assert manager.lifecycles["plc"].connected and not peer.requests
        finally:
            release.set()
            manager.retire("diagnostic-test")
            manager.close()


@pytest.mark.parametrize("unavailable", [False, True])
def testDiagnosticSubmissionFailureDoesNotLeakKeepalive(monkeypatch, unavailable):
    with PlcDebugServer() as peer:
        manager = managerFor(plcProject(peer.port))
        manager.authorizeMaintenance("diagnostic-test", "plc")
        def fail(*args, **kwargs):
            if unavailable:
                return None
            raise DeviceError("E_DEVICE_REPLACING", "fixture submission failure")
        monkeypatch.setattr(manager, "_connectEvent", fail)
        try:
            with pytest.raises(DeviceError) as failure:
                manager.connectDiagnostic("diagnostic-test", "plc")
            assert failure.value.code == ("E_DEVICE_UNAVAILABLE" if unavailable else "E_DEVICE_REPLACING")
            assert "diagnostic-test" not in manager.lifecycles["plc"].keepAlive
            assert not peer.connections
        finally:
            manager.retire("diagnostic-test")
            manager.close()
