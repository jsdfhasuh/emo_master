"""Run product Web CI in a guarded execution environment, not as a trust boundary."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


RECIPE = Path(__file__).with_name("web_recipe.json")
REQUIRED_HOST = {"ubuntu-latest": "Linux", "windows-latest": "Windows"}
FULL_EVENTS = {"push", "pull_request", "workflow_dispatch"}


def select(recipe, job, platform, source_event):
    selected = recipe["jobs"][job]
    if platform not in selected["platforms"] or source_event not in FULL_EVENTS:
        raise ValueError("invalid job host or source event")
    full = platform == "ubuntu-latest" or source_event == "workflow_dispatch"
    return selected, [
        step for step in selected["steps"]
        if (not step["full_only"] or full) and (not step["subset_only"] or not full)
    ]


def private_environment(extra):
    env = os.environ.copy()
    # Accidental injection defense, not an isolation mechanism. The controller
    # must already start a process with no credentials or runner-control access.
    for name in ("GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_PATH", "GITHUB_STATE",
                 "GITHUB_STEP_SUMMARY"):
        env.pop(name, None)
    env.update(extra)
    return env


def execute(recipe, job, platform, event, product, evidence):
    selected, steps = select(recipe, job, platform, event)
    if os.environ.get("RUNNER_OS") != REQUIRED_HOST[platform]:
        raise ValueError("runner does not match requested platform")
    cwd = product.joinpath(selected["cwd"])
    if not cwd.is_dir():
        raise ValueError("missing verified product directory")
    env = private_environment(selected["env"])
    evidence.mkdir(parents=True, exist_ok=False)
    for step in steps:
        shell = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"]
        script = step["run"]
        if step["shell"] == "pwsh":
            shell = ["pwsh", "-NoProfile", "-NonInteractive", "-Command"]
            script = "$ErrorActionPreference = 'Stop'\n" + script + "\nif (Test-Path variable:LASTEXITCODE) { exit $LASTEXITCODE }"
        elif step["shell"] != "bash":
            raise ValueError("unknown shell")
        with (evidence / f"step-{step['id']:02d}.log").open("xb") as log:
            completed = subprocess.run([*shell, script], cwd=cwd, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        if completed.returncode != 0:
            print(f"Web job failed at step {step['id']:02d}; see protected evidence.")
            return 1
    print("Web job completed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-event", required=True)
    parser.add_argument("--product", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        return execute(json.loads(RECIPE.read_text(encoding="utf-8")), args.job, args.platform,
                       args.source_event, args.product.resolve(), args.evidence.resolve())
    except (KeyError, OSError, ValueError):
        print("Web job setup failed; check trusted controller configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
