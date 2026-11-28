"""The diagnostic metadata must forward modern capture target arguments."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("use_targets", (True, False))
def testMeasuredImageAcceptsAndForwardsCaptureTargets(use_targets):
    # Execute the diagnostic wrapper and its exact metadata callback, not a
    # reimplementation. Importing the full measurement script requires Qt.
    script = Path(__file__).resolve().parents[1] / "scripts" / "r3_measure.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    wrapper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "wrap")
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "wrap" and len(node.args) > 5
        and isinstance(node.args[3], ast.Constant) and node.args[3].value == "image"
    ]
    assert len(calls) == 1
    scope = {}
    exec(compile(ast.Module(body=[wrapper], type_ignores=[]), str(script), "exec"), scope)
    metadata_callback = eval(compile(ast.Expression(calls[0].args[5]), str(script), "eval"), scope)

    class Trace:
        def call(self, label, function, *args, metadata=None, **kwargs):
            assert label == "capture.image_copy_descriptor"
            assert metadata == {"source": "image", "result_key": "test", "raw_bytes": 3}
            return function(*args, **kwargs)

    class Collector:
        def image(self, _key, _value, _item, *, targets=None):
            return targets

    scope["wrap"](Trace(), setattr, Collector, "image", "capture.image_copy_descriptor", metadata_callback)
    targets = [{"key": "test", "sourceId": "image"}]
    args = {"targets": targets} if use_targets else {}
    returned = Collector().image("image", SimpleNamespace(nbytes=3), {"identity": {"resultKey": "test"}}, **args)
    assert returned is (targets if use_targets else None)

@pytest.mark.parametrize("raises", (False, True))
def testCaptureCreditTraceForwardsOriginalKeywordsAndRetiresScope(raises):
    script = Path(__file__).resolve().parents[1] / "scripts" / "r3_capture_credit_trace.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    installation = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "installed")
    factory = next(node for node in installation.body if isinstance(node, ast.FunctionDef) and node.name == "image_factory")
    context = object()
    calls = []

    class Trace:
        def enter_image(self, *_args):
            calls.append("enter")
            return context

        def observe(self, callback, *args):
            return callback(*args)

        def leave_image(self, value):
            assert value is context
            calls.append("leave")

    trace = Trace()
    scope = {"trace_ref": lambda: trace}
    exec(compile(ast.Module(body=[factory], type_ignores=[]), str(script), "exec"), scope)
    targets = [{"key": "test", "sourceId": "image"}]

    def original(_collector, _key, _value, _item, **kwargs):
        assert kwargs == {"targets": targets}
        calls.append("original")
        if raises:
            raise RuntimeError("original failure")
        return kwargs["targets"]

    call = scope["image_factory"](original)
    if raises:
        with pytest.raises(RuntimeError, match="original failure"):
            call(None, "image", object(), {}, targets=targets)
    else:
        assert call(None, "image", object(), {}, targets=targets) is targets
    assert calls == ["enter", "original", "leave"]
