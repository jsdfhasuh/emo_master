import json
from pathlib import Path

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.number_compare.operator import NumberCompareOperator
from emo_master.plugins.builtins.number_value.operator import NumberValueOperator
from examples.flow_edge_readability.generate import buildProject


@pytest.mark.parametrize("workflow,count", [("fanout", 4), ("sparse", 1), ("dense", 20), ("crossing", 4)])
def testReadabilityExampleIsDeterministicDeviceFreeAndRuns(workflow, count):
    path = Path(__file__).resolve().parents[2] / "examples/flow_edge_readability/project.emoproj"
    original = path.read_bytes()
    payload = json.loads(original)
    assert payload == buildProject()
    document = ProjectDocument.model_validate(payload)
    registry = {"vision.value.number": NumberValueOperator, "vision.compare.number": NumberCompareOperator}
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(document), registry)
    result = runner.run(workflow, {}, RunContext.root("readability", workflow), CancellationToken())
    expected = {f"result{i}": (i % 5 >= (i + 1) % 5 if workflow == "dense" else False) for i in range(count)}
    assert result.outputs == expected
    assert path.read_bytes() == original
