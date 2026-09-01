from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.workflow.compiler import WorkflowCompiler


def _writeEmptyProject(root: Path, projectId: str) -> None:
    root.mkdir()
    payload = {
        "schemaVersion": "2.0",
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
    (root / "project.json").write_text(json.dumps(payload), encoding="utf-8")


def testProjectLoadSerializesAgainstStartJob(
    tmp_path: Path, monkeypatch
) -> None:
    projectA = tmp_path / "project-a"
    projectB = tmp_path / "project-b"
    _writeEmptyProject(projectA, "project-a")
    _writeEmptyProject(projectB, "project-b")
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
    )
    enteredCompile = threading.Event()
    releaseCompile = threading.Event()
    startEntered = threading.Event()
    originalCompile = WorkflowCompiler.compile

    def blockingCompile(self, project, pluginRootPaths=()):
        projectId = getattr(getattr(project, "project", None), "projectId", "")
        if projectId == "project-b":
            enteredCompile.set()
            assert releaseCompile.wait(2.0)
        return originalCompile(self, project, pluginRootPaths)

    monkeypatch.setattr(WorkflowCompiler, "compile", blockingCompile)
    try:
        assert service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectA)), None
        ).ok
        with ThreadPoolExecutor(max_workers=2) as executor:
            loadFuture = executor.submit(
                service.LoadProject,
                runtime_pb2.LoadProjectRequest(project_path=str(projectB)),
                None,
            )
            assert enteredCompile.wait(1.0)

            def startOldProject():
                startEntered.set()
                return service.StartJob(
                    runtime_pb2.StartJobRequest(project_id="project-a"), None
                )

            startFuture = executor.submit(startOldProject)
            assert startEntered.wait(1.0)
            assert startFuture.done() is False
            releaseCompile.set()
            assert loadFuture.result(timeout=2.0).ok is True
            startReply = startFuture.result(timeout=2.0)
        assert startReply.ok is False
        assert startReply.message == "project not loaded"
        assert service.loadedProjectId == "project-b"
        assert service.loadedDocument is not None
        assert service.loadedDocument.project.projectId == "project-b"
    finally:
        releaseCompile.set()
        service.close()
