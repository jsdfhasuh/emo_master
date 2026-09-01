from __future__ import annotations

import json
from pathlib import Path

from emo_master.apps.runtime.context.global_counters import MAX_GLOBAL_COUNTER_VALUE
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.runtime_test_utils import waitForTerminal


def testGlobalCounterGrpcCrudValidationAndProjectRestriction(tmp_path: Path) -> None:
    projectDir = _writeCounterProject(tmp_path, "counter-project")
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
    )
    try:
        notLoaded = service.ListGlobalCounters(
            runtime_pb2.ListGlobalCountersRequest(project_id="counter-project"),
            None,
        )
        assert notLoaded.ok is False
        assert notLoaded.code == "E_PROJECT_NOT_LOADED"

        loaded = service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        )
        assert loaded.ok is True

        created = service.GetGlobalCounter(
            runtime_pb2.GetGlobalCounterRequest(
                project_id="counter-project",
                name="零件数",
            ),
            None,
        )
        assert created.ok is True
        assert created.counter.value == 0

        changed = service.SetGlobalCounter(
            runtime_pb2.SetGlobalCounterRequest(
                project_id="counter-project",
                name="零件数",
                value=MAX_GLOBAL_COUNTER_VALUE,
            ),
            None,
        )
        assert changed.ok is True
        assert changed.counter.value == MAX_GLOBAL_COUNTER_VALUE

        reset = service.ResetGlobalCounter(
            runtime_pb2.ResetGlobalCounterRequest(
                project_id="counter-project",
                name="零件数",
            ),
            None,
        )
        assert reset.ok is True
        assert reset.counter.value == 0

        listed = service.ListGlobalCounters(
            runtime_pb2.ListGlobalCountersRequest(project_id=str(projectDir)),
            None,
        )
        assert listed.ok is True
        assert [(item.name, item.value) for item in listed.counters] == [("零件数", 0)]

        invalidName = service.GetGlobalCounter(
            runtime_pb2.GetGlobalCounterRequest(
                project_id="counter-project",
                name=" invalid",
            ),
            None,
        )
        invalidValue = service.SetGlobalCounter(
            runtime_pb2.SetGlobalCounterRequest(
                project_id="counter-project",
                name="零件数",
                value=-1,
            ),
            None,
        )
        wrongProject = service.ListGlobalCounters(
            runtime_pb2.ListGlobalCountersRequest(project_id="other-project"),
            None,
        )
        assert invalidName.code == "E_COUNTER_NAME_INVALID"
        assert invalidValue.code == "E_COUNTER_VALUE_RANGE"
        assert wrongProject.code == "E_PROJECT_NOT_LOADED"
    finally:
        service.close()


def testGlobalCounterPersistsAcrossConcurrentJobsAndRuntimeRestart(tmp_path: Path) -> None:
    projectDir = _writeCounterProject(tmp_path, "job-counter-project")
    dbPath = tmp_path / "runtime.db"
    workspaceRoot = tmp_path / "jobs"
    service = RuntimeService(dbPath=dbPath, workspaceRoot=workspaceRoot)
    try:
        assert service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        ).ok
        replies = [
            service.StartJob(
                runtime_pb2.StartJobRequest(
                    project_id="job-counter-project",
                    workflow_id="main",
                    inputs_json=json.dumps({"increment": True}),
                ),
                None,
            )
            for _ in range(4)
        ]
        assert all(reply.ok for reply in replies)
        statuses = [waitForTerminal(service, reply.job_id) for reply in replies]
        assert all(status.status == "COMPLETED" for status in statuses)
        assert service.GetGlobalCounter(
            runtime_pb2.GetGlobalCounterRequest(
                project_id="job-counter-project",
                name="jobs",
            ),
            None,
        ).counter.value == 4
    finally:
        service.close()

    reopened = RuntimeService(dbPath=dbPath, workspaceRoot=workspaceRoot)
    try:
        assert reopened.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        ).ok
        persisted = reopened.GetGlobalCounter(
            runtime_pb2.GetGlobalCounterRequest(
                project_id="job-counter-project",
                name="jobs",
            ),
            None,
        )
        assert persisted.ok is True
        assert persisted.counter.value == 4
    finally:
        reopened.close()


def _writeCounterProject(tmp_path: Path, projectId: str) -> Path:
    projectDir = tmp_path / projectId
    projectDir.mkdir(parents=True)
    payload = {
        "schemaVersion": "2.1",
        "project": {
            "projectId": projectId,
            "name": projectId,
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {"increment": "boolean"},
                "outputs": {"count": "integer"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "counter",
                        "kind": "operator",
                        "operatorId": "vision.state.counter",
                        "params": {"name": "jobs"},
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {
                        "fromNode": "input",
                        "fromPort": "increment",
                        "toNode": "counter",
                        "toPort": "increment",
                    },
                    {
                        "fromNode": "counter",
                        "fromPort": "count",
                        "toNode": "output",
                        "toPort": "count",
                    },
                ],
                "layout": {"nodePositions": {}},
            }
        },
        "runtime": {"maxConcurrentJobs": 4},
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }
    (projectDir / "project.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return projectDir
