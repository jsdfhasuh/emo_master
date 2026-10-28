from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import subprocess
import sys

from emo_master.apps import package_selftest


def testClassificationSelfTestRunsRealInputDependentOnnxAndPillow():
    result = package_selftest._checkYoloClassification()
    assert result["provider"] == "CPUExecutionProvider"
    assert result["inputShape"] == [1, 3, 8, 8]
    assert result["preprocess"] == "resize_full_frame_v1"
    assert result["labels"] == ["dark", "bright"]
    assert result["pillowVersion"]


def testDefaultPackageGateIncludesClassificationWithoutImportingQt():
    root = Path(__file__).resolve().parents[1]
    command = (
        "from emo_master.apps.package_selftest import runSelfTest; import sys; "
        "report = runSelfTest(); assert report['status'] == 'ok', report; "
        "assert report['checks']['yoloClassification']['labels'] == ['dark', 'bright']; "
        "assert 'PySide2.QtWidgets' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _rejectPillowImage(monkeypatch):
    original = builtins.__import__

    def importModule(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PIL" and "Image" in fromlist:
            raise ImportError("package probe: PIL.Image is missing")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", importModule)


def testDesignerPackageGateFailsWhenPillowCannotLoad(monkeypatch):
    _rejectPillowImage(monkeypatch)
    result = package_selftest.runSelfTest()
    assert result["status"] == "error"
    assert result["checks"]["onnxruntime"]["status"] == "ok"
    assert result["checks"]["yoloClassification"]["status"] == "error"
    assert any(error.startswith("yoloClassification:") for error in result["errors"])


def testRuntimePackageGateFailsWhenPillowCannotLoad(tmp_path, monkeypatch):
    from emo_master.apps.operator_runtime import validation
    from emo_master.apps import targeted_repair_selftest

    # Isolate this dependency gate from the unrelated GUI/managed-device probes.
    monkeypatch.setattr(validation, "_checkBuiltins", lambda: {})
    monkeypatch.setattr(validation, "_checkMigrations", lambda: {})
    monkeypatch.setattr(validation, "_checkWindow", lambda *_args: {})
    monkeypatch.setattr(targeted_repair_selftest, "checkTargetedRepairs", lambda: {})
    _rejectPillowImage(monkeypatch)
    resultPath = tmp_path / "runtime-self-test.json"
    assert validation.runValidationCommand(["--self-test", "--result-json", str(resultPath)]) == 1
    result = json.loads(resultPath.read_text(encoding="utf-8"))
    assert result["checks"]["onnxruntime"]["status"] == "ok"
    assert result["checks"]["yoloClassification"]["status"] == "error"
    assert any(error.startswith("yoloClassification:") for error in result["errors"])
