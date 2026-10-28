"""Dedicated operator entry, also usable as a frozen executable entry point."""
from pathlib import Path
import multiprocessing
import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    # Keep this before freeze_support without importing the application: a
    # windowed child still needs stderr when multiprocessing reports failure.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    multiprocessing.freeze_support()
    from emo_master.apps.operator_runtime.main import main

    raise SystemExit(main())
