"""Record P1 code identity and supervised checks without rewriting P0 evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Reuse only the proven test watchdog, not the P0 runtime prototype implementation.
from prototypes.runtime_pages_p0.watchdog import ProcessTree  # noqa: E402


def sourceIdentity(root):
    paths = [p for directory in ("src", "tests", "scripts", "prototypes/runtime_pages_p0")
             for p in (root / directory).rglob("*")
             if p.is_file() and p.suffix in {".py", ".json"}]
    paths += [root / name for name in ("pyproject.toml", "mypy.ini", "requirements.txt", "requirements-dev.txt")]
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(paths) if p.exists()}
    return {"files": hashes, "digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def run(command, root, logPath, timeout):
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / "src"), str(root))),
                       QT_QPA_PLATFORM="offscreen", HUARAY_CAMERA_SMOKE="0", PYTHONUNBUFFERED="1")
    gate = "import sys,json,subprocess; sys.stdin.readline(); sys.exit(subprocess.call(json.loads(sys.argv[1])))"
    started = time.monotonic()
    with logPath.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-c", gate, json.dumps(command)], cwd=root,
                                   env=environment, stdout=log, stderr=log, stdin=subprocess.PIPE,
                                   start_new_session=os.name != "nt")
        tree = None
        timedOut = False
        try:
            tree = ProcessTree(process.pid)
            process.communicate(input=b"GO\n", timeout=timeout)
        except subprocess.TimeoutExpired:
            timedOut = True
        finally:
            if tree:
                tree.close()
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    return {"command": command, "status": "FAIL" if timedOut or process.returncode else "PASS",
            "exit_code": process.returncode, "timeout": timedOut, "watchdog_seconds": timeout,
            "duration_seconds": time.monotonic() - started, "log": logPath.name,
            "log_sha256": hashlib.sha256(logPath.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--suite", choices=["focused", "regression", "ci"], default="focused")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists():
        parser.error("output already exists; preserve prior evidence with a new path")
    output.mkdir(parents=True)
    commands = {
        "focused": [[sys.executable, "-m", "pytest", "-q", "tests/core/presentation"]],
        "regression": [
            [sys.executable, "-m", "pytest", "-q", "tests/core", "tests/runtime", "tests/e2e", "-rs"],
            [sys.executable, "-m", "pytest", "-q", *["tests/designer/" + name for name in (
                "test_project_store.py", "test_workflow_store.py", "test_workflow_package.py",
                "test_workflow_boundary_nodes.py", "test_workflow_dependency_tree.py",
                "test_flow_graph_project_codec.py", "test_main_window_project_save_load.py")]],
        ],
        "ci": [[sys.executable, "scripts/ci_check.py"]],
    }
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "host_head": git("rev-parse", "HEAD"),
              "host_status": git("status", "--short"), "suite": args.suite,
              "python": sys.version, "executable": sys.executable, "os": platform.platform(),
              "versions": {name: importlib.metadata.version(name) for name in (
                  "PySide2", "pydantic", "grpcio", "grpcio-tools", "protobuf", "numpy", "opencv-python",
                  "pytest", "ruff", "mypy")}, "results": []}
    with tempfile.TemporaryDirectory(prefix="emo-p1-check-") as temporary:
        root = ROOT
        if args.baseline_ref:
            reference = git("rev-parse", "--verify", args.baseline_ref + "^{commit}")
            archive = Path(temporary) / "baseline.zip"
            subprocess.run(["git", "archive", "--format=zip", "-o", str(archive), reference], cwd=ROOT, check=True)
            root = Path(temporary) / "source"
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(root)
            report.update(tested_head=reference, tested_status="clean git archive (no workspace overlays)")
        else:
            report.update(tested_head=report["host_head"], tested_status=report["host_status"])
        report["code_before"] = sourceIdentity(root)
        for index, command in enumerate(commands[args.suite]):
            result = run(command, root, output / f"{index + 1}.log", args.timeout)
            report["results"].append(result)
            print(f"{args.suite}/{index + 1}: {result['status']}", flush=True)
        report["code_after"] = sourceIdentity(root)
        report["code_stable"] = report["code_before"] == report["code_after"]
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return int(not report["code_stable"] or any(r["status"] != "PASS" for r in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
