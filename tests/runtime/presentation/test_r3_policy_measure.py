"""Pure policy-harness accounting tests; no Qt windows, devices, or workloads."""
from collections import Counter

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from scripts import r3_policy_measure as measure


def testPolicyArmsKeepExplicitLegacyPolicyAndCapturePairing():
    assert measure.ARM_SPECS == {
        "all_off": {"policy": "ALL", "capture": False},
        "all_qt": {"policy": "ALL", "capture": True},
        "none_qt": {"policy": "NONE", "capture": True},
    }


def testArmRotationVisitsEveryPositionWithoutDroppingOrRepeatingArms():
    arms = ["all_off", "all_qt", "none_qt"]
    orders = [list(measure.armOrder(group)) for group in range(3)]
    assert orders == [arms, arms[1:] + arms[:1], arms[2:] + arms[:2]]
    for order in orders:
        assert Counter(order) == Counter(arms)
    for position in range(3):
        assert {order[position] for order in orders} == set(arms)
    for group in range(3, 9):
        assert list(measure.armOrder(group)) == orders[group % 3]


@pytest.mark.parametrize(("arm", "policy", "capture"), [
    ("all_off", "ALL", False),
    ("all_qt", "ALL", True),
    ("none_qt", "NONE", True),
])
def testStartRequestUsesPublicApiAndExplicitArmPolicy(arm, policy, capture):
    request = measure.buildStartRequest("synthetic-project", arm, "runtime-generation", "start-token")
    assert isinstance(request, pb.StartJobRequest)
    assert request.project_id == "synthetic-project"
    assert request.workflow_id == "main"
    assert request.legacy_snapshot_policy == policy
    assert request.capture_presentation is capture
    assert request.expected_runtime_instance_id == "runtime-generation"
    assert request.start_request_id == "start-token"
    # Round-trip through the actual wire message, including explicit ALL, so
    # the policy experiment cannot accidentally measure old-client defaults.
    restored = pb.StartJobRequest.FromString(request.SerializeToString())
    assert restored == request
    assert restored.legacy_snapshot_policy


def testUnknownArmCannotFallBackToAnotherPolicy():
    with pytest.raises((KeyError, ValueError)):
        measure.buildStartRequest("synthetic-project", "unknown", "runtime-generation", "start-token")


def _completeResult(capture=True, count=3, warmup=1):
    timings = [{"ordinal": ordinal, "startNs": (100 + (ordinal-1)*200)*1_000_000,
                "endNs": (110 + (ordinal-1)*200)*1_000_000, "outcome": "OK"}
               for ordinal in range(1, count+1)]
    measured = timings[warmup:]
    consumers, windows = [], []
    if capture:
        for index in range(2):
            consumers.append({"rows": [{"ordinal": row["ordinal"], "key": str(row["ordinal"]),
                "applied_to_live": True, "model_ns": row["endNs"]+(20+index)*1_000_000,
                "decoded": ["image"], "failures": []} for row in measured],
                "unique_received": len(measured), "unique_decoded": len(measured),
                "unique_applied": len(measured), "p95_model_ms": 21})
            windows.append({"raw_rows": [{"key": str(row["ordinal"]),
                "gui_ns": row["endNs"]+30_000_000,
                "paint_ns": row["endNs"]+(40+index)*1_000_000} for row in measured],
                "unique_committed": len(measured), "unique_painted": len(measured),
                "p95_scope_to_gui_ms": 30})
    end = timings[-1]["endNs"]
    stamps = [50_000_000, 110_000_000, end+10_000_000, end+42_000_000,
              end+100_000_000, end+600_000_000]
    result = {"capture_enabled": capture, "consumers": consumers, "ui": windows,
        "job_status": "COMPLETED", "qt_platform": "xcb",
        "timestamps": {"observation_start_ns": 0, "terminal_observed_ns": end+100_000_000,
            "observation_end_ns": end+600_000_000, "cleanup_start_ns": end+601_000_000,
            "cleanup_end_ns": end+651_000_000},
        "age_samples": [{"time_ns": stamp, "model_ms": [5, 6] if capture else [],
            "ui_ms": [7, 8] if capture else [], "ui_available": [True, True] if capture else []}
            for stamp in stamps],
        "pace": [{"index": ordinal, "due": ordinal*.2, "actual": ordinal*.2}
                 for ordinal in range(1, count+1)],
        "expected_source_ids": ["image", "count"],
        "observed_result_outcomes": {str(row["ordinal"]): {"ordinal": row["ordinal"],
            "mode": "runtime", "status": "COMPLETE", "source_outcomes": {
                "image": {"state": "AVAILABLE"}, "count": {"state": "AVAILABLE"}}}
            for row in measured} if capture else {}}
    measure.addAccounting(result, count, warmup, timings)
    return result


