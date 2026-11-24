import json
from pathlib import Path
import os
import subprocess
import sys
import time


def launch(tmp_path, test_source):
    (tmp_path / "test_phase.py").write_text(test_source, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import pytest_evidence\npytest_evidence.STACK_PERIOD_SECONDS = 0.05\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    process = subprocess.Popen([sys.executable, "-m", "pytest", "-p", "pytest_evidence",
                                "--emo-pytest-evidence-dir", str(tmp_path), "-q"], cwd=tmp_path,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    return process


def journal(tmp_path, name):
    return [json.loads(line) for line in (tmp_path / name).read_text(encoding="utf-8").splitlines()]


def test_records_success_failure_and_phase_durations(tmp_path):
    child = launch(tmp_path, "def test_ok(): pass\ndef test_wrong(): assert 2 == 3\n")
    try:
        assert child.wait(timeout=20) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    phases = journal(tmp_path, "pytest-phases.jsonl")
    assert {entry["items"] for entry in phases if entry["event"] == "collection_finish"} == {2}
    for name in ("test_ok", "test_wrong"):
        own = [entry for entry in phases if entry.get("nodeid","").endswith("::" + name)]
        assert [(r["event"], r["phase"]) for r in own] == [
            (event, phase) for phase in ("setup", "call", "teardown") for event in ("phase_start", "phase_end")]
        assert all(r["duration_ns"] >= 0 for r in own if r["event"] == "phase_end")
    failure, = [entry for entry in journal(tmp_path, "pytest-failures.jsonl") if entry["event"] == "failure"]
    assert failure["nodeid"].endswith("::test_wrong") and failure["phase"] == "call"
    assert "assert 2 == 3" in failure["detail"]


def test_active_phase_and_stack_are_retained_when_process_is_killed(tmp_path):
    child = launch(tmp_path, "def test_blocked():\n    import time\n    time.sleep(30)\n")
    try:
        deadline = time.monotonic() + 15
        stacks = tmp_path / "pytest-stacks.log"
        while time.monotonic() < deadline:
            if stacks.exists() and "in test_blocked" in stacks.read_text(encoding="utf-8", errors="replace"):
                break
            assert child.poll() is None
            time.sleep(0.05)
        else:
            raise AssertionError("no independent stack captured")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    phases = journal(tmp_path, "pytest-phases.jsonl")
    assert phases[-1]["event"] == "phase_start" and phases[-1]["phase"] == "call"
    assert phases[-1]["nodeid"].endswith("::test_blocked")
