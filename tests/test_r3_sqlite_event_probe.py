"""The diagnostic never changes event durability, loses rows or overwrites data."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r3_sqlite_event_probe.py"


@pytest.mark.parametrize("mode", ["baseline", "keeper", "reuse"])
def testDiagnosticArmsPreserveEveryCommittedSequence(tmp_path, mode):
    output = tmp_path / mode
    run = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(ROOT),
        "--output", str(output), "--mode", mode, "--count", "5", "--seconds", "1"],
        capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    completed = report["completed"]
    # The time bound is an alternative stopping condition, not a throughput
    # guarantee on a loaded runner. Every completed commit must still exist.
    assert 1 <= completed <= 5
    assert report["stopped_at_time_bound"] is (completed < 5)
    assert report["verified_row_counts"] == [completed, completed, 1, completed]
    assert report["verified"] and report["source_stable"]
    assert report["pragmas"]["journal_mode"] == "wal"
    assert report["pragmas"]["synchronous"] == 2  # FULL remains unchanged.
    assert report["stages"]["sql.commit"]["count"] == completed
    assert report["connections_during_measurement"]["created"] == (1 if mode == "reuse" else completed)
    assert report["connections_after_cleanup"]["live"] == 0
    assert report["cleanup_errors"] == []
    assert report["cpu_elapsed_seconds"] >= 0


def testDiagnosticRefusesExistingOutputWithoutChangingIt(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    run = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(ROOT),
        "--output", str(output), "--mode", "baseline", "--count", "1"],
        capture_output=True, text=True, timeout=20)
    assert run.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert list(output.iterdir()) == [marker]


def testDiagnosticRejectsUnboundedCountBeforeCreatingOutput(tmp_path):
    output = tmp_path / "oversized"
    run = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(ROOT),
        "--output", str(output), "--mode", "baseline", "--count", "10001"],
        capture_output=True, text=True, timeout=20)
    assert run.returncode == 2 and not output.exists()


def testFailedNativeCloseIsNotCountedAsRetired(monkeypatch):
    from scripts import r3_sqlite_event_probe as probe
    connection = probe.TimedConnection(":memory:")
    original = probe.measured
    def failClose(stage, operation, *args, **kwargs):
        if stage == "sql.explicit_close":
            raise OSError("close unavailable")
        return original(stage, operation, *args, **kwargs)
    try:
        monkeypatch.setattr(probe, "measured", failClose)
        with pytest.raises(OSError, match="close unavailable"):
            connection.close()
        assert connection.probe_token["closed"] is False
    finally:
        monkeypatch.setattr(probe, "measured", original)
        connection.close()