def testMissingFailedDuplicateAndUnknownExecutionsKeepPlannedDenominators():
    result = _completeResult(count=4)
    timings = [dict(row) for row in result["raw_timings"] if row["ordinal"] != 3]
    timings[-1]["outcome"] = "RuntimeError"
    timings.insert(2, dict(timings[1]))
    timings.append({"ordinal": None, "startNs": 1, "endNs": 2, "outcome": "OK"})
    result["consumers"][0]["rows"] = result["consumers"][0]["rows"][:1]
    result["consumers"][0]["rows"][0]["applied_to_live"] = False
    result["ui"][1]["raw_rows"] = []
    measure.addAccounting(result, 4, 1, timings)
    denominator = result["denominators"]
    assert denominator["measured_inputs"] == result["expected"] == 3
    assert denominator["executed_total"] == 5 and result["executed"] == 1
    assert denominator["missing_execution_ordinals"] == [3]
    assert denominator["missing_measured_execution_ordinals"] == [3, 4]
    assert denominator["duplicate_execution_ordinals"] == {"2": 2}
    assert denominator["out_of_range_execution_rows"] == [timings[-1]]
    assert denominator["failed_execution_rows"] == [timings[3]]
    assert result["raw_timings"] == timings
    assert result["consumers"][0]["missing_measured_ordinals"] == [3, 4]
    assert result["consumers"][0]["received_not_applied"] == 1
    assert result["ui"][1]["missing_committed_ordinals"] == [2, 3, 4]
    assert result["ui"][1]["missing_painted_ordinals"] == [2, 3, 4]
    assert not measure.exactCoverage(result)


def testPhasesRetainAllWindowAgeTailAndDoNotReplaceStrictVerdict():
    result = _completeResult()
    result["age_samples"][0].update(model_ms=[None, None], ui_ms=[None, None], ui_available=[False, False])
    result["age_samples"][-1].update(model_ms=[600, 700], ui_ms=[750, 800], ui_available=[True, False])
    original = [{key: value for key, value in row.items() if key != "phase"}
                for row in result["age_samples"]]
    measure.addAccounting(result, 3, 1, result["raw_timings"])
    assert [{key: value for key, value in row.items() if key != "phase"}
            for row in result["age_samples"]] == original
    assert [row["phase"] for row in result["age_samples"]] == [
        "startup", "active_input", "final_delivery", "terminal_wait",
        "post_terminal_observation", "post_terminal_observation"]
    phases = result["phases"]
    assert phases["first_input_start_ns"] == 100_000_000
    assert phases["final_input_end_ns"] == 510_000_000
    assert phases["all_measured_delivery_complete_ns"] == 551_000_000
    assert phases["active_input_ms"] == 410
    assert phases["final_delivery_after_input_ms"] == 41
    assert phases["terminal_wait_after_delivery_ms"] == 59
    assert phases["cleanup_ms"] == 50
    assert phases["raw_all_window"] == {"samples": 6, "max_model_ms": 700,
        "max_ui_ms": 800, "model_unavailable_samples": 2, "ui_unavailable_samples": 3}
    assert sum(row["samples"] for row in phases["age_by_phase"].values()) == 6
    assert result["age_samples_ms"][-1] == [600, 700]
    assert result["ui_age_samples_ms"][-1] == [750, 800]
    assert phases["export_deadline_seconds"] == phases["asset_rpc_deadline_seconds"] == .5
    verdict = measure.groupAssessment({"all_off": _completeResult(False),
        "all_qt": result, "none_qt": _completeResult()})
    comparison = verdict["comparisons"]["all_qt_vs_all_off"]
    assert comparison["exact_ordinal_coverage"]
    assert comparison["max_sampled_age_ms"] == 700
    assert comparison["max_sampled_ui_age_ms"] == 800
    assert not comparison["pass"] and not verdict["pass"]


