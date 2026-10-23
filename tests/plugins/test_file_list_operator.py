import json
from pathlib import Path

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from emo_master.plugins.builtins.file_list.operator import FileListOperator


def run(root, **params):
    return FileListOperator().executeNode({}, {"folderPath": str(root), **params}, {})


@pytest.mark.parametrize("recursive", [False, True])
def testNaturalOrderPatternsUnicodeAndRecursion(tmp_path, recursive):
    root = tmp_path / "文件"
    root.mkdir()
    names = ["clip10.MP4", "clip2.mp4", "clip1.avi", "note.txt", "sub/clip3.mp4"]
    for name in names:
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"paths only; no decoding")
    (root / "directory.mp4").mkdir()
    result = run(root, patterns=["*.mp4", "*.avi", "clip*"], recursive=recursive)
    assert result["status"] == "ok", result
    expected = ["clip1.avi", "clip2.mp4", "clip10.MP4"] + (["sub/clip3.mp4"] if recursive else [])
    assert result["outputs"] == {"paths": [str(root / name) for name in expected], "count": len(expected)}


def testEmptyFolderAndUnmatchedPatternsProduceEmptyList(tmp_path):
    assert run(tmp_path)["outputs"] == {"paths": [], "count": 0}
    (tmp_path / "note.txt").touch()
    assert run(tmp_path, patterns=["*.mp4"])["outputs"] == {"paths": [], "count": 0}
    assert run(tmp_path)["outputs"]["paths"] == [str(tmp_path / "note.txt")]
    assert run(tmp_path / "missing")["error"]["code"] == "E_INPUT_MISSING"


@pytest.mark.parametrize("params", [{}, {"folderPath": 1}, {"folderPath": " "},
    {"folderPath": "a", "patterns": []}, {"folderPath": "a", "patterns": "*.mp4"},
    {"folderPath": "a", "patterns": [1]}, {"folderPath": "a", "patterns": ["../*.mp4"]},
    {"folderPath": "a", "recursive": 1}])
def testInvalidParams(params):
    result = FileListOperator().executeNode({}, params, {})
    assert result["error"]["code"] == "E_PARAM_INVALID"


def testCancellationDuringListing(tmp_path):
    (tmp_path / "one.mp4").touch()
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CancellationRequested("cancelled")

    with pytest.raises(CancellationRequested):
        FileListOperator().executeNode({}, {"folderPath": str(tmp_path)}, {"raiseIfCancellationRequested": cancel})


def testManifestMatchesImplementation():
    path = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins/file_list/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key in ("operatorId", "displayName", "version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(FileListOperator.meta, key)
