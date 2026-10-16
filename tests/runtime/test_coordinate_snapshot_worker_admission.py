import json
from pathlib import Path
from queue import Queue
from threading import Event

import pytest

from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.worker_main import runJobProcess
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError
from emo_master.plugins.builtins._image_frame import defaultFrame
from tests.runtime.test_coordinate_snapshots import project


@pytest.mark.parametrize("content,code", [(None, "E_INPUT_MISSING"), (b"1 invalid\n", "E_INPUT_SHAPE"),
                                         (b"\xff\xfe", "E_INPUT_SHAPE")])
def testWorkerRejectsBadCoordinateAdmissionBeforeStartedOrDeviceNodes(tmp_path, content, code):
    path = tmp_path / "fixed.txt"
    if content is not None:
        path.write_bytes(content)
    document = project(path)
    # Even an inputless camera in the root cannot open before all reachable
    # session readers have passed admission (including the unselected branch).
    document["workflows"]["main"]["nodes"].insert(1, {
        "nodeId": "camera", "kind": "operator", "operatorId": "vision.io.huaray_camera",
        "params": {"selectionMode": "cameraKey", "cameraKey": "not-a-real-device", "retryCount": 0}})
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(document), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    spec = JobProcessSpec("admission-job", str(snapshot), "main",
        inputsJson=json.dumps(dict(phase=1, frame=defaultFrame(10, 10).toPayload())),
        pluginRootPaths=(str(root / "src/emo_master/plugins"),), jobWorkspacePath=str(tmp_path),
        legacySnapshotPolicy="NONE", copyArtifacts=False)
    queue = Queue()
    with pytest.raises(WorkflowExecutionError) as error:
        runJobProcess(spec, Event(), queue)
    assert error.value.code == code
    events = list(queue.queue)
    failed = [event for event in events if event["eventType"] == "job.failed"]
    assert len(failed) == 1 and failed[0]["code"] == code
    assert not any(event["eventType"] in {"job.started", "workflow.started", "node.started"} for event in events)
