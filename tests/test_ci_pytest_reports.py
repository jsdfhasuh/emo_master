import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from scripts import ci_pytest_reports


def testSuccessfulReportsDoNotCreateFalseFailureEvidence(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    reporter = ci_pytest_reports.FailureReports(Path("build/ci-failures.log"))
    reporter.pytest_sessionstart(None)
    reporter.pytest_runtest_logreport(SimpleNamespace(failed=False))
    assert Path("build/ci-failures.log").read_text(encoding="utf-8") == ""
    assert capsys.readouterr().out == ""


def testFailureDetailsAreSavedBeforeNextTestAndKeepExitCode(tmp_path):
    probe = tmp_path / "test_failure_probe.py"
    probe.write_text('''from pathlib import Path
import pytest

def test_1_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    print("captured failure output")
    assert False, "original assertion survives"

def test_2_report_already_saved():
    text = Path("build/ci-failures.log").read_text(encoding="utf-8")
    assert "test_1_failure (call)" in text
    assert "original assertion survives" in text
    assert "captured failure output" in text

@pytest.fixture
def broken_setup():
    raise ValueError("original setup error survives")

def test_3_setup_error(broken_setup):
    pass
''', encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short",
        "-p", "scripts.ci_pytest_reports", str(probe)], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 1 passed, 1 error" in result.stdout
    text = (tmp_path / "build/ci-failures.log").read_text(encoding="utf-8")
    assert "test_1_failure (call)" in text
    assert "test_3_setup_error (setup)" in text
    assert "original assertion survives" in text
    assert "original setup error survives" in text
