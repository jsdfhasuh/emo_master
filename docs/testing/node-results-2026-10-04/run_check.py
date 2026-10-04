"""Record an exact working-tree check and preserve all raw output."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def sources():
    paths = []
    for directory in ('src', 'tests', 'scripts', 'proto', 'examples'):
        paths.extend(path for path in (ROOT / directory).rglob('*')
                     if path.is_file() and path.suffix in ('.py', '.proto', '.toml')
                     and '__pycache__' not in path.parts)
    return {str(path.relative_to(ROOT)).replace('\\', '/'):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--watchdog', type=int, default=180)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('Evidence must not overwrite an earlier check: ' + str(output))
    metadata = {'head': git('rev-parse', 'HEAD'), 'dirty': git('status', '--porcelain=v1').splitlines(),
                'command': command, 'python': sys.version, 'platform': platform.platform(),
                'environment': {key: os.environ.get(key) for key in
                    ('QT_QPA_PLATFORM', 'QT_SCALE_FACTOR', 'PYTHONUTF8', 'PYTEST_ADDOPTS')},
                'dependencies': {name: importlib.metadata.version(name) for name in
                    ('PySide2', 'grpcio', 'protobuf', 'numpy', 'opencv-python')},
                'sources': sources(), 'clock': 'time.monotonic wall clock', 'watchdogSeconds': args.watchdog}
    metadataPath = output.with_suffix('.json')
    metadataPath.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    start = time.monotonic()
    with output.open('wb') as handle:
        process = subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=args.watchdog)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = 124
    metadata.update(exitCode=code, durationSeconds=time.monotonic() - start,
                    sourceChanged=sources() != metadata['sources'])
    metadataPath.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output.read_text(encoding='utf-8', errors='replace')[-8000:])
    print(json.dumps({'exitCode': code, 'durationSeconds': metadata['durationSeconds'],
                      'sourceChanged': metadata['sourceChanged']}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
