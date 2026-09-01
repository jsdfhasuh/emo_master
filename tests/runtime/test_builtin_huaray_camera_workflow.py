from __future__ import annotations

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins._huaray_imv import resetImvApiForTests
from emo_master.plugins.builtins.blob_analysis.operator import BlobAnalysisOperator
from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
from emo_master.plugins.builtins.threshold.operator import ThresholdOperator
from tests.plugins.test_huaray_camera_operator import _FakeImvApi


class _WorkflowCameraOperator(HuarayCameraOperator):
    api: _FakeImvApi

    def __init__(self) -> None:
        super().__init__(lambda: type(self).api)


def testFakeCameraThresholdBlobWorkflowReusesAndReleasesCamera() -> None:
    resetImvApiForTests()
    api = _FakeImvApi()
    _WorkflowCameraOperator.api = api
    registry = {
        "vision.io.huaray_camera": _WorkflowCameraOperator,
        "vision.preprocess.threshold": ThresholdOperator,
        "vision.analysis.blob": BlobAnalysisOperator,
    }
    payload = {
        "schemaVersion": "2.1",
        "project": {
            "projectId": "camera-workflow",
            "name": "Camera Workflow",
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
                "outputs": {"blobs": "blobCollection"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "camera",
                        "kind": "operator",
                        "operatorId": "vision.io.huaray_camera",
                        "params": {
                            "selectionMode": "ip",
                            "ipAddress": "192.168.1.10",
                            "triggerMode": "freeRun",
                            "retryCount": 0,
                        },
                    },
                    {
                        "nodeId": "threshold",
                        "kind": "operator",
                        "operatorId": "vision.preprocess.threshold",
                        "params": {"mode": "fixed", "threshold": 10},
                    },
                    {
                        "nodeId": "blob",
                        "kind": "operator",
                        "operatorId": "vision.analysis.blob",
                        "params": {"minArea": 1, "includeContour": True},
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {"fromNode": "camera", "fromPort": "image", "toNode": "threshold", "toPort": "image"},
                    {"fromNode": "camera", "fromPort": "frame", "toNode": "threshold", "toPort": "frame"},
                    {"fromNode": "camera", "fromPort": "image", "toNode": "blob", "toPort": "image"},
                    {"fromNode": "threshold", "fromPort": "mask", "toNode": "blob", "toPort": "mask"},
                    {"fromNode": "threshold", "fromPort": "frame", "toNode": "blob", "toPort": "frame"},
                    {"fromNode": "blob", "fromPort": "blobs", "toNode": "output", "toPort": "blobs"},
                ],
            }
        },
    }
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    events: list[dict[str, object]] = []
    runner = WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(event),
    )

    result = runner.run(
        "main",
        {},
        RunContext.root("camera-job", "main"),
        CancellationToken(),
    )

    assert len(result.outputs["blobs"]["items"]) == 1
    assert result.outputs["blobs"]["coordinateSpace"]["sourceId"] == "huaray-imv:SN-FAKE-001"
    assert api.openCount == 1
    assert (api.stopCount, api.closeCount, api.destroyCount) == (1, 1, 1)
    cameraLogs = [
        event
        for event in events
        if event["eventType"] == "node.log"
        and getattr(event["context"], "callerNodeId", "") == "camera"
    ]
    assert [event["payload"]["phase"] for event in cameraLogs] == [
        "lifecycle",
        "execute",
        "execute",
        "lifecycle",
        "lifecycle",
    ]
    assert all(
        event["payload"]["operatorId"] == "vision.io.huaray_camera"
        for event in cameraLogs
    )
    assert not any("image" in str(event["payload"]).casefold() for event in cameraLogs)
    resetImvApiForTests()
