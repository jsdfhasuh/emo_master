from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import threading

import pytest
from pydantic import ValidationError

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.runtime_test_utils import waitForTerminal


class _EchoOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        return {"status": "ok", "outputs": {"value": inputs.get("value")}}


class _FailWithMetadataOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = inputs
        _ = params
        _ = runtimeContext
        return {
            "status": "failed",
            "error": {"code": "E_OPERATOR_METADATA", "message": "failed with metadata"},
            "metrics": {"latencyMs": 12},
            "diagnostics": {"retryable": False},
        }


def _v2_project() -> dict[str, object]:
    return {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "project-v2",
            "name": "workflow",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {"image": "image"},
                "outputs": {"result": "json"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "empty",
                        "kind": "operator",
                        "operatorId": "vision.demo.empty",
                        "params": {},
                        "inputPorts": {"image": "image"},
                        "outputPorts": {"result": "json"},
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {
                        "fromNode": "input",
                        "fromPort": "image",
                        "toNode": "empty",
                        "toPort": "image",
                    },
                    {
                        "fromNode": "empty",
                        "fromPort": "result",
                        "toNode": "output",
                        "toPort": "result",
                    },
                ],
                "layout": {"nodePositions": {}},
            }
        },
        "runtime": {"maxConcurrentJobs": 2},
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }


def testProjectV1MigratesToV2() -> None:
    migrated = migrateProjectPayload(
        {
            "version": "1.0",
            "meta": {"name": "legacy"},
            "designer": {"nodes": [], "edges": []},
        }
    )
    assert migrated["schemaVersion"] == "2.1"
    assert migrated["entryWorkflowId"] == "main"
    assert "main" in migrated["workflows"]


def testProjectV20MigrationMarksExistingLoopsAsLegacyContracts() -> None:
    payload = _v2_project()
    payload["workflowOrder"] = ["main", "body"]
    payload["workflows"]["body"] = {
        "name": "Body",
        "inputs": {},
        "outputs": {},
        "nodes": [],
        "edges": [],
        "layout": {"nodePositions": {}},
    }
    payload["workflows"]["main"]["nodes"].append(
        {
            "nodeId": "foreach",
            "kind": "loop",
            "inputPorts": {"items": "list"},
            "outputPorts": {"results": "list"},
            "loop": {
                "mode": "foreach",
                "bodyWorkflowId": "body",
                "maxIterations": 10,
                "timeoutMs": 0,
            },
        }
    )

    migrated = migrateProjectPayload(payload)
    loop = migrated["workflows"]["main"]["nodes"][-1]["loop"]

    assert migrated["schemaVersion"] == "2.1"
    assert loop["contractVersion"] == 1


def testV2RejectsUnknownTopLevelAndNodeFields() -> None:
    payload = _v2_project()
    payload["futureField"] = True
    with pytest.raises(ValidationError):
        migrateProjectPayload(payload)

    payload = _v2_project()
    payload["workflows"]["main"]["nodes"][0]["futureField"] = True
    with pytest.raises(ValidationError):
        migrateProjectPayload(payload)


def testFutureProjectSchemaVersionIsRejected() -> None:
    payload = _v2_project()
    payload["schemaVersion"] = "2.2"

    with pytest.raises(ValueError, match="unsupported project schemaVersion"):
        migrateProjectPayload(payload)


def testLegacyUnknownNodeKindIsNotSilentlyFiltered() -> None:
    migrated = migrateProjectPayload(
        {
            "version": "1.0",
            "designer": {
                "nodes": [{"nodeId": "future", "kind": "future_kind"}],
                "edges": [],
            },
        }
    )

    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(migrated)


def testLegacyUnknownTopLevelAndWorkflowFieldsAreRejected() -> None:
    with pytest.raises(ValueError, match="top-level"):
        migrateProjectPayload(
            {
                "version": "1.0",
                "meta": {"name": "legacy"},
                "designer": {"nodes": [], "edges": []},
                "futureField": True,
            }
        )

    with pytest.raises(ValueError, match="workflow"):
        migrateProjectPayload(
            {
                "version": "1.0",
                "workflows": {"main": {"name": "Main", "futureField": True}},
            }
        )


def testMissingSchemaWithoutLegacyMarkersIsRejected() -> None:
    with pytest.raises(ValueError, match="v1 markers"):
        migrateProjectPayload({"project": {"name": "ambiguous"}})


def testNodeFailedEventPreservesMetricsAndDiagnostics() -> None:
    payload = _v2_project()
    payload["workflows"]["main"]["nodes"][1]["operatorId"] = "test.fail"
    events = []
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(
        operatorRegistry={"test.fail": _FailWithMetadataOperator}
    ).compile(document)
    runner = WorkflowRunner(
        compiled,
        {"test.fail": _FailWithMetadataOperator},
        eventPublisher=lambda **event: events.append(event),
    )

    with pytest.raises(WorkflowExecutionError) as error:
        runner.run("main", {"image": {}}, RunContext.root("job", "main"), CancellationToken())

    assert error.value.code == "E_OPERATOR_METADATA"
    failed = next(event for event in events if event["eventType"] == "node.failed")
    assert failed["payload"]["metrics"] == {"latencyMs": 12}
    assert failed["payload"]["diagnostics"] == {"retryable": False}


