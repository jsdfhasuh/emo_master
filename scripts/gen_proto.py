from pathlib import Path
import subprocess
import sys


def genProto() -> int:
  projectRoot = Path(__file__).resolve().parent.parent
  protoFile = projectRoot / "proto" / "runtime.proto"
  outDir = projectRoot / "src" / "emo_master" / "apps" / "runtime" / "grpc_server" / "generated"
  outDir.mkdir(parents=True, exist_ok=True)

  command = [
    sys.executable,
    "-m",
    "grpc_tools.protoc",
    f"-I{projectRoot / 'proto'}",
    f"--python_out={outDir}",
    f"--grpc_python_out={outDir}",
    str(protoFile),
  ]

  completed = subprocess.run(command, cwd=projectRoot)
  return completed.returncode


if __name__ == "__main__":
  raise SystemExit(genProto())
