"""Supervised visible native Qt path. Windows sizes are logical pixels, not field acceptance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from sqlite_writer_validate import ROOT, candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', choices=['1', '1.25', '1.5', '2'], default='1')
    parser.add_argument('--offscreen', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'candidate.json').write_text(json.dumps(candidate(), ensure_ascii=False, indent=2), encoding='utf-8')
    env = {**os.environ, 'PYTHONUTF8': '1', 'QT_SCALE_FACTOR': args.scale, 'HUARAY_CAMERA_SMOKE': '0',
           'PYTHONPATH': str(ROOT / 'src'), 'EMO_SQLITE_SCREEN_DIR': str(args.output.resolve()),
           'QT_QPA_PLATFORM': 'offscreen' if args.offscreen else 'windows'}
    env.pop('PYTHONIOENCODING', None)
    code = '''import emo_master
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
app = QApplication([])
from emo_master.apps.designer.ui.theme import configureTheme
configureTheme(app)
import pytest
raise SystemExit(pytest.main(['-q', 'tests/sqlite_writer/test_user_path.py::testCompleteSqliteDesignerPath']))
'''
    command = [sys.executable, '-c', code]
    start = time.monotonic()
    with (args.output / 'raw.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            exitCode = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            # Only the tree of this newly created validation process is targeted.
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=log, stderr=log)
            else:
                process.kill()
            process.wait()
            exitCode = 124
    report = {'exitCode': exitCode, 'seconds': time.monotonic() - start, 'watchdogSeconds': 180,
              'scale': args.scale, 'mode': 'offscreen' if args.offscreen else 'native-windows', 'command': command}
    (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    print((args.output / 'raw.log').read_text(encoding='utf-8', errors='replace')[-5000:])
    return int(exitCode != 0)


if __name__ == '__main__':
    raise SystemExit(main())
