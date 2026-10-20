"""Capture reproducible A0/A1 evidence without touching live Runtime processes."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs/testing/operator-step-2026-10-09"
TARGETS = ["tests/runtime", "tests/core", "tests/plugins", "tests/sqlite_writer"]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def sourceSnapshot() -> dict:
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).decode("utf-8").split("\0")
    hashes = {}
    for relative in sorted(set(paths)):
        if not relative or not relative.startswith(("src/", "proto/", "tests/", "scripts/")):
            continue
        path = ROOT / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "status": git("status", "--porcelain=v1"),
        "python": sys.executable,
        "pythonVersion": sys.version,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("PySide2", "pytest", "grpcio", "pydantic", "numpy")
        },
        "sha256": hashes,
    }


def inventory() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "src/emo_master/plugins/builtins").rglob("manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        module, className = manifest["entry"].split(":")
        source = ROOT / "src" / (module.replace(".", "/") + ".py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == className)
        methods = {node.name for node in definition.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        rows.append({
            "operatorId": manifest["operatorId"], "version": manifest["version"],
            "entry": manifest["entry"], "manifest": path.relative_to(ROOT).as_posix(),
            "source": source.relative_to(ROOT).as_posix(),
            "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "inputPorts": manifest["inputPorts"], "outputPorts": manifest["outputPorts"],
            "previewMode": (manifest.get("editor") or {}).get("previewMode", "undeclared"),
            "declaredLifecycleMethods": sorted(methods & {"initOperator", "disposeOperator"}),
            "baseClasses": [ast.unparse(base) for base in definition.bases],
            "parameterKeys": sorted(manifest.get("paramSchema", {}).get("properties", {})),
        })
    return rows


def writeJson(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--full", action="store_true", help="Run all tests, including Designer")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--shadow-runner", type=Path)
    args = parser.parse_args()
    if not args.label or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in args.label):
        parser.error("label must use lowercase ASCII letters, digits, and hyphens")
    directory = EVIDENCE / args.label
    directory.mkdir(parents=True, exist_ok=False)
    writeJson(directory / "source-before.json", sourceSnapshot())
    writeJson(directory / "operators.json", inventory())
    if args.inventory_only:
        return 0
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QT_QPA_PLATFORM": "offscreen"}
    if args.static_only:
        mypy = [sys.executable, "-m", "mypy", "--config-file", "mypy.ini", "--no-incremental"]
        if args.shadow_runner:
            mypy += ["--shadow-file", "src/emo_master/apps/runtime/workflow/runner.py", str(args.shadow_runner.resolve())]
        commands = [
            ("proto", [sys.executable, "scripts/gen_proto.py", "--check"]),
            ("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests"]),
            ("mypy", mypy + ["src"]),
        ]
    else:
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml=" + str(directory / "pytest.xml")]
        command += ["tests"] if args.full else TARGETS
        commands = [("pytest", command)]
    steps = []
    for name, command in commands:
        print("Running " + " ".join(command), flush=True)
        with (directory / (name + ".log")).open("wb") as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        steps.append({"name": name, "command": command, "returnCode": result.returncode})
        print((directory / (name + ".log")).read_text(encoding="utf-8", errors="replace")[-16000:], flush=True)
    after = sourceSnapshot()
    writeJson(directory / "source-after.json", after)
    before = json.loads((directory / "source-before.json").read_text(encoding="utf-8"))
    changed = sorted(path for path in before["sha256"].keys() | after["sha256"].keys()
                     if before["sha256"].get(path) != after["sha256"].get(path))
    failed = any(step["returnCode"] for step in steps)
    writeJson(directory / "result.json", {"steps": steps, "returnCode": int(failed),
                                        "sourceChangedDuringRun": changed})
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
