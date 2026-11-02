from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import re
from threading import Barrier
from types import SimpleNamespace

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


@pytest.mark.parametrize('fileName,stem,suffix', [(None, '结果', '.png'), ('人员 01', '人员 01', '.png'),
                                               ('人员 01.bmp', '人员 01', '.bmp')])
def testUniqueNamingKeepsDirectoryAndPrefixWithoutReplacingImages(tmp_path, fileName, stem, suffix):
    directory = tmp_path / '中文 保存目录'
    directory.mkdir()
    base = directory / '结果.png'
    base.write_bytes(b'previous fixed image')
    paths = []
    for value in (30, 70):
        inputs = {'image': np.full((8, 12, 3), value, np.uint8)}
        if fileName is not None:
            inputs['fileName'] = fileName
        result = ImageSaverOperator().executeNode(inputs, {'outputPath': str(base), 'namingMode': 'unique'}, {})
        assert result['status'] == 'ok', result
        path = Path(result['outputs']['result']['path'])
        assert path.parent == directory and path.suffix == suffix
        assert re.fullmatch(re.escape(stem) + r'_\d{8}T\d{12}Z_[0-9a-f]{32}' + re.escape(suffix), path.name)
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        assert np.all(image == value)
        paths.append(path)
    assert paths[0] != paths[1]
    assert base.read_bytes() == b'previous fixed image'


def testConcurrentUniqueSavesKeepBothImages(tmp_path):
    barrier = Barrier(2)
    def save(value):
        barrier.wait(timeout=5)
        return ImageSaverOperator().executeNode({'image': np.full((8, 12, 3), value, np.uint8)},
            {'outputPath': str(tmp_path / 'same.png'), 'namingMode': 'unique'}, {})
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, (40, 80)))
    assert all(result['status'] == 'ok' for result in results), results
    paths = [Path(result['outputs']['result']['path']) for result in results]
    assert len(set(paths)) == 2 and len(list(tmp_path.iterdir())) == 2
    for value, path in zip((40, 80), paths):
        assert np.all(cv2.imread(str(path)) == value)


def testUniqueCollisionNeverOverwritesOrRetriesEvenWithOverwriteEnabled(tmp_path, monkeypatch):
    import emo_master.plugins.builtins.image_saver.operator as module
    stamp = datetime(2026, 10, 30, 8, 10, 12, 345678, tzinfo=timezone.utc)
    monkeypatch.setattr(module, 'datetime', SimpleNamespace(now=lambda zone: stamp))
    monkeypatch.setattr(module, 'uuid4', lambda: SimpleNamespace(hex='a' * 32))
    target = tmp_path / 'saved_20261030T081012345678Z_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png'
    encode = cv2.imencode
    encodes = []
    def competingWriter(*args):
        encodes.append(True)
        encoded = encode(*args)
        target.write_bytes(b'other job')
        return encoded
    monkeypatch.setattr(cv2, 'imencode', competingWriter)
    result = ImageSaverOperator().executeNode({'image': np.zeros((8, 12, 3), np.uint8)},
        {'outputPath': str(tmp_path / 'saved.png'), 'namingMode': 'unique', 'overwrite': True}, {})
    assert result['error']['code'] == 'E_OUTPUT_EXISTS'
    assert target.read_bytes() == b'other job' and encodes == [True]
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('mode', ['', 'automatic', None, True, 1, [], {}])
def testInvalidNamingModeDoesNotWrite(tmp_path, mode):
    result = ImageSaverOperator().executeNode({'image': np.zeros((8, 12, 3), np.uint8)},
        {'outputPath': str(tmp_path / 'images/saved.png'), 'namingMode': mode}, {})
    assert result['error']['code'] == 'E_PARAM_INVALID'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('mode', ['fixed', 'unique'])
@pytest.mark.parametrize('stage', ['before', 'encoding'])
def testCancellationBeforeWritePropagatesWithoutCreatingOutput(tmp_path, monkeypatch, mode, stage):
    from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
    token = CancellationToken()
    if stage == 'before':
        token.cancel()
    else:
        encode = cv2.imencode
        def cancelDuringEncoding(*args):
            encoded = encode(*args)
            token.cancel()
            return encoded
        monkeypatch.setattr(cv2, 'imencode', cancelDuringEncoding)
    with pytest.raises(CancellationRequested):
        ImageSaverOperator().executeNode({'image': np.zeros((8, 12, 3), np.uint8)},
            {'outputPath': str(tmp_path / 'images/saved.png'), 'namingMode': mode},
            {'raiseIfCancellationRequested': token.raise_if_cancelled})
    assert not list(tmp_path.iterdir())
