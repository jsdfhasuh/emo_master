from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

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