@pytest.mark.parametrize("boundary", [1, 3])
@pytest.mark.parametrize("damage", ["missing", "duplicate", "failed"])
def testBoundaryRequiresExactlyOneSuccessfulExpectedInput(boundary, damage):
    result = _completeResult()
    timings = [dict(row) for row in result["raw_timings"]]
    target = next(row for row in timings if row["ordinal"] == boundary)
    if damage == "missing":
        timings.remove(target)
    elif damage == "duplicate":
        timings.append(dict(target))
    else:
        target["outcome"] = "ValueError"
    timings.insert(0, {"ordinal": 99, "startNs": 1, "endNs": 2, "outcome": "OK"})
    measure.addAccounting(result, 3, 1, timings)
    key = "first_input_start_ns" if boundary == 1 else "final_input_end_ns"
    assert result["phases"][key] is None
    assert result["phases"]["active_input_ms"] is None
    expected = "execution_boundary_unknown" if boundary == 1 else "active_input_unresolved"
    assert result["age_samples"][-1]["phase"] == expected


@pytest.mark.parametrize("damage", ["consumer", "decode", "application", "paint", "window"])
def testMissingFinalDeliveryCannotBecomePostTerminalSuccess(damage):
    result = _completeResult()
    if damage == "consumer":
        result["consumers"][1]["rows"].pop()
    elif damage == "decode":
        result["consumers"][1]["rows"][-1]["failures"] = ["image"]
    elif damage == "application":
        result["consumers"][1]["rows"][-1]["applied_to_live"] = False
    elif damage == "paint":
        result["ui"][1]["raw_rows"][-1].pop("paint_ns")
    else:
        result["ui"].pop()
    measure.addAccounting(result, 3, 1, result["raw_timings"])
    assert result["phases"]["all_measured_delivery_complete_ns"] is None
    assert result["phases"]["terminal_wait_after_delivery_ms"] is None
    assert result["age_samples"][-1]["phase"] == "final_delivery_unresolved"
    assert result["phases"]["raw_all_window"]["samples"] == 6
    assert not measure.exactCoverage(result)


def testRawRetentionCountCannotBeHiddenByWarmupFiltering():
    result = _completeResult()
    result["consumers"][0]["total_record_count"] = 128
    result["ui"][1]["total_record_count"] = 256
    measure.addAccounting(result, 3, 1, result["raw_timings"])
    assert result["record_retention"]["capacity_reached"]


@pytest.mark.parametrize("damage", ["execution", "consumer", "window", "pace", "source", "mode"])
def testCountEqualInvalidCoverageFailsGroupDespiteCompleteSummaryCounts(damage):
    result = _completeResult()
    if damage == "execution":
        result["raw_timings"][-1]["ordinal"] = 99
    elif damage == "consumer":
        result["consumers"][1]["rows"][-1]["ordinal"] = 99
    elif damage == "window":
        result["ui"][1]["raw_rows"][-1]["key"] = "unknown"
    elif damage == "pace":
        result["pace"][-1]["index"] = 99
    elif damage == "source":
        result["observed_result_outcomes"]["3"]["source_outcomes"].pop("count")
    else:
        result["observed_result_outcomes"]["3"]["mode"] = "debug"
    measure.addAccounting(result, 3, 1, result["raw_timings"])
    assert result["consumers"][1]["unique_received"] == result["expected"]
    assert result["ui"][1]["unique_painted"] == result["expected"]
    assert not measure.exactCoverage(result)
    verdict = measure.groupAssessment({"all_off": _completeResult(False),
        "all_qt": result, "none_qt": _completeResult()})
    assert not verdict["pass"]
    assert not verdict["comparisons"]["all_qt_vs_all_off"]["exact_ordinal_coverage"]
    assert verdict["comparisons"]["none_qt_vs_all_off"]["pass"]


