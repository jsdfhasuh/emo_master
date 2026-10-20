"""Offline attribution tests using invented timestamps, no benchmark workloads."""
import copy
import json
import sys

import pytest

from scripts import r3_asset_split_analyze as analyze


def trace(capture=True, count=2, passive=False):
    rows = []
    for ordinal in range(1, count+1):
        for channel in (3, 7):
            # Both consumers request the SAME resource; only call_id separates them.
            identity = {"call_id": f"opaque:{ordinal}:{channel}", "channel_id": channel,
                "result_key": f"result-{ordinal}", "ordinal": ordinal, "runtime_instance_id": "runtime",
                "job_id": "job", "resource_id": f"asset-{ordinal}", "asset_bytes": 100,
                "decoder_request": True, "resource_match": True, "runtime_matches_local_server": True}
            origin = (ordinal*1000+channel)*1_000_000
            stamps = {"rpc": (0, 90), "handler": (5, 25), "dispatch": (5, 6), "worker": (7, 22),
                "asset": (8, 21), "file": (10, 15), "server_sha": (16, 20), "serialize": (30, 33),
                "deserialize": (77, 79), "client_sha": (92, 98), "png": (99, 116), "opencv": (100, 115)}
            for name, (start, end) in stamps.items():
                rows.append({**identity, "stage": analyze.STAGES[name], "start_ns": origin+start*1_000_000,
                    "end_ns": origin+end*1_000_000, "outcome": "OK", "thread_cpu_ns":
                    None if name in ("handler", "dispatch") else (end-start)*500_000})
            for phase, start in (("admit", 8), ("retire", 20)):
                rows.append({**identity, "stage": analyze.STAGES["lock"], "lock_phase": phase,
                    "start_ns": origin+start*1_000_000, "end_ns": origin+start*1_000_000+100,
                    "outcome": "OK", "thread_cpu_ns": 101})
            if passive:
                for name, start, end in (("peer", 6, 6), ("loop", 33, 40), ("done", 85, 85)):
                    rows.append({**identity, "stage": analyze.PASSIVE_STAGES[name],
                        "start_ns": origin+start*1_000_000, "end_ns": origin+end*1_000_000,
                        "outcome": "OK", "thread_cpu_ns": None if name != "loop" else 0,
                        **({"peer_token": 1} if name == "peer" else {})})
    payload = {"rows": rows if capture else [], "clock": "same-process perf_counter_ns; synchronous CPU",
        "pid": 456, "source_complete": True, "source_unchanged": True,
        "source_before": {"source": "a"*64}, "source_after": {"source": "a"*64},
        "instrumentation_disabled": False, "diagnostic_errors": 0, "dropped_rows": 0,
        "outstanding_associations": {"requests": 0, "replies": 0, "tokens": 0},
        "stage_coverage": {"missing_success_stages": {}},
        "counters": {"client_calls_started": count*2 if capture else 0, "client_calls_finished": count*2 if capture else 0}}
    if passive:
        payload["features"] = {"passive_rpc_markers": True}
        payload["passive_markers"] = {"enabled": True, "grpc_version": "1.78.0", "peer_count": 1 if capture else 0,
            "pending_done": 0, "pending_turns": 0, "retirement_verified": True}
    return payload


def result(payload):
    return analyze.analyze_calls(payload, count=2, warmup=1, capture=True)


def testSameAssetCallsUseOnlyExactCallIdAndAdjacentDifferences():
    payload = trace()
    payload["rows"].reverse()  # Recording order is deliberately irrelevant.
    report = result(payload)
    assert report["status"] == "COMPLETE"
    assert report["joined_measured_calls"] == 2
    assert report["intervals_ms"]["serialize_end_to_deserialize_start"]["p95_ms"] == 44
    assert report["intervals_ms"]["worker_end_to_handler_end"]["p95_ms"] == 3
    for call in report["slowest_rpc_calls"]:
        assert sum(call["intervals_ms"][name] for name in analyze.CHAIN_NAMES) == call["intervals_ms"]["rpc_total"]
    # A tiny negative wall−CPU difference is retained; never called scheduling/GIL time.
    assert report["stage_wall_minus_thread_cpu_ms"]["server.asset_lock_wait:admit"]["negative_count"] == 2


