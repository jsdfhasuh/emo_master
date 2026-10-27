"""Open the delivered files in native Designer without any PLC/Runtime calls."""
from copy import deepcopy
from itertools import combinations
import json
import socket
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEventLoop, QSettings, QTimer, Qt

from emo_master.apps.designer.state.project_store import loadProject, saveProject
from tests.runtime.test_dual_plc_field_project import HERE, fixture, compiler
from tests.designer.test_workflow_entry_markers import readableNativeFont  # noqa: F401


@pytest.fixture(scope="module")
def registry():
    return fixture.builtin_registry()


def settle():
    loop = QEventLoop()
    QTimer.singleShot(30, loop.quit)
    loop.exec_()


def wire_keys(edges):
    return {(e["fromNode"], e["fromPort"], e["toNode"], e["toPort"]) for e in edges}


@pytest.mark.parametrize("simulation", [False, True])
def testNativeDesignerOpensEveryWorkflowAndSaveReopenPreservesWires(
        simulation, ownedDesignerWindow, tmp_path, monkeypatch, registry):
    def forbidden(*args, **kwargs):
        pytest.fail("opening/navigating the project must not access PLCs or start a Job")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    source = HERE / "simulation" / fixture.SIMULATION_FILE if simulation else HERE / fixture.FIELD_FILE
    expected = json.loads(source.read_text(encoding="utf-8"))
    runtime = SimpleNamespace(listOperators=lambda: [], startJob=forbidden, loadProject=forbidden)
    window = ownedDesignerWindow(runtime, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    assert window.loadProjectDirectory(str(source), confirmed=True)
    window.resize(1440, 900)
    window.show()
    settle()
    for workflow_id in expected["workflowOrder"]:
        window.workflowTabs.selectWorkflow(workflow_id)
        settle()
        assert window.activeWorkflowId == workflow_id
        keys = wire_keys(expected["workflows"][workflow_id]["edges"])
        assert len(window.flowModel.edges) == len(keys), workflow_id
        assert set(window.flowScene._edgeItems) == keys, workflow_id
        for (left_id, left), (right_id, right) in combinations(window.flowScene._nodeItems.items(), 2):
            assert not left.sceneBoundingRect().intersects(right.sceneBoundingRect()), (workflow_id, left_id, right_id)
        if not simulation and workflow_id in {"read_both", "plc50_cycle_uint16", "plc50_cycle_scalar_write"}:
            window.focusGraphContent()
            settle()
            screenshot = tmp_path / (workflow_id + ".png")
            assert window.grab().save(str(screenshot))
            print("PLC_SCREENSHOT=" + str(screenshot))
    window.pageCoordinator.sync()
    payload = deepcopy(window.pageCoordinator.session.payload())
    for key, flow in expected["workflows"].items():
        assert wire_keys(payload["workflows"][key]["edges"]) == wire_keys(flow["edges"]), key
    saved = tmp_path / source.name
    saveProject(saved, payload)
    reopened = dict(loadProject(saved))
    compiler(registry).compile(reopened)
    assert window.loadProjectDirectory(str(saved), confirmed=True)
    for workflow_id in expected["workflowOrder"]:
        window.workflowTabs.selectWorkflow(workflow_id)
        settle()
        assert set(window.flowScene._edgeItems) == wire_keys(expected["workflows"][workflow_id]["edges"]), workflow_id