def testPaceUsesIndicesInsteadOfListPositionAndNeverInventsZero():
    result = _completeResult()
    result["pace"] = [{"index": 3, "due": 1, "actual": 1.075},
                      {"index": 1, "due": 1, "actual": 9},
                      {"index": 2, "due": 1, "actual": 1.025}]
    measure.addAccounting(result, 3, 1, result["raw_timings"])
    assert result["max_schedule_lateness_ms"] == pytest.approx(75)
    assert measure.exactCoverage(result)
    result["pace"] = [{"index": 1, "due": 1, "actual": 1}]
    measure.addAccounting(result, 3, 1, result["raw_timings"])
    assert result["pace_denominators"]["missing_indices"] == [2, 3]
    assert result["max_schedule_lateness_ms"] is None
    verdict = measure.groupAssessment({"all_off": _completeResult(False),
        "all_qt": result, "none_qt": _completeResult()})
    assert not verdict["pass"] and result["max_schedule_lateness_ms"] is None


def testMissingArmIsRetainedRatherThanAssessingReducedGroup():
    verdict = measure.groupAssessment({"all_off": _completeResult(False)})
    assert not verdict["pass"] and verdict["missing_arms"] == ["all_qt", "none_qt"]


def testSamplerCoalescesWhileNativeReaderBlockedAndRetires():
    from threading import Event
    entered, release = Event(), Event()
    calls = []

    def sample(pid):
        calls.append(pid)
        entered.set()
        assert release.wait(5)
        return {"pid": pid, "status": "OBSERVED"}

    sampler = measure.BoundedSampler(sample=sample, limit=2)
    try:
        processes = {"owner": 111}
        assert sampler.request(processes, stamp=7)
        assert entered.wait(5)
        processes["owner"] = 999
        assert not sampler.request({"job": 222}, stamp=8)
        assert sampler.snapshot() == []
        assert sampler.report()["coalesced"] == 1
    finally:
        release.set()
        sampler.close()
    report = sampler.report()
    assert calls == [111] and report["retired"]
    assert report["requested"] == 2 and report["overflow"] == 0
    assert report["observed_roles"] == ["owner"] and report["resource_coverage_complete"]
    assert report["samples"][0]["requested_ns"] == 7
    assert set(report["samples"][0]["per_process_timestamps"]) == {"owner"}


def testSamplerCapacityIsExplicitWithoutAnUnboundedQueue():
    calls = []
    sampler = measure.BoundedSampler(sample=lambda pid: calls.append(pid) or {"status": "OBSERVED"}, limit=1)
    try:
        sampler.sampleNow({"owner": 111})
        assert not sampler.request({"job": 222})
        assert len(sampler.snapshot()) == 1
        assert sampler.report()["overflow"] == 1
    finally:
        sampler.close()
    assert calls == [111]


def testSamplerTimeoutDoesNotClaimRetirement():
    from threading import Event
    entered, release = Event(), Event()

    def sample(_pid):
        entered.set()
        assert release.wait(5)
        return {"status": "OBSERVED"}

    sampler = measure.BoundedSampler(sample=sample)
    try:
        assert sampler.request({"owner": 111}) and entered.wait(5)
        with pytest.raises(RuntimeError, match="still owns native enumeration"):
            sampler.close(timeout=0)
        assert not sampler.report()["retired"]
    finally:
        release.set()
        sampler.close()
    assert sampler.report()["retired"]


@pytest.mark.parametrize("error", [OSError("exited"), KeyError("gone"), ValueError("broken")])
def testSamplerRetirementDoesNotInventNativeResourceCoverage(error):
    def sample(_pid):
        raise error

    sampler = measure.BoundedSampler(sample=sample)
    try:
        sampler.sampleNow({"owner": 111, "job": 222})
    finally:
        sampler.close()
    report = sampler.report()
    assert report["retired"] and report["samples"]
    assert report["observed_roles"] == [] and not report["resource_coverage_complete"]
    expected = "ERROR" if isinstance(error, ValueError) else "EXITED_OR_UNAVAILABLE"
    assert all(row["status"] == expected for row in report["samples"][0]["processes"].values())
    assert bool(report["errors"]) is isinstance(error, ValueError)


