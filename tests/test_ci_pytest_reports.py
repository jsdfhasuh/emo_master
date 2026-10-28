import os
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import ci_pytest_reports


@pytest.mark.parametrize("handlesGrow", [False, True])
def testNativeOwnerFailureCollectsOriginalSnapshotsEvenWhenHandlesFailFirst(monkeypatch, handlesGrow):
    monkeypatch.setenv("EMO_A18_STRICT_PROCESS_RESOURCES", "1")
    from scripts import r3_native_thread_origins
    from tests.ui.presentation import test_retained_acceptance_lifecycle as owners

    before = {"handles": 10, "widgets": 5, "windows": 1, "native_threads": 1,
              "python_threads": 1, "native_thread_ids": frozenset((1,)),
              "python_thread_objects": frozenset()}
    after = dict(before, handles=14 if handlesGrow else 10,
                 native_threads=2, native_thread_ids=frozenset((1, 7)))
    monkeypatch.setattr(owners, "nativeCounts", lambda *_args, **_kwargs: after)
    calls = []
    monkeypatch.setattr(r3_native_thread_origins, "collect", lambda ids: calls.append(ids) or {
        "status": "queried", "threads": [{"native_thread_id": 7, "module": "test.dll"}]})
    with pytest.raises(AssertionError, match="handles" if handlesGrow else "native_thread_ids") as failure:
        owners.assertStableOwners(None, before, None, None, phase="navigation", index=299)
    originalFailure = failure.value
    report = SimpleNamespace(when="call", failed=True, sections=[])
    hook = ci_pytest_reports.FailureReports(Path("unused")).pytest_runtest_makereport(
        SimpleNamespace(nodeid=ci_pytest_reports.NATIVE_OWNER_TEST), SimpleNamespace(excinfo=failure))
    next(hook)
    with pytest.raises(StopIteration) as returned:
        hook.send(report)
    assert returned.value.value is report and report.failed
    assert failure.value is originalFailure and calls == [[7]]
    name, text = report.sections[0]
    evidence = json.loads(text)
    assert name == "A18 post-failure native origins"
    assert evidence["phase"] == "navigation" and evidence["index"] == 299
    assert evidence["before"]["handles"] == 10
    assert evidence["after"]["handles"] == after["handles"]
    assert evidence["selected_native_thread_ids"] == [7]
    assert evidence["evidence"]["threads"][0]["module"] == "test.dll"
    assert "python_thread_objects" not in text


@pytest.mark.parametrize("nodeid,when,failed", [
    (ci_pytest_reports.NATIVE_OWNER_TEST, "call", False),
    (ci_pytest_reports.NATIVE_OWNER_TEST, "setup", True),
    ("other.py::testOther", "call", True),
])
def testNativeEvidenceOnlyRunsForTheFailedTargetCall(monkeypatch, nodeid, when, failed):
    monkeypatch.setattr(ci_pytest_reports, "nativeOwnerFailureEvidence",
                        lambda *_: pytest.fail("unexpected native query"))
    report = SimpleNamespace(when=when, failed=failed, sections=[])
    hook = ci_pytest_reports.FailureReports(Path("unused")).pytest_runtest_makereport(
        SimpleNamespace(nodeid=nodeid), SimpleNamespace(excinfo=object()))
    next(hook)
    with pytest.raises(StopIteration) as returned:
        hook.send(report)
    assert returned.value.value is report and report.sections == []


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def testNativeEvidenceErrorsKeepTheOriginalFailedReport(monkeypatch, error):
    def unavailable(_excinfo):
        raise error("private failure details")
    monkeypatch.setattr(ci_pytest_reports, "nativeOwnerFailureEvidence", unavailable)
    report = SimpleNamespace(when="call", failed=True, sections=[])
    hook = ci_pytest_reports.FailureReports(Path("unused")).pytest_runtest_makereport(
        SimpleNamespace(nodeid=ci_pytest_reports.NATIVE_OWNER_TEST), SimpleNamespace(excinfo=object()))
    next(hook)
    with pytest.raises(StopIteration) as returned:
        hook.send(report)
    assert returned.value.value is report and report.failed
    assert json.loads(report.sections[0][1])["status"] == "diagnostic_unavailable"
    assert "private failure" not in report.sections[0][1]


def testSuccessfulReportsDoNotCreateFalseFailureEvidence(tmp_path, monkeypatch, capsys, pytestconfig):
    monkeypatch.chdir(tmp_path)
    reporter = ci_pytest_reports.FailureReports(Path("build/ci-failures.log"))
    reporter.pytest_sessionstart(SimpleNamespace(config=pytestconfig))
    reporter.pytest_runtest_logreport(SimpleNamespace(failed=False))
    assert Path("build/ci-failures.log").read_text(encoding="utf-8") == ""
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("consoleEncoding", ["utf-8", "cp1252"])
def testFailureDetailsAreSavedBeforeNextTestAndKeepExitCode(tmp_path, consoleEncoding):
    probe = tmp_path / "test_failure_probe.py"
    probe.write_text('''from pathlib import Path
import pytest

def test_1_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    print("captured failure output: \u4e2d\u6587")
    assert False, "original assertion survives: \u542f\u52a8\u5931\u8d25"

def test_2_report_already_saved():
    text = Path("build/ci-failures.log").read_text(encoding="utf-8")
    assert "test_1_failure (call)" in text
    assert "original assertion survives" in text
    assert "captured failure output: \u4e2d\u6587" in text
    assert "original assertion survives: \u542f\u52a8\u5931\u8d25" in text

@pytest.fixture
def broken_setup():
    raise ValueError("original setup error survives: \u4e2d\u6587")

def test_3_setup_error(broken_setup):
    pass
''', encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="",
               PYTHONIOENCODING=consoleEncoding + ":strict")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short",
        "-p", "scripts.ci_pytest_reports", str(probe)], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding=consoleEncoding, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr
    assert "1 failed, 1 passed, 1 error" in result.stdout
    text = (tmp_path / "build/ci-failures.log").read_text(encoding="utf-8")
    assert "test_1_failure (call)" in text
    assert "test_3_setup_error (setup)" in text
    assert "original assertion survives: \u542f\u52a8\u5931\u8d25" in text
    assert "original setup error survives: \u4e2d\u6587" in text
