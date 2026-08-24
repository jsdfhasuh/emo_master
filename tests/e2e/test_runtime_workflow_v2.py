from __future__ import annotations

import json
from pathlib import Path
import queue
import threading

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def _writePlugin(tmpPath: Path) -> tuple[Path, Path]:
    pluginRoot = tmpPath / "plugins"
    pluginDir = pluginRoot / "builtins" / "workflow_probe"
    pluginDir.mkdir(parents=True)
    moduleDir = tmpPath / "workflow_probe_plugin"
    moduleDir.mkdir()
    (moduleDir / "__init__.py").write_text("", encoding="utf-8")
    (moduleDir / "operator.py").write_text(
        "from pathlib import Path\n"
        "from threading import Event\n"
        "\n"
        "class ProbeOperator:\n"
        "    class Meta:\n"
        "        inputPorts = {'value': 'string'}\n"
        "        outputPorts = {'result': 'json'}\n"
        "    meta = Meta()\n"
        "\n"
        "    def validateParams(self, params):\n"
        "        return None\n"
        "\n"
        "    def executeNode(self, inputs, params, runtimeContext):\n"
        "        Path(params['startedPath']).touch()\n"
        "        releasePath = Path(params['releasePath'])\n"
        "        while not releasePath.exists():\n"
        "            Event().wait(0.01)\n"
        "        outputPath = Path(params['outputPath'])\n"
        "        outputPath.parent.mkdir(parents=True, exist_ok=True)\n"
        "        outputPath.write_text(str(inputs['value']), encoding='utf-8')\n"
        "        return {\n"
        "            'status': 'ok',\n"
        "            'outputs': {'result': {'path': str(outputPath)}},\n"
        "            'metrics': {'probeCalls': 1},\n"
        "            'diagnostics': {'probe': 'ok'},\n"
        "        }\n",
        encoding="utf-8",
    )
    (pluginDir / "manifest.json").write_text(
        json.dumps(
            {
                "operatorId": "vision.demo.workflow_probe",
                "displayName": "Workflow Probe",
                "version": "0.1.0",
                "entry": "workflow_probe_plugin.operator:ProbeOperator",
                "inputPorts": {"value": "string"},
                "outputPorts": {"result": "json"},
                "paramSchema": {"type": "object"},
                "minCoreVersion": "0.2.0",
                "maxCoreVersion": "1.x",
            }
        ),
        encoding="utf-8",
    )
    return pluginRoot, moduleDir


def _project(tmpPath: Path, startedPath: Path, releasePath: Path, outputPath: Path):
    return {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "runtime-workflow-v2",
            "name": "Runtime workflow v2",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main", "body"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {"value": "string"},
                "outputs": {"result": "json"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "repeat",
                        "kind": "loop",
                        "inputPorts": {"value": "string"},
                        "outputPorts": {"result": "json"},
                        "loop": {
                            "mode": "repeat",
                            "bodyWorkflowId": "body",
                            "repeatCount": 3,
                            "maxIterations": 3,
                            "timeoutMs": 10000,
                        },
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {"fromNode": "input", "fromPort": "value", "toNode": "repeat", "toPort": "value"},
                    {"fromNode": "repeat", "fromPort": "result", "toNode": "output", "toPort": "result"},
                ],
            },
            "body": {
                "name": "Body",
                "inputs": {"value": "string"},
                "outputs": {"result": "json"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "probe",
                        "kind": "operator",
                        "operatorId": "vision.demo.workflow_probe",
                        "params": {
                            "startedPath": str(startedPath),
                            "releasePath": str(releasePath),
                            "outputPath": str(outputPath),
                        },
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {"fromNode": "input", "fromPort": "value", "toNode": "probe", "toPort": "value"},
                    {"fromNode": "probe", "fromPort": "result", "toNode": "output", "toPort": "result"},
                ],
            },
        },
        "runtime": {"maxConcurrentJobs": 2, "gracefulStopTimeoutMs": 2000},
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }


def testRuntimeWorkflowV2StreamsLiveLoopEventsAndArtifacts(
    tmp_path: Path, monkeypatch
) -> None:
    pluginRoot, _moduleDir = _writePlugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    projectDir = tmp_path / "project"
    projectDir.mkdir()
    startedPath = tmp_path / "started.marker"
    releasePath = tmp_path / "release.marker"
    outputPath = tmp_path / "outputs" / "probe.txt"
    (projectDir / "project.json").write_text(
        json.dumps(_project(tmp_path, startedPath, releasePath, outputPath)),
        encoding="utf-8",
    )

    builtinsRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        pluginRootPaths=(str(builtinsRoot), str(pluginRoot)),
        workspaceRoot=tmp_path / "job-workspaces",
    )
    try:
        loaded = service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        )
        assert loaded.ok is True
        started = service.StartJob(
            runtime_pb2.StartJobRequest(
                project_id="runtime-workflow-v2",
                inputs_json=json.dumps({"value": "probe"}),
            ),
            None,
        )
        assert started.ok is True
        assert started.status == "ACCEPTED"

        eventStream = service.StreamJobEvents(
            runtime_pb2.StreamJobEventsRequest(job_id=started.job_id, follow=True), None
        )
        firstNodeEvent: queue.Queue[tuple[list[object], object]] = queue.Queue()

        def readUntilNodeStarted() -> None:
            prefix: list[object] = []
            for event in eventStream:
                if event.event_type == "node.started" and event.node_id == "probe":
                    firstNodeEvent.put((prefix, event))
                    return
                prefix.append(event)

        reader = threading.Thread(target=readUntilNodeStarted, daemon=True)
        reader.start()
        prefix, liveEvent = firstNodeEvent.get(timeout=5.0)
        assert liveEvent.event_type == "node.started"
        assert service.GetJobStatus(
            runtime_pb2.GetJobStatusRequest(job_id=started.job_id), None
        ).status in {"STARTING", "RUNNING"}
        assert startedPath.exists()

        releasePath.touch()
        reader.join(timeout=5.0)
        remaining = list(eventStream)
        events = [*prefix, liveEvent, *remaining]
        assert [event.sequence for event in events] == sorted(event.sequence for event in events)
        assert any(event.event_type == "job.completed" for event in events)

        bodyStarted = [
            event
            for event in events
            if event.event_type == "workflow.started" and event.workflow_id == "body"
        ]
        assert len(bodyStarted) == 3
        assert len({event.workflow_run_id for event in bodyStarted}) == 3
        assert [json.loads(event.iteration_path_json) for event in bodyStarted] == [[0], [1], [2]]
        mainStarted = next(
            event
            for event in events
            if event.event_type == "workflow.started" and event.workflow_id == "main"
        )
        assert all(event.parent_workflow_run_id == mainStarted.workflow_run_id for event in bodyStarted)

        completed = [
            event
            for event in events
            if event.event_type == "node.completed" and event.node_id == "probe"
        ]
        assert completed
        payload = json.loads(completed[0].payload_json)
        assert payload["metrics"] == {"probeCalls": 1}
        assert payload["diagnostics"] == {"probe": "ok"}

        artifacts = [event for event in events if event.event_type == "artifact.created"]
        assert len(artifacts) == 3
        artifact = json.loads(artifacts[0].payload_json)["artifact"]
        assert artifact["path"]
        assert artifact["checksum"]
        assert Path(artifact["path"]).exists()
    finally:
        service.close()
    assert not list((tmp_path / "job-workspaces").glob("*"))
