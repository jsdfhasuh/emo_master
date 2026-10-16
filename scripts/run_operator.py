"""Dedicated operator entry, also usable as a frozen executable entry point."""
from pathlib import Path
import multiprocessing
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from emo_master.apps.operator_runtime.main import main

    raise SystemExit(main())