@pytest.mark.parametrize("defect", ["duplicate", "missing", "identity", "failure", "ordinal", "lock_phase"])
def testInvalidCallIsNotRepairedOrSilentlyDropped(defect):
    payload = trace()
    target = next(row for row in payload["rows"] if row["ordinal"] == 2 and row["stage"] == analyze.STAGES["serialize"])
    if defect == "duplicate":
        payload["rows"].append(dict(target))
    elif defect == "missing":
        payload["rows"].remove(target)
    elif defect == "identity":
        target["channel_id"] = 99
    elif defect == "failure":
        target["outcome"] = "CancelledError"
    elif defect == "ordinal":
        for row in payload["rows"]:
            if row["call_id"] == target["call_id"]:
                row["ordinal"] = 900
    else:
        for row in payload["rows"]:
            if row["call_id"] == target["call_id"] and row["stage"] == analyze.STAGES["lock"]:
                row["lock_phase"] = "admit"
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["expected_measured_calls"] == 2
    assert report["joined_measured_calls"] == 1
    assert len(report["rejected_calls"]) == 1
    assert report["issues"]["channel_ordinal_coverage"]


def testNegativeBoundaryRemainsSignedAndInvalidatesAnalysis():
    payload = trace()
    for row in payload["rows"]:
        if row["stage"] == analyze.STAGES["deserialize"]:
            row["start_ns"] -= 50_000_000
            row["end_ns"] -= 50_000_000
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["joined_measured_calls"] == 2
    assert report["intervals_ms"]["serialize_end_to_deserialize_start"]["min_ms"] == -6
    assert report["intervals_ms"]["serialize_end_to_deserialize_start"]["negative_count"] == 2


@pytest.mark.parametrize("field,value", [("clock", "unknown origin"), ("pid", None)])
def testUnknownClockOriginSuppressesAttribution(field, value):
    payload = trace()
    payload[field] = value
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["intervals_ms"] == report["stage_wall_ms"] == {}
    assert report["slowest_rpc_calls"] == []


def evidence(root):
    root.mkdir()
    manifest = {"configuration": {"count": 2, "warmup": 1, "groups": 1}, "source_stable": True,
        "clock": "same-host perf_counter_ns", "measurement_status": "VALID",
        "performance_status": "NOT_ASSESSED", "trials": []}
    for position, arm in enumerate(("all_off", "all_qt", "none_qt")):
        directory = root / arm
        directory.mkdir()
        capture = arm != "all_off"
        row = {"ordinal": 2, "scope_ended_ns": 1_000_000_000, "received_ns": 1_030_000_000,
               "model_ns": 1_100_000_000, "owner_age_at_send_ms": 20,
               "applied_to_live": True, "failures": {}, "decoded": ["image"]}
        trial = {"arm": arm, "capture_enabled": capture, "asset_split_trace": {"complete": True},
                 "consumers": [{"rows": [row]}, {"rows": [row]}] if capture else []}
        (directory / "trial.json").write_text(json.dumps(trial))
        (directory / "asset-split.json").write_text(json.dumps(trace(capture)))
        manifest["trials"].append({"group": 0, "position": position, "arm": arm,
            "raw_evidence": f"{arm}/trial.json", "asset_split_trace": {"complete": True},
            "trace_complete": True, "watchdog": {"status": "PASS"}})
    (root / "evidence.json").write_text(json.dumps(manifest))
    return manifest


