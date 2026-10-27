import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.core.contracts.geometry2d import BBox2D, Detection2D, DetectionCollection
from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins.detection_archive import operator as archive


def inputs(count=2):
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    frame = defaultFrame(16, 12)
    items = tuple(Detection2D(str(i), 0, "face", 0.9, BBox2D(x, y, w, h, frame.coordinateSpace))
                  for i, (x, y, w, h) in enumerate([(2, 2, 6, 8), (10, 3, 4, 6)][:count]))
    return dict(image=image, overlay=255 - image, frame=frame.toPayload(),
                detections=DetectionCollection(items, frame.coordinateSpace).toPayload())


def testManifestMatchesMetadata():
    manifest = json.loads(Path(archive.__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    for name in ("operatorId", "version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[name] == getattr(archive.DetectionArchiveOperator.meta, name)


def testNoFacesCreatesNoDirectory(tmp_path):
    result = archive.DetectionArchiveOperator().executeNode(inputs(0), {"outputPath": str(tmp_path / "new/capture.jpg")}, {})
    assert result["outputs"]["result"]["reason"] == "no_detections"
    assert not list(tmp_path.iterdir())


def testAllCropsFullOverlayCooldownAndNonOverwritingRestart(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(archive, "monotonic", lambda: clock[0])
    params = {"outputPath": str(tmp_path / "capture.jpg"), "minIntervalMs": 2000}
    op = archive.DetectionArchiveOperator()
    data = inputs()
    result = op.executeNode(data, params, {})
    first = result["outputs"]
    assert first["saved"] and first["cropCount"] == 2
    progress = result["diagnostics"]["archiveProgress"]
    assert progress["stage"] == "completed" and progress["savedCropCount"] == 2
    assert progress["cropPaths"] == first["result"]["cropPaths"]
    assert progress["overlayPath"] == first["result"]["overlayPath"]
    for path, roi in zip(first["result"]["cropPaths"], [data["image"][2:10, 2:8], data["image"][3:9, 10:14]]):
        assert np.array_equal(cv2.imread(path), roi)
    assert cv2.imread(first["result"]["overlayPath"]).shape == data["image"].shape
    old = {p: p.read_bytes() for p in tmp_path.iterdir()}
    clock[0] += 1.9
    cooldown = op.executeNode(data, params, {})
    assert cooldown["outputs"]["result"]["reason"] == "cooldown"
    assert "archiveProgress" not in cooldown.get("diagnostics", {})
    noDetections = op.executeNode(inputs(0), params, {})
    assert noDetections["outputs"]["result"]["reason"] == "no_detections"
    assert "archiveProgress" not in noDetections.get("diagnostics", {})
    assert len(list(tmp_path.iterdir())) == 3
    clock[0] += 0.2
    resumed = op.executeNode(data, params, {})
    assert resumed["outputs"]["saved"]
    assert resumed["diagnostics"]["archiveProgress"]["savedCropCount"] == 2
    assert set(resumed["diagnostics"]["archiveProgress"]["cropPaths"]).isdisjoint(progress["cropPaths"])
    op.initOperator({})
    assert op.executeNode(data, params, {})["outputs"]["saved"]
    assert len(list(tmp_path.iterdir())) == 9
    assert all(p.read_bytes() == content for p, content in old.items())


@pytest.mark.parametrize('cancelAfter', [1, 2, 3])
def testCancellationDoesNotFinishRemainingSaves(tmp_path, monkeypatch, cancelAfter):
    token = CancellationToken()
    original = archive.ImageSaverOperator.executeNode
    calls = []
    def save(self, *args):
        result = original(self, *args)
        calls.append(result)
        if len(calls) == cancelAfter:
            token.cancel()
        return result
    monkeypatch.setattr(archive.ImageSaverOperator, "executeNode", save)
    with pytest.raises(CancellationRequested) as failure:
        archive.DetectionArchiveOperator().executeNode(inputs(), {"outputPath": str(tmp_path / "capture.jpg")},
            {"raiseIfCancellationRequested": token.raise_if_cancelled})
    assert len(list(tmp_path.iterdir())) == len(calls) == cancelAfter
    progress = failure.value.diagnostics['archiveProgress']
    assert progress['savedCropCount'] == min(cancelAfter, 2)
    savedPaths = list(progress['cropPaths'])
    if cancelAfter == 3:
        assert progress['stage'] == 'completed'
        savedPaths.append(progress['overlayPath'])
    else:
        assert progress['overlayPath'] is None
    assert set(map(Path, savedPaths)) == set(tmp_path.iterdir())


def testPartialCropFailureReportsSavedFilesAndTarget(tmp_path):
    data = inputs()
    detections = DetectionCollection.fromPayload(data['detections'])
    outside = Detection2D('outside', 0, 'part', .9, BBox2D(40, 40, 4, 6, detections.coordinateSpace))
    data['detections'] = DetectionCollection((detections.items[0], outside), detections.coordinateSpace).toPayload()
    result = archive.DetectionArchiveOperator().executeNode(data, {'outputPath': str(tmp_path / 'capture.jpg')}, {})
    assert result['status'] == 'error' and result['error']['code'] == 'E_INPUT_SHAPE'
    progress = result['diagnostics']['archiveProgress']
    assert progress['stage'] == 'crop' and progress['detectionId'] == 'outside'
    assert progress['detectionIndex'] == 2 and progress['savedCropCount'] == 1
    assert [Path(path) for path in progress['cropPaths']] == list(tmp_path.iterdir())
    assert progress['overlayPath'] is None


@pytest.mark.parametrize('failAt', [2, 3])
@pytest.mark.parametrize('throws', [False, True])
def testWriteFailureKeepsConfirmedPathsAndOriginalError(tmp_path, monkeypatch, failAt, throws):
    original = archive.ImageSaverOperator.executeNode
    calls = []
    error = OSError('access denied')
    error.diagnostics = {'cause': 'permission'}
    def save(self, inputs, *args):
        calls.append(inputs['fileName'])
        if len(calls) == failAt:
            if throws:
                raise error
            return dict(status='error', error=dict(code='E_OUTPUT_WRITE_FAILED', message='access denied'),
                        diagnostics={'cause': 'permission'})
        return original(self, inputs, *args)
    monkeypatch.setattr(archive.ImageSaverOperator, 'executeNode', save)
    op = archive.DetectionArchiveOperator()
    params = {'outputPath': str(tmp_path / 'capture.jpg')}
    if throws:
        with pytest.raises(OSError) as failure:
            op.executeNode(inputs(), params, {})
        assert failure.value is error
        diagnostics = error.diagnostics
    else:
        result = op.executeNode(inputs(), params, {})
        assert result['error'] == {'code': 'E_OUTPUT_WRITE_FAILED', 'message': 'access denied'}
        diagnostics = result['diagnostics']
    assert diagnostics['cause'] == 'permission'
    progress = diagnostics['archiveProgress']
    assert progress['stage'] == ('saveCrop' if failAt == 2 else 'saveOverlay')
    assert progress['savedCropCount'] == failAt - 1 and progress['overlayPath'] is None
    assert set(map(Path, progress['cropPaths'])) == set(tmp_path.iterdir())
    assert len(calls) == failAt


@pytest.mark.parametrize("port,value", [("image", "invalid"), ("overlay", np.zeros((2, 2, 3), np.uint8)),
                                       ("frame", defaultFrame(9, 9).toPayload()), ("detections", {})])
def testBadInputDoesNotSave(tmp_path, port, value):
    data = inputs()
    data[port] = value
    result = archive.DetectionArchiveOperator().executeNode(data, {"outputPath": str(tmp_path / "capture.jpg")}, {})
    assert result["status"] == "error"
    assert not list(tmp_path.iterdir())
