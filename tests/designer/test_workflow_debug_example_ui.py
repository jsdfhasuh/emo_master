"""Shipped examples must open through the actual Designer entry point."""
from pathlib import Path

import pytest
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from tests.designer.qt_wait import waitForCatalog
from tests.designer.test_operator_debug_window import wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime


EXAMPLES = Path(__file__).resolve().parents[2] / "examples/workflow_debugger"


@pytest.mark.parametrize("filename", ["01-nested-calls.emoproj", "02-foreach.emoproj", "03-image.emoproj"])
def testExampleOpensThroughDesignerAndCanonicalDebugEntry(runtime, ownedDesignerWindow, filename):
    path = EXAMPLES / filename
    original = path.read_bytes()
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    assert window.loadProjectDirectory(str(path))
    assert not window.pageCoordinator.session.dirty
    window.designerActions.invoke("workflow_debug")
    dialog = window._workflowDebugWindow
    try:
        wait(lambda: dialog.startButton.isEnabled())
        assert dialog.workflowId == "main" and dialog.state == "READY"
        assert not runtime.jobRepository.all()
        assert not window.pageCoordinator.session.dirty
        assert path.read_bytes() == original
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
    assert not runtime.operatorDebugManager.ownsResources()
