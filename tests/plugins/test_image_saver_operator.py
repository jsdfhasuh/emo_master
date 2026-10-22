from pathlib import Path
import json

import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator


def testManifestMatchesOptionalInputContract():
    import emo_master.plugins.builtins.image_saver.operator as module

    manifest = json.loads(Path(module.__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    for field in ("version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[field] == getattr(ImageSaverOperator.meta, field)
    assert manifest["inputPorts"]["fileName"] == {"type": "string", "required": False, "nullable": False}


def testImageSaverWritesImageToPath(tmp_path: Path) -> None:
    outputPath = tmp_path / "saved.png"
    image = np.zeros((24, 24, 3), dtype=np.uint8)
    image[:, :] = (10, 20, 30)

    operatorInstance = ImageSaverOperator()
    result = operatorInstance.executeNode(
        inputs={"image": image},
        params={"outputPath": str(outputPath), "overwrite": True},
        runtimeContext={},
    )

    assert result["status"] == "ok"
    assert outputPath.exists()
    assert result["outputs"]["result"] == {"saved": True, "path": str(outputPath)}
    np.testing.assert_array_equal(cv2.imread(str(outputPath)), image)


def testImageSaverRejectsOverwriteWhenDisabled(tmp_path: Path) -> None:
    outputPath = tmp_path / "locked.png"
    outputPath.write_bytes(b"existing")
    image = np.zeros((16, 16, 3), dtype=np.uint8)

    operatorInstance = ImageSaverOperator()
    result = operatorInstance.executeNode(
        inputs={"image": image},
        params={"outputPath": str(outputPath), "overwrite": False},
        runtimeContext={},
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_OUTPUT_EXISTS"
    assert outputPath.read_bytes() == b"existing"


@pytest.mark.parametrize("fileName,expected", [
    ("frame_0001.png", "frame_0001.png"),
    ("frame_0002", "frame_0002.png"),
    ("target.bmp", "target.bmp"),
    ("\u4eba\u5458 003.png", "\u4eba\u5458 003.png"),
])
def testInputFileNameUsesConfiguredDirectoryAndOptionalExtension(tmp_path, fileName, expected):
    output = tmp_path / "\u88c1\u526a\u56fe" / expected
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    result = ImageSaverOperator().executeNode(
        {"image": image, "fileName": fileName},
        {"outputPath": str(output.parent / "fallback.png")}, {},
    )
    assert result["status"] == "ok", result
    assert result["outputs"]["result"] == {"saved": True, "path": str(output)}
    saved = cv2.imdecode(np.frombuffer(output.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(saved, image)
    assert not (output.parent / "fallback.png").exists()


def testSuccessiveInputsSaveSeparateImages(tmp_path):
    operator = ImageSaverOperator()
    for index in range(3):
        image = np.full((8, 12, 3), index * 40, np.uint8)
        result = operator.executeNode(
            {"image": image, "fileName": f"frame_{index}.png"},
            {"outputPath": str(tmp_path / "fallback.png"), "overwrite": False}, {},
        )
        assert result["status"] == "ok", result
    assert len(list(tmp_path.glob("*.png"))) == 3
    for index in range(3):
        assert np.all(cv2.imread(str(tmp_path / f"frame_{index}.png")) == index * 40)


@pytest.mark.parametrize("fileName,code", [
    (None, "E_INPUT_TYPE"), (12, "E_INPUT_TYPE"),
    ("", "E_INPUT_SHAPE"), ("  ", "E_INPUT_SHAPE"), ("..", "E_INPUT_SHAPE"),
    ("../escape.png", "E_INPUT_SHAPE"), ("sub/escape.png", "E_INPUT_SHAPE"),
    (r"sub\escape.png", "E_INPUT_SHAPE"), (r"C:\escape.png", "E_INPUT_SHAPE"),
    ("bad\x00.png", "E_INPUT_SHAPE"), ("bad?.png", "E_INPUT_SHAPE"),
])
def testInvalidInputFileNameDoesNotFallBackOrWrite(tmp_path, fileName, code):
    result = ImageSaverOperator().executeNode(
        {"image": np.zeros((8, 12, 3), np.uint8), "fileName": fileName},
        {"outputPath": str(tmp_path / "outputs/fallback.png")}, {},
    )
    assert result["status"] == "error" and result["error"]["code"] == code, result
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize("overwrite", [False, True])
def testDynamicNameHonorsOverwriteAndReportsActualTarget(tmp_path, overwrite):
    target = tmp_path / "actual.png"
    target.write_bytes(b"original")
    result = ImageSaverOperator().executeNode(
        {"image": np.zeros((8, 12, 3), np.uint8), "fileName": "actual.png"},
        {"outputPath": str(tmp_path / "fallback.png"), "overwrite": overwrite}, {},
    )
    assert not (tmp_path / "fallback.png").exists()
    if overwrite:
        assert result["status"] == "ok", result
        assert result["outputs"]["result"]["path"] == str(target)
        assert cv2.imread(str(target)) is not None
    else:
        assert result["error"]["code"] == "E_OUTPUT_EXISTS"
        assert str(target) in result["error"]["message"]
        assert target.read_bytes() == b"original"


def testInvalidImageExtensionReturnsWriteFailure(tmp_path):
    result = ImageSaverOperator().executeNode(
        {"image": np.zeros((8, 12, 3), np.uint8), "fileName": "crop.unknown-format"},
        {"outputPath": str(tmp_path / "fallback.png")}, {},
    )
    assert result["error"]["code"] == "E_OUTPUT_WRITE_FAILED"
    assert list(tmp_path.iterdir()) == []


def testUnusableOutputDirectoryReturnsWriteFailure(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_bytes(b"existing file")
    result = ImageSaverOperator().executeNode(
        {"image": np.zeros((8, 12, 3), np.uint8), "fileName": "crop.png"},
        {"outputPath": str(parent / "fallback.png")}, {},
    )
    assert result["error"]["code"] == "E_OUTPUT_WRITE_FAILED"
    assert parent.read_bytes() == b"existing file"


def testNonOverwriteUsesExclusiveCreation(tmp_path, monkeypatch):
    target = tmp_path / "crop.png"
    encode = cv2.imencode

    def competing_writer(*args):
        encoded = encode(*args)
        target.write_bytes(b"other job")
        return encoded

    monkeypatch.setattr(cv2, "imencode", competing_writer)
    result = ImageSaverOperator().executeNode(
        {"image": np.zeros((8, 12, 3), np.uint8), "fileName": target.name},
        {"outputPath": str(tmp_path / "fallback.png"), "overwrite": False}, {},
    )
    assert result["error"]["code"] == "E_OUTPUT_EXISTS"
    assert target.read_bytes() == b"other job"
