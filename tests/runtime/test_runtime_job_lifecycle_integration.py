from __future__ import annotations

import json
from pathlib import Path
from threading import Event
import time

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.runtime_test_utils import waitForTerminal


def _writeLifecyclePlugin(tmpPath: Path) -> Path:
    pluginRoot = tmpPath / "plugins"
    pluginDir = pluginRoot / "builtins" / "lifecycle_probe"
    pluginDir.mkdir(parents=True)
    moduleDir = tmpPath / "lifecycle_probe_plugin"
    moduleDir.mkdir()
    (moduleDir / "__init__.py").write_text("", encoding="utf-8")
    (moduleDir / "operator.py").write_text(
        "from pathlib import Path\n"
        "from threading import Event\n"
        "\n"
        "class LifecycleProbeOperator:\n"
        "    class Meta:\n"
        "        inputPorts = {'mode': 'string'}\n"
        "        outputPorts = {'result': 'json'}\n"
        "    meta = Meta()\n"
        "\n"
        "    def validateParams(self, params):\n"
        "        return None\n"
        "\n"
        "    def executeNode(self, inputs, params, runtimeContext):\n"
        "        job_id = runtimeContext['jobId']\n"
        "        marker_dir = Path(params['markerDir'])\n"
        "        release_dir = Path(params['releaseDir'])\n"
        "        marker_dir.mkdir(parents=True, exist_ok=True)\n"
        "        release_dir.mkdir(parents=True, exist_ok=True)\n"
        "        (marker_dir / f'{job_id}.started').touch()\n"
        "        release = release_dir / f'{job_id}.release'\n"
        "        while not release.exists():\n"
        "            Event().wait(0.01)\n"
        "        return {\n"
        "            'status': 'ok',\n"
        "            'outputs': {'result': {'jobId': job_id, 'mode': inputs['mode']}},\n"
        "        }\n",
        encoding="utf-8",
    )
    (pluginDir / "manifest.json").write_text(
        json.dumps(
            {
                "operatorId": "test.lifecycle_probe",
                "displayName": "Lifecycle Probe",
                "version": "0.1.0",
                "entry": "lifecycle_probe_plugin.operator:LifecycleProbeOperator",
                "inputPorts": {"mode": "string"},
                "outputPorts": {"result": "json"},
                "paramSchema": {"type": "object"},
                "minCoreVersion": "0.2.0",
                "maxCoreVersion": "1.x",
            }
        ),
        encoding="utf-8",
    )
    return pluginRoot


