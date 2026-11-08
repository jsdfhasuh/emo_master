from pathlib import Path
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
    assert [name for _, name in calls] == ["proto-drift", "ruff", "mypy", "pytest"]


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


def projectCheckStep(job):
    matches = [step for step in job["steps"] if "scripts/ci_check.py" in step.get("run", "")]
    assert len(matches) == 1
    # No test selection or alternate entry point is passed to the full source check.
    command = shlex.split(matches[0]["run"])
    assert command[0] in {"python", "python3"}
    assert command[1:] == ["scripts/ci_check.py"]
    return matches[0]


def assertFailureArtifacts(job, paths):
    uploads = [step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert any("always()" in step.get("if", "")
               and paths <= set(step["with"]["path"].splitlines()) for step in uploads)


def testNativeWindowsCiKeepsFullCoverageAndFailureEvidenceWhenSelected():
    workflow = workflowFile("ci.yml")
    job = workflow["jobs"]["validate"]
    assert job["timeout-minutes"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert job["strategy"]["fail-fast"] is False
    projectCheckStep(job)
    assertFailureArtifacts(job, {"build/ci-results.xml", "build/ci-failures.log", "build/two-job-observation.json"})


@pytest.mark.parametrize("scopeResult,runFull", [("failure", ""), ("success", ""), ("success", "unknown")])
def testFailedOrMissingScopeCannotProduceSuccessfulSourceCheck(monkeypatch, scopeResult, runFull):
    job = workflowFile("ci.yml")["jobs"]["validate"]
    report = next(step for step in job["steps"] if "SCOPE_RESULT" in step.get("env", {}))
    monkeypatch.setenv("SCOPE_RESULT", scopeResult)
    monkeypatch.setenv("RUN_FULL", runFull)
    with pytest.raises(SystemExit):
        exec(compile(report["run"], "ci-selection-result", "exec"))


@pytest.mark.parametrize("filename", ["ci.yml", "release-windows.yml"])
@pytest.mark.parametrize("platform,width,height,valid", [
    ("windows", 1920, 1080, True), ("offscreen", 1920, 1080, False),
    ("windows", 1280, 1080, False), ("windows", 1920, 720, False),
])
def testNativeDesktopCheckRejectsWrongBackendOrSmallScreen(monkeypatch, filename, platform, width, height, valid):
    job = workflowFile(filename)["jobs"]["validate"]
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
