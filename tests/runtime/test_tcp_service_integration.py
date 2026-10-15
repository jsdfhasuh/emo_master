from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import socket

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.clients.runtime.communication import CommunicationClient, CommunicationClientError
from emo_master.core.project.migration import migrateProjectPayload
from tests.core.project.test_project_communication import project, settings
from tests.runtime.test_managed_tcp import freePort, waitUntil


def payload(policy="on_project_run"):
    value = migrateProjectPayload(project(), enableCommunication=True)
    config = settings(freePort(), startupPolicy=policy).model_dump()
    config["tcpServer"]["channels"].append(dict(channelId="rear", name="后工位", allowedIps=["127.0.0.2"]))
    value["communication"] = config
    value["workflows"] = {}
    for channel in ("front", "rear"):
        value["workflows"][channel] = dict(name=channel, inputs={"data": "string"}, outputs={"receipt": "tcpSendReceipt"}, nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="session", kind="operator", operatorId="communication.tcp.session", params=dict(channelId=channel)),
            dict(nodeId="send", kind="operator", operatorId="communication.tcp.session_send"),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="input", fromPort="data", toNode="send", toPort="data"),
                dict(fromNode="session", fromPort="session", toNode="send", toPort="session"),
                dict(fromNode="send", fromPort="receipt", toNode="output", toPort="receipt")])
    value.update(entryWorkflowId="front", workflowOrder=["front", "rear"])
    return value


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    yield service
    service.close()
    assert not service.communicationManager._threads and not service.communicationManager._workers


def load(runtime, tmp_path, value):
    path = tmp_path / "tcp.emoproj"
    path.write_text(json.dumps(value), encoding="utf-8")
    reply = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
    assert reply.ok, reply.message
    return path


@pytest.mark.parametrize("transport", ["embedded", "grpc"])
@pytest.mark.parametrize("clientType,errorType", [(RuntimeClient, RuntimeClientError), (CommunicationClient, CommunicationClientError)],
                         ids=["designer-client", "shared-client"])
def testManagementContractAndSameProjectReload(runtime, tmp_path, transport, clientType, errorType):
    value = payload("manual")
    path = load(runtime, tmp_path, value)
    assert runtime.communicationManager.state == "STOPPED"
    pool = ThreadPoolExecutor(max_workers=4)
    server = grpc.server(pool)
    runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(runtime, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    client = clientType(runtime if transport == "embedded" else runtime_pb2_grpc.RuntimeServiceStub(channel))
    try:
        state = client.communicationState("tcp-project")
        state = client.controlCommunication("START", state, requestId="start")
        assert state["state"] == "LISTENING"
        assert client.controlCommunication("START", state, requestId="start")["serviceGeneration"] == state["serviceGeneration"]
        with socket.create_connection(("127.0.0.1", value["communication"]["tcpServer"]["port"]), timeout=3) as robot:
            session = runtime.communicationManager.acquire("test", "tcp-server", "front", 1000, lambda: False)
            assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
            assert runtime.communicationManager.acquire("test", "tcp-server", "front", 1000, lambda: False)["connectionGeneration"] == session["connectionGeneration"]
            bad = tmp_path / "invalid.emoproj"
            bad.write_text("{}", encoding="utf-8")
            assert not runtime.LoadProject(pb.LoadProjectRequest(project_path=str(bad)), None).ok
            assert runtime.loadedProjectFile == str(path) and runtime.communicationManager.state == "LISTENING"
            disabled = deepcopy(value["communication"])
            disabled["enabled"] = False
            with pytest.raises(errorType) as error:
                client.controlCommunication("SYNC", state, config=disabled)
            assert error.value.code == "E_TCP_BUSY"
            assert robot.fileno() >= 0
        stopped = client.controlCommunication("STOP", state)
        assert stopped["state"] == "STOPPED"
        assert client.controlCommunication("SYNC", stopped, config=disabled)["enabled"] is False
    finally:
        channel.close()
        server.stop(0).wait()
        pool.shutdown()


def testRealSpawnJobsShareListenerAndKeepItAfterCompletion(runtime, tmp_path):
    value = payload()
    load(runtime, tmp_path, value)
    # Actual Job spawn is after the listener barrier, even before clients connect.
    front = runtime.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id="front",
                        inputs_json=json.dumps(dict(data="'12.345,-6.78EY'"))), None)
    rear = runtime.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id="rear",
                        inputs_json=json.dumps(dict(data="'0,0EN'"))), None)
    assert front.ok and rear.ok, (front.message, rear.message)
    assert runtime.communicationManager.state == "LISTENING"
    with socket.create_connection(("127.0.0.1", value["communication"]["tcpServer"]["port"]), timeout=10) as first, socket.socket() as second:
        second.bind(("127.0.0.2", 0))
        second.settimeout(10)
        second.connect(("127.0.0.1", value["communication"]["tcpServer"]["port"]))
        assert first.recv(100) == b"'12.345,-6.78EY'"
        assert second.recv(100) == b"'0,0EN'"
        waitUntil(lambda: all(runtime.jobRepository.get(job).isTerminal and not runtime.jobSupervisor.ownsJobResources(job)
                            for job in (front.job_id, rear.job_id)), 15)
        assert all(runtime.jobRepository.get(job).status == "COMPLETED" for job in (front.job_id, rear.job_id))
        assert runtime.communicationManager.state == "LISTENING"
        assert len(runtime.communicationManager._peers) == 2
        assert not runtime.communicationManager._workers


