"""Fixed off/on/on/off observer control for parent export-credit lifetime.

The unchanged NONE+Qt 96/8, 1080p/5Hz workload uses two consumers and windows.
Every arm keeps the existing asset split trace; passive RPC markers stay off.
Unavailable sources and missed images remain failed full-coverage evidence.
The new observer reports admitted-export boundaries, never performance PASS.
"""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "scripts")]

from scripts import r3_policy_measure as policy  # noqa: E402
from scripts.r3_asset_split_analyze import analyze_calls  # noqa: E402
from scripts.r3_rpc_marker_control import assetFingerprint  # noqa: E402
from scripts.r3_export_credit_trace import ADOPT_PHASE_COUNTS  # noqa: E402
from p2_validate import identity, git  # noqa: E402


ORDER = ("off", "on", "on", "off")
COUNT, WARMUP = 96, 8
REQUIRED_CREDIT_STAGES = ("parent.export_submit", "parent.task_dequeued", "parent.pipe_send",
    "parent.pipe_poll", "parent.pipe_recv", "parent.export_callback", "parent.credit_release", "parent.asset_adopt")


def childCommand(directory, platformName, mode):
    if mode not in {"off", "on"}:
        raise ValueError("unknown export-credit observer mode")
    command = [sys.executable, str(ROOT / "scripts" / "r3_policy_measure.py"), "--child",
        "--output", str(directory), "--arm", "none_qt", "--count", str(COUNT),
        "--warmup", str(WARMUP), "--qt-platform", platformName, "--asset-split-trace"]
    return command + (["--export-credit-trace", "--capture-credit-trace"] if mode == "on" else [])


def creditBoundaries(payload, trace):
    """Same-parent boundaries only; failed/missing phases stay explicit."""
    groups = {}
    for row in trace["rows"]:
        if "result_key" not in row:
            continue
        key = tuple(row.get(name) for name in ("pool_id", "job_id", "result_key", "source_id", "slot", "lane"))
        groups.setdefault(key, []).append(row)
    result = []
    for key, rows in groups.items():
        record = payload["observed_result_outcomes"].get(key[2])
        entry = {"pool_id": key[0], "job_id": key[1], "result_key": key[2],
            "source_id": key[3], "slot": key[4], "lane": key[5],
            "ordinal": record["ordinal"] if record is not None else None,
            "boundaries_ns": {}, "missing_or_duplicate": []}
        for label, stage, boundary in (
                ("reply_received", "parent.pipe_recv", "end_ns"),
                ("callback_entered", "parent.export_callback", "start_ns"),
                ("adopt_entered", "parent.asset_adopt", "start_ns"),
                ("adopt_returned", "parent.asset_adopt", "end_ns"),
                ("callback_returned", "parent.export_callback", "end_ns"),
                ("credit_release_entered", "parent.credit_release", "start_ns"),
                ("credit_release_returned", "parent.credit_release", "end_ns")):
            matched = [row for row in rows if row["stage"] == stage and row["outcome"] == "OK"]
            if len(matched) == 1 and type(matched[0][boundary]) is int:
                entry["boundaries_ns"][label] = matched[0][boundary]
            else:
                entry["boundaries_ns"][label] = None
                entry["missing_or_duplicate"].append(label)
        points = entry["boundaries_ns"]
        entry["intervals_ms"] = {}
        for label, start, end in (
                ("reply_to_callback", "reply_received", "callback_entered"),
                ("inclusive_pre_adopt", "callback_entered", "adopt_entered"),
                ("adopt", "adopt_entered", "adopt_returned"),
                ("adopt_return_to_callback_return", "adopt_returned", "callback_returned"),
                ("callback_return_to_credit_release", "callback_returned", "credit_release_returned")):
            entry["intervals_ms"][label] = ((points[end]-points[start])/1e6
                if points[start] is not None and points[end] is not None else None)
        result.append(entry)
    gated = [{"result_key": key, "ordinal": row["ordinal"], **row["source_outcomes"]["image"]}
             for key, row in payload["observed_result_outcomes"].items()
             if row["source_outcomes"].get("image", {}).get("state") != "AVAILABLE"]
    return {"exports": result, "nonavailable_sources": gated,
        "interpretation": "Same-parent timestamps, including observer overhead. Missing/duplicate or failed boundaries stay null; negative intervals are not clamped. "
            "This table is not a completeness or performance verdict. Source rejection has no invented export lifecycle. "
            "Only credit_release_returned represents a successfully returned original release; it does not guarantee future admission."}