@pytest.mark.parametrize("value", [None, 0, False, "", [], {}])
def testFalsyWorkflowInputIsNotSkipped(value: object) -> None:
    payload = _v2_project()
    payload["project"]["projectId"] = "falsy-project"
    payload["workflows"]["main"] = {
        "name": "Main",
        "inputs": {"value": "object"},
        "outputs": {"value": "object"},
        "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {
                "nodeId": "echo",
                "kind": "operator",
                "operatorId": "test.echo",
                "inputPorts": {"value": "object"},
                "outputPorts": {"value": "object"},
            },
            {"nodeId": "output", "kind": "workflow_output"},
        ],
        "edges": [
            {"fromNode": "input", "fromPort": "value", "toNode": "echo", "toPort": "value"},
            {"fromNode": "echo", "fromPort": "value", "toNode": "output", "toPort": "value"},
        ],
        "layout": {"nodePositions": {}},
    }
    events = []
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(operatorRegistry={"test.echo": _EchoOperator}).compile(document)
    runner = WorkflowRunner(
        compiled,
        {"test.echo": _EchoOperator},
        eventPublisher=lambda **event: events.append(event),
    )

    result = runner.run("main", {"value": value}, RunContext.root("job", "main"), CancellationToken())

    assert result.outputs == {"value": value}
    assert not any(event["eventType"] == "node.skipped" for event in events)


def testWorkflowCompilerRejectsCycleInsideWorkflow() -> None:
    project = _v2_project()
    workflow = project["workflows"]["main"]
    workflow["edges"].append(
        {"fromNode": "empty", "fromPort": "result", "toNode": "input", "toPort": "image"}
    )
    document = ProjectDocument.model_validate(project)
    try:
        WorkflowCompiler(operatorRegistry={"vision.demo.empty": object}).compile(document)
    except WorkflowCompileError as err:
        assert any(issue.code == "E_WORKFLOW_CYCLE" for issue in err.issues)
    else:
        raise AssertionError("expected cycle validation error")


def testCompiledProjectHasImmutablePlan() -> None:
    document = ProjectDocument.model_validate(_v2_project())
    compiled = WorkflowCompiler(operatorRegistry={"vision.demo.empty": object}).compile(document)
    assert compiled.workflows["main"].topologicalOrder == ("input", "empty", "output")
    with pytest.raises(FrozenInstanceError):
        compiled.entryWorkflowId = "other"


def testV2StartJobUsesSpawnProcessAndReplaysSequences(tmp_path: Path) -> None:
    projectDir = tmp_path / "project"
    projectDir.mkdir()
    (projectDir / "project.json").write_text(
        json.dumps(_v2_project(), ensure_ascii=True), encoding="utf-8"
    )
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    try:
        loaded = service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        )
        assert loaded.ok is True
        reply = service.StartJob(
            runtime_pb2.StartJobRequest(
                project_id="project-v2", inputs_json=json.dumps({"image": "x"})
            ),
            None,
        )
        assert reply.ok is True
        assert reply.status == "ACCEPTED"
        completed = threading.Event()
        for _ in range(100):
            status = service.GetJobStatus(
                runtime_pb2.GetJobStatusRequest(job_id=reply.job_id), None
            )
            if status.status in {"COMPLETED", "FAILED", "ABORTED"}:
                completed.set()
                break
            completed.wait(0.05)
        assert completed.is_set()
        status = service.GetJobStatus(
            runtime_pb2.GetJobStatusRequest(job_id=reply.job_id), None
        )
        assert status.status == "COMPLETED"
        events = list(
            service.StreamJobEvents(
                runtime_pb2.StreamJobEventsRequest(job_id=reply.job_id), None
            )
        )
        assert events
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        replayed = list(
            service.StreamJobEvents(
                runtime_pb2.StreamJobEventsRequest(
                    job_id=reply.job_id, after_sequence=1
                ),
                None,
            )
        )
        assert all(event.sequence > 1 for event in replayed)
    finally:
        service.close()