def testOfflineOutputIsExclusiveAndRawInputsRemainExact(tmp_path):
    root, output = tmp_path / "raw", tmp_path / "analysis"
    evidence(root)
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")}
    report = analyze.analyze(root, output)
    assert report["analysis_status"] == "COMPLETE"
    assert report["performance_verdict"] == "NOT_EVALUATED"
    assert report["cross_process_clock"]["independently_verified"] is False
    assert report["trials"][1]["consumers"][0]["intervals_ms"]["wire_result_age_to_receipt"]["p95_ms"] == 10
    assert before == {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")}
    saved = (output / "analysis.json").read_bytes()
    with pytest.raises(ValueError, match="new directory"):
        analyze.analyze(root, output)
    assert (output / "analysis.json").read_bytes() == saved


def testIncompleteOriginalCoverageCannotBecomeComplete(tmp_path):
    root = tmp_path / "raw"
    manifest = evidence(root)
    manifest["trials"].pop()
    (root / "evidence.json").write_text(json.dumps(manifest))
    report = analyze.analyze(root, tmp_path / "analysis")
    assert report["analysis_status"] == "INCOMPLETE"
    assert report["original_trial_coverage_complete"] is False


def testEvidencePathCannotEscapeRootAndOversizedFilesAreRejected(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    root.mkdir()
    (tmp_path / "elsewhere.json").write_text("{}")
    reader = analyze.EvidenceReader(root)
    with pytest.raises(ValueError, match="outside input"):
        reader.read("../elsewhere.json")
    (root / "large.json").write_text("{} ")
    monkeypatch.setattr(analyze, "FILE_LIMIT", 2)
    with pytest.raises(ValueError, match="file budget"):
        reader.read("large.json")


def testBoundedRowsAndInputMutationAreDetected(tmp_path):
    payload = trace()
    payload["rows"] = [{}]*(analyze.ROW_LIMIT+1)
    with pytest.raises(ValueError, match="row budget"):
        result(payload)
    path = tmp_path / "one.json"
    path.write_text("{}")
    reader = analyze.EvidenceReader(tmp_path)
    reader.read("one.json")
    path.write_text('{"changed":true}')
    assert not reader.unchanged()


def testNoClaimOfEventPollAttributionOrConsumerChannelMatching():
    payload = trace()
    original = copy.deepcopy(payload)
    report = result(payload)
    assert payload == original
    assert all("poll" not in name and "network" not in name for name in report["intervals_ms"])
    assert "consumer_index" not in json.dumps(report)


def testUnhashableStageIsReportedWithoutGuessingAStage():
    payload = trace()
    payload["rows"][0]["stage"] = ["not", "a", "stage"]
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["issues"]["invalid_stage"] == 1


def testLockOutsideAssetMakesAnalysisIncompleteAndRemainsVisible():
    payload = trace()
    for row in payload["rows"]:
        if row["stage"] == analyze.STAGES["lock"]:
            row["start_ns"] += 10**12
            row["end_ns"] += 10**12
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["joined_measured_calls"] == report["expected_measured_calls"] == 2
    assert "nesting:lock:admit:asset" in report["slowest_rpc_calls"][0]["boundary_errors"]


@pytest.mark.parametrize("field,value", [("result_key", None), ("runtime_instance_id", ""),
    ("job_id", None), ("resource_id", None), ("asset_bytes", None), ("asset_bytes", True),
    ("asset_bytes", 8*1024*1024+1)])
def testMatchingButInvalidIdentityCannotBecomeComplete(field, value):
    payload = trace()
    for row in payload["rows"]:
        row[field] = value
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["joined_measured_calls"] == 0
    assert len(report["rejected_calls"]) == report["expected_total_calls"]


def testMalformedMetadataHasBoundedCliFailure(tmp_path, monkeypatch, capsys):
    root = tmp_path / "raw"
    evidence(root)
    path = root / "all_off" / "asset-split.json"
    payload = json.loads(path.read_text())
    payload["counters"] = None
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(sys, "argv", ["analyze", "--input", str(root), "--output", str(tmp_path / "new")])
    assert analyze.main() == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    message = json.loads(captured.out)
    assert message["analysis_status"] == "INVALID"
    assert len(captured.out) < 500


def testOptionalMarkersPreserveSignedOrderAndOnlyLabelObservedClientOverlap():
    report = result(trace(passive=True))
    assert report["status"] == "COMPLETE"
    assert report["intervals_ms"]["serialize_end_to_next_loop_turn"]["p95_ms"] == 7
    assert report["intervals_ms"]["rpc_done_to_deserialize_start"]["p95_ms"] == -8
    assert report["passive_markers"]["overlapping_observed_client_rpc_same_peer_pairs"] == 1
    assert "do not prove simultaneous server RPC" in report["passive_markers"]["interpretation"]
    assert report["passive_markers"]["matched_pairs"] == report["passive_markers"]["expected_pairs"] == 1
    assert all(not key.startswith("_") for row in report["slowest_rpc_calls"] for key in row)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "undeclared", "pending", "boolean_pending", "retirement", "peer", "loop_origin", "done_before_handler"])
def testOptionalMarkerAccountingCannotManufactureCompleteness(defect):
    payload = trace(passive=True)
    row = next(item for item in payload["rows"] if item["ordinal"] == 2 and item["stage"] == analyze.PASSIVE_STAGES["loop"])
    if defect == "missing":
        payload["rows"].remove(row)
    elif defect == "duplicate":
        payload["rows"].append(dict(row))
    elif defect == "undeclared":
        del payload["features"]
    elif defect == "pending":
        payload["passive_markers"]["pending_turns"] = 1
    elif defect == "boolean_pending":
        payload["passive_markers"]["pending_turns"] = False
    elif defect == "retirement":
        payload["passive_markers"]["retirement_verified"] = False
    elif defect == "peer":
        next(item for item in payload["rows"] if item["stage"] == analyze.PASSIVE_STAGES["peer"])["peer_token"] = "endpoint"
    elif defect == "loop_origin":
        row["start_ns"] += 100
    else:
        item = next(item for item in payload["rows"] if item["stage"] == analyze.PASSIVE_STAGES["done"])
        item["start_ns"] -= 100_000_000
        item["end_ns"] = item["start_ns"]
    assert result(payload)["status"] == "INCOMPLETE"


