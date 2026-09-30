"""Snapshot identity/provenance regressions independent of Runtime execution."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.preview import store as module
from emo_master.apps.runtime.preview.store import PreviewAssetStore, PreviewSnapshotWriter


def capture(root, job, pixels=7, frame=True, outputs=None):
    writer = PreviewSnapshotWriter(root / job, jobId=job, projectRevision=3)
    node = SimpleNamespace(nodeId="source", outputPorts={"image": "image", "frame": "bbox2d"})
    values = {"image": np.full((3, 4), pixels, np.uint8)}
    if frame:
        values["frame"] = {"identity": job}
    writer.capture(node, values if outputs is None else outputs,
                   SimpleNamespace(workflowId="main", iterationPath=(0,), nodeRunId="same-node-label"))
    return writer


def testUniqueCaptureExpiresOldIdentityAndNeverMixesCompanionFrame(tmp_path):
    first = capture(tmp_path, "job-a")
    assets = PreviewAssetStore(tmp_path / "assets")
    try:
        assets.promote(first.root, "project")
        before = assets.listSources("project", "main", "source", [])[0]
        assert before.originJobId == "job-a" and before.originProjectRevision == 3
        assert before.captureId
        assert assets.companionPayload(before.assetId) == {"identity": "job-a"}
        second = capture(tmp_path, "job-b", pixels=9, frame=False)
        assets.promote(second.root, "project")
        after = assets.listSources("project", "main", "source", [])[0]
        assert after.assetId != before.assetId and after.captureId != before.captureId
        assert after.originJobId == "job-b"
        assert assets.resolve(before.assetId) is None
        assert not before.path.exists()
        with pytest.raises(KeyError):
            assets.readBytes(before.assetId)
        assert assets.companionPayload(after.assetId) is None
        assert np.max(assets.readImage(after.assetId)) == 9
    finally:
        assets.close()


def testMissingOutputRetainsPreviousOriginRatherThanCurrentRun(tmp_path):
    first = capture(tmp_path, "job-a")
    second = capture(tmp_path, "job-b", outputs={"frame": {"identity": "new-frame"}})
    assets = PreviewAssetStore(tmp_path / "assets")
    try:
        assets.promote(first.root, "project")
        original = assets.listSources("project", "main", "source", [])[0]
        assets.promote(second.root, "project")
        retained = assets.listSources("project", "main", "source", [])[0]
        assert retained.assetId == original.assetId and retained.originJobId == "job-a"
        assert assets.companionPayload(retained.assetId) is None
    finally:
        assets.close()


def testOldIndexWithoutOriginRemainsReadableAndUnknown(tmp_path):
    writer = capture(tmp_path, "job-a")
    index = writer.root / "index.json"
    payload = json.loads(index.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        for field in ("originJobId", "originProjectRevision", "captureId"):
            entry.pop(field)
    index.write_text(json.dumps(payload), encoding="utf-8")
    assets = PreviewAssetStore(tmp_path / "assets")
    try:
        assets.promote(writer.root, "project")
        source = assets.listSources("project", "main", "source", [])[0]
        assert not source.originJobId and not source.captureId and source.originProjectRevision == 0
        assert np.max(assets.readImage(source.assetId)) == 7
        assert assets.companionPayload(source.assetId) is None
        reopened = PreviewAssetStore(tmp_path / "assets")
        try:
            restored = reopened.listSources("project", "main", "source", [])[0]
            assert restored.assetId == source.assetId and not restored.originJobId
        finally:
            reopened.close()
    finally:
        assets.close()


def testFailedIndexPromotionKeepsOldBytesAndRemovesUnpublishedCopies(tmp_path, monkeypatch):
    first = capture(tmp_path, "job-a")
    second = capture(tmp_path, "job-b", pixels=33)
    assets = PreviewAssetStore(tmp_path / "assets")
    try:
        assets.promote(first.root, "project")
        original = assets.listSources("project", "main", "source", [])[0]
        files = set((assets.root / "project" / "assets").iterdir())
        atomic = module._atomicJson

        def failIndex(path, value):
            if path == assets.root / "project" / "index.json":
                raise OSError("injected index write failure")
            return atomic(path, value)

        monkeypatch.setattr(module, "_atomicJson", failIndex)
        with pytest.raises(OSError, match="injected"):
            assets.promote(second.root, "project")
        assert set((assets.root / "project" / "assets").iterdir()) == files
        assert np.max(assets.readImage(original.assetId)) == 7
        assert assets.listSources("project", "main", "source", [])[0].originJobId == "job-a"
    finally:
        assets.close()


def testPublishedCaptureOwnsIdentityEvenWhenQuotaMaintenanceFails(tmp_path, monkeypatch):
    first = capture(tmp_path, "job-a")
    second = capture(tmp_path, "job-b", pixels=19)
    assets = PreviewAssetStore(tmp_path / "assets")
    try:
        assets.promote(first.root, "project")
        before = assets.listSources("project", "main", "source", [])[0]
        monkeypatch.setattr(assets, "_enforceLimits", lambda: (_ for _ in ()).throw(OSError("quota failure")))
        with pytest.raises(OSError, match="quota failure"):
            assets.promote(second.root, "project")
        after = assets.listSources("project", "main", "source", [])[0]
        assert after.originJobId == "job-b" and after.assetId != before.assetId
        assert assets.resolve(before.assetId) is None and not before.path.exists()
        assert np.max(assets.readImage(after.assetId)) == 19
        after.path.unlink()
        assert assets.listSources("project", "main", "source", []) == []
    finally:
        assets.close()


@pytest.mark.parametrize("failure", ["companion", "index"])
def testWriterFailureAfterImageWritePreservesCommittedCapture(tmp_path, monkeypatch, failure):
    writer = capture(tmp_path, "job-a", pixels=7)
    originalIndex = (writer.root / "index.json").read_bytes()
    originalEntries = dict(writer._entries)
    originalFiles = {path: path.read_bytes() for path in (writer.root / "assets").iterdir()}
    atomic = module._atomicJson

    def fail(path, value):
        if (failure == "index" and path == writer.root / "index.json") or (
                failure == "companion" and path.parent.name == "assets"):
            raise OSError("injected after image write")
        return atomic(path, value)

    monkeypatch.setattr(module, "_atomicJson", fail)
    with pytest.raises(OSError, match="after image"):
        writer.capture(SimpleNamespace(nodeId="source", outputPorts={"image": "image", "frame": "bbox2d"}),
            {"image": np.full((3, 4), 99, np.uint8), "frame": {"identity": "new-invocation"}},
            SimpleNamespace(workflowId="main", iterationPath=(1,), nodeRunId="later"))
    assert (writer.root / "index.json").read_bytes() == originalIndex
    assert writer._entries == originalEntries
    assert {path: path.read_bytes() for path in (writer.root / "assets").iterdir()} == originalFiles
    monkeypatch.setattr(module, "_atomicJson", atomic)
    assets = PreviewAssetStore(tmp_path / "published")
    try:
        assets.promote(writer.root, "project")
        source = assets.listSources("project", "main", "source", [])[0]
        assert np.max(assets.readImage(source.assetId)) == 7
        assert assets.companionPayload(source.assetId) == {"identity": "job-a"}
    finally:
        assets.close()


def testWriterPerCapturePathsRemainBoundedByLatestPorts(tmp_path):
    writer = capture(tmp_path, "job-a")
    node = SimpleNamespace(nodeId="source", outputPorts={"image": "image", "frame": "bbox2d"})
    identities = set()
    for iteration in range(8):
        writer.capture(node, {"image": np.full((3, 4), iteration, np.uint8), "frame": {"iteration": iteration}},
            SimpleNamespace(workflowId="main", iterationPath=(iteration,)))
        identities.add(writer._entries[("main", "source", "image")]["captureId"])
        assert len(list((writer.root / "assets").iterdir())) == 2
        assert len(writer._entries) == 2
    assert len(identities) == 8


def testFailedStagingDeletionBlocksFurtherCaptureGrowthUntilRetired(tmp_path, monkeypatch):
    from pathlib import Path
    writer = capture(tmp_path, "job-a")
    oldFiles = set((writer.root / "assets").iterdir())
    unlink = Path.unlink

    def failOld(path, *args, **kwargs):
        if path in oldFiles:
            raise PermissionError("held old capture")
        return unlink(path, *args, **kwargs)

    node = SimpleNamespace(nodeId="source", outputPorts={"image": "image", "frame": "bbox2d"})
    monkeypatch.setattr(Path, "unlink", failOld)
    with pytest.raises(OSError, match="cleanup pending"):
        writer.capture(node, {"image": np.full((3, 4), 10, np.uint8), "frame": {"new": True}},
                       SimpleNamespace(workflowId="main", iterationPath=(1,)))
    committedIndex = (writer.root / "index.json").read_bytes()
    bounded = set((writer.root / "assets").iterdir())
    assert len(bounded) == 4 and writer._pendingCleanup == oldFiles
    for _ in range(8):
        with pytest.raises(OSError, match="new capture skipped"):
            writer.capture(node, {"image": np.full((3, 4), 20, np.uint8)},
                           SimpleNamespace(workflowId="main", iterationPath=(2,)))
        assert set((writer.root / "assets").iterdir()) == bounded
        assert (writer.root / "index.json").read_bytes() == committedIndex
    monkeypatch.setattr(Path, "unlink", unlink)
    writer.capture(node, {"image": np.full((3, 4), 30, np.uint8), "frame": {"last": True}},
                   SimpleNamespace(workflowId="main", iterationPath=(2,)))
    assert not writer._pendingCleanup
    assert len(list((writer.root / "assets").iterdir())) == 2


def testFailedPublishedDeletionBlocksGrowthAndReconstructsOwnershipOnReopen(tmp_path, monkeypatch):
    from pathlib import Path
    first, second, third = [capture(tmp_path, f"job-{i}", pixels=i + 3) for i in range(3)]
    assets = PreviewAssetStore(tmp_path / "published")
    assets.promote(first.root, "project")
    oldFiles = set((assets.root / "project" / "assets").iterdir())
    unlink = Path.unlink

    def failOld(path, *args, **kwargs):
        if path in oldFiles:
            raise PermissionError("held old asset")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failOld)
    with pytest.raises(OSError, match="cleanup pending"):
        assets.promote(second.root, "project")
    bounded = set((assets.root / "project" / "assets").iterdir())
    assert len(bounded) == 4
    assets.close()
    reopened = PreviewAssetStore(tmp_path / "published")
    try:
        for _ in range(8):
            with pytest.raises(OSError, match="new promotion skipped"):
                reopened.promote(third.root, "project")
            assert set((assets.root / "project" / "assets").iterdir()) == bounded
        assert reopened.listSources("project", "main", "source", [])[0].originJobId == "job-1"
        monkeypatch.setattr(Path, "unlink", unlink)
        reopened.promote(third.root, "project")
        assert not reopened._pendingCleanup
        assert len(list((assets.root / "project" / "assets").iterdir())) == 2
        assert reopened.listSources("project", "main", "source", [])[0].originJobId == "job-2"
    finally:
        reopened.close()


def testFailedAtomicTempDeletionIsOwnedBeforeAnotherStagingAllocation(tmp_path, monkeypatch):
    from pathlib import Path
    writer = capture(tmp_path, "job-a")
    oldIndex = (writer.root / "index.json").read_bytes()
    unlink, fsync = Path.unlink, module.os.fsync

    def failTemp(path, *args, **kwargs):
        if path.suffix == ".tmp" and path.exists():
            raise PermissionError("held atomic temp")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failTemp)
    monkeypatch.setattr(module.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync failure")))
    node = SimpleNamespace(nodeId="source", outputPorts={"image": "image"})
    with pytest.raises(OSError, match="cleanup pending"):
        writer.capture(node, {"image": np.full((3, 4), 40, np.uint8)},
                       SimpleNamespace(workflowId="main", iterationPath=(1,)))
    assert writer._pendingCleanup and all(path.suffix == ".tmp" for path in writer._pendingCleanup)
    bounded = set((writer.root / "assets").iterdir())
    for _ in range(5):
        with pytest.raises(OSError, match="new capture skipped"):
            writer.capture(node, {"image": np.full((3, 4), 50, np.uint8)},
                           SimpleNamespace(workflowId="main", iterationPath=(2,)))
        assert set((writer.root / "assets").iterdir()) == bounded
        assert (writer.root / "index.json").read_bytes() == oldIndex
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(module.os, "fsync", fsync)
    writer.capture(node, {"image": np.full((3, 4), 60, np.uint8)},
                   SimpleNamespace(workflowId="main", iterationPath=(2,)))
    assert not writer._pendingCleanup
    assert len(list((writer.root / "assets").iterdir())) == 2
