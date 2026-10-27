"""Dashboard calculations through real isolated single-node debug workers."""
import time

from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_resources import connected, run
from tests.runtime.test_operator_debug_sessions import waitFor


def test_percentage_debug_preserves_null_denominator(runtime):
    with connected(runtime, "vision.math.percentage") as debug:
        result = run(debug, {}, {"numerator": {"inline": .85}})
        assert result["status"] == "SUCCEEDED", result
        assert result["outputs"] == {"value": 85.0, "defined": True}
        empty = run(debug, {}, {"numerator": {"inline": 0}, "denominator": {"inline": 0}})
        assert empty["status"] == "SUCCEEDED", empty
        assert empty["outputs"] == {"value": None, "defined": False}


def test_elapsed_debug_retains_and_resets_session(runtime):
    with connected(runtime, "vision.state.elapsed_time") as debug:
        first = run(debug, {}, {"after": {"inline": True}})
        assert first["status"] == "SUCCEEDED", first
        assert first["outputs"]["seconds"] == 0.0
        time.sleep(.02)
        second = run(debug, {}, {"after": {"inline": False}})
        assert second["status"] == "SUCCEEDED", second
        assert second["outputs"]["seconds"] > 0.0
        debug.mutation("ResetOperatorDebugSession")
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        third = run(debug, {}, {"after": {"inline": True}})
        assert third["status"] == "SUCCEEDED", third
        assert third["outputs"]["seconds"] == 0.0
