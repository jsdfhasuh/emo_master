"""Supervised P0 evidence with code identity and distinct acceptance dimensions."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from prototypes.runtime_pages_p0.watchdog import supervised  # noqa: E402


def identity():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    paths = sorted({p for directory in ("prototypes/runtime_pages_p0", "tests/p0", "src/emo_master/apps/runtime", "src/emo_master/apps/designer")
                    for p in (ROOT/directory).rglob("*.py")} | {ROOT/"scripts/p0_validate.py"})
    return dict(head=git("rev-parse", "HEAD").decode().strip(), status=git("status", "--short").decode(),
                diff_sha256=hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
                source_sha256={str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "manual_test_workspace/p0-round2/evidence.json")
    parser.add_argument("--scenarios", nargs="+", choices=["exports", "network", "network-faults", "pipeline-faults", "continuous", "benchmark"],
                        default=["exports", "network", "network-faults", "pipeline-faults", "benchmark"])
    parser.add_argument("--count", type=int, default=96)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--benchmark-timeout", type=float)
    args = parser.parse_args()
    if args.count < 2 or args.rounds < 1 or args.warmup < 0:
        parser.error("invalid window size")
    if args.output.exists():
        parser.error("evidence already exists; choose a new output name")
    report = dict(code=identity(), started_utc=datetime.now(timezone.utc).isoformat(),
                  python=sys.version, executable=sys.executable, os=platform.platform(), processor=platform.processor(),
                  versions={name: importlib.metadata.version(name) for name in ["PySide2", "grpcio", "grpcio-tools", "protobuf",
                            "numpy", "opencv-python", "pydantic", "pytest", "ruff", "mypy"]},
                  single_task_deadlines="unchanged: export/read/seal 500ms, reap 1s", p0_exit="NOT_EVALUATED")
    failed = False
    for name in args.scenarios:
        timeout = (args.benchmark_timeout or (args.count+args.warmup)*args.rounds+120) if name == "benchmark" else 90
        try:
            stdout, stderr = supervised(name, timeout=timeout,
                                        parameters=dict(count=args.count, rounds=args.rounds, warmup=args.warmup) if name == "benchmark" else None)
            evidence = json.loads(stdout.strip().splitlines()[-1])
            report[name] = dict(execution_status="PASS", correctness_status=evidence.get("correctness_status", "PASS"),
                                performance_status=evidence.get("performance_status", "NOT_RUN"),
                                compatibility_status=evidence.get("compatibility_status", "NOT_RUN"),
                                evidence=evidence, stderr=stderr, outer_watchdog_seconds=timeout)
        except Exception as error:
            report[name] = dict(execution_status="FAIL", correctness_status="FAIL", performance_status="NOT_RUN",
                                compatibility_status="NOT_RUN", error=str(error), outer_watchdog_seconds=timeout)
            failed = True
        print(name, report[name]["execution_status"], flush=True)
        report["code_after"] = identity()
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
