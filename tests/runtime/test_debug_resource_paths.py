from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.operator_debug.contracts import DebugError
from emo_master.apps.runtime.operator_debug.resources import DebugResources, runtimePath
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_resources import run, sqliteConfig, uploadImage
from tests.runtime.test_operator_debug_sessions import waitFor


@pytest.mark.parametrize("operator,key,directory", [
    ("vision.io.image_loader", "imagePath", False),
    ("vision.io.image_batch_loader", "folderPath", True),
    ("vision.io.file_list", "folderPath", True),
    ("vision.io.coordinate_reader", "filePath", False),
    ("vision.io.video_loader", "videoPath", False),
    ("vision.inference.yolo", "modelPath", False),
    ("vision.inference.yolo_classification", "modelPath", False),
    ("vision.inference.patchcore", "modelPath", True),
    ("vision.io.image_saver", "outputPath", False),
])
@pytest.mark.parametrize("relative", [True, False])
def testResourcePathsUseProjectRootWithoutChangingDraftOrWorkspace(tmp_path, operator, key, directory, relative):
    root, workspace = tmp_path / "project", tmp_path / "debug"
    root.mkdir()
    workspace.mkdir()
    target = root / "resource"
    if directory:
        target.mkdir()
    elif operator != "vision.io.image_saver":
        target.write_bytes(b"fixture")
    raw = target.name if relative else str(target)
    params = {key: raw}
    context = SimpleNamespace(workspacePath=str(workspace))
    effective, values, _, notes = DebugResources(dict(operatorId=operator, resourceRoot=str(root))).prepare(
        params, {}, context, CancellationToken())
    assert effective[key] == str(target)
    assert params == {key: raw} and values == {}
    assert notes["resourcePaths"][key] == dict(original=raw, baseDirectory=str(root), resolved=str(target))
    assert context.workspacePath == str(workspace)
    if operator == "vision.io.image_saver":
        assert not target.exists()


def testVideoInputOverridesParameterWithoutMutatingEither(tmp_path):
    target = tmp_path / "movie.mp4"
    target.write_bytes(b"fixture")
    params, values = {"videoPath": "ignored.mp4"}, {"videoPath": "movie.mp4"}
    effective, result, _, _ = DebugResources(dict(operatorId="vision.io.video_loader", resourceRoot=str(tmp_path))).prepare(
        params, values, SimpleNamespace(), CancellationToken())
    assert result["videoPath"] == effective["videoPath"] == str(target)
    assert params == {"videoPath": "ignored.mp4"} and values == {"videoPath": "movie.mp4"}


def testMissingRootAndFileExplainResolution(tmp_path):
    with pytest.raises(DebugError, match="绝对路径") as missingRoot:
        runtimePath("models/model.onnx")
    assert missingRoot.value.code == "E_PARAM_INVALID"
    with pytest.raises(DebugError) as missingFile:
        runtimePath("models/model.onnx", resourceRoot=str(tmp_path))
    assert missingFile.value.code == "E_INPUT_MISSING"
    assert "models/model.onnx" in str(missingFile.value)
    assert str(tmp_path / "models" / "model.onnx") in str(missingFile.value)


@pytest.mark.parametrize("transport,matching", [("embedded", False), ("grpc", True), ("grpc", False)])
def testSessionUsesLocalOrMatchingRuntimeRootAndNeverRemoteClientDirectory(runtime, tmp_path, monkeypatch, transport, matching):
    from emo_master.apps.designer.services import operator_debug
    root, remote = tmp_path / "local", tmp_path / "remote"
    root.mkdir()
    remote.mkdir()
    for folder, pixel in ((root, 40), (remote, 190)):
        assert cv2.imwrite(str(folder / "image.png"), np.full((7, 9, 3), pixel, np.uint8))
    monkeypatch.setattr(operator_debug, "runtimeConnectionDescription", lambda _: dict(kind=transport))
    monkeypatch.setattr(runtime, "loadedProjectId", "draft" if matching else "different-project")
    monkeypatch.setattr(runtime, "loadedProjectPath", str(remote))
    debug = DebugConnection(RuntimeClient(runtime))
    debug.projectDirectory = str(root)
    payload = project("vision.io.image_loader")
    before = deepcopy(payload)
    try:
        opened = debug.open(payload, EditorKey("draft", "main", "node"), "vision.io.image_loader")
        expected = str(root) if transport == "embedded" else str(remote) if matching else ""
        assert opened["session"]["resourceRoot"] == expected
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        record = run(debug, {"imagePath": "image.png"})
        if expected:
            assert record["status"] == "SUCCEEDED", record
            assert record["rawParams"]["imagePath"] == "image.png"
            raw = debug.download(record["outputAssets"]["image"]["assetId"])["content"]
            pixels = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
            assert np.all(pixels == (40 if transport == "embedded" else 190))
            debug.projectDirectory = str(tmp_path)
            debug.mutation("ResetOperatorDebugSession")
            waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
            assert debug.snapshot()["session"]["resourceRoot"] == expected
        else:
            assert record["code"] == "E_PARAM_INVALID"
        assert payload == before
    finally:
        debug.close()


@pytest.mark.parametrize("operator", ["vision.io.sqlite_writer", "vision.io.result_writer", "vision.io.image_saver"])
def testRelativeWritesKeepIsolationAndExplicitConfirmation(runtime, tmp_path, operator):
    debug = DebugConnection(RuntimeClient(runtime))
    debug.projectDirectory = str(tmp_path)
    path = tmp_path / "business.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE records(value INTEGER NOT NULL)")
        db.execute("INSERT INTO records VALUES(999)")
    original = path.read_bytes()
    try:
        debug.open(project(operator), EditorKey("draft", "main", "node"), operator)
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        if operator == "vision.io.sqlite_writer":
            record = run(debug, sqliteConfig(path.name), {"mapping_1": {"inline": 7}})
            assert record["status"] == "SUCCEEDED", record
            isolated = Path(record["diagnostics"]["debugDatabasePath"])
            assert isolated.parent != tmp_path
            with closing(sqlite3.connect(isolated)) as db:
                assert db.execute("SELECT value FROM records").fetchall() == [(7,)]
        elif operator == "vision.io.result_writer":
            record = run(debug, {"relativePath": "results/test"}, {"numberValue": {"inline": 12}})
            assert record["status"] == "SUCCEEDED", record
            isolated = Path(record["outputs"]["result"]["path"])
            assert not (tmp_path / "results").exists()
        else:
            image = uploadImage(debug)
            with pytest.raises(RuntimeClientError) as denied:
                run(debug, {"outputPath": "image.png"}, {"image": image})
            assert denied.value.code == "E_DEBUG_CONFIRM_REQUIRED"
            assert not (tmp_path / "image.png").exists()
            record = run(debug, {"outputPath": "image.png"}, {"image": image}, confirmSideEffects=True)
            assert record["status"] == "SUCCEEDED", record
            assert (tmp_path / "image.png").exists()
        assert path.read_bytes() == original
    finally:
        debug.close()
    if operator != "vision.io.image_saver":
        assert not isolated.exists()
