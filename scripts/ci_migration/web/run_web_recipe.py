"""Run product Web CI in a guarded execution environment, not as a trust boundary."""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile


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


def resolve_windows_git_bash(env):
    """Find Git for Windows, not System32's Windows Subsystem for Linux shim."""
    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        if value := env.get(key):
            candidate = Path(value) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate.resolve())
    if git := shutil.which("git", path=env.get("PATH", "")):
        git_root = Path(git).resolve().parent.parent
        candidate = git_root / "bin" / "bash.exe"
        if (git_root / "usr" / "bin" / "bash.exe").is_file() and candidate.is_file():
            return str(candidate.resolve())
    return None


def shell_command(step, platform, env):
    if step["shell"] == "bash":
        if platform == "windows-latest":
            bash = resolve_windows_git_bash(env)
        else:
            bash = shutil.which("bash", path=env.get("PATH", ""))
        return ([bash, "--noprofile", "--norc", "-eo", "pipefail"] if bash else None,
                ".sh", step["run"])
    if step["shell"] == "pwsh":
        pwsh = shutil.which("pwsh", path=env.get("PATH", ""))
        script = ("$ErrorActionPreference = 'Stop'\n" + step["run"]
                  + "\nif (Test-Path variable:LASTEXITCODE) { exit $LASTEXITCODE }")
        return ([pwsh, "-NoProfile", "-NonInteractive", "-File"] if pwsh else None,
                ".ps1", script)
    raise ValueError("unknown shell")


def failure(category, step=None):
    suffix = "" if step is None else f":{step:02d}"
    print(f"WEB_RECIPE_FAILURE:{category}{suffix}", flush=True)
    return 1


def execute(recipe, job, platform, event, product, evidence):
    selected, steps = select(recipe, job, platform, event)
    if os.environ.get("RUNNER_OS") != REQUIRED_HOST[platform]:
        raise ValueError("runner does not match requested platform")
    cwd = product.joinpath(selected["cwd"])
    if not cwd.is_dir():
        raise ValueError("missing verified product directory")
    env = private_environment(selected["env"])
    evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="emo-web-recipe-") as temp:
        for step in steps:
            number = step["id"]
            shell, extension, script = shell_command(step, platform, env)
            if shell is None:
                return failure("shell-unavailable", number)
            script_path = Path(temp) / f"step-{number:02d}{extension}"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            with (evidence / f"step-{number:02d}.log").open("xb") as log:
                try:
                    completed = subprocess.run(
                        [*shell, script_path.as_posix()], cwd=cwd, env=env,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    )
                except OSError:
                    return failure("shell-launch", number)
            if completed.returncode != 0:
                return failure("command-failed", number)
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
        return failure("setup")


if __name__ == "__main__":
    raise SystemExit(main())
