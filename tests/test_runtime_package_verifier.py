import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell required")


def invoke(script, arguments):
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / script), *map(str, arguments)], capture_output=True,
        encoding="utf-8", errors="replace", timeout=60)


def testMissingFrozenReportRetainsExitAndRawLogs(tmp_path):
    report = tmp_path / "missing.json"
    result = invoke("verify_runtime_package.ps1", ["-Executable", sys.executable,
        "-ReportPath", report, "-TimeoutSeconds", "20"])
    assert result.returncode != 0
    execution = json.loads(Path(str(report) + ".execution.json").read_text(encoding="utf-8-sig"))
    assert execution["status"] == "FAIL" and execution["phase"] == "self-test"
    assert execution["exitCode"] != 0
    assert "Runtime report missing" in execution["error"]
    assert Path(str(report) + ".stderr.log").read_text().strip()
    assert Path(str(report) + ".stdout.log").is_file()


def testBadArchiveRetainsPreparationFailure(tmp_path):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")
    report = tmp_path / "broken.json"
    result = invoke("verify_runtime_package.ps1", ["-Archive", archive, "-ReportPath", report])
    assert result.returncode != 0
    execution = json.loads(Path(str(report) + ".execution.json").read_text(encoding="utf-8-sig"))
    assert execution["status"] == "FAIL" and execution["phase"] == "prepare"
    assert execution["exitCode"] is None and execution["error"]


@pytest.mark.parametrize("mismatch", ["packager", "zip"])
def testCloudIdentityMismatchFailsBeforeLaunchingExecutable(tmp_path, mismatch):
    source, packager = "1" * 40, "2" * 40
    archive = tmp_path / "emo-master-runtime-windows-v0.6.1.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("EmoMasterRuntime/EmoMasterRuntime.exe", b"must not execute")
    manifest = dict(source_repo="jsdfhasuh/emo_master", source_commit=source, version="0.6.1",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    identity = dict(source_commit=source, packager_commit=packager, publish_requested=False)
    if mismatch == "packager":
        identity["packager_commit"] = "3" * 40
    else:
        manifest["sha256"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "runtime-build.json").write_text(json.dumps(identity))
    result = invoke("verify_runtime_artifacts.ps1", ["-ArtifactDirectory", tmp_path,
        "-ReleaseTag", "v0.6.1", "-SourceCommit", source, "-PackagerCommit", packager])
    assert result.returncode != 0
    validation = json.loads((tmp_path / "archive-check/validation.json").read_text(encoding="utf-8-sig"))
    assert validation["status"] == "FAIL"
    assert validation["archiveAcceptance"] == validation["nativeAcceptance"] == "NOT_RUN"
    assert "mismatch" in validation["error"]
    assert not list((tmp_path / "archive-check").glob("*.execution.json"))
