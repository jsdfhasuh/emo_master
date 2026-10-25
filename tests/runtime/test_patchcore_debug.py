"""Spawned single-node debug workers, isolated from production jobs."""
import json
from pathlib import Path

import cv2
import numpy as np

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from tests.plugins.patchcore_fixture import makeDeployment, wrapTransfer
from tests.runtime.patchcore_fixture import project
from tests.runtime.test_operator_debug_resources import run, uploadImage
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


def testPatchcoreSpawnedDebugAssetsResetSwitchErrorsAndClose(runtime, tmp_path):
    first = wrapTransfer(makeDeployment(tmp_path / "\u6a21\u578b"))
    second = makeDeployment(tmp_path / "other", score=1.)
    document = project(first)
    debug = DebugConnection(RuntimeClient(runtime))
    try:
        debug.open(document.model_dump(mode="json"), EditorKey(document.project.projectId, "main", "patchcore"),
                   "vision.inference.patchcore")
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        image = uploadImage(debug)
        inputs = {"image": image}
        params = {"modelPath": str(first), "outputAnomalyMap": True}
        record = run(debug, params, inputs)
        assert record["status"] == "SUCCEEDED", record
        assert record["outputs"]["score"] == record["outputs"]["threshold"] == 2.5
        assert record["outputs"]["isAnomaly"] is True and record["outputs"]["label"] == "NG"
        assert set(record["outputAssets"]) == {"heatmap", "overlay", "anomalyMap"}
        heat = debug.download(record["outputAssets"]["heatmap"]["assetId"])
        assert cv2.imdecode(np.frombuffer(heat["content"], np.uint8), cv2.IMREAD_COLOR).shape == (8, 12, 3)
        raw = debug.download(record["outputAssets"]["anomalyMap"]["assetId"])
        payload = json.loads(raw["content"])
        assert payload["dtype"] == "float32" and payload["shape"] == [256, 256]
        expected = np.arange(256 * 256, dtype=np.float32).reshape(256, 256) / 20000
        np.testing.assert_array_equal(np.asarray(payload["values"], np.float32), expected)
        assert run(debug, params, inputs)["outputs"]["score"] == 2.5
        assert run(debug, {"modelPath": str(second)}, inputs)["outputs"]["score"] == 1.
        assert run(debug, params, inputs)["outputs"]["score"] == 2.5
        originalWorker = next(iter(runtime.operatorDebugManager.sessions.values())).worker
        debug.mutation("ResetOperatorDebugSession")
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        assert originalWorker.retired and originalWorker.processClosed
        inputs = {"image": uploadImage(debug)}
        result = run(debug, {"modelPath": str(second), "drawHeatmap": False, "drawOverlay": False}, inputs)
        assert result["status"] == "SUCCEEDED" and result["outputs"]["label"] == "OK", result
        assert not result["outputAssets"]
        assert set(result["diagnostics"]["imageOutputMessages"]) == {"heatmap", "overlay"}
        for path, code in (("relative", "E_PARAM_INVALID"), (str(tmp_path / "missing"), "E_INPUT_MISSING"),
                           (str(second / "model.onnx"), "E_INPUT_MISSING")):
            result = run(debug, {"modelPath": path}, inputs)
            assert result["status"] == "FAILED" and result["code"] == code, result
            assert "Runtime" in result["message"]
        invalid = tmp_path / "incomplete"
        invalid.mkdir()
        result = run(debug, {"modelPath": str(invalid)}, inputs)
        assert result["status"] == "FAILED" and result["code"] == "E_MODEL_LOAD_FAILED", result
        assert run(debug, {"modelPath": str(second)}, inputs)["outputs"]["label"] == "OK"
        session = next(iter(runtime.operatorDebugManager.sessions.values()))
        worker = session.worker
    finally:
        debug.close()
    assert not runtime.operatorDebugManager.ownsResources()
    assert runtime.loadedDocument is None and not runtime.jobRepository.all()
    assert worker.retired and worker.processClosed
    assert not worker.reader.is_alive() and not worker.writer.is_alive()
    assert worker.commandPipe.closed and worker.eventPipe.closed
    assert not Path(worker.workspace.name).exists()
