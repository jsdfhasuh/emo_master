from pathlib import Path
import argparse
import os
import subprocess
import sys


projectRoot = Path(__file__).resolve().parent.parent


def commandEnvironment(localDesigner: bool = False) -> dict[str, str]:
  environment = dict(os.environ)
  # Select this checkout rather than an inherited path to another work copy.
  environment["PYTHONPATH"] = str(projectRoot / "src")
  if localDesigner:
    for name in (
      "EMO_RUNTIME_TARGET", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH",
      "EMO_RUNTIME_LOG_DIR", "QT_QPA_PLATFORM",
    ):
      environment.pop(name, None)
    environment["EMO_RUNTIME_DATA_DIR"] = str(
      projectRoot / "manual_test_workspace" / "runtime-embedded"
    )
  return environment


def runCommand(command: list[str], *, localDesigner: bool = False) -> int:
  completed = subprocess.run(
    command, cwd=projectRoot, env=commandEnvironment(localDesigner)
  )
  return completed.returncode


def runProto() -> int:
  return runCommand([sys.executable, "scripts/gen_proto.py"])


def runTest() -> int:
  return runCommand([sys.executable, "-m", "pytest", "-q"])


def runRuntime() -> int:
  return runCommand([sys.executable, "-m", "emo_master.apps.runtime.main"])


def runDesigner(*, local: bool = False, check: bool = False) -> int:
  if sys.version_info[:2] != (3, 10):
    print("Designer requires Python 3.10. Select the existing emo_master environment.")
    return 1
  environment = commandEnvironment(local)
  print(f"[designer] Python: {sys.executable}", flush=True)
  print(f"[designer] Source: {projectRoot / 'src'}", flush=True)
  target = environment.get("EMO_RUNTIME_TARGET", "").strip()
  print(f"[designer] Runtime: {target or 'embedded'}", flush=True)
  if local:
    print(f"[designer] Data: {environment['EMO_RUNTIME_DATA_DIR']}", flush=True)
  if check:
    # Import the real entry point without creating QApplication or RuntimeService.
    code = """
from pathlib import Path
import sys
import emo_master
import PySide2
import grpc
import emo_master.apps.designer.main
expected = Path(sys.argv[1]) / 'src' / 'emo_master' / '__init__.py'
assert expected.samefile(emo_master.__file__), 'Imported a different work copy'
print('Source import:', emo_master.__file__)
print('PySide2:', PySide2.__version__)
print('gRPC:', grpc.__version__)
print('Designer startup check passed (no window or Job started).')
"""
    return runCommand([sys.executable, "-c", code, str(projectRoot)], localDesigner=local)
  return runCommand(
    [sys.executable, "-m", "emo_master.apps.designer.main"], localDesigner=local
  )


def main(argv: list[str]) -> int:
  parser = argparse.ArgumentParser(description="Run tools from this source checkout.")
  parser.add_argument("action", choices=["proto", "test", "run-runtime", "run-designer"])
  parser.add_argument("--local", action="store_true",
                      help="Designer: visible embedded Runtime with isolated local data")
  parser.add_argument("--check", action="store_true",
                      help="Designer: check imports and configuration without opening a window")
  arguments = parser.parse_args(argv[1:])
  action = arguments.action
  if action != "run-designer" and (arguments.local or arguments.check):
    parser.error("--local and --check apply only to run-designer")
  if action == "proto":
    return runProto()
  if action == "test":
    return runTest()
  if action == "run-runtime":
    return runRuntime()
  if action == "run-designer":
    return runDesigner(local=arguments.local, check=arguments.check)

  print(f"Unknown action: {action}")
  return 1


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