def testManualDisabledAndCameraGuardsDoNotBind(runtime, tmp_path, monkeypatch):
    value = payload("manual")
    load(runtime, tmp_path, value)
    request = pb.StartJobRequest(project_id="tcp-project", workflow_id="front", inputs_json='{"data":"NO"}')
    assert "E_TCP_NOT_RUNNING" in runtime.StartJob(request, None).message
    assert runtime.communicationManager.state == "STOPPED" and not runtime.jobRepository.all()
    value["communication"]["startupPolicy"] = "on_project_run"
    load(runtime, tmp_path, value)
    monkeypatch.setattr(runtime.livePreviewManager, "hasConfigurationSession", lambda: True)
    assert "E_RESOURCE_BUSY" in runtime.StartJob(request, None).message
    client = RuntimeClient(runtime)
    with pytest.raises(RuntimeClientError, match="配置"):
        client.controlCommunication("START", client.communicationState("tcp-project"))
    assert runtime.communicationManager.state == "STOPPED" and not runtime.jobRepository.all()
    monkeypatch.setattr(runtime.livePreviewManager, "hasConfigurationSession", lambda: False)
    value["communication"]["enabled"] = False
    load(runtime, tmp_path, value)
    assert "E_TCP_DISABLED" in runtime.StartJob(request, None).message


def testOldRuntimeExplicitUnsupported():
    with pytest.raises(RuntimeClientError) as error:
        RuntimeClient(object()).communicationState("project")
    assert error.value.code == "E_TCP_UNSUPPORTED"


def testReplayedStopReturnsFreshStateWithoutStoppingNewGeneration(runtime, tmp_path):
    load(runtime, tmp_path, payload("manual"))
    client = RuntimeClient(runtime)
    first = client.controlCommunication("START", client.communicationState("tcp-project"))
    stopped = client.controlCommunication("STOP", first, requestId="stop-once")
    second = client.controlCommunication("START", stopped)
    replay = client.controlCommunication("STOP", first, requestId="stop-once")
    assert replay["state"] == "LISTENING" and replay["serviceGeneration"] == second["serviceGeneration"]


@pytest.mark.parametrize("mode", ["graceful", "force"])
def testStoppingWaitingWorkerRevokesOnlyItsPermissions(runtime, tmp_path, mode):
    value = payload()
    value["workflows"]["front"]["nodes"][1]["params"]["timeoutMs"] = 60000
    load(runtime, tmp_path, value)
    job = runtime.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id="front", inputs_json='{"data":"NEVER"}'), None)
    assert job.ok
    waitUntil(lambda: runtime.jobRepository.get(job.job_id).status == "RUNNING", 10)
    assert job.job_id in runtime.communicationManager._workers
    stop = runtime.StopJob(pb.StopJobRequest(job_id=job.job_id, mode=mode), None)
    assert stop.ok, stop.message
    waitUntil(lambda: runtime.jobRepository.get(job.job_id).isTerminal and not runtime.jobSupervisor.ownsJobResources(job.job_id), 10)
    assert runtime.jobRepository.get(job.job_id).status == "ABORTED"
    assert job.job_id not in runtime.communicationManager._workers
    assert runtime.communicationManager.state == "LISTENING"
    assert not [item for item, _ in runtime.communicationManager._records if item["kind"] == "tx"]


def testDisabledUnusedFlowRunsButReachableSubflowDependencyIsRejected(runtime, tmp_path):
    value = payload()
    value["communication"]["enabled"] = False
    value["workflows"]["independent"] = dict(name="Independent", nodes=[dict(nodeId="in", kind="workflow_input"),
        dict(nodeId="out", kind="workflow_output")], edges=[])
    value["workflows"]["parent"] = dict(name="Parent", inputs={"data": "string"}, outputs={}, nodes=[
        dict(nodeId="in", kind="workflow_input"), dict(nodeId="call", kind="subflow", targetWorkflowId="front"),
        dict(nodeId="out", kind="workflow_output")], edges=[dict(fromNode="in", fromPort="data", toNode="call", toPort="data")])
    value["workflowOrder"] += ["independent", "parent"]
    load(runtime, tmp_path, value)
    unrelated = runtime.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id="independent"), None)
    assert unrelated.ok
    rejected = runtime.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id="parent", inputs_json='{"data":"NO"}'), None)
    assert not rejected.ok and "E_TCP_DISABLED" in rejected.message
    assert runtime.communicationManager.state == "STOPPED" and not runtime.communicationManager._workers
    waitUntil(lambda: runtime.jobRepository.get(unrelated.job_id).isTerminal and not runtime.jobSupervisor.ownsJobResources(unrelated.job_id), 10)
