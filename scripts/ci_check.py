from pathlib import Path
import subprocess
import sys


projectRoot = Path(__file__).resolve().parent.parent


def runStep(command: list[str], name: str) -> int:
  print(f"[ci-check] running {name}: {' '.join(command)}")
  completed = subprocess.run(command, cwd=projectRoot)
  if completed.returncode != 0:
    print(f"[ci-check] failed {name} with code {completed.returncode}")
  return completed.returncode


def main() -> int:
  steps = [
    ([sys.executable, "-m", "ruff", "check", "src", "tests"], "ruff"),
    ([sys.executable, "-m", "mypy", "--config-file", "mypy.ini", "src"], "mypy"),
    ([sys.executable, "-m", "pytest", "-q"], "pytest")
  ]

  for command, name in steps:
    code = runStep(command, name)
    if code != 0:
      return code

  print("[ci-check] all checks passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