def adoptPhaseIssues(rows, encodedBytes=None):
    issues = Counter()
    phases = {stage: [row for row in rows if row.get("stage") == stage and row.get("outcome") == "OK"]
              for stage in ("parent.asset_adopt", *ADOPT_PHASE_COUNTS)}
    for stage, count in {"parent.asset_adopt": 1, **ADOPT_PHASE_COUNTS}.items():
        if len(phases[stage]) != count:
            issues[stage + "_expected_" + str(count)] += 1
    if issues:
        return issues
    owner = phases["parent.asset_adopt"][0]
    if not all(type(owner.get(key)) is int for key in ("start_ns", "end_ns")) or owner["end_ns"] < owner["start_ns"]:
        return Counter({"invalid_adopt_clock": 1})
    aggregates = {}
    for stage in ADOPT_PHASE_COUNTS:
        for row in phases[stage]:
            if (not all(type(row.get(key)) is int for key in ("start_ns", "end_ns"))
                    or not owner["start_ns"] <= row["start_ns"] <= row["end_ns"] <= owner["end_ns"]):
                issues["fine_phase_outside_adopt"] += 1
                continue
            if stage not in ("parent.asset_adopt.read", "parent.asset_adopt.hash_update"):
                continue
            aggregate = row.get("aggregate", {})
            fields = ("calls", "failures", "wall_sum_ns", "wall_max_ns", "bytes", "max_chunk_bytes", "empty_calls")
            if (row.get("span_kind") != "first_to_last_call_envelope" or not isinstance(aggregate, dict)
                    or not all(type(aggregate.get(key)) is int and aggregate[key] >= 0 for key in fields)
                    or aggregate["calls"] < 1 or aggregate["failures"] != 0
                    or not aggregate["wall_max_ns"] <= aggregate["wall_sum_ns"] <= row["end_ns"]-row["start_ns"]
                    or aggregate["wall_sum_ns"] > aggregate["calls"]*aggregate["wall_max_ns"]):
                issues["invalid_fine_aggregate"] += 1
                continue
            cpuSum, cpuMax, cpuSpan = (aggregate.get("thread_cpu_sum_ns"), aggregate.get("thread_cpu_max_ns"), row.get("thread_cpu_ns"))
            if not ((cpuSum is None and cpuMax is None and cpuSpan is None)
                    or (all(type(value) is int and value >= 0 for value in (cpuSum, cpuMax, cpuSpan))
                        and cpuMax <= cpuSum <= cpuSpan and cpuSum <= aggregate["calls"]*cpuMax)):
                issues["invalid_fine_cpu_aggregate"] += 1
            aggregates[stage] = aggregate
    reads, updates = (aggregates.get("parent.asset_adopt." + stage) for stage in ("read", "hash_update"))
    if reads is None or updates is None:
        issues["missing_fine_aggregate"] += 1
    elif (not 0 < reads["bytes"] == updates["bytes"] <= 8*1024*1024
            or (encodedBytes is not None and reads["bytes"] != encodedBytes)
            or updates["calls"] != (reads["bytes"] + 65535)//65536
            or reads["calls"] != updates["calls"] + 1
            or reads["empty_calls"] != 1 or updates["empty_calls"] != 0
            or reads["max_chunk_bytes"] != min(reads["bytes"], 65536)
            or updates["max_chunk_bytes"] != reads["max_chunk_bytes"]):
        issues["streaming_read_hash_byte_or_call_mismatch"] += 1
    return issues


