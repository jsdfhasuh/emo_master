import json
from multiprocessing import Pipe
from pathlib import Path
from queue import Queue
from threading import Event

import pytest

from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.worker_main import runJobProcess
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError
from emo_master.plugins.builtins._image_frame import defaultFrame
from tests.runtime.test_coordinate_snapshots import project
from tests.runtime.test_device_runtime import simulatedProject


@pytest.mark.parametrize("content,code", [(None, "E_INPUT_MISSING"), (b"1 invalid\n", "E_INPUT_SHAPE"),
                                         (b"\xff\xfe", "E_INPUT_SHAPE")])
def testWorkerRejectsBadCoordinateAdmissionBeforeStartedOrDeviceNodes(tmp_path, monkeypatch, content, code):
    path = tmp_path / "fixed.txt"
    if content is not None:
        path.write_bytes(content)
    document = simulatedProject()
    coordinates = project(path)
    for key in ('workflows', 'workflowOrder', 'entryWorkflowId'):
        document[key] = coordinates[key]
    # Even an inputless camera in the root cannot open before all reachable
    # session readers have passed admission (including the unselected branch).
    document["workflows"]["main"]["nodes"].insert(1, {
        "nodeId": "camera", "kind": "operator", "operatorId": "vision.io.simulated_camera",
        "deviceBindings": {"camera": {"deviceId": "camera"}}})
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(document), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    spec = JobProcessSpec("admission-job", str(snapshot), "main",
        inputsJson=json.dumps(dict(phase=1, frame=defaultFrame(10, 10).toPayload())),
        pluginRootPaths=(str(root / "src/emo_master/plugins"),), jobWorkspacePath=str(tmp_path),
        legacySnapshotPolicy="NONE", copyArtifacts=False, deviceIds=('camera',))
    queue = Queue()
    from emo_master.apps.runtime.devices.ipc import DeviceProxy
    monkeypatch.setattr(DeviceProxy, '_call', lambda *_args, **_kwargs:
        pytest.fail('device IPC must not run before coordinate admission'))
    parent, child = Pipe()
    with parent, child, pytest.raises(WorkflowExecutionError) as error:
        runJobProcess(spec, Event(), queue, devicePipe=child)
    assert error.value.code == code
    events = list(queue.queue)
    failed = [event for event in events if event["eventType"] == "job.failed"]
    assert len(failed) == 1 and failed[0]["code"] == code
    assert not any(event["eventType"] in {"job.started", "workflow.started", "node.started"} for event in events)
