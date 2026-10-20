"""Repeat synthetic operator acceptance and sample Windows owner/worker resources."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from p2_resources import resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="Frozen Runtime; otherwise use source")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--restarts", type=int, default=10)
    parser.add_argument("--qt-platform", choices=["offscreen", "windows"], default="offscreen")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration < 0 or args.restarts < 0:
        parser.error("duration and restarts must be nonnegative")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = root / "self-test.json"
    command = [str(args.executable.resolve())] if args.executable else [
        sys.executable, str(Path(__file__).with_name("run_operator.py"))]
    command += ["--self-test", "--result-json", str(report), "--duration", str(args.duration), "--restarts", str(args.restarts)]
    environment = dict(os.environ, QT_QPA_PLATFORM=args.qt_platform)
    if args.executable:
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
    samples = []
    with (root / "process.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=root, env=environment, stdout=log, stderr=log)
        deadline = time.monotonic() + args.duration + 60 + (args.restarts + 1) * 30
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("operator validation exceeded its deadline")
                row = dict(timestamp=time.time(), ownerPid=process.pid)
                try:
                    row["owner"] = resources(process.pid)
                    if report.exists():
                        progress = json.loads(report.read_text(encoding="utf-8"))
                        if progress.get("workerPid"):
                            row["workerPid"] = progress["workerPid"]
                            row["worker"] = resources(progress["workerPid"])
                except (OSError, AssertionError, json.JSONDecodeError) as error:
                    row["sampleError"] = str(error)
                samples.append(row)
                time.sleep(.5)
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True)
                else:
                    process.terminate()
                process.wait(timeout=10)
    payload = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"status": "error", "errors": ["report missing"]}
    summary = dict(status="PASS" if process.returncode == 0 and payload["status"] == "ok" else "FAIL",
        exitCode=process.returncode, selfTest=str(report), samples=samples,
        fixture="synthetic-two-blobs", fieldAcceptance="NOT_RUN",
        resourceAcceptance="NOT_RUN: actual workload and budgets not supplied")
    (root / "measurement.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{summary['status']}: {root / 'measurement.json'}")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