def creditIdentityCoverage(payload, trace, encodedBytes=None):
    """Reject empty/partial observers even if their self-summary says complete."""
    expected = set(payload["observed_result_outcomes"])
    groups, groupedRows, issues = {}, {}, Counter()
    for row in trace["rows"]:
        if row.get("stage") not in (*REQUIRED_CREDIT_STAGES, *ADOPT_PHASE_COUNTS):
            continue
        key = tuple(row.get(name) for name in ("pool_id", "job_id", "result_key", "source_id", "slot", "lane"))
        if (type(key[0]) is not int or not 1 <= key[0] <= 4
                or key[1] != payload["job"] or key[2] not in expected or key[3] != "image"
                or type(key[4]) is not int or key[4] not in (0, 1)
                or type(key[5]) is not int or key[5] != 0):
            issues["unexpected_export_identity"] += 1
            continue
        stages = groups.setdefault(key, Counter())
        groupedRows.setdefault(key, []).append(row)
        if row.get("outcome") == "OK":
            stages[row["stage"]] += 1
        else:
            issues["failed_required_stage"] += 1
        if row["stage"] == "parent.export_callback" and row.get("export_outcome") != "AVAILABLE":
            issues["nonavailable_export_callback"] += 1
    if len(expected) != COUNT:
        issues["result_denominator"] += 1
    if len(groups) != COUNT or {key[2] for key in groups} != expected:
        issues["raw_export_denominator"] += 1
    for key, stages in groups.items():
        for stage in REQUIRED_CREDIT_STAGES:
            if stages[stage] != 1:
                issues[stage + "_expected_one"] += 1
        issues.update(adoptPhaseIssues(groupedRows[key], encodedBytes))
    return {"complete": not issues, "expected_exports": COUNT,
        "observed_result_keys": len(expected), "raw_export_identities": len(groups), "issues": dict(issues),
        "interpretation": "Strict full-input coverage for this fixed single-image workload. Source rejection remains incomplete; no invented export or relaxed denominator."}


def captureIdentityCoverage(payload, trace):
    expected = payload["observed_result_outcomes"]
    counts, issues = Counter(), Counter()
    accepted = refused = 0
    runtimeId = payload["request"]["expected_runtime_instance_id"]
    for row in trace["rows"]:
        key = row.get("result_key")
        if (row.get("stage") != "producer.capture_credit_acquire" or row.get("job_id") != payload["job"]
                or row.get("runtime_id") != runtimeId or key not in expected
                or row.get("result_ordinal") != expected[key]["ordinal"] or row.get("source_id") != "image"
                or type(row.get("slot")) is not int or row["slot"] not in (0, 1)
                or type(row.get("lane")) is not int or row["lane"] != 0
                or type(row.get("offset")) is not int or row["offset"] != 0
                or row.get("capacity") != 8*1024*1024 or row.get("raw_bytes") != 1920*1080*3):
            issues["unexpected_capture_identity_or_profile"] += 1
            continue
        counts[key] += 1
        if (not all(type(row.get(name)) is int and row[name] >= 0 for name in ("start_ns", "end_ns"))
                or row["end_ns"] < row["start_ns"]
                or type(row.get("elapsed_ms")) not in (int, float) or not math.isfinite(row["elapsed_ms"])
                or row["elapsed_ms"] != (row["end_ns"]-row["start_ns"])/1e6
                or (row.get("thread_cpu_ns") is not None
                    and (type(row["thread_cpu_ns"]) is not int or row["thread_cpu_ns"] < 0))):
            issues["invalid_acquire_clock"] += 1
            continue
        arguments = row.get("acquire_args")
        if (not isinstance(arguments, list) or len(arguments) != 1 or arguments[0] is not False
                or row.get("acquire_kwargs") != {} or row.get("outcome") != "OK"
                or type(row.get("acquired")) is not bool):
            issues["unexpected_native_acquire"] += 1
            continue
        accepted += row["acquired"] is True
        refused += row["acquired"] is False
        source = expected[key]["source_outcomes"]["image"]
        if row["acquired"] is False and (source.get("state") != "UNAVAILABLE"
                or source.get("reason") != "BUDGET_EXCEEDED"):
            issues["refusal_source_mismatch"] += 1
    if len(expected) != COUNT or set(counts) != set(expected) or any(value != 1 for value in counts.values()):
        issues["capture_attempt_denominator"] += 1
    return {"complete": not issues, "expected_attempts": COUNT, "observed_attempts": sum(counts.values()),
        "accepted": accepted, "refused": refused, "issues": dict(issues),
        "interpretation": "Original nonblocking acquire return only. False proves no credit obtained by this attempt, not the identity of its prior holder. Refusal is valid diagnostic data but never full-image coverage."}


