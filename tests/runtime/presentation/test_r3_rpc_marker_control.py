"""The paired runner delegates unchanged workload arguments; no real workload."""
import json
from types import SimpleNamespace

import pytest

from scripts import r3_rpc_marker_control as control


def testFixedControlOrderAndChildArgumentsPreserveOriginalContract(tmp_path):
    assert control.ORDER == ("off", "on", "on", "off")
    off = control.childCommand(tmp_path, "windows", "off")
    on = control.childCommand(tmp_path, "windows", "on")
    assert on == off + ["--passive-rpc-markers"]
    for flag, value in (("--arm", "none_qt"), ("--count", "96"), ("--warmup", "8"), ("--qt-platform", "windows")):
        assert off[off.index(flag)+1] == value
    assert "--asset-split-trace" in off
    assert all("subchannel" not in value for value in on)
    with pytest.raises(ValueError):
        control.childCommand(tmp_path, "windows", "maybe")


def testPayloadFingerprintsRetainAllCallsAndRequireExactHashAndSize():
    rows = [{"stage": "client.asset_rpc_split", "asset_bytes": 123, "asset_sha256": "a"*64} for _ in range(192)]
    assert control.assetFingerprint({"rows": rows})["complete"]
    rows.pop()
    assert not control.assetFingerprint({"rows": rows})["complete"]
    rows.append({"stage": "client.asset_rpc_split", "asset_bytes": 124, "asset_sha256": "a"*64})
    assert not control.assetFingerprint({"rows": rows})["complete"]


@pytest.mark.parametrize("mismatch", [False, True])
def testRunnerUsesFourSeparateSupervisedChildrenAndRetainsRaw(tmp_path, monkeypatch, mismatch):
    calls = []
    monkeypatch.setattr(control, "identity", lambda: {"digest": "fixed"})
    monkeypatch.setattr(control, "git", lambda *_args: "test")
    monkeypatch.setattr(control.policy.base, "diskPreflight", lambda _path: {"free_bytes": 1024**3})
    monkeypatch.setattr(control, "analyze_calls", lambda *_args, **_kwargs: {"status": "COMPLETE"})
    monkeypatch.setattr(control, "analyze", lambda *_args: {"analysis_status": "COMPLETE"})
    monkeypatch.setattr(control.policy, "exactCoverage", lambda _payload: True)

    def supervised(command, directory):
        calls.append(command)
        passive = "--passive-rpc-markers" in command
        digest = "c"*64 if mismatch and len(calls) == 4 else "a"*64
        split = {"rows": [{"stage": "client.asset_rpc_split", "asset_bytes": 123,
            "asset_sha256": digest} for _ in range(192)]}
        if passive:
            split["features"] = {"passive_rpc_markers": True}
        (directory / "asset-split.json").write_text(json.dumps(split))
        return {"status": "PASS", "owner_retirement_verified": True}
    monkeypatch.setattr(control.policy.base, "supervisedTrial", supervised)

    def evidence(directory, output, warmup, arm):
        assert warmup == 8 and arm == "none_qt"
        payload = {"asset_split_trace": {"complete": True}, "input_sha256": "b"*64,
            "job_status": "COMPLETED", "executed": 88, "achieved_hz": 5,
            "max_schedule_lateness_ms": 0, "p95_execution_ms": 2, "consumers": [{"p95_model_ms": 225}]*2,
            "ui": [{"p95_scope_to_gui_ms": 248, "p95_scope_to_paint_ms": 250}]*2,
            "phases": {"raw_all_window": {"max_ui_ms": 800}}, "qt_platform": "windows"}
        (directory / "trial.json").write_text(json.dumps(payload))
        return payload, {"trace_complete": True, "raw_evidence": str((directory / "trial.json").relative_to(output))}
    monkeypatch.setattr(control.policy, "trialEvidence", evidence)
    assert control.run(SimpleNamespace(output=tmp_path, qt_platform="windows")) == int(mismatch)
    report = json.loads((tmp_path / "evidence.json").read_text())
    assert len(calls) == len(report["trials"]) == 4
    assert ["--passive-rpc-markers" in command for command in calls] == [False, True, True, False]
    assert report["measurement_status"] == ("INVALID" if mismatch else "VALID")
    assert report["performance_status"] == "NOT_ASSESSED"
    assert all(row["delivery"]["p95_model_ms"] == [225, 225] for row in report["trials"])
    assert len(list(tmp_path.glob("trial-*/trial.json"))) == 4
