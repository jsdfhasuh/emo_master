import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(os.environ.get('FLOAT_TOOLBOX_REPO', Path(__file__).resolve().parents[2]))
OUT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, encoding='utf-8').strip()


def sources():
    result = {}
    paths = git('ls-files', '--cached', '--others', '--exclude-standard', 'src', 'tests', 'scripts').splitlines()
    for name in sorted(set(paths)):
        path = ROOT / name
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    return result


name = sys.argv[1]
timeout = int(sys.argv[2])
command = [sys.executable, *sys.argv[3:]]
environment = os.environ.copy()
environment.update(PYTHONUTF8='1', PYTHONPATH=str(ROOT / 'src'), HUARAY_CAMERA_SMOKE='0')
environment.pop('PYTHONIOENCODING', None)
before = sources()
provenance = {
    'head': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
    'dirty': git('status', '--porcelain=v1').splitlines(), 'sources': before,
    'command': command, 'python': sys.version, 'os': platform.platform(),
    'qtPlatform': environment.get('QT_QPA_PLATFORM'), 'qtScale': environment.get('QT_SCALE_FACTOR'),
    'clock': 'time.monotonic wall-clock seconds', 'outerWatchdogSeconds': timeout,
    'startedUtc': datetime.now(timezone.utc).isoformat(),
}
start = time.monotonic()
with (OUT / (name + '.txt')).open('w', encoding='utf-8') as log:
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        process.wait(timeout=10)
        code = 124
        provenance['watchdogExpired'] = True
after = sources()
provenance.update(exitCode=code, seconds=time.monotonic() - start,
                  changedDuringRun=[key for key in set(before) | set(after) if before.get(key) != after.get(key)])
(OUT / (name + '-provenance.json')).write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
print((OUT / (name + '.txt')).read_text(encoding='utf-8')[-18000:])
print(json.dumps({key: provenance[key] for key in ('exitCode', 'seconds', 'changedDuringRun')}, ensure_ascii=False))
raise SystemExit(code)
