"""Read existing R3 policy evidence; never run a workload or alter raw evidence.

Usage: python scripts/r3_asset_split_analyze.py --input EXISTING --output NEW
The output directory must not exist. Stdlib only; no Runtime/Qt imports.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics


FILE_LIMIT = 24 * 1024 * 1024
OUTPUT_LIMIT = 16 * 1024 * 1024
TRIAL_LIMIT = 9
ROW_LIMIT = 6000
CALL_LIMIT = 192
STAGES = {
    "rpc": "client.asset_rpc_split", "handler": "server.handler",
    "dispatch": "server.dispatch_to_worker", "worker": "server.worker",
    "asset": "server.asset_read", "lock": "server.asset_lock_wait",
    "file": "server.file_read", "server_sha": "server.sha256_bytes",
    "serialize": "server.protobuf_serialize", "deserialize": "client.protobuf_deserialize",
    "client_sha": "client.sha256_bytes", "png": "client.png_decode_inclusive",
    "opencv": "client.opencv_decode",
}
IDENTITY = ("call_id", "channel_id", "result_key", "ordinal", "runtime_instance_id",
            "job_id", "resource_id", "asset_bytes", "decoder_request")
CHAIN = (("rpc", "start_ns"), ("handler", "start_ns"), ("worker", "start_ns"),
         ("worker", "end_ns"), ("handler", "end_ns"), ("serialize", "start_ns"),
         ("serialize", "end_ns"), ("deserialize", "start_ns"),
         ("deserialize", "end_ns"), ("rpc", "end_ns"))
CHAIN_NAMES = ("rpc_start_to_handler_start", "handler_start_to_worker_start", "worker",
               "worker_end_to_handler_end", "handler_end_to_serialize_start", "serialize",
               "serialize_end_to_deserialize_start", "deserialize", "deserialize_end_to_rpc_end")


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summary(values):
    ordered = sorted(values)
    return {"count": len(ordered), "negative_count": sum(value < 0 for value in ordered),
            "min_ms": min(ordered) if ordered else None,
            "p50_ms": statistics.median(ordered) if ordered else None,
            # Preserve the original R3 harness's upper empirical P95 convention.
            "p95_ms": ordered[min(len(ordered)-1, int(len(ordered)*.95))] if ordered else None,
            "max_ms": max(ordered) if ordered else None,
            "mean_ms": statistics.mean(ordered) if ordered else None}


def summarize_vectors(rows, field):
    values = defaultdict(list)
    for row in rows:
        for name, value in row[field].items():
            values[name].append(value)
    return {name: summary(items) for name, items in sorted(values.items())}


def analyze_calls(payload, *, count, warmup, capture):
    """Join by an exact recorded call ID; missing/ambiguous calls stay excluded."""
    rows = payload.get("rows", [])
    if not isinstance(rows, list) or len(rows) > ROW_LIMIT:
        raise ValueError("asset trace row budget exceeded or rows invalid")
    issues = Counter()
    recorded_clock = payload.get("clock", "")
    clock_ok = (isinstance(recorded_clock, str) and recorded_clock.startswith("same-process perf_counter_ns")
                and isinstance(payload.get("pid"), int) and not isinstance(payload["pid"], bool) and payload["pid"] > 0)
    if not clock_ok:
        issues["same_process_clock_origin_missing"] += 1
    if (payload.get("instrumentation_disabled") is not False or payload.get("diagnostic_errors") != 0
            or payload.get("dropped_rows") != 0):
        issues["trace_disabled_faulted_or_dropped"] += 1
    if not (payload.get("source_complete") is True and payload.get("source_unchanged") is True
            and payload.get("source_before") and payload.get("source_before") == payload.get("source_after")
            and all(payload["source_before"].values())):
        issues["source_fingerprint_incomplete_or_changed"] += 1
    if set(payload.get("outstanding_associations", {})) != {"requests", "replies", "tokens"}:
        issues["missing_association_accounting"] += 1
    if any(payload.get("outstanding_associations", {}).values()):
        issues["outstanding_associations"] += 1
    for name, value in payload.get("counters", {}).items():
        if value and any(part in name for part in (
                "overflow", "unmatched", "ambiguous", "conflict", "invalid", "untraced", "dropped", "replaced")):
            issues["trace_counter:" + name] += value
    if payload.get("stage_coverage", {}).get("missing_success_stages"):
        issues["original_missing_success_stages"] += 1
    grouped = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("call_id"), str) or not 0 < len(row["call_id"]) <= 160:
            issues["invalid_or_missing_call_id"] += 1
            continue
        grouped[row["call_id"]].append(row)
    if len(grouped) > CALL_LIMIT:
        raise ValueError("asset trace call budget exceeded")
    valid, rejected = [], []
    for call_id, call_rows in grouped.items():
        defects = Counter()
        groups = defaultdict(list)
        for row in call_rows:
            if not isinstance(row.get("stage"), str) or not 0 < len(row["stage"]) <= 120:
                defects["invalid_stage"] += 1
                continue
            groups[row.get("stage")].append(row)
            if row.get("outcome") != "OK":
                defects["failed_stage"] += 1
            if not all(isinstance(row.get(key), int) and not isinstance(row[key], bool) for key in ("start_ns", "end_ns")):
                defects["invalid_timestamps"] += 1
            elif row["end_ns"] < row["start_ns"]:
                defects["reversed_span"] += 1
            if row.get("resource_match") is not True or row.get("runtime_matches_local_server") is not True:
                defects["unverified_identity"] += 1
        reference = call_rows[0]
        if any(key not in reference for key in IDENTITY):
            defects["missing_identity_field"] += 1
        if any(not isinstance(reference.get(key), str) or not 0 < len(reference[key]) <= 160
               for key in ("result_key", "runtime_instance_id", "job_id", "resource_id")):
            defects["invalid_identity_value"] += 1
        if (not isinstance(reference.get("asset_bytes"), int) or isinstance(reference["asset_bytes"], bool)
                or not 0 < reference["asset_bytes"] <= 8*1024*1024):
            defects["invalid_asset_bytes"] += 1
        for row in call_rows[1:]:
            if any(row.get(key) != reference.get(key) for key in IDENTITY):
                defects["identity_conflict"] += 1
        ordinal, channel = reference.get("ordinal"), reference.get("channel_id")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or not 1 <= ordinal <= count:
            defects["out_of_range_ordinal"] += 1
        if not isinstance(channel, int) or isinstance(channel, bool) or channel <= 0:
            defects["invalid_channel"] += 1
        if reference.get("decoder_request") is not True:
            defects["not_decoder_request"] += 1
        for short, stage in STAGES.items():
            wanted = 2 if short == "lock" else 1
            if len(groups.get(stage, [])) != wanted:
                defects["stage_count:" + stage] += 1
        if set(groups) - set(STAGES.values()):
            defects["unknown_stage"] += 1
        if not defects and Counter(row.get("lock_phase") for row in groups[STAGES["lock"]]) != {"admit": 1, "retire": 1}:
            defects["lock_phase_coverage"] += 1
        if defects:
            issues.update(defects)
            rejected.append({"call_id": call_id, "ordinal": ordinal, "defects": dict(defects)})
            continue
        stages = {short: groups[stage][0] for short, stage in STAGES.items() if short != "lock"}
        boundaries = [stages[stage][edge] for stage, edge in CHAIN]
        intervals = {name: (right-left)/1e6 for name, left, right in zip(CHAIN_NAMES, boundaries, boundaries[1:])}
        rpc, sha, png = (stages[name] for name in ("rpc", "client_sha", "png"))
        intervals.update(rpc_total=(rpc["end_ns"]-rpc["start_ns"])/1e6,
            rpc_end_to_client_sha_start=(sha["start_ns"]-rpc["end_ns"])/1e6,
            client_sha=(sha["end_ns"]-sha["start_ns"])/1e6,
            client_sha_end_to_png_start=(png["start_ns"]-sha["end_ns"])/1e6,
            png=(png["end_ns"]-png["start_ns"])/1e6,
            rpc_end_to_png_end=(png["end_ns"]-rpc["end_ns"])/1e6)
        negative = [name for name, value in intervals.items() if value < 0]
        for child, parent in (("worker", "handler"), ("asset", "worker"), ("file", "asset"),
                              ("server_sha", "asset"), ("opencv", "png")):
            a, b = stages[child], stages[parent]
            if not b["start_ns"] <= a["start_ns"] <= a["end_ns"] <= b["end_ns"]:
                negative.append("nesting:" + child + ":" + parent)
        dispatch, handler, worker = (stages[name] for name in ("dispatch", "handler", "worker"))
        if dispatch["start_ns"] != handler["start_ns"] or not handler["start_ns"] <= dispatch["end_ns"] <= worker["start_ns"]:
            negative.append("dispatch_boundaries")
        locks = {row["lock_phase"]: row for row in groups[STAGES["lock"]]}
        asset = stages["asset"]
        for phase, lock in locks.items():
            if not asset["start_ns"] <= lock["start_ns"] <= lock["end_ns"] <= asset["end_ns"]:
                negative.append("nesting:lock:" + phase + ":asset")
        if not (locks["admit"]["end_ns"] <= stages["file"]["start_ns"]
                <= stages["file"]["end_ns"] <= stages["server_sha"]["start_ns"]
                <= stages["server_sha"]["end_ns"] <= locks["retire"]["start_ns"]):
            negative.append("asset_read_order")
        if negative:
            issues["calls_with_boundary_or_nesting_errors"] += 1
        wall, cpu, uncharged = {}, {}, {}
        for row in call_rows:
            label = row["stage"] + (":" + row["lock_phase"] if row["stage"] == STAGES["lock"] else "")
            duration = row["end_ns"]-row["start_ns"]
            wall[label] = duration/1e6
            used = row.get("thread_cpu_ns")
            if used is not None:
                if not finite(used) or used < 0:
                    issues["invalid_thread_cpu"] += 1
                else:
                    cpu[label] = used/1e6
                    uncharged[label] = (duration-used)/1e6
        valid.append({"call_id": call_id, "ordinal": ordinal, "channel_id": channel,
                      "intervals_ms": intervals, "boundary_errors": negative,
                      "wall_ms": wall, "thread_cpu_ms": cpu, "wall_minus_thread_cpu_ms": uncharged})
    expected_total = count*2 if capture else 0
    expected_measured = (count-warmup)*2 if capture else 0
    counters = payload.get("counters", {})
    for key in ("client_calls_started", "client_calls_finished"):
        if counters.get(key, 0) != expected_total:
            issues["unexpected_total:" + key] += 1
    if len(grouped) != expected_total:
        issues["unexpected_observed_call_total"] += 1
    channels = sorted({row["channel_id"] for row in valid})
    if len(channels) != (2 if capture else 0):
        issues["unexpected_channel_count"] += 1
    coverage = []
    for channel in channels:
        counts = Counter(row["ordinal"] for row in valid if row["channel_id"] == channel)
        missing = sorted(set(range(1, count+1))-set(counts))
        duplicates = {str(key): value for key, value in counts.items() if value != 1}
        if missing or duplicates:
            issues["channel_ordinal_coverage"] += 1
        coverage.append({"channel_id": channel, "missing_ordinals": missing, "duplicate_ordinals": duplicates})
    measured = [row for row in valid if warmup < row["ordinal"] <= count]
    if len(measured) != expected_measured:
        issues["unexpected_measured_call_total"] += 1
    return {"status": "COMPLETE" if not issues else "INCOMPLETE", "issues": dict(issues),
            "clock": {"recorded_origin": recorded_clock, "pid": payload.get("pid"),
                      "origin_usable": clock_ok, "cross_process_join": False},
            "expected_total_calls": expected_total, "observed_call_ids": len(grouped),
            "expected_measured_calls": expected_measured, "joined_measured_calls": len(measured),
            "rejected_calls": rejected, "channel_coverage": coverage,
            "intervals_ms": summarize_vectors(measured, "intervals_ms") if clock_ok else {},
            "stage_wall_ms": summarize_vectors(measured, "wall_ms") if clock_ok else {},
            "stage_thread_cpu_ms": summarize_vectors(measured, "thread_cpu_ms") if clock_ok else {},
            "stage_wall_minus_thread_cpu_ms": summarize_vectors(measured, "wall_minus_thread_cpu_ms") if clock_ok else {},
            "by_channel": [{"channel_id": channel, "intervals_ms": summarize_vectors(
                [row for row in measured if row["channel_id"] == channel], "intervals_ms")}
                for channel in channels] if clock_ok else [],
            "slowest_rpc_calls": [{key: value for key, value in row.items() if key !=
                "wall_minus_thread_cpu_ms"} for row in sorted(
                    measured, key=lambda row: row["intervals_ms"]["rpc_total"], reverse=True)[:5]] if clock_ok else []}


def consumer_intervals(trial, count, warmup):
    """No guessed channel→consumer mapping or cross-file result-key join."""
    outputs = []
    for index, consumer in enumerate(trial.get("consumers", [])):
        records = consumer.get("rows", [])
        if len(records) > 128:
            raise ValueError("consumer record budget exceeded")
        values, issues, ordinals = defaultdict(list), Counter(), Counter()
        for row in records:
            ordinal = row.get("ordinal")
            if not isinstance(ordinal, int) or not warmup < ordinal <= count:
                issues["unexpected_ordinal"] += 1
                continue
            ordinals[ordinal] += 1
            received, model, scope = (row.get(key) for key in ("received_ns", "model_ns", "scope_ended_ns"))
            age = row.get("owner_age_at_send_ms")
            if not all(finite(value) for value in (received, model, scope, age)):
                issues["missing_timestamps_or_age"] += 1
                continue
            vector = {"scope_to_wire_result_age": age, "wire_result_age_to_receipt": (received-scope)/1e6-age,
                      "receipt_to_model": (model-received)/1e6, "scope_to_model": (model-scope)/1e6}
            for name, value in vector.items():
                values[name].append(value)
                if value < 0:
                    issues["negative:" + name] += 1
            if not row.get("applied_to_live") or row.get("failures") or "image" not in row.get("decoded", []):
                issues["not_complete_applied_image"] += 1
        missing = sorted(set(range(warmup+1, count+1))-set(ordinals))
        duplicates = {str(key): value for key, value in ordinals.items() if value != 1}
        if missing or duplicates:
            issues["ordinal_coverage"] += 1
        outputs.append({"consumer_index": index, "issues": dict(issues), "missing_ordinals": missing,
                        "duplicate_ordinals": duplicates, "intervals_ms": {name: summary(items) for name, items in values.items()}})
    return outputs


class EvidenceReader:
    def __init__(self, root):
        self.root = root.resolve()
        self.hashes = {}

    def read(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("evidence reference is absent or outside input root")
        if path.stat().st_size > FILE_LIMIT:
            raise ValueError("evidence file budget exceeded")
        content = path.read_bytes()
        if len(content) > FILE_LIMIT:
            raise ValueError("evidence file budget exceeded")
        self.hashes[str(path.relative_to(self.root))] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    def unchanged(self):
        return all((self.root / name).is_file() and (self.root / name).stat().st_size <= FILE_LIMIT
                   and hashlib.sha256((self.root / name).read_bytes()).hexdigest() == digest
                   for name, digest in self.hashes.items())


def analyze(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination == root or root.is_relative_to(destination):
        raise ValueError("output must be a new directory outside or below the input root")
    reader = EvidenceReader(root)
    manifest = reader.read("evidence.json")
    config = manifest.get("configuration", {})
    count, warmup = config.get("count"), config.get("warmup")
    if not (isinstance(count, int) and 2 <= count <= 96 and isinstance(warmup, int) and 0 <= warmup < count):
        raise ValueError("missing or invalid original count/warmup configuration")
    trials = manifest.get("trials", [])
    if not isinstance(trials, list) or not 1 <= len(trials) <= TRIAL_LIMIT:
        raise ValueError("trial budget exceeded or no trials")
    results, seen = [], set()
    clock_record = manifest.get("clock", "")
    cross_clock_recorded = isinstance(clock_record, str) and "same-host perf_counter_ns" in clock_record
    for entry in trials:
        relative = entry.get("raw_evidence")
        if not isinstance(relative, str) or relative in seen:
            raise ValueError("missing or repeated raw trial reference")
        seen.add(relative)
        trial = reader.read(relative)
        split = reader.read(str(Path(relative).parent / "asset-split.json"))
        arm = entry.get("arm")
        if arm not in ("all_off", "all_qt", "none_qt") or arm != trial.get("arm"):
            raise ValueError("unknown or conflicting original trial arm")
        capture = arm != "all_off"
        if trial.get("capture_enabled") is not capture:
            raise ValueError("original capture configuration disagrees with arm")
        calls = analyze_calls(split, count=count, warmup=warmup, capture=capture)
        consumers = consumer_intervals(trial, count, warmup)
        issues = []
        if calls["status"] != "COMPLETE":
            issues.append("call_accounting_incomplete")
        if len(consumers) != (2 if capture else 0) or any(row["issues"] for row in consumers):
            issues.append("consumer_accounting_incomplete")
        if entry.get("asset_split_trace", {}).get("complete") is not True or trial.get("asset_split_trace", {}).get("complete") is not True:
            issues.append("original_split_trace_incomplete")
        if entry.get("trace_complete") is not True or entry.get("watchdog", {}).get("status") != "PASS":
            issues.append("original_trial_incomplete")
        if not cross_clock_recorded:
            issues.append("cross_process_clock_assumption_unrecorded")
        results.append({"group": entry.get("group"), "position": entry.get("position"), "arm": arm,
                        "status": "COMPLETE" if not issues else "INCOMPLETE", "issues": issues,
                        "calls": calls, "consumers": consumers})
    unchanged = reader.unchanged()
    groups = config.get("groups")
    expected_trials = {(group, position, ("all_off", "all_qt", "none_qt")[(position+group) % 3])
                       for group in range(groups) for position in range(3)} if isinstance(groups, int) and 1 <= groups <= 3 else set()
    observed_trials = [(row["group"], row["position"], row["arm"]) for row in results]
    trial_coverage = bool(expected_trials) and len(observed_trials) == len(expected_trials) and set(observed_trials) == expected_trials
    status = "COMPLETE" if unchanged and trial_coverage and manifest.get("source_stable") is True and all(
        trial["status"] == "COMPLETE" for trial in results) else "INCOMPLETE"
    output = {"analysis_status": status, "performance_verdict": "NOT_EVALUATED",
        "input_head": manifest.get("head"), "original_measurement_status": manifest.get("measurement_status"),
        "original_performance_status": manifest.get("performance_status"), "count": count, "warmup": warmup,
        "input_unchanged": unchanged, "original_trial_coverage_complete": trial_coverage, "input_sha256": reader.hashes,
        "cross_process_clock": {"recorded_origin": manifest.get("clock"), "os": manifest.get("os"),
            "python": manifest.get("python"), "independently_verified": False,
            "assumption": "Consumer scope intervals reuse the harness's same-host perf_counter_ns assumption. "
                "The analyzer cannot independently verify host/clock origins or infer clock drift. "
                "RPC chains stay within each split trace's recorded owner PID."},
        "interpretation": ["Differences are formed per exact call before summarizing; never subtract stage quantiles.",
            "Adjacent RPC-chain intervals sum to that call's RPC total; stage durations are inclusive and overlap.",
            "No gap is pure network time. Scheduling, gRPC/C-core, memory copies and instrumentation may contribute.",
            "Wall minus current-thread CPU is uncharged wall time, not proof of GIL ownership or its cause; negative values remain visible.",
            "wire_result_age_to_receipt uses owner_age measured inside wireResult, before metadata serialization/send; it is not pure transport.",
            "Consumer indices and trace channel IDs are not joined or inferred. Export completion, closure and event-poll waits are not separately observed here.",
            "Rejected calls remain counted and make analysis incomplete. Boundary errors remain signed in summaries.",
            "All original deadlines, denominators and acceptance verdicts remain unchanged. No new workload ran."],
        "trials": results}
    content = json.dumps(output, indent=2, allow_nan=False).encode()
    if len(content) > OUTPUT_LIMIT:
        raise ValueError("analysis output budget exceeded")
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "analysis.json").open("xb") as stream:
        stream.write(content)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.input, args.output)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError) as error:
        # Do not print raw paths, untrusted values, or a potentially huge traceback.
        print(json.dumps({"analysis_status": "INVALID", "performance_verdict": "NOT_EVALUATED",
                          "error_type": type(error).__name__,
                          "message": "Input shape, bounds, references, or output-path validation failed; raw evidence was not changed."}))
        return 2
    compact = {key: result[key] for key in ("analysis_status", "performance_verdict", "input_unchanged",
                                           "original_trial_coverage_complete")}
    compact["trials"] = [{"group": row["group"], "arm": row["arm"], "status": row["status"],
        "issues": row["issues"], "call_issues": row["calls"]["issues"],
        "joined_measured_calls": row["calls"]["joined_measured_calls"],
        "intervals_ms": row["calls"]["intervals_ms"], "consumers": row["consumers"]} for row in result["trials"]]
    print(json.dumps(compact, separators=(",", ":"), allow_nan=False))
    return int(result["analysis_status"] != "COMPLETE")


if __name__ == "__main__":
    raise SystemExit(main())
