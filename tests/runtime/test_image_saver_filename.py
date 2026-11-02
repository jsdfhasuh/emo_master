"""Local Runtime jobs and explicit debug writes with connected file names."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from tests.core.test_runtime_directory import namedSaver
from tests.runtime.production_fixture import productionProject
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_operator_debug_resources import connected, run, uploadImage
from tests.runtime.test_operator_debug_rpc import runtime as runtime


def testRuntimeCatalogPublishesOptionalFileName(runtime):
    operator = next(item for item in RuntimeClient(runtime).listOperators()
                    if item.operatorId == ImageSaverOperator.meta.operatorId)
    assert operator.version == "1.2.0"
    assert operator.inputPorts["fileName"] == "string"
    assert operator.inputPortSpecs["fileName"]["required"] is False
    assert operator.paramSchema == ImageSaverOperator.meta.paramSchema


@pytest.mark.parametrize('namingMode', ['fixed', 'unique'])
def testRuntimeJobsSaveDistinctConnectedFileNames(runtime, tmp_path, namingMode):
    root = tmp_path / "project"
    document = productionProject(root, mode="single")
    namedSaver(document, "outputs/fallback.png")
    saver = next(node for node in document.workflows['main'].nodes if node.operatorId == ImageSaverOperator.meta.operatorId)
    saver.params['namingMode'] = namingMode
    document = prepareRuntimeProject(document, root, runtime.pluginScanResult.activeOperators)
    (root / "project.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")
    reply = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None)
    assert reply.ok, reply.message
    names = (["frame_0001.png", "frame_0002", "\u4eba\u5458 003.png"] if namingMode == 'fixed'
             else ['same.png', 'same.png', 'same.png'])
    for name in names:
        started = runtime.StartJob(pb.StartJobRequest(
            project_id=document.project.projectId, workflow_id="main",
            inputs_json=json.dumps({"fileName": name}),
        ), None)
        assert started.status == "ACCEPTED", started.message
        status = waitForTerminal(runtime, started.job_id)
        assert status.status == "COMPLETED", status.message
    files = list((root / "outputs").glob("*.png"))
    if namingMode == 'fixed':
        assert {file.name for file in files} == {"frame_0001.png", "frame_0002.png", names[2]}
    else:
        assert len(files) == 3 and all(file.name.startswith('same_') for file in files)
    for file in files:
        saved = cv2.imdecode(np.frombuffer(file.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        assert saved is not None


def testExplicitDebugSaveAcceptsPreparedFileName(runtime, tmp_path):
    params = {"outputPath": str(tmp_path / "fallback.png"), "overwrite": False}
    with connected(runtime, ImageSaverOperator.meta.operatorId) as debug:
        image = uploadImage(debug)
        for name in ("first.png", "second.png"):
            record = run(debug, params, {"image": image, "fileName": {"inline": name}},
                         confirmSideEffects=True)
            assert record["status"] == "SUCCEEDED", record
            assert record["outputs"]["result"]["path"] == str(tmp_path / name)
            assert np.all(cv2.imread(str(tmp_path / name)) == 120)
    assert not (tmp_path / "fallback.png").exists()


@pytest.mark.parametrize('namingMode', ['fixed', 'unique'])
def testCancellationAfterSaveKeepsFileAndDoesNotRepeatWrite(tmp_path, namingMode):
    from emo_master.apps.runtime.execution.operator_executor import OperatorExecutor
    from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
    from emo_master.apps.runtime.workflow.context import RunContext
    from emo_master.core.workflow.models import CompiledNode
    token = CancellationToken()
    saved = []
    class CancelAfterSave(ImageSaverOperator):
        def executeNode(self, inputs, params, context):
            result = super().executeNode(inputs, params, context)
            saved.append(result)
            token.cancel()
            return result
    meta = ImageSaverOperator.meta
    node = CompiledNode('save', 'operator', meta.operatorId, dict(meta.inputPorts), dict(meta.outputPorts),
        {'outputPath': str(tmp_path / 'saved.png'), 'namingMode': namingMode}, paramSchema=dict(meta.paramSchema))
    context = RunContext.root('local-save', 'main', projectId='local-save').forNode('save')
    executor = OperatorExecutor({meta.operatorId: CancelAfterSave})
    try:
        with pytest.raises(CancellationRequested):
            executor.execute(node, {'image': np.full((8, 12, 3), 90, np.uint8)}, context, token)
        assert len(saved) == 1 and saved[0]['status'] == 'ok'
        path = Path(saved[0]['outputs']['result']['path'])
        assert list(tmp_path.iterdir()) == [path]
        assert np.all(cv2.imread(str(path)) == 90)
    finally:
        executor.closeSession(context)
