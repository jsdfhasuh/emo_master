from pathlib import Path
from fnmatch import fnmatch
import shlex
from types import ModuleType, SimpleNamespace
import sys

import pytest
import yaml

from scripts import ci_check


def testCiPytestReportsNamesAndHasCrossPlatformHangBound(monkeypatch):
    calls = []
    monkeypatch.setattr(ci_check, "runStep", lambda command, name: calls.append((command, name)) or 0)
    assert ci_check.main() == 0
    command, name = calls[-1]
    assert name == "pytest"
    assert command[:3] == [ci_check.sys.executable, "-m", "pytest"]
    assert command[command.index("-p") + 1] == "scripts.ci_pytest_reports"
    assert "-vv" in command and "--tb=short" in command
    assert "-x" not in command and not any(value.startswith("--maxfail") for value in command)
    assert "--junitxml=build/ci-results.xml" in command
    assert "--timeout=120" in command and "--timeout-method=thread" in command
    assert command[command.index("-o") + 1] == "faulthandler_timeout=60"
    assert "--durations=30" in command and "--durations-min=1" in command
    assert not any(value.startswith("--ci-shard") for value in command)
    assert [name for _, name in calls] == ["proto-drift", "ruff", "mypy", "pytest"]


@pytest.mark.parametrize("shard", ["designer-ui", "runtime-other"])
def testCiShardOnlyChangesPytestSelectionAndKeepsAllChecks(monkeypatch, shard):
    calls = []
    monkeypatch.setattr(ci_check, "runStep", lambda command, name: calls.append((command, name)) or 0)
    assert ci_check.main(["--shard", shard]) == 0
    assert [name for _, name in calls] == ["proto-drift", "ruff", "mypy", "pytest"]
    command = calls[-1][0]
    assert "scripts.ci_pytest_shard" in command
    assert f"--ci-shard={shard}" in command
    assert "--ci-shard-report=build/ci-shard.json" in command
    assert "--timeout=120" in command and "--timeout-method=thread" in command


def testCiStopsOnFailedCheckAndPreservesFailureCode(monkeypatch):
    calls = []
    def fail(command, name):
        calls.append(name)
        return 17
    monkeypatch.setattr(ci_check, "runStep", fail)
    assert ci_check.main() == 17
    assert calls == ["proto-drift"]


def testCiPreservesFailedPytestExitCodeAfterAllChecks(monkeypatch):
    calls = []
    def failPytest(command, name):
        calls.append(name)
        return 1 if name == "pytest" else 0
    monkeypatch.setattr(ci_check, "runStep", failPytest)
    assert ci_check.main() == 1
    assert calls == ["proto-drift", "ruff", "mypy", "pytest"]


@pytest.mark.parametrize("platform", ["windows", "offscreen"])
def testCiPreservesSelectedQtPlatform(monkeypatch, platform):
    monkeypatch.setenv("QT_QPA_PLATFORM", platform)
    platforms = []
    monkeypatch.setattr(ci_check, "runStep", lambda *_:
        platforms.append(ci_check.os.environ["QT_QPA_PLATFORM"]) or 0)
    assert ci_check.main() == 0
    assert platforms == [platform] * 4


def workflowFile(name):
    return yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows" / name).read_text())


def projectCheckStep(job, shard=None):
    matches = [step for step in job["steps"] if "scripts/ci_check.py" in step.get("run", "")]
    assert len(matches) == 1
    command = shlex.split(matches[0]["run"].replace("${{ matrix.shard }}", shard or ""))
    assert command[0] in {"python", "python3"}
    expected = ["scripts/ci_check.py"] + (["--shard", shard] if shard else [])
    assert command[1:] == expected
    return matches[0]


