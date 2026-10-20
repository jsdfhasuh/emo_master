"""A successful harness invocation must not hide a failed acceptance predicate."""
import json
import sys

import pytest

from scripts import p0_validate as cli


@pytest.mark.parametrize("correctness,performance,strict,code", [
    ("PASS", "PASS", True, 0),
    ("PASS", "FAIL", True, 1),
    ("PASS", "NOT_RUN", True, 1),
    ("FAIL", "PASS", False, 1),
    ("PASS", "FAIL", False, 0),
])
def testGatePreservesRawEvidenceAndDistinguishesExecution(tmp_path, monkeypatch, correctness, performance, strict, code):
    path = tmp_path / "report.json"
    args = ["p0_validate", "--output", str(path), "--scenarios", "benchmark"]
    if strict:
        args.append("--require-performance")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(cli, "identity", lambda: {"head": "fixed"})
    raw = {"correctness_status": correctness, "performance_status": performance, "first_failure": "retained"}
    commands = []
    def run(name, **kwargs):
        commands.append((name, kwargs))
        return json.dumps(raw), "original stderr"
    monkeypatch.setattr(cli, "supervised", run)
    assert cli.main() == code
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["benchmark"]["execution_status"] == "PASS"
    assert saved["benchmark"]["evidence"] == raw
    assert saved["benchmark"]["stderr"] == "original stderr"
    assert saved["exit_code"] == code and saved["require_performance"] is strict
    assert saved["p0_exit"] == "NOT_EVALUATED"  # This flag is not a release/Qt/hardware approval.
    assert commands == [("benchmark", {"timeout": 432, "parameters": {"count": 96, "rounds": 3, "warmup": 8}})]


def testStrictPerformanceCannotSilentlyOmitBenchmark(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["p0_validate", "--output", str(tmp_path / "report.json"),
                                    "--scenarios", "exports", "--require-performance"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and not (tmp_path / "report.json").exists()
