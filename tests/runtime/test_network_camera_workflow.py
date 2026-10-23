import json
from uuid import uuid4

import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.contracts import trustedEntries
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.rtsp_camera import operator as rtsp
from tests.plugins.test_hikvision_audio_operator import localCamera
from tests.plugins.test_rtsp_camera_operator import Capture
from tests.runtime.network_camera_fixture import cameraProject
from tests.runtime.test_image_batch_while import _registry
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


def testCurrentProjectRoundtripAndCompilation(tmp_path):
    registry = _registry()
    document = cameraProject(registry)
    assert document.schemaVersion == "2.7" and document.devices.schemaVersion == "1.0"
    assert not document.devices.definitions
    path = tmp_path / "network-camera.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(path)
    assert reloaded == document
    WorkflowCompiler(operatorRegistry=registry).compile(reloaded)


@pytest.mark.parametrize("trigger", [False, True])
def testWorkflowPassesCameraImageAndConditionallyPlays(tmp_path, monkeypatch, trigger):
    registry, captures = _registry(), []
    def capture():
        item = Capture()
        captures.append(item)
        return item
    monkeypatch.setattr(rtsp.cv2, "VideoCapture", capture)
    with localCamera() as (url, calls, played):
        document = cameraProject(registry, url)
        compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
        runner = WorkflowRunner(compiled, registry)
        for job in ("first", "second"):
            result = runner.run("main", {"trigger": trigger}, RunContext.root(job, "main"), CancellationToken())
            assert np.all(result.outputs["image"] == 42)
            assert result.outputs["frame"]["coordinateSpace"]["imageWidth"] == 8
            assert result.outputs["triggered"] is trigger
        assert len(played) == (2 if trigger else 0)
        assert trigger or not calls
    assert len(captures) == 2 and all(item.released for item in captures)


def testNetworkOperatorsAreExplicitDebugOnly(runtime):
    admitted = trustedEntries(runtime.pluginScanResult.activeOperators)
    for operatorId in ("vision.io.rtsp_camera", "communication.camera.hikvision_audio"):
        assert admitted[operatorId]["singleNodeOnly"]
        assert admitted[operatorId]["executionWarning"]
    rows = json.loads(runtime.GetWorkflowDebugCapabilities(pb.OperatorDebugRequest(), None).snapshot_json)["operators"]
    assert all(not row["supported"] for row in rows if row["operatorId"] in {
        "vision.io.rtsp_camera", "communication.camera.hikvision_audio"})


def testSpawnedAudioDebugRequiresConfirmationAndPublishesResult(runtime):
    registry = runtime.pluginScanResult.activeOperators
    with localCamera() as (url, calls, played):
        document = cameraProject(registry, url)
        params = next(node.params for node in document.workflows["main"].nodes if node.nodeId == "audio")
        debug = DebugConnection(RuntimeClient(runtime))
        try:
            debug.open(document.model_dump(mode="json"), EditorKey(document.project.projectId, "main", "audio"),
                       "communication.camera.hikvision_audio")
            waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
            assert not calls
            prepared = debug.mutation("PrepareOperatorDebugInputs", params_json=json.dumps(params), inputs={})
            reply = runtime.ExecuteOperatorDebugNode(pb.OperatorDebugRequest(
                **debug.identity, request_id=uuid4().hex, input_set_id=prepared["inputSetId"],
                params_json=json.dumps(params), control_json="{}"), None)
            assert not reply.ok and reply.code == "E_DEBUG_CONFIRM_REQUIRED"
            assert not calls
            execution = debug.execute(params, {}, confirmSideEffects=True)
            def finished():
                result = debug.snapshot(execution["executionId"])["result"]
                return result if result["status"] != "RUNNING" else None
            result = waitFor(finished)
            assert result["status"] == "SUCCEEDED", result
            assert result["outputs"]["triggered"] is True
            assert len(played) == 1
        finally:
            debug.close()
    assert not runtime.operatorDebugManager.ownsResources()
