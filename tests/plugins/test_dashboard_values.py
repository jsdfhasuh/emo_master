import json
from pathlib import Path

import pytest

from emo_master.plugins.builtins.elapsed_time import operator as elapsed
from emo_master.plugins.builtins.percentage.operator import PercentageOperator


@pytest.mark.parametrize("folder,operator", [("elapsed_time", elapsed.ElapsedTimeOperator),
                                            ("percentage", PercentageOperator)])
def test_manifest_matches_meta(folder, operator):
    root = Path(elapsed.__file__).parents[1]
    manifest = json.loads((root / folder / "manifest.json").read_text())
    for key in ("operatorId", "displayName", "version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(operator.meta, key)


def test_elapsed_retains_node_time_resets_new_job_and_exceeds_one_day(monkeypatch):
    clock = iter([10.0, 11.25, 90071.0, 99999.0])
    monkeypatch.setattr(elapsed, "monotonic", lambda: next(clock))
    operator = elapsed.ElapsedTimeOperator()
    def run():
        return operator.executeNode({"after": False}, {}, {})["outputs"]
    assert run() == {"seconds": 0.0, "text": "00:00:00"}
    assert run() == {"seconds": 1.25, "text": "00:00:01"}
    assert run() == {"seconds": 90061.0, "text": "25:01:01"}
    operator.initOperator({"jobId": "new"})
    assert run() == {"seconds": 0.0, "text": "00:00:00"}


def test_elapsed_requires_trigger_and_preserves_cancellation():
    operator = elapsed.ElapsedTimeOperator()
    assert operator.executeNode({}, {}, {})["error"]["code"] == "E_INPUT_MISSING"

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        operator.executeNode({"after": 0}, {}, {"raiseIfCancellationRequested": cancel})
    assert operator._started is None


@pytest.mark.parametrize("inputs,expected", [({"numerator": 0.873}, 87.3),
    ({"numerator": 2, "denominator": 3}, 200 / 3),
    ({"numerator": 0, "denominator": 0}, None),
    ({"numerator": 0, "denominator": 3}, 0)])
def test_percentage(inputs, expected):
    result = PercentageOperator().executeNode(inputs, {}, {})
    assert result["status"] == "ok"
    if expected is None:
        assert result["outputs"]["value"] is None
    else:
        assert result["outputs"]["value"] == pytest.approx(expected)
    assert result["outputs"]["defined"] is (expected is not None)


@pytest.mark.parametrize("value", [True, "2", None, -1, float("nan"), float("inf")])
def test_percentage_rejects_invalid_values(value):
    operator = PercentageOperator()
    assert operator.executeNode({"numerator": value}, {}, {})["status"] == "error"
    assert operator.executeNode({"numerator": 1, "denominator": value}, {}, {})["status"] == "error"