def _writeProject(
    tmpPath: Path,
    markerDir: Path,
    releaseDir: Path,
) -> Path:
    projectDir = tmpPath / "project"
    projectDir.mkdir()
    payload = {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "lifecycle-project",
            "name": "Lifecycle",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {"mode": "string"},
                "outputs": {"result": "json"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "probe",
                        "kind": "operator",
                        "operatorId": "test.lifecycle_probe",
                        "params": {
                            "markerDir": str(markerDir),
                            "releaseDir": str(releaseDir),
                        },
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {
                        "fromNode": "input",
                        "fromPort": "mode",
                        "toNode": "probe",
                        "toPort": "mode",
                    },
                    {
                        "fromNode": "probe",
                        "fromPort": "result",
                        "toNode": "output",
                        "toPort": "result",
                    },
                ],
                "layout": {"nodePositions": {}},
            }
        },
        "runtime": {
            "maxConcurrentJobs": 2,
            "gracefulStopTimeoutMs": 2000,
            "heartbeatTimeoutMs": 5000,
        },
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }
    (projectDir / "project.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return projectDir


def _start(service: RuntimeService, mode: str):
    return service.StartJob(
        runtime_pb2.StartJobRequest(
            project_id="lifecycle-project",
            inputs_json=json.dumps({"mode": mode}),
        ),
        None,
    )


def _waitForPath(path: Path, timeoutSeconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeoutSeconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        Event().wait(0.01)
    raise AssertionError(f"path was not created: {path}")


def _waitForRunning(service: RuntimeService, jobId: str):
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        status = service.GetJobStatus(
            runtime_pb2.GetJobStatusRequest(job_id=jobId),
            None,
        )
        if status.status == "RUNNING":
            return status
        Event().wait(0.01)
    raise AssertionError(f"job did not reach RUNNING: {jobId}")


def _waitForReaped(service: RuntimeService, jobId: str) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if service.jobSupervisor.getProcess(jobId) is None:
            return
        Event().wait(0.01)
    raise AssertionError(f"job process handle was not reaped: {jobId}")


def testRealSpawnConcurrencyStopsAndCleanup(tmp_path: Path, monkeypatch) -> None:
    markerDir = tmp_path / "markers"
    releaseDir = tmp_path / "releases"
    pluginRoot = _writeLifecyclePlugin(tmp_path)
    projectDir = _writeProject(tmp_path, markerDir, releaseDir)
    monkeypatch.syspath_prepend(str(tmp_path))
    workspaceRoot = tmp_path / "job-workspaces"
    builtinsRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        pluginRootPaths=(str(builtinsRoot), str(pluginRoot)),
        workspaceRoot=workspaceRoot,
    )
    completedJobIds: list[str] = []
    try:
        loaded = service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)),
            None,
        )
        assert loaded.ok is True

        first = _start(service, "cooperative")
        second = _start(service, "cooperative")
        assert first.status == "ACCEPTED"
        assert second.status == "ACCEPTED"
        _waitForPath(markerDir / f"{first.job_id}.started")
        _waitForPath(markerDir / f"{second.job_id}.started")
        firstRunning = _waitForRunning(service, first.job_id)
        secondRunning = _waitForRunning(service, second.job_id)
        assert firstRunning.pid > 0
        assert secondRunning.pid > 0
        assert firstRunning.pid != secondRunning.pid

        rejected = _start(service, "cooperative")
        assert rejected.ok is False
        assert rejected.status == "FAILED"
        assert "MAX_CONCURRENT" in rejected.message
        assert not (workspaceRoot / rejected.job_id).exists()

        (releaseDir / f"{first.job_id}.release").touch()
        (releaseDir / f"{second.job_id}.release").touch()
        assert waitForTerminal(service, first.job_id).status == "COMPLETED"
        assert waitForTerminal(service, second.job_id).status == "COMPLETED"
        completedJobIds.extend((first.job_id, second.job_id))
        _waitForReaped(service, first.job_id)
        _waitForReaped(service, second.job_id)
        assert first.job_id not in service.jobSupervisor._terminalEvents
        assert second.job_id not in service.jobSupervisor._terminalEvents
        assert (workspaceRoot / first.job_id).exists()
        assert (workspaceRoot / second.job_id).exists()

        graceful = _start(service, "cooperative")
        _waitForPath(markerDir / f"{graceful.job_id}.started")
        _waitForRunning(service, graceful.job_id)
        stopping = service.StopJob(
            runtime_pb2.StopJobRequest(job_id=graceful.job_id, mode="graceful"),
            None,
        )
        assert stopping.status == "STOPPING"
        (releaseDir / f"{graceful.job_id}.release").touch()
        assert waitForTerminal(service, graceful.job_id).status == "ABORTED"
        _waitForReaped(service, graceful.job_id)
        assert not (workspaceRoot / graceful.job_id).exists()

        blocked = _start(service, "blocked")
        _waitForPath(markerDir / f"{blocked.job_id}.started")
        blockedRunning = _waitForRunning(service, blocked.job_id)
        aborted = service.StopJob(
            runtime_pb2.StopJobRequest(job_id=blocked.job_id, mode="force"),
            None,
        )
        assert aborted.status == "ABORTED"
        assert waitForTerminal(service, blocked.job_id).status == "ABORTED"
        _waitForReaped(service, blocked.job_id)
        assert not (workspaceRoot / blocked.job_id).exists()
        assert service.GetJobStatus(
            runtime_pb2.GetJobStatusRequest(job_id=first.job_id),
            None,
        ).status == "COMPLETED"
        assert blockedRunning.pid != firstRunning.pid
    finally:
        service.close()

    assert completedJobIds
    assert not list(workspaceRoot.glob("*"))
