import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import run


@pytest.mark.parametrize("suite", tuple(run.STEP_LIMITS))
def test_timeouts_remain_under_boundary(suite):
    recipe = run.RECIPES[suite]
    assert set(run.STEP_LIMITS[suite]) == {step["ordinal"] for step in recipe["steps"]}
    assert sum(run.STEP_LIMITS[suite].values()) <= 78


def test_timed_child_is_terminated_and_failure_is_reported(tmp_path):
    class Child:
        pid = 1234
        killed = False

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout):
            if not self.killed:
                assert timeout == 50 * 60
                raise subprocess.TimeoutExpired("observed workload", timeout)
            return 1

        def poll(self):
            return 1 if self.killed else None

        def kill(self):
            self.killed = True

    def fail_taskkill(cmd, **kwargs):
        assert cmd[-4:] == ["/PID", "1234", "/T", "/F"]
        raise subprocess.TimeoutExpired(cmd, 20)

    with patch.dict(os.environ, SystemRoot="C:/Windows"), patch.object(subprocess, "Popen", Child), patch.object(subprocess, "run", fail_taskkill):
        result = run.execute(
            {"job_if": None, "env": {}, "steps": [{"ordinal": 5, "run": "pass", "shell": "python"}]},
            "refs/heads/test", tmp_path, tmp_path, suite="runtime-capture-diagnostics")
    assert result == {"status": "failure", "steps": [{"ordinal": 5, "status": "failure", "timed_out": True}]}
