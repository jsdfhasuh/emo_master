from pathlib import Path

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


def testNativeWindowsCiProvidesDesktopWithoutReducingTestSelection():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["validate"]
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    setup = steps["Configure native Windows test desktop"]
    assert setup["if"] == "runner.os == 'Windows'"
    assert setup["run"] == "Set-DisplayResolution -Width 1920 -Height 1080 -Force"
    check = steps["Verify native Windows test desktop"]
    assert check["if"] == setup["if"]
    compile(check["run"], "ci-native-desktop", "exec")
    assert 'geometry.width() >= 1920 and geometry.height() >= 1080' in check["run"]
    assert steps["Run project checks"]["run"] == "python scripts/ci_check.py"
    upload = steps["Upload test results"]
    assert upload["if"] == "always()"
    assert upload["with"]["path"].splitlines() == ["build/ci-results.xml", "build/ci-failures.log"]


def testReleaseUsesNativeWindowsDesktopAndPreservesFullSourceGate():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/release-windows.yml").read_text())
    job = workflow["jobs"]["validate"]
    assert job["runs-on"] == "windows-latest"
    assert job["env"]["QT_QPA_PLATFORM"] == "windows"
    assert job["timeout-minutes"] == 90
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    assert steps["Configure native Windows test desktop"]["run"] == "Set-DisplayResolution -Width 1920 -Height 1080 -Force"
    check = steps["Verify native Windows test desktop"]
    compile(check["run"], "release-native-desktop", "exec")
    assert 'geometry.width() >= 1920 and geometry.height() >= 1080' in check["run"]
    assert steps["Run project checks and ONNX tests"]["run"] == "python scripts/ci_check.py"
    assert workflow["jobs"]["package"]["needs"] == "validate"
    upload = steps["Upload test results"]
    assert upload["if"] == "always()"
    assert upload["with"]["path"].splitlines() == ["build/ci-results.xml", "build/ci-failures.log"]
