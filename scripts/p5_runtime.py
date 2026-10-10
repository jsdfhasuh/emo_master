"""Explicit source test-release host. Does not start a Job."""
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from emo_master.apps.runtime.release_host import main
    raise SystemExit(main())
