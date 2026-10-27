from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import cv2
import pytest

from emo_master.apps.runtime.preview import store as module
from emo_master.apps.runtime.preview.store import (
    PreviewAssetStore,
    PreviewSnapshotWriter,
)


def testLoopSnapshotKeepsLastSuccessfulIterationAndFullResolution(tmp_path: Path) -> None:
    staging = tmp_path / "job"
    writer = PreviewSnapshotWriter(staging)
    node = SimpleNamespace(nodeId="node-1", outputPorts={"image": "image"})
    first = np.full((7, 9), 10, dtype=np.uint8)
    last = np.full((7, 9), 240, dtype=np.uint8)
    writer.capture(
        node,
        {"image": first},
        SimpleNamespace(workflowId="main", iterationPath=(0,)),
    )
    writer.capture(
        node,
        {"image": last},
        SimpleNamespace(workflowId="main", iterationPath=(1,)),
    )

    index = json.loads(
        (staging / "preview_staging" / "index.json").read_text(encoding="utf-8")
    )
    assert len(index["entries"]) == 1
    assert index["entries"][0]["iterationPath"] == [1]

    store = PreviewAssetStore(tmp_path / "cache")
    try:
        store.promote(staging / "preview_staging", "project-key")
        assets = store.listSources("project-key", "main", "node-1", [])
        assert len(assets) == 1
        assert assets[0].iterationPath == (1,)
        restored = store.readImage(assets[0].assetId)
        assert restored.shape == (7, 9)
        assert np.array_equal(restored, last)
    finally:
        store.close()


def testPersistedPreviewAssetsAreProjectIsolated(tmp_path: Path) -> None:
    store = PreviewAssetStore(tmp_path / "cache")
    try:
        transient = store.addTransientImage(np.zeros((3, 4), dtype=np.uint8))
        assert store.isUsableByProject(transient.assetId, "any-project") is True
    finally:
        store.close()


def testPreviewCaptureAndPromotionWithLongWindowsPaths(tmp_path: Path) -> None:
    root = tmp_path / ("project-" + "x" * 80) / ("workspace-" + "y" * 80)
    writer = PreviewSnapshotWriter(root / "job")
    image = np.full((7, 9, 3), 123, dtype=np.uint8)
    writer.capture(SimpleNamespace(nodeId="source", outputPorts={"image": "image"}),
                   {"image": image}, SimpleNamespace(workflowId="main", iterationPath=()))
    store = PreviewAssetStore(root / "cache")
    try:
        store.promote(root / "job" / "preview_staging", "project-key")
        assets = store.listSources("project-key", "main", "source", [])
        assert len(assets) == 1
        assert len(str(assets[0].path)) > 260
        assert np.array_equal(store.readImage(assets[0].assetId), image)
    finally:
        store.close()


def testJobStagingPublishesCompleteImagesWithoutFsyncAndPromotionRemainsDurable(tmp_path, monkeypatch):
    syncs, events = [], []
    originalOs = module.os

    class OsProxy:
        def __getattr__(self, name):
            return getattr(originalOs, name)

        def fsync(self, fd):
            syncs.append(fd)
            return originalOs.fsync(fd)

    monkeypatch.setattr(module, "os", OsProxy())
    node = SimpleNamespace(nodeId="source", outputPorts={"image": "image", "frame": "bbox2d"})
    context = SimpleNamespace(workflowId="main", iterationPath=(1,), workflowRunId="workflow-run",
                              nodeRunId="node-run")
    image = np.arange(7 * 9 * 3, dtype=np.uint8).reshape(7, 9, 3)

    def publish(**event):
        entry = event["payload"]["entries"][0]
        frozen = cv2.imdecode(np.fromfile(writer.inspectionRoot / entry["relativePath"], np.uint8), -1)
        np.testing.assert_array_equal(frozen, image)
        events.append(event)

    writer = PreviewSnapshotWriter(tmp_path / "job", jobId="job", projectRevision=3, inspectionPublisher=publish)
    writer.capture(node, {"image": image, "frame": {"sequence": 1}}, context)
    assert not syncs and len(events) == 1
    row = writer._entries[("main", "source", "image")]
    live = events[0]["payload"]["entries"][0]
    assert row["captureId"] == live["captureId"]
    assert live["originJobId"] == "job" and live["originProjectRevision"] == 3
    assert live["nodeRunId"] == context.nodeRunId and live["iterationPath"] == [1]
    image[:] = 0
    store = PreviewAssetStore(tmp_path / "cache")
    try:
        store.promote(writer.root, "project")
        assert syncs, "persistent index commits must still flush to disk"
        asset = store.listSources("project", "main", "source", [])[0]
        restored = store.readImage(asset.assetId)
        np.testing.assert_array_equal(restored, np.arange(7 * 9 * 3, dtype=np.uint8).reshape(7, 9, 3))
        assert store.companionPayload(asset.assetId) == {"sequence": 1}
    finally:
        store.close()


@pytest.mark.parametrize("durable", [False, True])
def testAtomicWriteFailureRetainsPriorBytesAndCleansTemporaryFile(tmp_path, monkeypatch, durable):
    path = tmp_path / "snapshot.png"
    module._atomicBytes(path, b"previous", durable=durable)
    replace = Path.replace

    def fail(source, destination):
        if destination == path:
            assert source.read_bytes() == b"replacement"
            assert path.read_bytes() == b"previous"
            raise OSError("replacement failed")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        module._atomicBytes(path, b"replacement", durable=durable)
    assert path.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [path]
