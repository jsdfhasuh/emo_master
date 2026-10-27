from pathlib import Path

import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.blur.operator import BlurOperator
from emo_master.plugins.builtins.video_loader.operator import VideoLoaderOperator
from tests.plugins.test_video_loader_operator import makeVideo
from tests.runtime.test_image_batch_while import _registry


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/video_sample_while/video-sample-while.emoproj"


def testExampleContainsDesignerPinsAndVideoEditor():
    from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel

    document = loadProjectDocument(EXAMPLE)
    for workflow in document.workflows.values():
        graph = FlowGraphModel()
        graph.loadProjectGraph(workflow.model_dump(mode="json"))
        for edge in graph.edges:
            assert edge.fromPort in graph.nodes[edge.fromNode].outputPorts
            assert edge.toPort in graph.nodes[edge.toNode].inputPorts
    assert graph.nodes["load"].paramSchema == VideoLoaderOperator.meta.paramSchema


def prepare(tmp_path, count=11):
    makeVideo(tmp_path / "video.avi", count=count)
    document = loadProjectDocument(EXAMPLE)
    node = next(node for node in document.workflows["body"].nodes if node.nodeId == "load")
    node.params = {"videoPath": "video.avi", "intervalSec": .25}
    registry = _registry()
    prepared = prepareRuntimeProject(document, tmp_path, registry)
    assert node.params["videoPath"] == "video.avi"
    preparedNode = next(node for node in prepared.workflows["body"].nodes if node.nodeId == "load")
    assert preparedNode.params["videoPath"] == str((tmp_path / "video.avi").resolve())
    return WorkflowCompiler(operatorRegistry=registry).compile(prepared), registry


@pytest.mark.parametrize("count,expected", [(1, [0]), (11, [0, 30, 50, 80, 100])])
def testRealWhilePassesEverySampleDownstreamAndRestarts(tmp_path, monkeypatch, count, expected):
    compiled, registry = prepare(tmp_path, count)
    processed = []
    execute = BlurOperator.executeNode

    def record(self, inputs, params, context):
        processed.append(float(np.mean(inputs["image"])))
        return execute(self, inputs, params, context)

    monkeypatch.setattr(BlurOperator, "executeNode", record)
    runner = WorkflowRunner(compiled, registry)
    for job in ("first", "second"):
        result = runner.run("main", {}, RunContext.root(job, "main"), CancellationToken())
        assert result.outputs == {"hasNext": False}
        assert runner._lifecycleOperators == {}
    assert processed == pytest.approx(expected * 2, abs=2)


def testStoppingWhileReleasesVideo(tmp_path, monkeypatch):
    compiled, registry = prepare(tmp_path)
    token = CancellationToken()
    captures = []
    read = VideoLoaderOperator.executeNode
    execute = BlurOperator.executeNode

    def track(self, inputs, params, context):
        result = read(self, inputs, params, context)
        captures.append(self._capture)
        return result

    def stop(self, inputs, params, context):
        token.cancel()
        return execute(self, inputs, params, context)

    monkeypatch.setattr(VideoLoaderOperator, "executeNode", track)
    monkeypatch.setattr(BlurOperator, "executeNode", stop)
    runner = WorkflowRunner(compiled, registry)
    with pytest.raises(CancellationRequested):
        runner.run("main", {}, RunContext.root("cancel", "main"), token)
    assert captures and all(not capture.isOpened() for capture in captures)
    assert runner._lifecycleOperators == {}
