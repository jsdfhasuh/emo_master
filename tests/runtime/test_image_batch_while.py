import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.blur.operator import BlurOperator


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/image_batch_while_portable"


def _project():
    return loadProjectDocument(EXAMPLE)


@pytest.mark.parametrize("filePath", [False, True])
def testExampleLoadsThroughRuntimeProjectEntry(tmp_path, filePath):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        path = EXAMPLE / "image-batch-while.emoproj" if filePath else EXAMPLE
        reply = service.LoadProject(runtime_pb2.LoadProjectRequest(project_path=str(path)), None)
        assert reply.ok, reply.message
        assert service.loadedDocument.entryWorkflowId == "main"
        assert set(service.loadedDocument.workflows) == {"main", "condition", "body"}
    finally:
        service.close()


def _registry():
    scan = PluginRegistry("0.6.1").scan(ROOT / "src/emo_master/plugins/builtins")
    assert not scan.rejectedOperators
    return scan.activeOperators


@pytest.mark.parametrize("count", [1, 3])
def testRealWhileProcessesEveryImageAndResetsForNewRun(tmp_path, monkeypatch, count):
    images = tmp_path / "images"
    images.mkdir()
    for index in range(count):
        assert cv2.imwrite(str(images / f"{index}.png"), np.full((8, 12, 3), index, np.uint8))
    registry = _registry()
    document = prepareRuntimeProject(_project(), tmp_path, registry)
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    processed = []
    execute = BlurOperator.executeNode

    def record(self, inputs, params, context):
        processed.append(int(inputs["image"][0, 0, 0]))
        return execute(self, inputs, params, context)

    monkeypatch.setattr(BlurOperator, "executeNode", record)
    runner = WorkflowRunner(compiled, registry)
    for job in ("first", "second"):
        result = runner.run("main", {}, RunContext.root(job, "main"), CancellationToken())
        assert result.outputs == {"hasNext": False}
        assert runner._lifecycleOperators == {}
    assert processed == list(range(count)) * 2


def testDirectoryResolvedRelativeToProjectAndDraftUnchanged(tmp_path):
    (tmp_path / "images").mkdir()
    document = _project()
    prepared = prepareRuntimeProject(document, tmp_path, _registry())
    assert document.workflows["body"].nodes[1].params["folderPath"] == "images"
    assert prepared.workflows["body"].nodes[1].params["folderPath"] == str((tmp_path / "images").resolve())


def testMissingInputDirectoryRejected(tmp_path):
    with pytest.raises(ValueError, match="input directory not found"):
        prepareRuntimeProject(_project(), tmp_path, _registry())


def testOutputCannotOverwriteBatchInputDirectory(tmp_path):
    (tmp_path / "images").mkdir()
    payload = json.loads((EXAMPLE / "image-batch-while.emoproj").read_text())
    payload["workflows"]["body"]["nodes"].append({
        "nodeId": "save", "kind": "operator", "operatorId": "vision.io.image_saver",
        "params": {"outputPath": "images/overwrite.png"},
    })
    with pytest.raises(ValueError, match="output overlaps project input"):
        prepareRuntimeProject(ProjectDocument.model_validate(payload), tmp_path, _registry())
