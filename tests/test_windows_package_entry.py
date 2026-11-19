from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import pytest

from emo_master.apps import windows_entry
from emo_master.apps.package_selftest import runSelfTest, runSelfTestCommand


def testPackageSelfTestValidatesBundledResourcesAndOnnxRuntime() -> None:
    result = runSelfTest()

    assert result["status"] == "ok"
    checks = result["checks"]
    assert "cameraDrivers" not in checks
    assert checks["builtins"]["operatorCount"] >= 49
    assert checks["builtins"]["editorUiCount"] >= 3
    assert checks["onnxruntime"]["provider"] == "CPUExecutionProvider"
    assert checks["migrations"]["versions"] == [1, 2, 3, 4, 5, 6]
    assert checks["migrations"]["files"] == [
        "001_init.sql", "002_runtime_workflow.sql", "003_runtime_timestamps.sql",
        "004_global_counters.sql", "006_logging_metadata.sql",
    ]
    assert checks["migrations"]["journalMode"] == "delete"
    assert len(checks["operatorIcons"]["examples"]) == 3


@pytest.mark.parametrize("empty", [False, True])
def testPackageMigrationGateRequiresLoggingMetadataSql(monkeypatch, tmp_path, empty):
    from emo_master.apps import package_selftest
    migrationPath = tmp_path / "apps/runtime/context/migrations"
    migrationPath.mkdir(parents=True)
    for name in package_selftest._EXPECTED_MIGRATIONS:
        if name != "006_logging_metadata.sql":
            (migrationPath / name).write_text("-- existing migration", encoding="utf-8")
    if empty:
        (migrationPath / "006_logging_metadata.sql").touch()
    monkeypatch.setattr(package_selftest, "_packageRoot", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="SQL migration is missing or empty: 006_logging_metadata.sql"):
        package_selftest._checkMigrations()


def testDefaultPackageSelfTestDoesNotImportQtOrCreateApplication():
    root = Path(__file__).resolve().parents[1]
    command = "from emo_master.apps.package_selftest import runSelfTest; import sys; assert runSelfTest()['status'] == 'ok'; assert 'PySide2.QtWidgets' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", command], env={**os.environ, "PYTHONPATH": str(root / "src")},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def testPackageResourceGateRejectsChangedExampleDigest(monkeypatch):
    from emo_master.apps import package_selftest
    monkeypatch.setattr(package_selftest, "_ICON_EXAMPLES", {"vision.edge.canny": ("0" * 64, "#0891b2")})
    result = runSelfTest()
    assert result["status"] == "error"
    assert result["checks"]["operatorIcons"]["status"] == "error"


def testPackageSelfTestCommandWritesResultJson(tmp_path: Path) -> None:
    resultPath = tmp_path / "results" / "self-test.json"

    exitCode = runSelfTestCommand(["--self-test", "--result-json", str(resultPath)])

    assert exitCode == 0
    assert json.loads(resultPath.read_text(encoding="utf-8"))["status"] == "ok"


@pytest.mark.skipif(sys.platform != "win32", reason="MVS SDK supports Windows")
@pytest.mark.parametrize("product", ["designer", "runtime"])
def testCameraPackageProbeUsesVersionOnlySdkInSpawnedHost(tmp_path, product):
    root = Path(__file__).resolve().parents[1]
    bindings = tmp_path / "Samples/Python/MvImport"
    bindings.mkdir(parents=True)
    (bindings / "MvCameraControl_class.py").write_text(
        "class MvCamera:\n    @staticmethod\n    def MV_CC_GetSDKVersion(): return 0x09080706\n",
        encoding="ascii",
    )
    entry = ["-m", "emo_master.apps.windows_entry"]
    if product == "runtime":
        # The independent Runtime package suite exercises the full UI and Dispatch Jobs.
        # Here the actual CLI parser, driver registry and spawned camera Host are under test.
        entry = ["-c", """
import sys
from unittest.mock import patch
from emo_master.apps.operator_runtime.validation import runValidationCommand
with patch("emo_master.apps.operator_runtime.validation._checkWindow", return_value={}), \\
     patch("emo_master.apps.targeted_repair_selftest.checkTargetedRepairs", return_value={}):
    sys.exit(runValidationCommand(sys.argv[1:]))
"""]
    path = tmp_path / "result.json"
    completed = subprocess.run([sys.executable, *entry, "--self-test", "--camera-drivers", "--result-json", str(path)],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root / "src"), MVCAM_COMMON_RUNENV=str(tmp_path),
                               QT_QPA_PLATFORM="offscreen"), capture_output=True, text=True, timeout=90)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert completed.returncode == 0, (report, completed.stderr)
    result = report["checks"]["cameraDrivers"]
    assert result["operatorId"] == "vision.io.huaray_camera" and result["operatorVersion"] == "1.2.0"
    assert set(result["deviceTypes"]) == {"device.camera.huaray_imv", "device.camera.hikrobot_mvs"}
    assert result["hostPid"] > 0 and result["syntheticFrames"] == 2
    assert not result["hostFrozen"] and not result["hardwareAccessed"]
    assert result["sdkVersion"] == "9.8.7.6"


def testExplicitManagedPackageProbeUsesSpawnWithoutHardware(tmp_path):
    root = Path(__file__).resolve().parents[1]
    resultPath = tmp_path / "managed-selftest.json"
    completed = subprocess.run([sys.executable, "-m", "emo_master.apps.windows_entry", "--self-test",
        "--managed-runtime", "--result-json", str(resultPath)],
        env={**os.environ, "PYTHONPATH": str(root / "src"), "QT_QPA_PLATFORM": "offscreen", "HUARAY_CAMERA_SMOKE": "0"},
        capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(resultPath.read_text(encoding="utf-8"))
    result = report["checks"]["managedRuntime"]
    assert report["status"] == "ok" and result["hardwareAccessed"] is False
    assert result["originalFramesRetired"] and result["runtimeClosed"]
    assert result["replacementRevision"] == 3 and len(set(result["workerPids"])) == 2
    assert result["dispatch"]["consumers"] == 2
    assert len(set(result["dispatch"]["workerPids"])) == 3
    assert result["dispatch"]["sameFrame"] and result["dispatch"]["joinCompleted"]
    assert result["dispatch"]["resourcesRetired"]
    host = result["hostFailureProbe"]
    assert host["processRetired"] and host["stagingRetired"] and host["quarantined"]
    assert not host["sdkLoaded"] and not host["hardwareAccessed"]
    leases = result["networkAndResourceLease"]
    assert leases["liveConsumerProtected"] and leases["lateConsumerRejected"]
    assert not leases["sdkLoaded"] and not leases["hardwareAccessed"]
    transfer = result["resourceTransfer"]
    assert transfer["ready"] and transfer["separateCommit"] and transfer["immutableCopy"]
    assert transfer["designerSourceRemoved"] and transfer["recoveredAfterRestart"]


def testWindowsEntryCallsFreezeSupportBeforeImportingDesigner(monkeypatch) -> None:
    events: list[str] = []
    fakeDesignerMain = ModuleType("emo_master.apps.designer.main")

    def runDesigner() -> None:
        events.append("designer")

    fakeDesignerMain.runDesigner = runDesigner  # type: ignore[attr-defined]
    monkeypatch.setattr(
        windows_entry.multiprocessing,
        "freeze_support",
        lambda: events.append("freeze"),
    )
    monkeypatch.setitem(sys.modules, "emo_master.apps.designer.main", fakeDesignerMain)

    assert windows_entry.main([]) == 0
    assert events == ["freeze", "designer"]
