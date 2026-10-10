"""Platform status is valid schema, but not an invocation's captured output."""
import pytest

from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from emo_master.core.presentation.models import DataSource
from emo_master.core.presentation.validation import validateBindings
from tests.runtime.presentation.test_normal_capture import normalProject
from tests.runtime.test_builtin_vision_operator_workflows import _registry


@pytest.mark.parametrize("name", ["job_state", "connection_state"])
def testPlatformStatusBindingExplainsNormalCaptureBoundary(tmp_path, name):
    root, document = normalProject(tmp_path)
    document.presentation.dataSources["count"] = DataSource(
        kind="runtime_status", name=name, resultScopeId="root", expectedType="string")
    document.presentation.pages["one"].components[0].type = "runtime_status"
    registry = _registry()
    manifests = {key: entry.manifest for key, entry in registry.items()}
    assert validateBindings(document, manifests) == []
    before = document.model_dump_json()
    with pytest.raises(ValueError) as caught:
        freezeNormalCapture(document, registry, root, "main")
    message = str(caught.value)
    assert "count: runtime_status is not a captured workflow output" in message
    assert "native live Job status label for execution state" in message
    assert "unbound runtime_status component shows connection state" in message
    assert "explicit invocation scope" not in message
    assert document.model_dump_json() == before