def captureExportJoin(capture, export):
    attempts, admitted, issues = {}, {}, Counter()
    for row in capture["rows"]:
        key = tuple(row.get(name) for name in ("job_id", "result_key", "source_id"))
        if key in attempts:
            issues["duplicate_capture_identity"] += 1
        attempts[key] = row
    for row in export["rows"]:
        if row.get("stage") != "parent.export_submit" or row.get("outcome") != "OK":
            continue
        key = tuple(row.get(name) for name in ("job_id", "result_key", "source_id"))
        if key in admitted:
            issues["duplicate_export_identity"] += 1
        admitted[key] = row
        source = attempts.get(key)
        if source is None or source.get("acquired") is not True:
            issues["export_without_accepted_capture"] += 1
        elif (source.get("slot"), source.get("lane")) != (row.get("slot"), row.get("lane")):
            issues["capture_export_lane_mismatch"] += 1
    for key, row in attempts.items():
        if row.get("acquired") is True and key not in admitted:
            issues["accepted_capture_without_export"] += 1
    return {"complete": not issues, "capture_identities": len(attempts), "admitted_exports": len(admitted),
        "issues": dict(issues), "interpretation": "Exact Job/result/source and slot/lane join. Refused attempts have no invented export or pool identity. Acquisition alone does not prove copy/transfer success; unmatched accepted attempts remain explicit."}


