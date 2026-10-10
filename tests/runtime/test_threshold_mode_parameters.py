from copy import deepcopy

import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.operator_debug.assets import imageBytes
from emo_master.core.project.snapshots import _parameters
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins.threshold.operator import ThresholdOperator
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_builtin_vision_operator_workflows import _project, _registry, _run
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "fixed", "blockSize": 2},
        {"mode": "otsu", "threshold": 999, "blockSize": 2},
        {"mode": "triangle", "threshold": -1, "blockSize": 2},
        {"mode": "adaptiveMean", "threshold": 999, "blockSize": 3},
        {"mode": "adaptiveGaussian", "threshold": -1, "blockSize": 3},
    ],
)
def testModeParametersPassSnapshotAndFormalWorkflow(params):
    original = deepcopy(params)
    normalized = _parameters(params, ThresholdOperator.meta.paramSchema, "params")
    assert params == original
    assert ThresholdOperator().validateParams(normalized) is None
    for key, value in params.items():
        assert normalized[key] == value

    image = np.arange(81, dtype=np.uint8).reshape(9, 9)
    result = _run(
        _registry(),
        _project("vision.preprocess.threshold", "mask", "image", normalized),
        image,
    )
    assert result.outputs["result"].shape == image.shape
    assert set(np.unique(result.outputs["result"])).issubset({0, 255})


@pytest.mark.parametrize(
    "params,field",
    [
        ({"mode": "fixed", "threshold": 999}, "threshold"),
        ({"mode": "adaptiveMean", "blockSize": 2}, "blockSize"),
        ({"mode": "adaptiveGaussian", "blockSize": 4}, "blockSize"),
    ],
)
def testActiveModeConstraintsAreEnforcedByFormalCompiler(params, field):
    normalized = _parameters(params, ThresholdOperator.meta.paramSchema, "params")
    with pytest.raises(WorkflowCompileError, match=field):
        _run(
            _registry(),
            _project("vision.preprocess.threshold", "mask", "image", normalized),
            np.zeros((9, 9), dtype=np.uint8),
        )


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "otsu", "threshold": "999"},
        {"mode": "fixed", "blockSize": "2"},
        {"mode": "fixed", "constant": "unused"},
        {"mode": "fixed", "constant": float("nan")},
        {"mode": "unknown"},
        {"invert": 1},
    ],
)
def testSnapshotStillEnforcesTypesEnumsAndFiniteJson(params):
    with pytest.raises(ValueError):
        _parameters(params, ThresholdOperator.meta.paramSchema, "params")


def testDisplayConditionsCannotBypassNormalSchemaConstraints():
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "default": "fixed"},
            "value": {
                "type": "integer",
                "minimum": 3,
                "xMinimum": -999,
                "xEnabledWhen": {"mode": ["adaptive"]},
            },
        },
    }
    with pytest.raises(ValueError, match="minimum"):
        _parameters({"mode": "fixed", "value": 2}, schema, "params")


def testThresholdModeConstraintsRemainIndependentOfDisplayHints():
    schema = deepcopy(ThresholdOperator.meta.paramSchema)
    schema["properties"]["threshold"].update(
        xMinimum=-9999, xMaximum=9999, xEnabledWhen={"mode": []}
    )
    normalized = _parameters({"mode": "fixed", "threshold": 999}, schema, "params")
    assert ThresholdOperator().validateParams(normalized)["code"] == "E_PARAM_INVALID"


def testModeParametersThroughDebugConnectionAndSpawnWorker(runtime):
    connection = DebugConnection(RuntimeClient(runtime))
    try:
        connection.open(
            project("vision.preprocess.threshold"),
            EditorKey("draft", "main", "node"),
            "vision.preprocess.threshold",
        )
        waitFor(lambda: connection.snapshot()["session"]["state"] == "READY")
        image = np.arange(81, dtype=np.uint8).reshape(9, 9)
        asset = connection.upload(imageBytes(image), "image/png", {"kind": "upload"})
        cases = [
            ({"mode": "fixed", "blockSize": 2}, "SUCCEEDED"),
            ({"mode": "otsu", "threshold": 999, "blockSize": 2}, "SUCCEEDED"),
            ({"mode": "triangle", "threshold": 999, "blockSize": 2}, "SUCCEEDED"),
            ({"mode": "fixed", "threshold": 999}, "FAILED"),
            ({"mode": "adaptiveMean", "blockSize": 2}, "FAILED"),
            ({"mode": "adaptiveGaussian", "blockSize": 2}, "FAILED"),
            ({"mode": "adaptiveMean", "threshold": 999, "blockSize": 3}, "SUCCEEDED"),
            ({"mode": "adaptiveGaussian", "threshold": 999, "blockSize": 3}, "SUCCEEDED"),
            ({"mode": "fixed", "threshold": 127, "blockSize": 2}, "SUCCEEDED"),
        ]
        for params, status in cases:
            started = connection.execute(params, {"image": asset})

            def terminal():
                result = connection.snapshot(started["executionId"])["result"]
                return result if result["status"] != "RUNNING" else None

            record = waitFor(terminal)
            assert record["status"] == status, record
            assert record["rawParams"] == params
            if status == "FAILED":
                assert record["code"] == "E_PARAM_INVALID"
            else:
                assert record["outputAssets"]["mask"]
        with pytest.raises(RuntimeClientError, match="expected integer"):
            connection.execute({"mode": "fixed", "blockSize": "2"}, {"image": asset})
        assert runtime.loadedDocument is None
        assert runtime.jobRepository.all() == []
    finally:
        connection.close()
