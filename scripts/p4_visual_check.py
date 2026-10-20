"""Native Windows Qt user path; invoked under p4_validate's process-tree watchdog."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, QT_QPA_PLATFORM='windows', P4_SCREEN_DIR=str(args.output.absolute()))
    return subprocess.call([sys.executable, '-m', 'pytest', '-q',
        'tests/ui/page_designer/test_user_path.py', '-s'], env=env)


if __name__ == '__main__':
    raise SystemExit(main())
