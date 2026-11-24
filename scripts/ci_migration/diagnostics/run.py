"""Execute a reviewed recipe in an isolated, credential-free Windows worker."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

RECIPES = json.loads(Path(__file__).with_name("recipes.json").read_text(encoding="utf-8"))
# Per-step wall-clock budgets reserve time to finish the report and seal it.
STEP_LIMITS = {
    "runtime-a18-diagnostics": {3: 12, 4: 2, 5: 50, 6: 3, 7: 2},
    "runtime-capture-diagnostics": {3: 12, 4: 2, 5: 50, 6: 3},
    "runtime-stop-diagnostics": {3: 12, 4: 2, 5: 50, 6: 3, 7: 3},
    "runtime-sqlite-failure-diagnostics": {3: 12, 4: 2, 5: 50, 6: 3, 7: 8, 8: 3},
}
ARCH_REF = "refs/heads/agent/runtime-workflow-architecture-v1"
EXPR = re.compile(r"\$\{\{ steps\.([a-z_]+)\.outcome \}\}")
AFTER = re.compile(r"\$\{\{ always\(\) && !cancelled\(\) && \(steps\.([a-z_]+)\.outcome == 'success' \|\| steps\.\1\.outcome == 'failure'\) \}\}")

def should_run(condition, failed, outcome):
    if condition is None:
        return not failed
    if condition == "always()":
        return True
    match = AFTER.fullmatch(condition)
    if match:
        return outcome.get(match[1]) in {"success", "failure"}
    raise ValueError("Unsupported recipe condition")

def execute(recipe, ref, product, evidence, source_sha="", run_id="0", run_attempt="1", *, suite=None):
    if recipe["job_if"] not in (None, f"github.ref == '{ARCH_REF}'"):
        raise ValueError("Unsupported job guard")
    if recipe["job_if"] and ref != ARCH_REF:
        return {"status": "skipped", "steps": []}
    temp = evidence / "temp"
    temp.mkdir()
    base_env = {**os.environ, **recipe["env"], "GITHUB_WORKSPACE": str(product), "RUNNER_TEMP": str(temp),
                "GITHUB_SHA": source_sha, "GITHUB_RUN_ID": run_id, "GITHUB_RUN_ATTEMPT": run_attempt}
    outcomes = {}
    result = []
    failed = False
    for step in recipe["steps"]:
        number = step["ordinal"]
        ident = step.get("id")
        status = "skipped"
        timed_out = False
        if should_run(step.get("if"), failed, outcomes):
            env = {**base_env, **{key: EXPR.sub(lambda m: outcomes[m[1]], value)
                                 for key, value in step.get("env", {}).items()}}
            if any("${{" in value for value in env.values() if isinstance(value, str)):
                raise ValueError("Unresolved recipe expression")
            shell = step.get("shell", "pwsh")
            if shell not in ("python", "pwsh"):
                raise ValueError("Unsupported recipe shell")
            source = evidence / f"step-{number:02d}.{('py' if shell == 'python' else 'ps1')}"
            script = step["run"]
            if shell == "python":
                # The shell script is stored outside the checkout; give inline
                # Python the same staged-source imports as the product job.
                script = ("import sys as _emo_sys, os as _emo_os\n"
                          "_emo_sys.path.insert(0, _emo_os.environ['GITHUB_WORKSPACE'])\n"
                          + script)
            if shell == "pwsh":
                script = "$ErrorActionPreference = 'stop'\n" + script + "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
            source.write_text(script, encoding="utf-8")
            cmd = [sys.executable, str(source)] if shell == "python" else [
                "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(source)
            ]
            with (evidence / f"step-{number:02d}.log").open("xb") as output:
                try:
                    with subprocess.Popen(cmd, cwd=product, env=env, stdin=subprocess.DEVNULL,
                                          stdout=output, stderr=subprocess.STDOUT) as child:
                        try:
                            minutes = step.get("timeout-minutes")
                            limit = STEP_LIMITS.get(suite, {}).get(number)
                            if limit is not None:
                                minutes = min(limit, int(minutes)) if minutes else limit
                            code = child.wait(timeout=int(minutes) * 60 if minutes else None)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                            try:
                                systemroot = os.environ["SystemRoot"]
                                subprocess.run([str(Path(systemroot) / "System32" / "taskkill.exe"),
                                                 "/PID", str(child.pid), "/T", "/F"],
                                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                                check=False, timeout=20)
                            except (OSError, KeyError, subprocess.TimeoutExpired):
                                pass
                            finally:
                                if child.poll() is None:
                                    child.kill()
                                child.wait(timeout=15)
                            code = 1
                except OSError:
                    code = 1
            status = "success" if code == 0 else "failure"
            failed |= status == "failure"
        result.append({"ordinal": number, "status": status})
        if timed_out:
            result[-1]["timed_out"] = True
        if ident:
            outcomes[ident] = status
    if "display-evidence" in recipe.get("private_artifacts", ()):
        artifact_ok = any(p.is_file() for p in (product / "display-evidence").rglob("*"))
        result.append({"ordinal": 6, "status": "success" if artifact_ok else "failure"})
        failed |= not artifact_ok
    return {"status": "failure" if failed else "success", "steps": result}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, choices=sorted(RECIPES))
    parser.add_argument("--product-root", required=True, type=Path)
    parser.add_argument("--product-ref", required=True)
    parser.add_argument("--private-evidence-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", default="0")
    parser.add_argument("--run-attempt", default="1")
    args = parser.parse_args()
    if os.name != "nt" or sys.version_info[:2] != (3, 10):
        parser.error("Windows Python 3.10 is required")
    evidence = args.private_evidence_dir.resolve()
    product = args.product_root.resolve(strict=True)
    if not (product / "requirements-dev.txt").is_file() or product in evidence.parents or evidence == product:
        parser.error("Expected a staged product and separate private evidence directory")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha) or not all(
            re.fullmatch(r"[0-9]{1,24}", value) for value in (args.run_id, args.run_attempt)):
        parser.error("Expected a verified SHA and numeric run context")
    actual_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=product, check=True,
                                capture_output=True, text=True).stdout.strip()
    if actual_sha != args.source_sha:
        parser.error("Staged source does not match verified SHA")
    evidence.mkdir(exist_ok=False)
    result = execute(RECIPES[args.suite], args.product_ref, product, evidence, args.source_sha,
                     args.run_id, args.run_attempt, suite=args.suite)
    (evidence / "status.json").write_text(json.dumps(result)+"\n", encoding="utf-8")
    return int(result["status"] == "failure")

if __name__ == "__main__":
    sys.exit(main())