def testDistinctPeersAndNonoverlapAreNotReportedAsSharedTransport():
    payload = trace(passive=True)
    payload["passive_markers"]["peer_count"] = 2
    for row in payload["rows"]:
        if row["channel_id"] == 7:
            row["start_ns"] += 200_000_000
            row["end_ns"] += 200_000_000
            if row["stage"] == analyze.PASSIVE_STAGES["peer"]:
                row["peer_token"] = 2
    report = result(payload)
    assert report["status"] == "COMPLETE"
    assert report["passive_markers"]["different_peer_pairs"] == 1
    assert report["passive_markers"]["overlapping_observed_client_rpc_same_peer_pairs"] == report["passive_markers"]["overlapping_observed_client_rpc_different_peer_pairs"] == 0


def testUnknownClockSuppressesPeerOverlapAttributionToo():
    payload = trace(passive=True)
    payload["clock"] = "unknown"
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert not report["passive_markers"]["clock_usable"]
    assert "overlapping_observed_client_rpc_same_peer_pairs" not in report["passive_markers"]


def testPeerPairingNeverJoinsDifferentJobsWithMatchingResultAndAssetKeys():
    payload = trace(passive=True)
    for row in payload["rows"]:
        if row["channel_id"] == 7:
            row["job_id"] = "other-job"
    report = result(payload)
    assert report["status"] == "INCOMPLETE"
    assert report["passive_markers"]["matched_pairs"] == 0
    assert report["issues"]["passive_pair_coverage"] == 1


def marker_evidence(root):
    root.mkdir()
    manifest = {"experiment": analyze.MARKER_EXPERIMENT,
        "configuration": {"count": 96, "warmup": 8, "marker_order": ["off", "on", "on", "off"]},
        "source_stable": True, "clock": "same-host perf_counter_ns", "trials": []}
    for position, mode in enumerate(("off", "on", "on", "off")):
        directory = root / f"trial-{position}"
        directory.mkdir()
        split = trace(count=96, passive=mode == "on")
        for row in split["rows"]:
            row["asset_sha256"] = "a"*64
        rows = [{"ordinal": ordinal, "scope_ended_ns": ordinal*1_000_000_000,
            "received_ns": ordinal*1_000_000_000+30_000_000,
            "model_ns": ordinal*1_000_000_000+100_000_000, "owner_age_at_send_ms": 20,
            "applied_to_live": True, "failures": {}, "decoded": ["image"]} for ordinal in range(9, 97)]
        trial = {"arm": "none_qt", "capture_enabled": True, "asset_split_trace": {"complete": True},
            "input_sha256": "b"*64, "consumers": [{"rows": rows}, {"rows": rows}]}
        (directory / "trial.json").write_text(json.dumps(trial))
        (directory / "asset-split.json").write_text(json.dumps(split))
        manifest["trials"].append({"group": 0, "position": position, "arm": "none_qt", "marker_mode": mode,
            "raw_evidence": f"trial-{position}/trial.json", "asset_split_trace": {"complete": True},
            "trace_complete": True, "watchdog": {"status": "PASS"}})
    (root / "evidence.json").write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize("defect", [None, "marker_order", "declared_mode", "payload", "denominator"])
def testExplicitFourArmControlPreservesExactOrderPayloadAndDenominators(tmp_path, defect):
    root = tmp_path / "raw"
    manifest = marker_evidence(root)
    if defect == "marker_order":
        manifest["configuration"]["marker_order"] = ["on", "off", "on", "off"]
    elif defect == "declared_mode":
        manifest["trials"][0]["marker_mode"] = "on"
    elif defect == "denominator":
        manifest["configuration"]["count"] = 95
    elif defect == "payload":
        path = root / "trial-0" / "asset-split.json"
        split = json.loads(path.read_text())
        for row in split["rows"]:
            row["asset_sha256"] = "c"*64
        path.write_text(json.dumps(split))
    (root / "evidence.json").write_text(json.dumps(manifest))
    report = analyze.analyze(root, tmp_path / "analysis")
    assert report["analysis_status"] == ("COMPLETE" if defect is None else "INCOMPLETE")
    assert report["performance_verdict"] == "NOT_EVALUATED"
    assert [row["calls"]["passive_markers"]["enabled"] for row in report["trials"]] == [False, True, True, False]
