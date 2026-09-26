"""Run supervised P0 risks and write reviewable measurements (no hardware)."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prototypes.runtime_pages_p0.watchdog import supervised  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "manual_test_workspace/p0/evidence.json")
    parser.add_argument("--scenarios", nargs="+", choices=["exports", "network", "benchmark"],
                        default=["exports", "network", "benchmark"])
    args = parser.parse_args()
    report = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "python": sys.version, "executable": sys.executable, "os": platform.platform(),
              "processor": platform.processor(),
              "versions": {name: importlib.metadata.version(name) for name in ["PySide2", "grpcio", "grpcio-tools",
                           "protobuf", "numpy", "opencv-python", "pydantic", "pytest", "ruff", "mypy"]}}
    failed = False
    for name in args.scenarios:
        try:
            stdout, stderr = supervised(name, timeout=90)
            report[name] = {"execution": "PASS", "evidence": json.loads(stdout.strip().splitlines()[-1]), "stderr": stderr}
        except Exception as error:
            report[name] = {"execution": "FAIL", "error": str(error)}
            failed = True
        print(name, report[name]["execution"], flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    # Execution success does not imply the performance targets or P0 exit passed.
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
