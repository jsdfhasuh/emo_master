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
