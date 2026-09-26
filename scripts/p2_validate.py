"""Supervised P2 checks with immutable logs and source identity (including proto)."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

from p1_validate import git, run, sourceIdentity

ROOT = Path(__file__).resolve().parents[1]


def identity():
    result = sourceIdentity(ROOT)
    for path in (ROOT / "proto").glob("*.proto"):
        result["files"][path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["digest"] = hashlib.sha256(json.dumps(result["files"], sort_keys=True).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--suite", choices=["focused", "regression", "ci", "demo", "measure"], default="focused")
    parser.add_argument("--timeout", default=300, type=float)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    commands = {
        "focused": [[sys.executable, "-m", "pytest", "-q", "tests/runtime/presentation", "tests/core/presentation", "-rs"],
                    [sys.executable, "scripts/gen_proto.py", "--check"],
                    [sys.executable, "-m", "ruff", "check", "src", "tests"],
                    [sys.executable, "-m", "mypy", "--config-file", "mypy.ini", "src"]],
        "regression": [[sys.executable, "-m", "pytest", "-q", "tests/core", "tests/runtime", "tests/e2e", "-rs"]],
        "ci": [[sys.executable, "scripts/ci_check.py"]],
        "demo": [[sys.executable, "scripts/p2_demo.py"]],
        "measure": [[sys.executable, "scripts/p2_measure.py"]],
    }
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "head": git("rev-parse", "HEAD"),
              "dirty": git("status", "--short"), "python": sys.version, "os": platform.platform(),
              "executable": sys.executable, "code_before": identity(), "results": []}
    for index, command in enumerate(commands[args.suite]):
        result = run(command, ROOT, args.output / f"{index + 1}.log", args.timeout)
        report["results"].append(result)
        print(result, flush=True)
    report["code_after"] = identity()
    report["code_stable"] = report["code_before"] == report["code_after"]
    (args.output / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return int(not report["code_stable"] or any(r["status"] != "PASS" for r in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
