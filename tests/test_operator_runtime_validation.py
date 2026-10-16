import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("restarts", [0, 2])
def testDedicatedSelfTestExercisesRealSpawnAndPagesWithoutDesigner(tmp_path, restarts):
    resultPath = tmp_path / "result.json"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_operator.py"), "--self-test",
        "--result-json", str(resultPath), "--duration", "0", "--restarts", str(restarts)],
        cwd=tmp_path, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr + resultPath.read_text()
    payload = json.loads(resultPath.read_text())
    assert payload["status"] == "ok" and not payload["frozen"] and not payload["designerModules"]
    assert payload["checks"]["onnxruntime"]["provider"] == "CPUExecutionProvider"
    sessions = payload["checks"]["operatorRuntime"]["sessions"]
    assert len(sessions) == restarts + 1
    assert all(session["cycles"] >= 4 and session["pid"] > 0 for session in sessions)
    assert all(session["stopCode"] == "E_CANCELLED" for session in sessions)
    assert all(session["workerRetired"] for session in sessions)
    assert payload["checks"]["operatorRuntime"]["runtimeClosed"]
    assert payload["checks"]["operatorRuntime"]["packageRoundTrip"] == "PASS"
    assert all(session["retainedEvents"] <= 500 for session in sessions)
    assert Path(payload["checks"]["operatorRuntime"]["screenshot"]).exists()
    assert payload["fieldAcceptance"] == "NOT_RUN"


def testRuntimeWorkflowBuildsExplicitRevisionsWithoutPublishing():
    payload = yaml.safe_load((ROOT / ".github/workflows/runtime-windows.yml").read_text())
    package = payload["jobs"]["package"]
    assert package["with"]["target"] == "emo-master-runtime"
    assert package["with"]["publish_release"] is False
    assert package["with"]["source_ref"] == "${{ needs.validate.outputs.source_sha }}"
    assert package["with"]["packager_ref"] == "${{ needs.validate.outputs.packager_sha }}"
    assert re.fullmatch(r"jsdfhasuh/python_build_scripts/.github/workflows/release-windows.yml@[0-9a-f]{40}", package["uses"])
    validation = payload["jobs"]["validate"]
    assert validation["outputs"]["packager_sha"] == "${{ steps.version.outputs.packager_sha }}"
    uploads = [step for job in payload["jobs"].values() for step in job.get("steps", [])
               if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 2 and all(step["if"] == "${{ always() }}" for step in uploads)
    assert "validate_runtime_source.ps1" in next(step["run"] for step in validation["steps"]
                                               if step.get("name") == "Runtime source regression")
    assert "verify_archive" in payload["jobs"]
    text = (ROOT / "scripts/verify_runtime_package.ps1").read_text()
    assert "-not $report.frozen" in text and "Remove-Item Env:PYTHONPATH" in text
