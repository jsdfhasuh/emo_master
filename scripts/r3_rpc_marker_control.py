"""Diagnostic NONE+Qt marker-off/on/on/off; original 96/8 workload unchanged.

Run into a new directory. Every arm uses the existing split trace and original
two consumers/windows, input, codec, deadlines, quotas and process-tree watchdog.
This is an observer control, never an acceptance or production-option change.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "scripts")]

from scripts import r3_policy_measure as policy  # noqa: E402
from scripts.r3_asset_split_analyze import analyze, analyze_calls  # noqa: E402
from p2_validate import identity, git  # noqa: E402


ORDER = ("off", "on", "on", "off")
COUNT, WARMUP = 96, 8
EXPERIMENT = "rpc_passive_markers_abba_v1"


def childCommand(directory, qt_platform, mode):
    if mode not in {"off", "on"}:
        raise ValueError("unknown passive marker mode")
    command = [sys.executable, str(ROOT / "scripts" / "r3_policy_measure.py"), "--child",
        "--output", str(directory), "--arm", "none_qt", "--count", str(COUNT), "--warmup", str(WARMUP),
        "--qt-platform", qt_platform, "--asset-split-trace"]
    if mode == "on":
        command.append("--passive-rpc-markers")
    return command


def assetFingerprint(split):
    rows = [row for row in split["rows"] if row.get("stage") == "client.asset_rpc_split"]
    fingerprints = {(row.get("asset_bytes"), row.get("asset_sha256")) for row in rows}
    complete = (len(rows) == COUNT*2 and len(fingerprints) == 1 and all(
        isinstance(size, int) and not isinstance(size, bool) and 0 < size <= 8*1024*1024
        and isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
        for size, sha in fingerprints))
    return {"complete": complete, "calls": len(rows), "distinct_fingerprints": len(fingerprints),
            "asset_bytes": next(iter(fingerprints))[0] if complete else None,
            "asset_sha256": next(iter(fingerprints))[1] if complete else None}


def run(args):
    base = policy.base
    before = identity()
    manifest = {"experiment": EXPERIMENT, "started_ns": time.perf_counter_ns(),
        "head": git("rev-parse", "HEAD"), "dirty": git("status", "--short"), "source_before": before,
        "configuration": {"count": COUNT, "warmup": WARMUP, "marker_order": list(ORDER),
            "qt_platform": args.qt_platform, "output": str(args.output)},
        "original_script_hashes": {name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
            for name in ("p2_measure.py", "p3_measure.py", "r3_measure.py", "r3_policy_measure.py", "r3_asset_split_trace.py")},
        "python": sys.version, "os": platform.platform(), "clock": "same-host perf_counter_ns; nested spans inclusive",
        "runtime_route": "Unchanged normal LoadProject/StartJob RPC; NONE+capture; two consumers and two Qt windows",
        "load": "Original seed 20260926, 1920x1080x3, 5Hz, 96 inputs with 8 warmup",
        "bounds": {"maximum_trials": 4, "watchdog_seconds": 90, "trace_rows": 6000,
            "trace_file_bytes": 24*1024*1024, "log_bytes_per_trial": base.LOG_BYTES,
            "observation_seconds": policy.OBSERVATION_SECONDS, "post_terminal_observation_seconds": policy.FINAL_OBSERVATION_SECONDS,
            "export_deadline_seconds": .5, "asset_rpc_deadline_seconds": .5},
        "disk_preflight": base.diskPreflight(args.output), "trials": [],
        "measurement_status": "RUNNING", "performance_status": "NOT_ASSESSED",
        "interpretation": "Diagnostic observer control only. Do not subtract presumed observer cost or change historical verdicts. "
            "Marker-off and marker-on use identical production defaults; no subchannel-pool option is changed. "
            "Keep all sampled age, lateness, exact denominator and resource evidence, including failed gates."}
    base.writeManifest(args.output, manifest)
    for position, mode in enumerate(ORDER):
        directory = args.output / f"trial-{position}-markers-{mode}"
        directory.mkdir()
        entry = {"group": 0, "position": position, "arm": "none_qt", "marker_mode": mode,
            "disk_preflight": base.diskPreflight(directory),
            "watchdog": base.supervisedTrial(childCommand(directory, args.qt_platform, mode), directory)}
        payload, fields = policy.trialEvidence(directory, args.output, WARMUP, "none_qt")
        if payload is not None:
            entry.update(fields)
            entry["asset_split_trace"] = payload.get("asset_split_trace", {"complete": False})
            split_path = directory / "asset-split.json"
            if not split_path.is_file() or split_path.stat().st_size > 24*1024*1024:
                raise ValueError("asset split missing or oversized")
            split = json.loads(split_path.read_text(encoding="utf-8"))
            entry["split_accounting"] = analyze_calls(split, count=COUNT, warmup=WARMUP, capture=True)
            entry["marker_configuration_matches"] = bool(split.get("features", {}).get("passive_rpc_markers")) == (mode == "on")
            entry["asset_fingerprint"] = assetFingerprint(split)
            entry["input_sha256"] = payload["input_sha256"]
            entry["exact_ordinal_coverage"] = policy.exactCoverage(payload)
            # Preserve full scalar gate observations without inventing a new
            # acceptance comparison against a capture-off baseline absent here.
            entry["delivery"] = {"job_status": payload["job_status"], "executed": payload["executed"],
                "achieved_hz": payload["achieved_hz"], "max_schedule_lateness_ms": payload["max_schedule_lateness_ms"],
                "p95_execution_ms": payload["p95_execution_ms"],
                "p95_model_ms": [row["p95_model_ms"] for row in payload["consumers"]],
                "p95_scope_to_gui_ms": [row["p95_scope_to_gui_ms"] for row in payload["ui"]],
                "p95_scope_to_paint_ms": [row["p95_scope_to_paint_ms"] for row in payload["ui"]],
                "phases": payload["phases"], "qt_platform": payload["qt_platform"]}
        manifest["trials"].append(entry)
        base.writeManifest(args.output, manifest)
        print(json.dumps({"position": position, "marker_mode": mode, "watchdog": entry["watchdog"]["status"],
                          "split": entry.get("split_accounting", {}).get("status"), "delivery": entry.get("delivery")}), flush=True)
    manifest["source_after"] = identity()
    manifest["source_stable"] = before == manifest["source_after"]
    fingerprints = [(row.get("input_sha256"), row.get("asset_fingerprint", {}).get("asset_bytes"),
                     row.get("asset_fingerprint", {}).get("asset_sha256")) for row in manifest["trials"]]
    manifest["identical_input_and_asset"] = len(fingerprints) == 4 and len(set(fingerprints)) == 1 and all(
        row.get("asset_fingerprint", {}).get("complete") for row in manifest["trials"])
    valid = manifest["source_stable"] and manifest["identical_input_and_asset"] and all(
        row["watchdog"]["status"] == "PASS" and row["watchdog"].get("owner_retirement_verified")
        and row.get("trace_complete") and row.get("exact_ordinal_coverage") and row.get("marker_configuration_matches")
        and row.get("asset_split_trace", {}).get("complete") and row.get("split_accounting", {}).get("status") == "COMPLETE"
        for row in manifest["trials"])
    manifest["measurement_status"] = "VALID" if valid else "INVALID"
    manifest["finished_ns"] = time.perf_counter_ns()
    base.writeManifest(args.output, manifest)
    # New exclusive analysis directory, raw files retained. The analyzer knows
    # this explicit four-arm experiment; the original nine-arm mode is unchanged.
    analyzed = analyze(args.output, args.output / "analysis")
    observations = [{"position": row["position"], "marker_mode": row["marker_mode"],
        "status": row["status"], "measured_calls": row["calls"]["joined_measured_calls"],
        "passive_markers": row["calls"]["passive_markers"],
        "intervals_ms": {name: values for name, values in row["calls"]["intervals_ms"].items()
            if name in {"rpc_total", "serialize_end_to_deserialize_start", "serialize_end_to_next_loop_turn",
                        "serialize_end_to_rpc_done", "next_loop_turn_to_deserialize_start", "rpc_done_to_deserialize_start"}}}
        for row in analyzed.get("trials", [])]
    print(json.dumps({"measurement_status": manifest["measurement_status"], "performance_status": "NOT_ASSESSED",
                      "analysis_status": analyzed["analysis_status"],
                      "identical_input_and_asset": manifest["identical_input_and_asset"],
                      "observations": observations}), flush=True)
    return int(not valid or analyzed["analysis_status"] != "COMPLETE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--qt-platform", default="windows" if os.name == "nt" else "offscreen")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if any(args.output.is_relative_to(ROOT / name) for name in ("src", "tests", "scripts", "proto", "examples", "prototypes")):
        parser.error("evidence must be outside source identity directories")
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        return run(args)
    except BaseException as error:
        (args.output / "failure.json").write_text(json.dumps({"measurement_status": "INVALID",
            "error_type": type(error).__name__, "at_ns": time.perf_counter_ns()}), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