def testWorkerCrashDoesNotAffectOtherJobAndUsesDistinctPids(
    tmp_path: Path, monkeypatch
) -> None:
    pluginRoot = tmp_path / "plugins"
    pluginDir = pluginRoot / "builtins" / "crash_edge"
    pluginDir.mkdir(parents=True)
    moduleDir = tmp_path / "crash_plugin"
    moduleDir.mkdir()
    (moduleDir / "__init__.py").write_text("", encoding="utf-8")
    (moduleDir / "operator.py").write_text(
        "class CrashOperator:\n"
        "  class Meta:\n"
        "    inputPorts = {'mode': 'string'}\n"
        "    outputPorts = {'result': 'json'}\n"
        "  meta = Meta()\n"
        "  def validateParams(self, params):\n"
        "    return None\n"
        "  def executeNode(self, inputs, params, runtimeContext):\n"
        "    if inputs.get('mode') == 'crash':\n"
        "      raise RuntimeError('intentional worker crash')\n"
        "    return {'status': 'ok', 'outputs': {'result': {'mode': inputs.get('mode')}}, 'metrics': {}, 'diagnostics': {}}\n",
        encoding="utf-8",
    )
    (pluginDir / "manifest.json").write_text(
        json.dumps(
            {
                "operatorId": "vision.demo.crash",
                "displayName": "Crash Operator",
                "version": "0.1.0",
                "entry": "crash_plugin.operator:CrashOperator",
                "inputPorts": {"mode": "string"},
                "outputPorts": {"result": "json"},
                "paramSchema": {"type": "object"},
                "minCoreVersion": "0.2.0",
                "maxCoreVersion": "1.x",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    project = _v2_project()
    project["project"]["projectId"] = "crash-project"
    project["workflows"]["main"] = {
        "name": "Main",
        "inputs": {"mode": "string"},
        "outputs": {"result": "json"},
        "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {
                "nodeId": "crash",
                "kind": "operator",
                "operatorId": "vision.demo.crash",
            },
            {"nodeId": "output", "kind": "workflow_output"},
        ],
        "edges": [
            {"fromNode": "input", "fromPort": "mode", "toNode": "crash", "toPort": "mode"},
            {"fromNode": "crash", "fromPort": "result", "toNode": "output", "toPort": "result"},
        ],
        "layout": {"nodePositions": {}},
    }
    (projectDir := tmp_path / "project").mkdir()
    (projectDir / "project.json").write_text(json.dumps(project), encoding="utf-8")

    builtinsRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        pluginRootPaths=(str(builtinsRoot), str(pluginRoot)),
        maxConcurrentJobs=2,
    )
    try:
        loaded = service.LoadProject(
            runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
        )
        assert loaded.ok is True
        crashed = service.StartJob(
            runtime_pb2.StartJobRequest(
                project_id="crash-project", inputs_json=json.dumps({"mode": "crash"})
            ),
            None,
        )
        healthy = service.StartJob(
            runtime_pb2.StartJobRequest(
                project_id="crash-project", inputs_json=json.dumps({"mode": "ok"})
            ),
            None,
        )
        assert crashed.ok is True
        assert healthy.ok is True
        crashedStatus = waitForTerminal(service, crashed.job_id)
        healthyStatus = waitForTerminal(service, healthy.job_id)
        assert crashedStatus.status == "FAILED"
        assert healthyStatus.status == "COMPLETED"
        assert crashedStatus.pid > 0
        assert healthyStatus.pid > 0
        assert crashedStatus.pid != healthyStatus.pid
    finally:
        service.close()


def _compile_and_run(payload: dict[str, object], inputs: dict[str, object]) -> dict[str, object]:
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler().compile(document)
    runner = WorkflowRunner(compiled, {})
    result = runner.run(
        document.entryWorkflowId,
        inputs,
        RunContext.root("job", document.entryWorkflowId),
        CancellationToken(),
    )
    return result.outputs


def testRepeatExecutesExactCount() -> None:
    payload = _v2_project()
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {"value": "object"},
            "outputs": {"value": "object"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "repeat",
                    "kind": "loop",
                    "inputPorts": {"value": "object"},
                    "outputPorts": {"value": "object"},
                    "loop": {
                        "mode": "repeat",
                        "bodyWorkflowId": "body",
                        "repeatCount": 3,
                        "maxIterations": 3,
                    },
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "value", "toNode": "repeat", "toPort": "value"},
                {"fromNode": "repeat", "fromPort": "value", "toNode": "output", "toPort": "value"},
            ],
            "layout": {"nodePositions": {}},
        },
        "body": {
            "name": "Body",
            "inputs": {"value": "object"},
            "outputs": {"value": "object"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "value", "toNode": "output", "toPort": "value"}
            ],
            "layout": {"nodePositions": {}},
        },
    }
    payload["workflowOrder"] = ["main", "body"]
    assert _compile_and_run(payload, {"value": "preserved"}) == {"value": "preserved"}


def testForEachPreservesOrder() -> None:
    payload = _v2_project()
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {"items": "list"},
            "outputs": {"results": "list"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "foreach",
                    "kind": "loop",
                    "inputPorts": {"items": "list"},
                    "outputPorts": {"results": "list"},
                    "loop": {
                        "mode": "foreach",
                        "bodyWorkflowId": "body",
                        "maxIterations": 3,
                    },
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "items", "toNode": "foreach", "toPort": "items"},
                {"fromNode": "foreach", "fromPort": "results", "toNode": "output", "toPort": "results"},
            ],
            "layout": {"nodePositions": {}},
        },
        "body": {
            "name": "Body",
            "inputs": {"item": "object", "index": "integer"},
            "outputs": {"item": "object"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "item", "toNode": "output", "toPort": "item"}
            ],
            "layout": {"nodePositions": {}},
        },
    }
    payload["workflowOrder"] = ["main", "body"]
    assert _compile_and_run(payload, {"items": ["a", "b", "c"]}) == {
        "results": [{"item": "a"}, {"item": "b"}, {"item": "c"}]
    }