def run(args):
    base = policy.base
    before = identity()
    manifest = {"experiment": "export_credit_lifetime_abba_v2", "head": git("rev-parse", "HEAD"),
        "dirty": git("status", "--short"), "source_before": before, "started_ns": time.perf_counter_ns(),
        "python": sys.version, "os": platform.platform(), "configuration": {
            "count": COUNT, "warmup": WARMUP, "observer_order": list(ORDER), "qt_platform": args.qt_platform},
        "load": "Original seed20260926, 1920x1080x3, scheduled5Hz,96inputs/8warmup; two consumers/windows",
        "bounds": {"trials": 4, "watchdog_seconds_per_trial": 90, "log_bytes": base.LOG_BYTES,
            "raw_trial_bytes": policy.RAW_BYTES, "asset_split_bytes": 24*1024*1024,
            "export_deadline_seconds": .5, "asset_rpc_deadline_seconds": .5},
        "interpretation": "Diagnostic-only observer control. No ownership, channel, codec, quota or deadline change. "
            "Pre-adopt time includes callback lock wait and Python work; it is not a pure lock measurement. "
            "Credit trace completeness describes observed admitted exports, never missing-source acceptance. "
            "Keep original coverage/age/latency failures; never subtract presumed observer overhead.",
        "trials": [], "measurement_status": "RUNNING", "performance_status": "NOT_ASSESSED"}
    base.writeManifest(args.output, manifest)
    for position, mode in enumerate(ORDER):
        directory = args.output / f"trial-{position}-credit-{mode}"
        directory.mkdir()
        entry = {"position": position, "observer_mode": mode,
            "disk_preflight": base.diskPreflight(directory),
            "watchdog": base.supervisedTrial(childCommand(directory, args.qt_platform, mode), directory)}
        payload, fields = policy.trialEvidence(directory, args.output, WARMUP, "none_qt")
        if payload is not None:
            entry.update(fields)
            entry["input_sha256"] = payload["input_sha256"]
            entry["exact_ordinal_coverage"] = policy.exactCoverage(payload)
            path = directory / "asset-split.json"
            if not path.is_file() or not 0 < path.stat().st_size <= 24*1024*1024:
                raise ValueError("asset split missing or oversized")
            split = json.loads(path.read_text(encoding="utf-8"))
            entry["asset_fingerprint"] = assetFingerprint(split)
            entry["split_accounting"] = analyze_calls(split, count=COUNT, warmup=WARMUP, capture=True)
            entry["passive_markers_disabled"] = not split.get("features", {}).get("passive_rpc_markers", False)
            credit = payload.get("export_credit_trace")
            entry["export_credit_trace"] = credit
            if mode == "on":
                creditPath = directory / "export-credit.json"
                if not creditPath.is_file() or not 0 < creditPath.stat().st_size <= 12*1024*1024:
                    raise ValueError("export-credit evidence missing or oversized")
                rawCredit = json.loads(creditPath.read_text(encoding="utf-8"))
                if (rawCredit.get("role") != "export_credit" or not isinstance(rawCredit.get("rows"), list)
                        or len(rawCredit["rows"]) > 6000):
                    raise ValueError("export-credit evidence shape invalid")
                entry["credit_identity_coverage"] = creditIdentityCoverage(payload, rawCredit, entry["asset_fingerprint"].get("asset_bytes"))
                (directory / "credit-boundaries.json").write_text(
                    json.dumps(creditBoundaries(payload, rawCredit), indent=2), encoding="utf-8")
            entry["observer_configuration_matches"] = (
                isinstance(credit, dict) and credit.get("enabled") is True
                if mode == "on" else credit is None and not (directory / "export-credit.json").exists())
            capture = payload.get("capture_credit_trace")
            entry["capture_credit_trace"] = capture
            paths = sorted(directory.glob("capture-credit-*.json"))
            if mode == "on":
                if len(paths) != 1 or not 0 < paths[0].stat().st_size <= 1024*1024:
                    raise ValueError("capture-credit evidence missing or oversized")
                rawCapture = json.loads(paths[0].read_text(encoding="utf-8"))
                if (rawCapture.get("role") != "capture_credit" or not isinstance(rawCapture.get("rows"), list)
                        or len(rawCapture["rows"]) > 512):
                    raise ValueError("capture-credit evidence shape invalid")
                entry["capture_identity_coverage"] = captureIdentityCoverage(payload, rawCapture)
                entry["capture_export_join"] = captureExportJoin(rawCapture, rawCredit)
                entry["observer_configuration_matches"] = (entry["observer_configuration_matches"]
                    and isinstance(capture, dict) and capture.get("enabled") is True)
            else:
                entry["observer_configuration_matches"] = entry["observer_configuration_matches"] and capture is None and not paths
            entry["source_outcomes"] = dict(Counter(
                source["state"] + "/" + (source.get("reason") or "")
                for row in payload["observed_result_outcomes"].values()
                for key, source in row["source_outcomes"].items() if key == "image"))
            entry["delivery"] = {key: payload[key] for key in (
                "job_status", "executed", "achieved_hz", "max_schedule_lateness_ms", "p95_execution_ms", "phases")}
            entry["delivery"]["consumer_p95_model_ms"] = [row["p95_model_ms"] for row in payload["consumers"]]
            entry["delivery"]["gui_p95_ms"] = [row["p95_scope_to_gui_ms"] for row in payload["ui"]]
            entry["delivery"]["paint_p95_ms"] = [row["p95_scope_to_paint_ms"] for row in payload["ui"]]
        manifest["trials"].append(entry)
        base.writeManifest(args.output, manifest)
        print(json.dumps({"position": position, "observer_mode": mode,
            "watchdog": entry["watchdog"]["status"], "coverage": entry.get("exact_ordinal_coverage"),
            "sources": entry.get("source_outcomes"), "credit": entry.get("export_credit_trace"),
            "capture": entry.get("capture_credit_trace")}), flush=True)
    manifest["source_after"] = identity()
    manifest["source_stable"] = before == manifest["source_after"]
    fingerprints = [(row.get("input_sha256"), row.get("asset_fingerprint", {}).get("asset_bytes"),
        row.get("asset_fingerprint", {}).get("asset_sha256")) for row in manifest["trials"]]
    manifest["identical_input_and_asset"] = len(set(fingerprints)) == 1 and all(
        row.get("asset_fingerprint", {}).get("complete") for row in manifest["trials"])
    valid = manifest["source_stable"] and manifest["identical_input_and_asset"] and all(
        row["watchdog"]["status"] == "PASS" and row["watchdog"].get("owner_retirement_verified")
        and row.get("trace_complete") and row.get("exact_ordinal_coverage")
        and row.get("passive_markers_disabled") and row.get("observer_configuration_matches")
        and row.get("split_accounting", {}).get("status") == "COMPLETE"
        and (row["observer_mode"] == "off" or (row.get("export_credit_trace", {}).get("complete") is True
            and row.get("credit_identity_coverage", {}).get("complete") is True
            and row.get("capture_credit_trace", {}).get("complete") is True
            and row.get("capture_identity_coverage", {}).get("complete") is True
            and row.get("capture_export_join", {}).get("complete") is True))
        for row in manifest["trials"])
    manifest["measurement_status"] = "VALID" if valid else "INVALID"
    manifest["finished_ns"] = time.perf_counter_ns()
    base.writeManifest(args.output, manifest)
    print(json.dumps({"measurement_status": manifest["measurement_status"], "performance_status": "NOT_ASSESSED"}))
    return int(not valid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qt-platform", default="windows" if os.name == "nt" else "offscreen")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if any(args.output.is_relative_to(ROOT/name) for name in ("src", "tests", "scripts", "proto", "examples", "prototypes")):
        parser.error("evidence must be outside source identity directories")
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        return run(args)
    except BaseException as error:
        (args.output/"failure.json").write_text(json.dumps({"measurement_status": "INVALID",
            "error_type": type(error).__name__, "at_ns": time.perf_counter_ns()}), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
