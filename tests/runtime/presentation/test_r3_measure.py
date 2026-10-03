"""Pure harness accounting tests; no performance workload or devices."""
import json

import pytest

from scripts import r3_measure as measure


def testBoundedTraceReportsOverflowAndRestoresNestedContext(tmp_path, monkeypatch):
    monkeypatch.setenv("EMO_R3_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(measure, "TRACE_LIMIT", 2)
    trace = measure.Trace("test")
    trace.call("outer", lambda: trace.call("inner", lambda: 7), metadata={"ordinal": 2})
    trace.call("overflow", lambda: None)
    trace.save()
    payload = json.loads(next(tmp_path.glob("trace-*.json")).read_text())
    assert len(payload["rows"]) == 2 and payload["dropped_rows"] == 1
    assert payload["rows"][0]["parent_stage"] == "outer"
    assert payload["rows"][0]["ordinal"] == 2
    assert trace.local.metadata == {}


def testPatchRestoredAfterFailure():
    class Owner:
        value = "original"
    with pytest.raises(RuntimeError), measure.patches() as patch:
        patch(Owner, "value", "temporary")
        raise RuntimeError("stop")
    assert Owner.value == "original"


def testStagesSeparateWarmupAndUnattributedWithoutDroppingRows():
    traces = [{"role": "job", "rows": [
        {"stage": "legacy", "elapsed_ms": 1, "ordinal": 1},
        {"stage": "legacy", "elapsed_ms": 3, "ordinal": 2},
        {"stage": "legacy", "elapsed_ms": 5}]}]
    result = measure.summaryStages(traces, 1)
    assert {row["phase"]: row["sum_ms"] for row in result} == {
        "warmup": 1, "measurement": 3, "unattributed": 5}
    assert sum(row["count"] for row in result) == 3


def testMissingConsumerAndPaintDenominatorsAreRetained():
    result = {"raw_timings": [1, 2], "consumers": [{"rows": [
        {"ordinal": 2, "key": "two", "applied_to_live": False}]}],
        "ui": [{"raw_rows": [{"key": "two", "gui_ns": 1}]}]}
    measure.addDenominators(result, 4, 1)
    assert result["denominators"]["measured_inputs"] == 3
    assert result["denominators"]["missing_execution_count"] == 2
    assert result["consumers"][0]["missing_measured_ordinals"] == [3, 4]
    assert result["consumers"][0]["received_not_applied"] == 1
    assert result["ui"][0]["missing_painted_ordinals"] == [2, 3, 4]
    assert result["phases"]["export_deadline_seconds"] == .5


def testDiskPreflightFailsBeforeWorkload(tmp_path, monkeypatch):
    monkeypatch.setattr(measure.shutil, "disk_usage", lambda _path: type("Disk", (), {"free": 1, "total": 2})())
    with pytest.raises(RuntimeError, match="insufficient free disk"):
        measure.diskPreflight(tmp_path)


def testExporterTraceUsesExistingReplyAndBoundsReceiver(monkeypatch):
    class Connection:
        def send(self, value):
            self.value = value

        def recv(self):
            return self.value

    connection = Connection()
    trace = measure.Trace("exporter")
    trace.call("export.png_encode", lambda: None)
    wrapper = measure.MeasuredConnection(connection, trace)
    wrapper.send({"ok": True})
    assert trace.rows == []
    pid = connection.value["r3_trace"]["pid"]
    records = {pid: {"rows": [], "dropped_rows": 0}}
    monkeypatch.setattr(measure, "ENCODER_TRACES", records)
    monkeypatch.setattr(measure, "TRACE_LIMIT", 0)
    assert measure.TraceReceiver(connection).recv() == {"ok": True}
    assert records[pid]["rows"] == [] and records[pid]["dropped_rows"] == 1


def testWatchdogCapsLogAndStopsOverflowProcess(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "LOG_BYTES", 1024)
    result = measure.supervisedTrial([measure.sys.executable, "-c",
        "import sys,time; sys.stdout.write('x'*4096); sys.stdout.flush(); time.sleep(30)"], tmp_path)
    assert result["status"] == "FAIL" and result["log_overflow"]
    assert result["log_bytes"] == 1024
    assert not result["timeout"]


def testRunPreservesTrialPayloadWhenEnrichingTraceRows(tmp_path, monkeypatch):
    from argparse import Namespace
    import p2_validate

    monkeypatch.setattr(p2_validate, "identity", lambda: {"digest": "same"})
    monkeypatch.setattr(p2_validate, "git", lambda *_args: "test")

    def fakeTrial(command, directory):
        enabled = command[command.index("--enabled") + 1] == "1"
        consumer = {"unique_received": 2, "unique_decoded": 2, "unique_applied": 2, "p95_model_ms": 1}
        payload = {"expected": 2, "p95_execution_ms": 10 if enabled else 8,
            "job_status": "COMPLETED", "executed": 2, "achieved_hz": 5,
            "max_schedule_lateness_ms": 0, "age_samples_ms": [[2, 2]],
            "record_retention": {"capacity_reached": False},
            "consumers": [consumer, consumer] if enabled else []}
        (directory / "trial.json").write_text(json.dumps(payload))
        for index, role in enumerate(("exporter", "exporter", "owner", "job")):
            trace = {"role": role, "dropped_rows": 0, "rows": [
                {"stage": "example", "ordinal": 1, "result_key": "one", "elapsed_ms": 2}]}
            (directory / f"trace-{index}.json").write_text(json.dumps(trace))
        return {"status": "PASS"}

    monkeypatch.setattr(measure, "supervisedTrial", fakeTrial)
    args = Namespace(output=tmp_path, family="capture", count=2, warmup=0,
                     pairs=1, qt_platform="offscreen")
    assert measure.run(args) == 0
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert evidence["measurement_status"] == "VALID"
    assert evidence["performance_status"] == "NOT_ASSESSED"
    assert evidence["pairs"][0]["regression_percent"] == 25
    assert all(trial["trace_complete"] for trial in evidence["trials"])


def testFailedCleanupPreservesWorkloadUntilOwnerAndTreeProof(tmp_path):
    workload = tmp_path / "work-trial"
    workload.mkdir()
    owned = workload / "still-owned.bin"
    owned.write_bytes(b"preserve")
    # An exit alone cannot prove original finally completed.
    first = measure.cleanupTrialDirectory(tmp_path, True)
    assert not first["owner_retirement_verified"] and owned.exists()
    (tmp_path / "owners-closed.json").write_text(json.dumps({
        "all_original_trial_close_calls_returned": True}))
    # A successful close marker alone cannot prove outer tree retirement.
    second = measure.cleanupTrialDirectory(tmp_path, False)
    assert not second["owner_retirement_verified"] and owned.exists()
    third = measure.cleanupTrialDirectory(tmp_path, True)
    assert third["owner_retirement_verified"] and not workload.exists()
