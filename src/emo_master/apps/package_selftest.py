from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from typing import Callable, Sequence

import cv2
import numpy as np

import emo_master
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.core.contracts.geometry2d import DetectionCollection
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxYoloSession
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator


_EXPECTED_MINIMUM_OPERATOR_COUNT = 49
_EXPECTED_EDITOR_UI_COUNT = 3
_ICON_EXAMPLES = {
    "vision.edge.canny": ("1ecbff9850c560ade9f1526e3439dfb6979d1a619f769e41eafadaa0c0537a37", "#0891b2"),
    "vision.io.huaray_camera": ("23dd34d1889baed1531108e3c48174d1fed081ec96c80446c51b3ddad5f62e70", "#059669"),
    "vision.inference.yolo": ("989138d077ff17257b7cb96f4db0d5f8e0cac694b04c62f04a2cf0b76a1cdcaf", "#db2777"),
}
_guiSelfTestApplication: object | None = None
_EXPECTED_MIGRATIONS = (
    "001_init.sql",
    "002_runtime_workflow.sql",
    "003_runtime_timestamps.sql",
    "004_global_counters.sql",
)
_TINY_YOLO_ONNX = (
    "CAg6qAEKUxIHb3V0cHV0MCIIQ29uc3RhbnQqPgoFdmFsdWUqMggBCAUIAhABSigAAIBAZmaG"
    "QAAAgEAzM4NAAACAQAAAgEAAAIBAAACAQGZmZj/NzEw/oAEEEhB0aW55X3lvbG9fZGV0ZWN0"
    "WiAKBmltYWdlcxIWChQIARIQCgIIAQoCCAMKAggICgIICGIdCgdvdXRwdXQwEhIKEAgBEgwK"
    "AggBCgIIBQoCCAJCBAoAEA1yFQoFbmFtZXMSDHswOiAnc2NyZXcnfXIOCgR0YXNrEgZkZXRl"
    "Y3RyEAoHZW5kMmVuZBIFRmFsc2VyFgoEYXJncxIOeydubXMnOiBGYWxzZX0="
)


def _packageRoot() -> Path:
    return Path(emo_master.__file__).resolve().parent


def _checkBuiltins() -> dict[str, object]:
    pluginRoot = _packageRoot() / "plugins" / "builtins"
    scan = PluginRegistry(coreVersion=emo_master.__version__).scan(pluginRoot)
    operatorIds = sorted(scan.activeOperators)
    if scan.rejectedOperators:
        rejected = ", ".join(sorted(scan.rejectedOperators))
        raise RuntimeError(f"built-in operator validation failed: {rejected}")
    if len(operatorIds) < _EXPECTED_MINIMUM_OPERATOR_COUNT:
        raise RuntimeError(
            "built-in operator collection is incomplete: "
            f"expected at least {_EXPECTED_MINIMUM_OPERATOR_COUNT}, got {len(operatorIds)}"
        )
    if "vision.inference.yolo" not in operatorIds:
        raise RuntimeError("vision.inference.yolo is missing")

    editorResources = sorted(
        str(path.relative_to(pluginRoot)).replace("\\", "/")
        for path in pluginRoot.glob("**/*.ui")
    )
    if len(editorResources) < _EXPECTED_EDITOR_UI_COUNT:
        raise RuntimeError(
            "built-in editor UI collection is incomplete: "
            f"expected at least {_EXPECTED_EDITOR_UI_COUNT}, got {len(editorResources)}"
        )
    return {
        "operatorCount": len(operatorIds),
        "editorUiCount": len(editorResources),
        "yoloVersion": scan.activeOperators["vision.inference.yolo"].manifest.version,
    }


def _checkDesignerStyle() -> dict[str, object]:
    qssPath = _packageRoot() / "apps" / "designer" / "ui" / "styles" / "app.qss"
    content = qssPath.read_text(encoding="utf-8")
    if "QMainWindow" not in content or "QMenuBar" not in content:
        raise RuntimeError("Designer QSS is incomplete")
    return {"path": str(qssPath), "bytes": qssPath.stat().st_size}


def _iconDescriptors():
    scan = PluginRegistry(coreVersion=emo_master.__version__).scan(_packageRoot() / "plugins" / "builtins")
    descriptors = {}
    for operatorId, (expectedSha, _accent) in _ICON_EXAMPLES.items():
        descriptor = scan.activeOperators.get(operatorId)
        if descriptor is None or descriptor.iconStatus != "ready" or descriptor.iconAsset is None:
            raise RuntimeError(f"required example icon is missing or invalid: {operatorId}")
        asset = descriptor.iconAsset
        if asset.sha256 != expectedSha or hashlib.sha256(asset.content).hexdigest() != expectedSha:
            raise RuntimeError(f"example icon digest mismatch: {operatorId}")
        descriptors[operatorId] = descriptor
    return descriptors


