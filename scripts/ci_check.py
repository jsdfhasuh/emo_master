from pathlib import Path
import argparse
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


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Run source checks, optionally selecting one CI test shard.")
  parser.add_argument("--shard", choices=("runtime", "other"))
  options = parser.parse_args([] if argv is None else argv)
  os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
  pytestCommand = [sys.executable, "-m", "pytest", "-p", "scripts.ci_pytest_reports", "-vv", "--tb=short",
    "--junitxml=build/ci-results.xml", "--timeout=120",
    "--timeout-method=thread", "-o", "faulthandler_timeout=60",
    "--durations=30", "--durations-min=1"]
  if options.shard:
    pytestCommand.extend(["-p", "scripts.ci_pytest_shard", f"--ci-shard={options.shard}",
      "--ci-shard-report=build/ci-shard.json"])
  steps = [
    ([sys.executable, "scripts/gen_proto.py", "--check"], "proto-drift"),
    ([sys.executable, "-m", "ruff", "check", "src", "tests"], "ruff"),
    ([sys.executable, "-m", "mypy", "--config-file", "mypy.ini", "src"], "mypy"),
    (pytestCommand, "pytest")
  ]

  for command, name in steps:
    code = runStep(command, name)
    if code != 0:
      return code

  print("[ci-check] all checks passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