def testCachedReaderReturnsPendingWithoutWaitingAndBoundsPidRegistry():
    from threading import Event
    from scripts.r3_sampling import CachedResources
    entered, release = Event(), Event()

    def sample(pid):
        entered.set()
        assert release.wait(5)
        return {"pid": pid, "status": "OBSERVED", "rss_bytes": 123}

    cache = CachedResources(sample=sample, pidLimit=1)
    try:
        pending = cache(111)
        assert pending["status"] == "SAMPLE_PENDING" and pending["sample_age_ms"] is None
        assert entered.wait(5)
        assert cache(111)["status"] == "SAMPLE_PENDING"
        assert cache(222)["status"] == "SAMPLER_PID_LIMIT"
        assert cache.report()["registered_pids"] == [111]
        assert cache.report()["registry_overflow"] == 1
        assert cache.report()["rate_limited_cache_reads"] == 1
    finally:
        release.set()
        cache.close()
    observed = cache(111)
    assert observed["status"] == "OBSERVED" and observed["rss_bytes"] == 123
    assert observed["cached"] and observed["sampler_closed"]
    assert observed["sample_requested_ns"] <= observed["sample_start_ns"] <= observed["sample_end_ns"]
    assert observed["sample_age_ms"] >= 0 and cache.report()["retired"]
    with pytest.raises(RuntimeError, match="closed"):
        cache.sampleNow(111)


def testCachedAgeUsesEachPidAcquisitionEndRatherThanSlowBatchEnd(monkeypatch):
    import time
    from types import SimpleNamespace
    from scripts import r3_sampling as sampling
    clock = [100_000_000]
    monkeypatch.setattr(sampling, "time", SimpleNamespace(
        perf_counter_ns=lambda: clock[0], monotonic=time.monotonic))

    def sample(pid):
        clock[0] += 10_000_000 if pid == 111 else 70_000_000
        return {"pid": pid, "status": "OBSERVED"}

    cache = sampling.CachedResources(sample=sample)
    try:
        cache.sampler.sampleNow({"111": 111, "222": 222})
    finally:
        cache.close()
    clock[0] = 200_000_000
    first, last = cache(111), cache(222)
    assert first["sample_age_ms"] == 90 and last["sample_age_ms"] == 20
    assert first["sample_end_ns"] == 110_000_000
    assert first["sample_batch_end_ns"] == last["sample_end_ns"] == 180_000_000


def testFreshCachedReadBypassesThrottleAndCannotReturnOldCounter():
    from scripts.r3_sampling import CachedResources
    calls = []

    def sample(pid):
        calls.append(pid)
        return {"pid": pid, "status": "OBSERVED", "rss_bytes": len(calls)}

    cache = CachedResources(sample=sample)
    try:
        first = cache.sampleNow(111)
        second = cache.sampleNow(111)
        assert first["rss_bytes"] == 1 and second["rss_bytes"] == 2
        assert first["fresh_request"] and second["fresh_request"]
        assert second["sample_requested_ns"] >= first["sample_end_ns"]
    finally:
        cache.close()
    assert cache.report()["resource_coverage_complete"] and cache.report()["retired"]


