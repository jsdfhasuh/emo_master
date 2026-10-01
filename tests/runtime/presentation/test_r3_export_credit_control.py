"""Pure-data control orchestration; no measurement workload."""
import json
from types import SimpleNamespace

import pytest

from scripts import r3_export_credit_control as control


def fineRows(identity):
    rows = []
    for stage, count in control.ADOPT_PHASE_COUNTS.items():
        for _ in range(count):
            row = {**identity, "stage": stage, "outcome": "OK", "start_ns": 110,
                   "end_ns": 190, "thread_cpu_ns": 0}
            if stage.endswith((".read", ".hash_update")):
                reading = stage.endswith(".read")
                row.update(span_kind="first_to_last_call_envelope", aggregate={
                    "calls": 2 if reading else 1, "failures": 0, "wall_sum_ns": 0,
                    "wall_max_ns": 0, "thread_cpu_sum_ns": 0, "thread_cpu_max_ns": 0,
                    "bytes": 123, "max_chunk_bytes": 123, "empty_calls": 1 if reading else 0})
            rows.append(row)
    return rows


def testCreditControlKeepsOriginalLoadAndDisablesRpcMarkers(tmp_path):
    off = control.childCommand(tmp_path, "windows", "off")
    on = control.childCommand(tmp_path, "windows", "on")
    assert on == off + ["--export-credit-trace", "--capture-credit-trace"]
    assert "--passive-rpc-markers" not in on
    for flag, value in (("--count", "96"), ("--warmup", "8"), ("--arm", "none_qt"),
                        ("--qt-platform", "windows")):
        assert on[on.index(flag)+1] == value
    assert control.ORDER == ("off", "on", "on", "off")
    with pytest.raises(ValueError):
        control.childCommand(tmp_path, "windows", "invalid")


