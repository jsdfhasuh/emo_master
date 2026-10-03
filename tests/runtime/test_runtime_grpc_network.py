from __future__ import annotations

import json
from pathlib import Path

import grpc

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer


def _empty_project() -> dict[str, object]:
    return {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "network-project",
            "name": "Network",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {},
                "outputs": {},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [],
                "layout": {"nodePositions": {}},
            }
        },
        "runtime": {"maxConcurrentJobs": 1},
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }


def testRuntimeClientUsesRandomPortGrpcContract(tmp_path: Path) -> None:
    projectDir = tmp_path / "network-project"
    projectDir.mkdir()
    (projectDir / "project.json").write_text(
        json.dumps(_empty_project(), ensure_ascii=True), encoding="utf-8"
    )
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    server, boundPort, _ = createRuntimeServer(
        port=0,
        runtimeService=service,
    )
    assert boundPort > 0
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{boundPort}")
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
        loaded = client.loadProject(str(projectDir))
        assert loaded.ok is True
        started = client.startJob("network-project", workflowId="main")
        assert started.ok is True
        events = client.streamJobEvents(started.job_id, follow=True)
        assert any(event.eventType == "job.completed" for event in events)
        assert all(event.projectId == "network-project" for event in events if event.projectId)
        status = client.getJobStatus(started.job_id)
        assert status.status == "COMPLETED"
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def testDefaultServerExposesReadonlyDisplayWithoutStartingJob(tmp_path: Path) -> None:
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2

    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    try:
        stub = runtime_pb2_grpc.DisplayServiceStub(channel)
        capabilities = stub.Capabilities(runtime_pb2.DisplayEmpty(), timeout=5)
        assert {'normal_start_capture', 'project_jobs', 'source_coverage'} <= set(capabilities.capabilities)
        assert capabilities.runtime_instance_id == service.runtimeInstanceId
        assert not stub.ListJobs(runtime_pb2.DisplayEmpty(project_id='unloaded'), timeout=5).jobs
        assert not service.jobRepository.all()
        assert service._presentationOwner.exporter is None
    finally:
        channel.close()
        server.stop(0).wait()
        server.stop(0).wait()  # Idempotent transport shutdown does not touch Jobs.
        assert server.wait_for_termination(timeout=0) is False
        service.close()
        assert service._presentationOwner is None
