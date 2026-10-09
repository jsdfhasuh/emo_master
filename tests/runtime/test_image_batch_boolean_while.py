from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.blur.operator import BlurOperator
from tests.runtime.test_image_batch_while import _registry


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/image_batch_while_boolean"


def _project(mode):
    document = loadProjectDocument(EXAMPLE)
    if mode == "boolean":
        return document
    payload = document.toPayload()
    loop = next(n for n in payload["workflows"]["main"]["nodes"] if n["kind"] == "loop")["loop"]
    loop.pop("conditionMode")
    loop.pop("conditionPort")
    loop["conditionWorkflowId"] = "condition"
    payload["workflowOrder"] = ["main", "condition", "body"]
    payload["workflows"]["condition"] = {
        "name": "Legacy condition", "inputs": {"hasNext": "boolean"},
        "outputs": {"continue": "boolean"},
        "nodes": [{"nodeId": "input", "kind": "workflow_input"},
                  {"nodeId": "output", "kind": "workflow_output"}],
        "edges": [{"fromNode": "input", "fromPort": "hasNext", "toNode": "output", "toPort": "continue"}],
    }
    return ProjectDocument.model_validate(payload)


@pytest.mark.parametrize("mode", ["boolean", "legacy-workflow"])
@pytest.mark.parametrize("count", [1, 3])
def testBothConditionModesProcessLastImageAndResetOnNewJob(tmp_path, monkeypatch, mode, count):
    images = tmp_path / "images"
    images.mkdir()
    for index in range(count):
        assert cv2.imwrite(str(images / f"{index}.png"), np.full((8, 12, 3), index, np.uint8))
    registry = _registry()
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        prepareRuntimeProject(_project(mode), tmp_path, registry))
    processed = []
    execute = BlurOperator.executeNode

    def record(self, inputs, params, context):
        processed.append(int(inputs["image"][0, 0, 0]))
        return execute(self, inputs, params, context)

    monkeypatch.setattr(BlurOperator, "executeNode", record)
    started = []
    runner = WorkflowRunner(compiled, registry,
        eventPublisher=lambda **event: started.append(event["context"].workflowId)
        if event["eventType"] == "workflow.started" else None)
    for job in ("first", "second"):
        result = runner.run("main", {}, RunContext.root(job, "main"), CancellationToken())
        assert result.outputs == {"hasNext": False}
        assert runner._lifecycleOperators == {}
    assert processed == list(range(count)) * 2
    assert started.count("body") == count * 2
    assert started.count("condition") == (0 if mode == "boolean" else (count + 1) * 2)


@pytest.mark.parametrize("filePath", [False, True])
def testBooleanExampleLoadsThroughRuntimeWithoutConditionWorkflow(tmp_path, filePath):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        path = EXAMPLE / "image-batch-while-boolean.emoproj" if filePath else EXAMPLE
        reply = service.LoadProject(runtime_pb2.LoadProjectRequest(project_path=str(path)), None)
        assert reply.ok, reply.message
        assert set(service.loadedDocument.workflows) == {"main", "body"}
    finally:
        service.close()