def _checkIconResources() -> dict[str, object]:
    # Core-only gate: no QApplication or Qt imports, including on failure.
    return {"examples": {operatorId: {"sha256": descriptor.iconAsset.sha256,
                                     "byteSize": len(descriptor.iconAsset.content),
                                     "mimeType": descriptor.iconAsset.mimeType}
                         for operatorId, descriptor in _iconDescriptors().items()}}


def _checkGuiIcons() -> dict[str, object]:
    global _guiSelfTestApplication
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from emo_master.apps.designer.main import configureHighDpi
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.designer.ui.operator_icon_provider import renderIconAsset

    if QApplication.instance() is None:
        configureHighDpi()
        _guiSelfTestApplication = QApplication([])
    samples = []
    for operatorId, descriptor in _iconDescriptors().items():
        expectedSha, accent = _ICON_EXAMPLES[operatorId]
        for scale in (1.0, 1.5, 2.0):
            rendered = renderIconAsset(descriptor.iconAsset, 24, scale)
            size = int(24 * scale)
            image = rendered.icon.pixmap(size, size).toImage()
            colored = sum(image.pixelColor(x, y).name() == accent and image.pixelColor(x, y).alpha() > 200
                          for y in range(image.height()) for x in range(image.width()))
            if (rendered.source != "custom" or rendered.sha256 != expectedSha
                    or rendered.pixelSize != size or rendered.nonTransparentPixels < 20
                    or colored < 8):
                raise RuntimeError(f"custom icon pixel assertion failed: {operatorId} at {scale}")
            samples.append({"operatorId": operatorId, "renderSource": rendered.source,
                            "sha256": rendered.sha256, "scale": scale, "pixelSize": rendered.pixelSize,
                            "nonTransparentPixels": rendered.nonTransparentPixels, "accentPixels": colored})
    return {"samples": samples}


def _checkMigrations() -> dict[str, object]:
    migrationRoot = _packageRoot() / "apps" / "runtime" / "context" / "migrations"
    for migrationName in _EXPECTED_MIGRATIONS:
        migrationPath = migrationRoot / migrationName
        if not migrationPath.is_file() or migrationPath.stat().st_size == 0:
            raise RuntimeError(f"SQL migration is missing or empty: {migrationName}")

    with TemporaryDirectory(
        prefix="emo-master-self-test-",
        ignore_cleanup_errors=True,
    ) as tempDirectory:
        databasePath = Path(tempDirectory) / "runtime.db"
        SqliteStore(databasePath).initialize()
        connection = sqlite3.connect(databasePath)
        try:
            versions = [
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schemaMigrations ORDER BY version"
                )
            ]
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            journalModeRow = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            journalMode = "" if journalModeRow is None else str(journalModeRow[0])
            if journalMode.lower() != "delete":
                raise RuntimeError(
                    f"failed to leave migration test database in DELETE mode: {journalMode}"
                )
        finally:
            connection.close()
    if versions != [1, 2, 3, 4]:
        raise RuntimeError(f"unexpected migration versions: {versions}")
    return {
        "files": list(_EXPECTED_MIGRATIONS),
        "versions": versions,
        "journalMode": journalMode,
    }


def _checkOnnxRuntime() -> dict[str, object]:
    import onnxruntime

    if onnxruntime.__version__ != "1.23.2":
        raise RuntimeError(
            f"expected onnxruntime 1.23.2, got {onnxruntime.__version__}"
        )
    with TemporaryDirectory(prefix="emo-master-onnx-self-test-") as tempDirectory:
        modelPath = Path(tempDirectory) / "tiny.onnx"
        modelPath.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
        result = OnnxYoloSession(modelPath).predict(
            np.zeros((4, 8), dtype=np.uint8),
            confidence=0.25,
            iou=0.45,
            imageSize=640,
            maxDetections=300,
            classes=(),
            agnosticNms=False,
        )
    if result.provider != "CPUExecutionProvider" or result.scores.size != 1:
        raise RuntimeError("tiny ONNX inference returned an unexpected result")
    return {
        "version": onnxruntime.__version__,
        "provider": result.provider,
        "inputShape": list(result.inputShape),
        "detectionCount": int(result.scores.size),
    }