def assertFailureArtifacts(job, paths):
    uploads = [step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert any("always()" in step.get("if", "")
               and paths <= set(step["with"]["path"].splitlines()) for step in uploads)


def testLinuxCiKeepsFullDefaultSourceChecksAndFailureEvidence():
    workflow = workflowFile("ci.yml")
    job = workflow["jobs"]["validate"]
    assert job["timeout-minutes"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["name"] == "ubuntu-latest / Python 3.10"
    assert job["needs"] == "scope"
    projectCheckStep(job)
    assertFailureArtifacts(job, {"build/ci-results.xml", "build/ci-failures.log", "build/two-job-observation.json"})


def testWindowsShardsKeepNativeCoverageAndAggregateTheOriginalRequiredCheck():
    jobs = workflowFile("ci.yml")["jobs"]
    shards = jobs["windows"]
    assert shards["runs-on"] == "windows-latest"
    assert shards["env"]["QT_QPA_PLATFORM"] == "windows"
    assert shards["needs"] == "scope"
    assert shards["if"] == "needs.scope.outputs.run_full == 'true'"
    assert shards["strategy"]["fail-fast"] is False
    assert set(shards["strategy"]["matrix"]["shard"]) == {"designer-ui", "runtime-other"}
    for shard in shards["strategy"]["matrix"]["shard"]:
        projectCheckStep(shards, shard)
    assertFailureArtifacts(shards, {"build/ci-results.xml", "build/ci-failures.log",
                                   "build/two-job-observation.json", "build/ci-shard.json"})
    gate = jobs["windows-summary"]
    assert gate["name"] == "windows-latest / Python 3.10"
    assert set(gate["needs"]) == {"scope", "windows"}
    assert "always()" in gate["if"]
    upload = next(step["with"] for step in shards["steps"]
                  if step.get("uses", "").startswith("actions/upload-artifact@"))
    download = next(step["with"] for step in gate["steps"]
                    if step.get("uses", "").startswith("actions/download-artifact@"))
    summary = next(step for step in gate["steps"] if "scripts/ci_shard_summary.py" in step.get("run", ""))
    command = shlex.split(summary["run"])
    assert command[:2] == ["python", "scripts/ci_shard_summary.py"]
    reports = []
    for shard in shards["strategy"]["matrix"]["shard"]:
        name = upload["name"].replace("${{ matrix.shard }}", shard)
        assert fnmatch(name, download["pattern"])
        reports.append(f"{download['path']}/{name}/ci-shard.json")
    assert command[2:] == reports
    assert summary["if"] == "needs.scope.outputs.run_full == 'true'"


@pytest.mark.parametrize("jobId", ["validate", "windows-summary"])
@pytest.mark.parametrize("scopeResult,runFull", [("failure", ""), ("success", ""), ("success", "unknown")])
def testFailedOrMissingScopeCannotProduceSuccessfulSourceCheck(monkeypatch, jobId, scopeResult, runFull):
    job = workflowFile("ci.yml")["jobs"][jobId]
    report = next(step for step in job["steps"] if "SCOPE_RESULT" in step.get("env", {}))
    monkeypatch.setenv("SCOPE_RESULT", scopeResult)
    monkeypatch.setenv("RUN_FULL", runFull)
    with pytest.raises(SystemExit):
        exec(compile(report["run"], "ci-selection-result", "exec"))


@pytest.mark.parametrize("shardsResult", ["failure", "cancelled", "skipped", ""])
def testWindowsGateRejectsIncompleteShardJobs(monkeypatch, shardsResult):
    gate = workflowFile("ci.yml")["jobs"]["windows-summary"]["steps"][0]
    monkeypatch.setenv("SCOPE_RESULT", "success")
    monkeypatch.setenv("RUN_FULL", "true")
    monkeypatch.setenv("SHARDS_RESULT", shardsResult)
    with pytest.raises(SystemExit, match="did not all succeed"):
        exec(compile(gate["run"], "ci-windows-gate", "exec"))


@pytest.mark.parametrize("runFull,shardsResult", [("true", "success"), ("false", "skipped")])
def testWindowsGateAcceptsCompleteJobsOrExplicitlyReportsUnrunChecks(monkeypatch, tmp_path, runFull, shardsResult):
    gate = workflowFile("ci.yml")["jobs"]["windows-summary"]["steps"][0]
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("SCOPE_RESULT", "success")
    monkeypatch.setenv("RUN_FULL", runFull)
    monkeypatch.setenv("SHARDS_RESULT", shardsResult)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    exec(compile(gate["run"], "ci-windows-gate", "exec"))
    if runFull == "false":
        assert "Full source checks were not run" in summary.read_text()


@pytest.mark.parametrize("filename", ["ci.yml", "release-windows.yml"])
@pytest.mark.parametrize("platform,width,height,valid", [
    ("windows", 1920, 1080, True), ("offscreen", 1920, 1080, False),
    ("windows", 1280, 1080, False), ("windows", 1920, 720, False),
])
def testNativeDesktopCheckRejectsWrongBackendOrSmallScreen(monkeypatch, filename, platform, width, height, valid):
    job = workflowFile(filename)["jobs"]["windows" if filename == "ci.yml" else "validate"]
    check = next(step for step in job["steps"] if "QApplication" in step.get("run", ""))
    geometry = SimpleNamespace(width=lambda: width, height=lambda: height, getRect=lambda: (0, 0, width, height))
    screen = SimpleNamespace(geometry=lambda: geometry, availableGeometry=lambda: geometry, devicePixelRatio=lambda: 1)
    application = SimpleNamespace(platformName=lambda: platform, primaryScreen=lambda: screen)
    widgets = ModuleType("PySide2.QtWidgets")
    widgets.QApplication = lambda _: application
    monkeypatch.setitem(sys.modules, "PySide2.QtWidgets", widgets)
    code = compile(check["run"], "ci-native-desktop", "exec")
    if valid:
        exec(code)
    else:
        with pytest.raises(AssertionError):
            exec(code)


def testReleaseUsesNativeWindowsDesktopAndPreservesFullSourceGate():
    workflow = workflowFile("release-windows.yml")
    job = workflow["jobs"]["validate"]
    assert job["runs-on"] == "windows-latest"
    assert job["env"]["QT_QPA_PLATFORM"] == "windows"
    assert isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] > 0
    projectCheckStep(job)
    assert workflow["jobs"]["package"]["needs"] == "validate"
    assertFailureArtifacts(job, {"build/ci-results.xml", "build/ci-failures.log"})
