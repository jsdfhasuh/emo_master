from pathlib import Path
import os
import subprocess
import sys


projectRoot = Path(__file__).resolve().parent.parent


def runStep(command: list[str], name: str) -> int:
  print(f"[ci-check] running {name}: {' '.join(command)}", flush=True)
  completed = subprocess.run(command, cwd=projectRoot)
  if completed.returncode != 0:
    print(f"[ci-check] failed {name} with code {completed.returncode}")
  return completed.returncode


def main() -> int:
  os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
  steps = [
    ([sys.executable, "scripts/gen_proto.py", "--check"], "proto-drift"),
    ([sys.executable, "-m", "ruff", "check", "src", "tests"], "ruff"),
    ([sys.executable, "-m", "mypy", "--config-file", "mypy.ini", "src"], "mypy"),
    ([sys.executable, "-m", "pytest", "-p", "scripts.ci_pytest_reports", "-vv", "--tb=short",
      "--junitxml=build/ci-results.xml", "--timeout=120",
      "--timeout-method=thread", "-o", "faulthandler_timeout=60"], "pytest")
  ]

  if os.environ.get("EMO_A18_CREATION_TRACE") == "1":
    # Scope the temporary observer to this pytest process, not nested pytest
    # subprocesses through an inherited PYTEST_ADDOPTS environment variable.
    steps[-1][0].extend(["-p", "scripts.r3_a18_creation_trace"])

  for command, name in steps:
    code = runStep(command, name)
    if code != 0:
      return code

  print("[ci-check] all checks passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
