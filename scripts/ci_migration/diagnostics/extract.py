"""Offline translation of the reviewed product YAML. No dynamic CI extraction."""
import json
from pathlib import Path
import sys
import yaml

NAMES = (
    "display-session-contract",
    "runtime-a18-diagnostics",
    "runtime-capture-diagnostics",
    "runtime-credit-diagnostics",
    "runtime-sqlite-append-controls",
    "runtime-sqlite-failure-diagnostics",
    "runtime-stop-diagnostics",
)

def add_junit(step):
    if step.get("id") != "observed_tests" and "--assert=plain --tb=long" not in step["run"]:
        return step
    filename = ("wal-control-junit.xml" if "--assert=plain --tb=long" in step["run"]
                else "observed-junit.xml")
    lines = step["run"].splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith("python -m pytest ")]
    if len(matches) != 1:
        raise ValueError("expected exactly one original pytest command")
    i = matches[0]
    lines[i] = lines[i].removesuffix("\n") + f' --junitxml="$env:RUNNER_TEMP/{filename}"\n'
    if step.get("id") == "observed_tests":
        original = "python -m pytest -q "
        if not lines[i].startswith(original):
            raise ValueError("expected the default quiet pytest command")
        lines[i] = lines[i].replace(
            original, "python -m pytest -v -o faulthandler_timeout=90 "
            '-p scripts.ci_migration.diagnostics.pytest_evidence --emo-pytest-evidence-dir "$env:RUNNER_TEMP" ', 1)
    return {**step, "run": "".join(lines)}

def extract(source, dest):
    recipes = {}
    for name in NAMES:
        with (source / ".github" / "workflows" / f"{name}.yml").open(encoding="utf-8") as f:
            data = yaml.load(f, Loader=yaml.BaseLoader)
        job, = data["jobs"].values()
        steps = []
        for i, step in enumerate(job["steps"], 1):
            if "uses" in step:
                action = step["uses"]
                if action not in ("actions/checkout@v4", "actions/setup-python@v5", "actions/upload-artifact@v4"):
                    raise ValueError(f"unexpected action in {name}:{i}")
                continue
            assert set(step) <= {"id", "if", "env", "timeout-minutes", "name", "shell", "run"}
            assert "run" in step
            steps.append({"ordinal": i, **add_junit(step)})
        recipes[name] = {
            "workflow_name": data["name"],
            "triggers": data["on"],
            "job_if": job.get("if"),
            "private_artifacts": [s["with"]["path"] for s in job["steps"] if "actions/upload-artifact@v4" in s.get("uses", "")],
            "timeout_minutes": int(job["timeout-minutes"]),
            "env": job["env"],
            "steps": steps,
        }
    dest.write_text(json.dumps(recipes, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

if __name__ == "__main__":
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
