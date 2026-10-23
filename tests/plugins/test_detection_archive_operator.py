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
    first = op.executeNode(data, params, {})["outputs"]
    assert first["saved"] and first["cropCount"] == 2
    for path, roi in zip(first["result"]["cropPaths"], [data["image"][2:10, 2:8], data["image"][3:9, 10:14]]):
        assert np.array_equal(cv2.imread(path), roi)
    assert cv2.imread(first["result"]["overlayPath"]).shape == data["image"].shape
    old = {p: p.read_bytes() for p in tmp_path.iterdir()}
    clock[0] += 1.9
    assert op.executeNode(data, params, {})["outputs"]["result"]["reason"] == "cooldown"
    assert len(list(tmp_path.iterdir())) == 3
    clock[0] += 0.2
    assert op.executeNode(data, params, {})["outputs"]["saved"]
    op.initOperator({})
    assert op.executeNode(data, params, {})["outputs"]["saved"]
    assert len(list(tmp_path.iterdir())) == 9
    assert all(p.read_bytes() == content for p, content in old.items())


def testCancellationDoesNotFinishRemainingSaves(tmp_path, monkeypatch):
    token = CancellationToken()
    original = archive.ImageSaverOperator.executeNode
    def save(self, *args):
        result = original(self, *args)
        token.cancel()
        return result
    monkeypatch.setattr(archive.ImageSaverOperator, "executeNode", save)
    with pytest.raises(CancellationRequested):
        archive.DetectionArchiveOperator().executeNode(inputs(), {"outputPath": str(tmp_path / "capture.jpg")},
            {"raiseIfCancellationRequested": token.raise_if_cancelled})
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("port,value", [("image", "invalid"), ("overlay", np.zeros((2, 2, 3), np.uint8)),
                                       ("frame", defaultFrame(9, 9).toPayload()), ("detections", {})])
def testBadInputDoesNotSave(tmp_path, port, value):
    data = inputs()
    data[port] = value
    result = archive.DetectionArchiveOperator().executeNode(data, {"outputPath": str(tmp_path / "capture.jpg")}, {})
    assert result["status"] == "error"
    assert not list(tmp_path.iterdir())
