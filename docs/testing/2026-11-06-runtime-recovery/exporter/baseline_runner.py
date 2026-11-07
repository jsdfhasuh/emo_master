"""Replay only the selected focused tests against an immutable exporter source.

This replaces the imported module in this one Python process; it never writes
source files in either checkout and never starts a native exporter process.
"""
import hashlib
from pathlib import Path
import subprocess
import sys

BASE = "cdf1250ccc41b78f79b25b87e6db197809875384"
SOURCE = "src/emo_master/apps/runtime/presentation/exporter.py"
REPO = Path(__file__).resolve().parents[4]
from emo_master.apps.runtime.presentation import exporter

source = subprocess.check_output(["git", "show", f"{BASE}:{SOURCE}"], cwd=REPO)
print(f"Baseline {BASE} {SOURCE} sha256={hashlib.sha256(source).hexdigest()}", flush=True)
exec(compile(source, f"{BASE}:{SOURCE}", "exec"), exporter.__dict__)
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