@pytest.mark.parametrize("fault", [None, "coverage", "credit", "input", "owner", "markers", "wrong_mode", "raw_empty", "wrong_job", "duplicate_release", "capture_empty", "capture_wrong_lane", "capture_wrong_job", "fine_missing", "fine_sum", "fine_count", "fine_bytes", "fine_outside", "export_lane", "join_slot", "capture_time_missing", "capture_time_reverse", "capture_time_elapsed", "capture_cpu_invalid"])
def testControlPreservesMissingFrameAndObserverFailures(tmp_path, monkeypatch, fault):
    calls = []
    monkeypatch.setattr(control, "identity", lambda: {"source": "fixed"})
    monkeypatch.setattr(control, "git", lambda *_: "test")
    monkeypatch.setattr(control.policy.base, "diskPreflight", lambda _: {"free_bytes": 1024**3})
    monkeypatch.setattr(control.policy, "exactCoverage", lambda _: fault != "coverage")
    monkeypatch.setattr(control, "analyze_calls", lambda *_, **__: {"status": "COMPLETE"})

    def supervised(command, directory):
        calls.append(command)
        rows = [{"stage": "client.asset_rpc_split", "asset_bytes": 123,
                 "asset_sha256": "a"*64} for _ in range(192)]
        split = {"rows": rows, "features": {"passive_rpc_markers": fault == "markers"}}
        (directory/"asset-split.json").write_text(json.dumps(split))
        return {"status": "PASS", "owner_retirement_verified": fault != "owner"}

    monkeypatch.setattr(control.policy.base, "supervisedTrial", supervised)

    def evidence(directory, output, warmup, arm):
        assert warmup == 8 and arm == "none_qt"
        payload = {"input_sha256": "b"*64 if fault != "input" or len(calls) != 4 else "c"*64,
            "job": "job", "job_status": "COMPLETED", "executed": 88, "achieved_hz": 5,
            "request": {"expected_runtime_instance_id": "runtime"},
            "max_schedule_lateness_ms": 0, "p95_execution_ms": 5,
            "phases": {"raw_all_window": {"max_ui_ms": 800}},
            "consumers": [{"p95_model_ms": 225}]*2,
            "ui": [{"p95_scope_to_gui_ms": 250, "p95_scope_to_paint_ms": 270}]*2,
            "observed_result_outcomes": {f"result-{ordinal}": {"ordinal": ordinal, "source_outcomes": {"image": {
                "state": "UNAVAILABLE" if fault == "coverage" else "AVAILABLE",
                "reason": "BUDGET_EXCEEDED" if fault == "coverage" else None}}} for ordinal in range(1, 97)}}
        if "--export-credit-trace" in calls[-1] or fault == "wrong_mode":
            payload["export_credit_trace"] = {"enabled": True, "complete": fault != "credit"}
            rows = [{"pool_id": 1, "job_id": "other-job" if fault == "wrong_job" else "job",
                     "result_key": f"result-{ordinal}", "source_id": "image", "slot": 0, "lane": 0,
                     "stage": stage, "outcome": "OK", "export_outcome": "AVAILABLE",
                     "start_ns": 100, "end_ns": 200}
                    for ordinal in range(1, 97) for stage in control.REQUIRED_CREDIT_STAGES]
            if fault != "fine_missing":
                for ordinal in range(1, 97):
                    rows.extend(fineRows({"pool_id": 1, "job_id": "job", "result_key": f"result-{ordinal}",
                                         "source_id": "image", "slot": 0, "lane": 0}))
            if fault in {"fine_sum", "fine_count", "fine_bytes", "fine_outside"}:
                fine = next(row for row in rows if row["stage"] == "parent.asset_adopt.read")
                if fault == "fine_sum":
                    fine["aggregate"]["wall_sum_ns"] = 10**12
                elif fault == "fine_count":
                    fine["aggregate"]["calls"] = 4
                elif fault == "fine_bytes":
                    fine["aggregate"]["bytes"] = 124
                else:
                    fine["start_ns"] = fine["end_ns"] = 10**12
            if fault in {"export_lane", "join_slot"}:
                for row in rows:
                    row["lane" if fault == "export_lane" else "slot"] = 1
            if fault == "raw_empty":
                rows.clear()
            elif fault == "duplicate_release":
                rows.append(next(dict(row) for row in rows if row["stage"] == "parent.credit_release"))
            (directory/"export-credit.json").write_text(json.dumps({"role": "export_credit", "rows": rows}))
            attempts = [{"stage": "producer.capture_credit_acquire", "runtime_id": "runtime",
                         "job_id": "other-job" if fault == "capture_wrong_job" else "job",
                         "result_key": f"result-{ordinal}", "result_ordinal": ordinal,
                         "source_id": "image", "slot": 0, "lane": 1 if fault == "capture_wrong_lane" else 0,
                         "capacity": 8*1024*1024, "offset": 0, "raw_bytes": 1920*1080*3,
                         "start_ns": 100, "end_ns": 200, "elapsed_ms": .0001, "thread_cpu_ns": 0,
                         "acquire_args": [False], "acquire_kwargs": {}, "outcome": "OK",
                         "acquired": fault != "coverage"} for ordinal in range(1, 97)]
            if fault == "capture_empty":
                attempts.clear()
            elif fault == "capture_time_missing":
                attempts[0].pop("start_ns")
            elif fault == "capture_time_reverse":
                attempts[0]["end_ns"] = 99
            elif fault == "capture_time_elapsed":
                attempts[0]["elapsed_ms"] = 999
            elif fault == "capture_cpu_invalid":
                attempts[0]["thread_cpu_ns"] = -1
            (directory/"capture-credit-123.json").write_text(json.dumps({"role": "capture_credit", "rows": attempts}))
            payload["capture_credit_trace"] = {"enabled": True, "complete": True}
        (directory/"trial.json").write_text(json.dumps(payload))
        return payload, {"trace_complete": True, "raw_evidence": str((directory/"trial.json").relative_to(output))}

    monkeypatch.setattr(control.policy, "trialEvidence", evidence)
    assert control.run(SimpleNamespace(output=tmp_path, qt_platform="windows")) == int(fault is not None)
    result = json.loads((tmp_path/"evidence.json").read_text())
    assert result["measurement_status"] == ("INVALID" if fault else "VALID")
    assert result["performance_status"] == "NOT_ASSESSED"
    assert ["--export-credit-trace" in command for command in calls] == [False, True, True, False]
    assert all(row["delivery"]["consumer_p95_model_ms"] == [225, 225] for row in result["trials"])
    if fault == "coverage":
        assert all(row["source_outcomes"] == {"UNAVAILABLE/BUDGET_EXCEEDED": 96} for row in result["trials"])
        assert all(not row["exact_ordinal_coverage"] for row in result["trials"])
    if fault in {"raw_empty", "wrong_job", "duplicate_release", "fine_missing", "fine_sum", "fine_count", "fine_bytes", "fine_outside", "export_lane"}:
        assert all(not row["credit_identity_coverage"]["complete"] for row in result["trials"] if row["observer_mode"] == "on")
    if fault == "join_slot":
        assert all(not row["capture_export_join"]["complete"] for row in result["trials"] if row["observer_mode"] == "on")
    if fault in {"capture_empty", "capture_wrong_lane", "capture_wrong_job", "capture_time_missing", "capture_time_reverse", "capture_time_elapsed", "capture_cpu_invalid"}:
        assert all(not row["capture_identity_coverage"]["complete"] for row in result["trials"] if row["observer_mode"] == "on")
    if fault == "coverage":
        assert all(row["capture_identity_coverage"]["complete"] for row in result["trials"] if row["observer_mode"] == "on")
        assert all(row["capture_identity_coverage"]["refused"] == 96 for row in result["trials"] if row["observer_mode"] == "on")
    assert len(list(tmp_path.glob("trial-*/trial.json"))) == 4


def testBoundarySummaryDoesNotInventReleasedCreditOrDroppedExport():
    payload = {"observed_result_outcomes": {
        "available": {"ordinal": 38, "source_outcomes": {"image": {"state": "AVAILABLE", "reason": ""}}},
        "gated": {"ordinal": 39, "source_outcomes": {"image": {"state": "UNAVAILABLE", "reason": "BUDGET_EXCEEDED"}}}}}
    identity = {"pool_id": 1, "job_id": "job", "result_key": "available", "source_id": "image", "slot": 0, "lane": 0}
    rows = [{**identity, "stage": stage, "start_ns": start, "end_ns": end, "outcome": outcome}
            for stage, start, end, outcome in (
                ("parent.pipe_recv", 100, 200, "OK"),
                ("parent.export_callback", 190, 600, "OK"),
                ("parent.asset_adopt", 400, 500, "OK"),
                ("parent.credit_release", 610, 630, "ValueError"))]
    report = control.creditBoundaries(payload, {"rows": rows})
    assert len(report["exports"]) == 1
    item = report["exports"][0]
    assert item["ordinal"] == 38
    assert item["boundaries_ns"]["credit_release_returned"] is None
    assert item["intervals_ms"]["reply_to_callback"] == -10/1e6
    assert item["intervals_ms"]["callback_return_to_credit_release"] is None
    assert report["nonavailable_sources"] == [{"result_key": "gated", "ordinal": 39,
        "state": "UNAVAILABLE", "reason": "BUDGET_EXCEEDED"}]
