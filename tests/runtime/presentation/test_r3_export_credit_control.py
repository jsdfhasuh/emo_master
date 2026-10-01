"""Pure-data control orchestration; no measurement workload."""
import json
from types import SimpleNamespace

import pytest

from scripts import r3_export_credit_control as control


def testCreditControlKeepsOriginalLoadAndDisablesRpcMarkers(tmp_path):
    off = control.childCommand(tmp_path, "windows", "off")
    on = control.childCommand(tmp_path, "windows", "on")
    assert on == off + ["--export-credit-trace"]
    assert "--passive-rpc-markers" not in on
    for flag, value in (("--count", "96"), ("--warmup", "8"), ("--arm", "none_qt"),
                        ("--qt-platform", "windows")):
        assert on[on.index(flag)+1] == value
    assert control.ORDER == ("off", "on", "on", "off")
    with pytest.raises(ValueError):
        control.childCommand(tmp_path, "windows", "invalid")


@pytest.mark.parametrize("fault", [None, "coverage", "credit", "input", "owner", "markers", "wrong_mode"])
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
            "job_status": "COMPLETED", "executed": 88, "achieved_hz": 5,
            "max_schedule_lateness_ms": 0, "p95_execution_ms": 5,
            "phases": {"raw_all_window": {"max_ui_ms": 800}},
            "consumers": [{"p95_model_ms": 225}]*2,
            "ui": [{"p95_scope_to_gui_ms": 250, "p95_scope_to_paint_ms": 270}]*2,
            "observed_result_outcomes": {"key": {"source_outcomes": {"image": {
                "state": "UNAVAILABLE" if fault == "coverage" else "AVAILABLE",
                "reason": "BUDGET_EXCEEDED" if fault == "coverage" else None}}}}}
        if "--export-credit-trace" in calls[-1] or fault == "wrong_mode":
            payload["export_credit_trace"] = {"enabled": True, "complete": fault != "credit"}
            (directory/"export-credit.json").write_text(json.dumps({"role": "export_credit", "rows": []}))
        payload["observed_result_outcomes"]["key"]["ordinal"] = 9
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
        assert all(row["source_outcomes"] == {"UNAVAILABLE/BUDGET_EXCEEDED": 1} for row in result["trials"])
        assert all(not row["exact_ordinal_coverage"] for row in result["trials"])
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
