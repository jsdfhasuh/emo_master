from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from emo_master.apps.designer.operator_editors import OperatorEditorManager


pytest.importorskip("PySide2")

from emo_master.plugins.builtins.histogram.editor import _safePixelCount


class _Settings:
    def value(self, _key: str, default: object = None) -> object:
        return default

    def setValue(self, _key: str, _value: object) -> None:
        return


class _Runtime:
    def __init__(self, assets: dict[str, bytes]) -> None:
        self.assets = assets

    def getOperatorEditorAsset(self, operatorId: str, version: str):
        _ = version
        content = self.assets[operatorId]
        return type(
            "Reply",
            (),
            {
                "ok": True,
                "content": content,
                "sha256": hashlib.sha256(content).hexdigest(),
                "message": "ok",
            },
        )()

    def listNodePreviewSources(
        self, projectId: str, workflowId: str, nodeId: str
    ) -> list[object]:
        _ = projectId, workflowId, nodeId
        return []


def testBuiltinCameraRoiAndHistogramUiControllersLoad(tmp_path: Path) -> None:
    pluginRoot = Path("src/emo_master/plugins/builtins")
    definitions = []
    assets: dict[str, bytes] = {}
    for directory in ("huaray_camera", "roi", "histogram"):
        manifest = json.loads(
            (pluginRoot / directory / "manifest.json").read_text(encoding="utf-8")
        )
        operatorId = manifest["operatorId"]
        assets[operatorId] = (
            pluginRoot / directory / manifest["editor"]["uiResource"]
        ).read_bytes()
        definitions.append(manifest)

    manager = OperatorEditorManager(
        runtimeClient=_Runtime(assets),
        settingsStore=_Settings(),
        applyParams=lambda _key, _params: True,
        appendLog=lambda _level, _message: None,
        cacheRoot=tmp_path / "ui-cache",
    )
    try:
        for index, manifest in enumerate(definitions):
            window = manager.open(
                projectId="project-a",
                workflowId="main",
                nodeId=f"node-{index}",
                operatorId=manifest["operatorId"],
                displayName=manifest["displayName"],
                schema=manifest["paramSchema"],
                values={},
                operatorDefinition={
                    "version": manifest["version"],
                    "editorSpec": manifest["editor"],
                    "editorIssues": [],
                },
            )
            controller = getattr(window, "_controller", None)
            assert controller is not None
            assert controller.__class__.__module__.startswith(
                "emo_master.plugins.builtins."
            )
        assert manager.count() == 3
    finally:
        manager.closeAll()
    assert manager.count() == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), ("bad", 0), (float("nan"), 0), (-1, 0), (12, 12)],
)
def testHistogramEditorToleratesMalformedPixelCount(
    value: object, expected: int
) -> None:
    assert _safePixelCount(value) == expected


@pytest.mark.parametrize("singleFrame", [False, True])
def testCameraEditorShowsOriginalStreamFailure(singleFrame) -> None:
    from types import SimpleNamespace

    from emo_master.plugins.builtins.huaray_camera.editor import HuarayCameraEditorController

    errors = []
    closed = []

    def fail(_sessionId):
        raise RuntimeError("E_CAMERA_DEVICE_NOT_FOUND: IMV_CreateHandle (-106)")

    controller = HuarayCameraEditorController()
    controller.context = SimpleNamespace(
        streamLivePreview=fail,
        closeLivePreview=closed.append,
        setError=errors.append,
        log=lambda *_args: None,
    )
    controller._sessionId = "failed-session"
    controller._singleFrame = singleFrame
    controller._streamFrames("failed-session")
    controller._pollFrame()
    assert len(errors) == 1
    assert "E_CAMERA_DEVICE_NOT_FOUND" in errors[0] and "-106" in errors[0]
    assert closed == ["failed-session"]
