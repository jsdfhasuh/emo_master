from scripts import ci_check


def testCiPytestReportsNamesAndHasCrossPlatformHangBound(monkeypatch):
    calls = []
    monkeypatch.setattr(ci_check, "runStep", lambda command, name: calls.append((command, name)) or 0)
    assert ci_check.main() == 0
    command, name = calls[-1]
    assert name == "pytest"
    assert command[:3] == [ci_check.sys.executable, "-m", "pytest"]
    assert "-vv" in command and "-x" in command and "--tb=short" in command
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
