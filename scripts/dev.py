from pathlib import Path
import subprocess
import sys


projectRoot = Path(__file__).resolve().parent.parent


def runCommand(command: list[str]) -> int:
  completed = subprocess.run(command, cwd=projectRoot)
  return completed.returncode


def runProto() -> int:
  return runCommand([sys.executable, "scripts/gen_proto.py"])


def runTest() -> int:
  return runCommand([sys.executable, "-m", "pytest", "-q"])


def runRuntime() -> int:
  return runCommand([sys.executable, "-m", "emo_master.apps.runtime.main"])


def runDesigner() -> int:
  return runCommand([sys.executable, "-m", "emo_master.apps.designer.main"])


def main(argv: list[str]) -> int:
  if len(argv) < 2:
    print("Usage: python scripts/dev.py [proto|test|run-runtime|run-designer]")
    return 1

  action = argv[1]
  if action == "proto":
    return runProto()
  if action == "test":
    return runTest()
  if action == "run-runtime":
    return runRuntime()
  if action == "run-designer":
    return runDesigner()

  print(f"Unknown action: {action}")
  return 1


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