@pytest.mark.parametrize("missing", [None, "trial", "resource_coverage"])
@pytest.mark.parametrize("asset_split_trace", [False, True])
def testMockRunPreservesPayloadAndMarksMissingEvidenceInvalid(tmp_path, monkeypatch, missing, asset_split_trace):
    import json
    from argparse import Namespace
    import p2_validate
    monkeypatch.setattr(p2_validate, "identity", lambda: {"digest": "unchanged"})
    monkeypatch.setattr(p2_validate, "git", lambda *_args: "test")
    monkeypatch.setattr(measure.base, "diskPreflight", lambda _path: {"free_bytes": 10**9})
    calls = []

    def fakeTrial(command, directory):
        arm = command[command.index("--arm")+1]
        assert ("--asset-split-trace" in command) == asset_split_trace
        calls.append(arm)
        if missing == "trial" and arm == "none_qt":
            return {"status": "FAIL"}
        payload = _completeResult(measure.ARM_SPECS[arm]["capture"], count=2, warmup=0)
        payload.update(outcome_overflow=0, sampler={"overflow": 0, "errors": [], "retired": True,
            "observed_roles": ["owner", "job", "exporter-0", "exporter-1"]})
        if asset_split_trace:
            payload["asset_split_trace"] = {"enabled": True, "complete": True}
        if missing == "resource_coverage" and arm == "none_qt":
            payload["sampler"]["observed_roles"] = ["owner"]
        (directory/"trial.json").write_text(json.dumps(payload), encoding="utf-8")
        roles = ["execution", "job", "owner"] + (["exporter", "exporter"] if payload["capture_enabled"] else [])
        for index, role in enumerate(roles):
            event = {"stage": "example", "elapsed_ms": 2, "result_key": "two"}
            if role == "execution":
                event["ordinal"] = 2
            (directory/f"trace-{index}.json").write_text(json.dumps({
                "role": role, "dropped_rows": 0, "rows": [event]}), encoding="utf-8")
        return {"status": "PASS"}

    monkeypatch.setattr(measure.base, "supervisedTrial", fakeTrial)
    args = Namespace(output=tmp_path, count=2, warmup=0, groups=1, qt_platform="offscreen", asset_split_trace=asset_split_trace)
    assert measure.run(args) == (0 if missing is None else 1)
    evidence = json.loads((tmp_path/"evidence.json").read_text(encoding="utf-8"))
    assert calls == ["all_off", "all_qt", "none_qt"]
    assert evidence["source_stable"] and evidence["performance_status"] == "NOT_ASSESSED"
    assert evidence["measurement_status"] == ("VALID" if missing is None else "INVALID")
    if asset_split_trace:
        assert evidence["asset_split_status"] == ("INCOMPLETE" if missing == "trial" else "COMPLETE")
    else:
        assert "asset_split_status" not in evidence
    assert len(evidence["trials"]) == 3
    assert all(row["phase"] == "measurement" for trial in evidence["trials"]
               for row in trial.get("stage_summary", []))
    if missing != "trial":
        assert evidence["groups"][0]["comparisons"]["all_qt_vs_all_off"]["regression_percent"] == 0
        assert evidence["trials"][1]["phases"]["raw_all_window"]["samples"] == 6
    else:
        assert evidence["groups"][0]["missing_arms"] == ["none_qt"]


@pytest.mark.parametrize("failed", [False, True])
def testMeasuredJobHandlesKeywordWorkflowCallsAndRestoresWrapper(tmp_path, monkeypatch, failed):
    import json
    from types import SimpleNamespace
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    context = SimpleNamespace(iterationPath=[1], workflowId="detect",
                              workflowRunId="invocation", callerNodeId="detect")
    calls = []

    def run(self, workflowId, inputs, context, cancellation):
        calls.append(workflowId)
        if workflowId == "detect" and failed:
            raise ValueError("injected")
        return workflowId

    def job(spec, cancelEvent, eventQueue):
        assert spec is sentinel and cancelEvent is sentinel and eventQueue is sentinel
        runner = object.__new__(WorkflowRunner)
        assert runner.run(workflowId="main", inputs={}, context=context, cancellation=None) == "main"
        return runner.run(workflowId="detect", inputs={}, context=context, cancellation=None)

    sentinel = object()
    monkeypatch.setenv("EMO_R3_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(WorkflowRunner, "run", run)
    monkeypatch.setattr(measure.base, "measuredJob", job)
    if failed:
        with pytest.raises(ValueError, match="injected"):
            measure.measuredJob(sentinel, sentinel, sentinel)
    else:
        measure.measuredJob(sentinel, sentinel, sentinel)
    assert WorkflowRunner.run is run and calls == ["main", "detect"]
    payload = json.loads(next(tmp_path.glob("trace-execution-*.json")).read_text(encoding="utf-8"))
    assert payload["role"] == "execution" and payload["dropped_rows"] == 0
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["stage"] == "workflow.detect" and row["ordinal"] == 2
    assert row["invocation"] == "invocation"
    assert row["outcome"] == ("ValueError" if failed else "OK")
