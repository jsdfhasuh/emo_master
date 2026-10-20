from pathlib import Path
import argparse
import subprocess
import sys
import tempfile


def genProto(check: bool = False) -> int:
  projectRoot = Path(__file__).resolve().parent.parent
  protoFile = projectRoot / "proto" / "runtime.proto"
  sourceDir = projectRoot / "src" / "emo_master" / "apps" / "runtime" / "grpc_server" / "generated"
  if check:
    with tempfile.TemporaryDirectory(prefix="emo-master-proto-") as tempDir:
      generatedDir = Path(tempDir)
      code = _generate(projectRoot, protoFile, generatedDir)
      if code != 0:
        return code
      _patchImports(generatedDir)
      _normalizeGeneratedFiles(generatedDir)
      for name in ("runtime_pb2.py", "runtime_pb2_grpc.py"):
        if not (sourceDir / name).exists() or not _sameGeneratedFile(sourceDir / name, generatedDir / name):
          print(f"[proto-drift] generated file differs: {sourceDir / name}")
          return 1
      return 0
  sourceDir.mkdir(parents=True, exist_ok=True)
  code = _generate(projectRoot, protoFile, sourceDir)
  if code == 0:
    _patchImports(sourceDir)
    _normalizeGeneratedFiles(sourceDir)
  return code


def _generate(projectRoot: Path, protoFile: Path, outDir: Path) -> int:
  command = [
    sys.executable,
    "-m",
    "grpc_tools.protoc",
    f"-I{projectRoot / 'proto'}",
    f"--python_out={outDir}",
    f"--grpc_python_out={outDir}",
    str(protoFile),
  ]
  return subprocess.run(command, cwd=projectRoot).returncode


def _patchImports(outDir: Path) -> None:
  grpcFile = outDir / "runtime_pb2_grpc.py"
  generated = grpcFile.read_text(encoding="utf-8")
  generated = generated.replace(
    "import runtime_pb2 as runtime__pb2",
    "from . import runtime_pb2 as runtime__pb2",
  )
  grpcFile.write_text(generated, encoding="utf-8")


def _normalizeGeneratedFiles(outDir: Path) -> None:
  for name in ("runtime_pb2.py", "runtime_pb2_grpc.py"):
    path = outDir / name
    path.write_bytes(_normalizedBytes(path.read_bytes()))


def _sameGeneratedFile(left: Path, right: Path) -> bool:
  return _normalizedBytes(left.read_bytes()) == _normalizedBytes(right.read_bytes())


def _normalizedBytes(value: bytes) -> bytes:
  return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--check", action="store_true", help="fail when checked-in generated files are stale")
  raise SystemExit(genProto(check=parser.parse_args().check))
