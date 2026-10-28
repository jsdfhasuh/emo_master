"""Exercise both frozen entry scripts before multiprocessing takes control."""
import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("entry", ["src/emo_master/apps/windows_entry.py", "scripts/run_operator.py"])
@pytest.mark.parametrize("hasConsoleStreams", [False, True])
def testWorkerFailureCanExitWithoutConsoleStreams(tmp_path, entry, hasConsoleStreams):
    root = Path(__file__).resolve().parents[1]
    resultPath = tmp_path / "bootstrap.json"
    # _bootstrap mutates multiprocessing's process state, so run this direct
    # regression in a separate interpreter, with the actual entry script.
    command = textwrap.dedent(f"""
        import io
        import json
        import multiprocessing
        from pathlib import Path
        import runpy
        import sys

        originalOut = io.StringIO() if {hasConsoleStreams!r} else None
        originalError = io.StringIO() if {hasConsoleStreams!r} else None
        sys.stdout, sys.stderr = originalOut, originalError

        def fail():
            raise RuntimeError("worker dependency failure")

        def freeze():
            report = {{
                "applicationImported": "emo_master" in sys.modules,
                "streamsAvailable": sys.stdout is not None and sys.stderr is not None,
                "streamsPreserved": sys.stdout is originalOut and sys.stderr is originalError,
            }}
            try:
                report["workerExitCode"] = multiprocessing.Process(target=fail)._bootstrap()
            except BaseException as error:
                report["bootstrapError"] = type(error).__name__ + ": " + str(error)
            if originalError is not None:
                report["stderr"] = originalError.getvalue()
            Path({str(resultPath)!r}).write_text(json.dumps(report), encoding="utf-8")
            raise SystemExit(0)

        multiprocessing.freeze_support = freeze
        runpy.run_path({str(root / entry)!r}, run_name="__main__")
    """)
    completed = subprocess.run([sys.executable, "-c", command], capture_output=True,
                               text=True, timeout=30)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(resultPath.read_text(encoding="utf-8"))
    assert "bootstrapError" not in report, report
    assert report["workerExitCode"] == 1
    assert report["streamsAvailable"]
    assert not report["applicationImported"]
    if hasConsoleStreams:
        assert report["streamsPreserved"]
        assert "RuntimeError: worker dependency failure" in report["stderr"]
