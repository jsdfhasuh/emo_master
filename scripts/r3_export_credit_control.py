"""Fixed off/on/on/off observer control for parent export-credit lifetime.

The unchanged NONE+Qt 96/8, 1080p/5Hz workload uses two consumers and windows.
Every arm keeps the existing asset split trace; passive RPC markers stay off.
Unavailable sources and missed images remain failed full-coverage evidence.
The new observer reports admitted-export boundaries, never performance PASS.
"""
import argparse
from collections import Counter
import json
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
from p2_validate import identity, git  # noqa: E402


ORDER = ("off", "on", "on", "off")
COUNT, WARMUP = 96, 8


def childCommand(directory, platformName, mode):
    if mode not in {"off", "on"}:
        raise ValueError("unknown export-credit observer mode")
    command = [sys.executable, str(ROOT / "scripts" / "r3_policy_measure.py"), "--child",
        "--output", str(directory), "--arm", "none_qt", "--count", str(COUNT),
        "--warmup", str(WARMUP), "--qt-platform", platformName, "--asset-split-trace"]
    return command + (["--export-credit-trace"] if mode == "on" else [])


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


def run(args):
    base = policy.base
    before = identity()
    manifest = {"experiment": "export_credit_lifetime_abba_v1", "head": git("rev-parse", "HEAD"),
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
                (directory / "credit-boundaries.json").write_text(
                    json.dumps(creditBoundaries(payload, rawCredit), indent=2), encoding="utf-8")
            entry["observer_configuration_matches"] = (
                isinstance(credit, dict) and credit.get("enabled") is True
                if mode == "on" else credit is None and not (directory / "export-credit.json").exists())
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
            "sources": entry.get("source_outcomes"), "credit": entry.get("export_credit_trace")}), flush=True)
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
        and (row["observer_mode"] == "off" or row.get("export_credit_trace", {}).get("complete") is True)
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