def _runExternalInference(
    modelPath: Path,
    imagePath: Path,
    expectedDetections: int | None,
) -> dict[str, object]:
    image = cv2.imread(str(imagePath), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError(f"failed to read image: {imagePath}")
    result = YoloInferenceOperator().executeNode(
        {"image": image},
        {
            "modelPath": str(modelPath),
            "confidence": 0.25,
            "iou": 0.45,
            "drawOverlay": True,
        },
        {},
    )
    if result.get("status") != "ok":
        error = result.get("error", {})
        raise RuntimeError(f"external ONNX inference failed: {error}")

    outputs = result["outputs"]
    detections = DetectionCollection.fromPayload(outputs["detections"])
    overlay = np.asarray(outputs["overlay"])
    if overlay.shape != (image.shape[0], image.shape[1], 3):
        raise RuntimeError(
            f"unexpected overlay shape {overlay.shape} for image shape {image.shape}"
        )
    if expectedDetections is not None and len(detections.items) != expectedDetections:
        raise RuntimeError(
            f"expected {expectedDetections} detections, got {len(detections.items)}"
        )

    return {
        "modelPath": str(modelPath.resolve()),
        "imagePath": str(imagePath.resolve()),
        "imageShape": list(image.shape),
        "overlayShape": list(overlay.shape),
        "detectionCount": len(detections.items),
        "detections": [
            {
                "classId": detection.classId,
                "label": detection.label,
                "confidence": detection.confidence,
                "boxXyxy": [
                    detection.bbox.x,
                    detection.bbox.y,
                    detection.bbox.x + detection.bbox.width,
                    detection.bbox.y + detection.bbox.height,
                ],
            }
            for detection in detections.items
        ],
    }


def runSelfTest(
    *,
    modelPath: Path | None = None,
    imagePath: Path | None = None,
    expectedDetections: int | None = None,
    progressCallback: Callable[[str], None] | None = None,
    guiIcons: bool = False,
) -> dict[str, object]:
    checks: dict[str, object] = {}
    errors: list[str] = []
    checkFunctions: tuple[tuple[str, Callable[[], dict[str, object]]], ...] = (
        ("builtins", _checkBuiltins),
        ("operatorIcons", _checkIconResources),
        ("designerQss", _checkDesignerStyle),
        ("migrations", _checkMigrations),
        ("onnxruntime", _checkOnnxRuntime),
    )
    if guiIcons:
        checkFunctions += (("guiIcons", _checkGuiIcons),)
    for name, checkFunction in checkFunctions:
        if progressCallback is not None:
            progressCallback(f"checking:{name}")
        try:
            checks[name] = {"status": "ok", **checkFunction()}
        except Exception as err:
            message = f"{name}: {type(err).__name__}: {err}"
            checks[name] = {"status": "error", "message": str(err)}
            errors.append(message)

    inference: dict[str, object] | None = None
    if (modelPath is None) != (imagePath is None):
        errors.append("external inference requires both --model and --image")
    elif modelPath is not None and imagePath is not None:
        if progressCallback is not None:
            progressCallback("checking:externalInference")
        try:
            inference = {
                "status": "ok",
                **_runExternalInference(modelPath, imagePath, expectedDetections),
            }
        except Exception as err:
            message = f"inference: {type(err).__name__}: {err}"
            inference = {"status": "error", "message": str(err)}
            errors.append(message)
    elif expectedDetections is not None:
        errors.append("--expected-detections requires --model and --image")

    payload: dict[str, object] = {
        "status": "ok" if not errors else "error",
        "version": emo_master.__version__,
        "checks": checks,
        "errors": errors,
    }
    if inference is not None:
        payload["inference"] = inference
    return payload


def _argumentParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an Emo Master frozen package")
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.add_argument("--gui-icons", action="store_true", help="Require real Qt SVG rendering and example pixels")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--result-json", type=Path)
    parser.add_argument("--expected-detections", type=int)
    return parser


def runSelfTestCommand(arguments: Sequence[str]) -> int:
    parsed = _argumentParser().parse_args(list(arguments))

    def writeProgress(phase: str) -> None:
        if parsed.result_json is None:
            return
        parsed.result_json.parent.mkdir(parents=True, exist_ok=True)
        progress = {
            "status": "running",
            "version": emo_master.__version__,
            "phase": phase,
        }
        parsed.result_json.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    writeProgress("starting")
    payload = runSelfTest(
        modelPath=parsed.model,
        imagePath=parsed.image,
        expectedDetections=parsed.expected_detections,
        progressCallback=writeProgress,
        guiIcons=parsed.gui_icons,
    )
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if parsed.result_json is not None:
        parsed.result_json.parent.mkdir(parents=True, exist_ok=True)
        parsed.result_json.write_text(serialized + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(serialized)
    return 0 if payload["status"] == "ok" else 1
