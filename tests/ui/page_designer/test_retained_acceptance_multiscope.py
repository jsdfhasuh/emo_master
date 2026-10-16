"""Retained B11 / A21 acceptance, using offline synthetic normal-run jobs.

The bounded test plugin only selects two local ImageLoader images and gates the
second cycle. Blob, Count, number comparison, normal StartJob, capture, gRPC,
decode, pin leases and Qt rendering are the real implementation. No field,
device, throughput or soak acceptance is inferred from these tests.
"""
from copy import deepcopy
import json
from pathlib import Path
import time

import numpy as np
import shiboken2
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.presentation.models import Action
from emo_master.core.project.models import ProjectDocument
from examples.p3_selector import Selector
from examples.runtime_pages_p2 import sampleProject
from examples.runtime_pages_p3 import sampleProjectP3
from tests.runtime.runtime_test_utils import jobFailureDetails
from tests.ui.page_designer.test_normal_run_viewing import waitFor, displayedImagesReady


class GatedSelector(Selector):
    """Spawn-importable local fixture; never installed in production."""
    def executeNode(self, inputs, params, runtimeContext):
        iteration = runtimeContext["iterationPath"][-1]
        if iteration:
            deadline = time.monotonic() + 20
            while not Path(params["gate"]).exists():
                runtimeContext["raiseIfCancellationRequested"]()
                if time.monotonic() >= deadline:
                    raise TimeoutError("test did not release the second synthetic cycle")
                time.sleep(.01)
        odd = (iteration + params["offset"]) % 2
        return {"status": "ok", "outputs": {
            "image": inputs["b" if odd else "a"], "frame": inputs["fb" if odd else "fa"]}}


def twoScopeNormalLoop(root, gate):
    raw = sampleProjectP3(root, count=2).model_dump()
    child = raw["workflows"].pop("detect")
    raw["workflows"]["main"]["nodes"][1]["loop"]["bodyWorkflowId"] = "cycle"
    raw["workflows"]["cycle"] = {"name": "Two synthetic call sites", "nodes": [
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "a", "kind": "subflow", "targetWorkflowId": "detect-a"},
        {"nodeId": "b", "kind": "subflow", "targetWorkflowId": "detect-b"},
        {"nodeId": "output", "kind": "workflow_output"}]}
    raw["workflowOrder"] = ["main", "cycle", "detect-a", "detect-b"]
    bindings = raw["resources"]["parameterBindings"]
    raw["resources"]["parameterBindings"] = []
    presentation = {"defaultPageId": "overview", "pageOrder": ["overview", "detail-a", "detail-b"],
        "resultScopes": {}, "dataSources": {}, "pages": {}}
    overview = []
    for offset, scope in enumerate(("a", "b")):
        workflow = deepcopy(child)
        for node in workflow["nodes"]:
            if node["nodeId"] in ("load", "load-b"):
                node["params"] = {"imagePath": str(root / ("input.png" if node["nodeId"] == "load" else "second.png"))}
            elif node["nodeId"] == "select":
                node.update(operatorId="test.acceptance.gated_selector", params={"gate": str(gate), "offset": offset})
        workflow["nodes"].append({"nodeId": "judge", "operatorId": "vision.compare.number",
            "params": {"operator": "gte" if scope == "a" else "lte", "rightValue": 3.0 if scope == "a" else 2.0}})
        workflow["edges"].append({"fromNode": "count", "fromPort": "count", "toNode": "judge", "toPort": "left"})
        raw["workflows"][f"detect-{scope}"] = workflow
        for binding in bindings:
            duplicate = deepcopy(binding)
            duplicate["target"]["workflowId"] = f"detect-{scope}"
            raw["resources"]["parameterBindings"].append(duplicate)
        path = [{"nodeId": "loop", "relation": "loop_body"}, {"nodeId": scope, "relation": "subflow"}]
        presentation["resultScopes"][scope] = {"entryWorkflowId": "main", "scopeWorkflowId": f"detect-{scope}", "callPath": path}
        detail = []
        for index, (name, node, port, kind, componentType, prop) in enumerate([
                ("image", "blob", "overlay", "image", "image", "image"),
                ("count", "count", "count", "integer", "number", "value"),
                ("judge", "judge", "result", "boolean", "indicator", "value")]):
            source = f"{scope}-{name}"
            presentation["dataSources"][source] = {"kind": "node_output", "resultScopeId": scope,
                "workflowId": f"detect-{scope}", "callPath": path, "nodeId": node, "port": port, "expectedType": kind}
            props = {"indicatorStates": {"false": {"text": "NG", "color": "red"},
                                         "true": {"text": "OK", "color": "green"}}} if name == "judge" else {}
            overview.append({"componentId": f"overview-{source}", "type": componentType,
                "bindings": {prop: source}, "props": props, "layout": {"row": index, "column": offset}})
            detail.append({"componentId": f"detail-{source}", "type": componentType,
                "bindings": {prop: source}, "props": props, "layout": {"row": index}})
        overview.append({"componentId": f"open-{scope}", "type": "navigation_button", "props": {"text": f"{scope} NG detail"},
            "layout": {"row": 3, "column": offset}, "actions": {"clicked": {"type": "navigate",
                "pageId": f"detail-{scope}", "context": "displayed_result", "resultScopeId": scope}}})
        presentation["pages"][f"detail-{scope}"] = {"name": f"Detail {scope}", "resultScopeIds": [scope], "components": detail}
    presentation["pages"]["overview"] = {"name": "Two-scope overview", "layout": {"columns": 2},
        "resultScopeIds": ["a", "b"], "components": overview}
    raw["presentation"] = presentation
    plugins = root / "plugins" / "gated-selector"
    plugins.mkdir(parents=True)
    (plugins / "manifest.json").write_text(json.dumps({"operatorId": "test.acceptance.gated_selector",
        "displayName": "Bounded synthetic selector", "version": "1.0.0",
        "entry": "tests.ui.page_designer.test_retained_acceptance_multiscope:GatedSelector", "category": "test",
        "iconKey": "test", "summary": "Offline acceptance fixture only", "inputPorts": Selector.meta.inputPorts,
        "outputPorts": Selector.meta.outputPorts, "paramSchema": {"type": "object", "properties": {
            "gate": {"type": "string"}, "offset": {"type": "integer"}}},
        "minCoreVersion": "0.1.0", "maxCoreVersion": "1.x"}), encoding="utf-8")
    builtinRoot = Path(__file__).resolve().parents[3] / "src/emo_master/plugins/builtins"
    return ProjectDocument.model_validate(raw), (str(builtinRoot), str(root / "plugins"))


def clickDetail(renderer, scope):
    button = renderer.widgets["overview"][f"open-{scope}"][1]
    renderer.pages["overview"].ensureWidgetVisible(button)
    QTest.mouseClick(button, Qt.LeftButton)
    assert renderer.currentPageId == f"detail-{scope}"
    assert renderer.frozen


def retire(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken2.isValid(widget)


def assertScopeWidgets(renderer, prefix, scope, expected):
    rows = renderer.widgets[renderer.currentPageId]
    key = expected.result.identity.resultKey
    assert renderer.displayed[scope].result.identity == expected.result.identity
    assert rows[f"{prefix}-{scope}-count"][1].text() == next(
        source.valueJson for source in expected.result.sources if source.sourceId == f"{scope}-count")
    image = rows[f"{prefix}-{scope}-image"][1]
    assert image.key == key and not image.image.isNull()
    pixels = expected.images[f"{scope}-image"]
    assert (image.image.width(), image.image.height()) == (pixels.shape[1], pixels.shape[0])
    # Compare every pixel of the actual Qt-owned image, not just source arrays.
    from emo_master.ui.presentation.images import ownedImage
    assert image.image == ownedImage(pixels)


def testNormalTwoScopeOverviewButtonsPinGuiNgDespiteNewerBackgroundOk(qtApp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / "synthetic-two-scope"
    root.mkdir()
    gate = tmp_path / "release-second-cycle"
    document, plugins = twoScopeNormalLoop(root, gate)
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "jobs", pluginRootPaths=plugins)
    window = MainWindow(RuntimeClient(runtime))
    floating = None
    observedSession = None
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        coordinator = window.pageCoordinator
        coordinator.showPages()
        window.startJob()  # The actual original normal-run controller.
        waitFor(lambda: window.currentJobId is not None or window.runtimePanelState.jobStatus == "FAILED")
        job = window.currentJobId
        assert job, window.runtimePanelState.jobMessage
        coordinator.preview.watchCurrent()
        waitFor(lambda: coordinator.preview.hub is not None or coordinator.preview.error)
        assert coordinator.preview.error is None
        hub = coordinator.preview.hub
        renderer = coordinator.preview.observer
        observedSession = coordinator.preview.session
        waitFor(lambda: set(renderer.displayed) == {"a", "b"} and displayedImagesReady(renderer))
        displayed = dict(renderer.displayed)
        assert {value.result.identity.resultOrdinal for value in displayed.values()} == {1}
        assert len({value.result.identity.resultKey for value in displayed.values()}) == 2
        assert len({value.result.identity.invocationId for value in displayed.values()}) == 2
        assert not np.array_equal(displayed["a"].images["a-image"], displayed["b"].images["b-image"])
        for scope in ("a", "b"):
            assert displayed[scope].result.identity.jobId == job
            assert displayed[scope].result.identity.mode == "runtime"
            assert displayed[scope].result.status == "COMPLETE", jobFailureDetails(runtime, job, result=displayed[scope].result)
            assertScopeWidgets(renderer, "overview", scope, displayed[scope])
            assert renderer.widgets["overview"][f"overview-{scope}-judge"][1].text() == "NG"
        from emo_master.ui.presentation.renderer import RuntimePages
        floating = RuntimePages(renderer.config, hub=hub)
        floating.navigate(renderer.currentPageId)
        floating.show()
        qtApp.processEvents()
        assert floating is not None and floating.hub is hub
        assert floating.displayed == displayed
        hub.timer.stop()  # Deliberately hold both GUI commits while the real backend advances.
        gate.touch()
        waitFor(lambda: set(observedSession.readSnapshot().scopes) == {"a", "b"}
            and all(scope.result.identity.resultOrdinal == 2 for scope in observedSession.readSnapshot().scopes.values()))
        newer = observedSession.readSnapshot().scopes
        for scope in ("a", "b"):
            assert newer[scope].result.identity.resultKey != displayed[scope].result.identity.resultKey
            assert not np.array_equal(newer[scope].images[f"{scope}-image"], displayed[scope].images[f"{scope}-image"])
            assert next(source.valueJson for source in newer[scope].result.sources if source.sourceId == f"{scope}-judge") == "true"
            assertScopeWidgets(renderer, "overview", scope, displayed[scope])
        clickDetail(renderer, "a")
        frozen = renderer.frozen
        # Reopening the existing popup must not refresh the source canvas or
        # replace its committed NG detail with the background's newer OK.
        assert coordinator.preview.openObserver() is renderer and renderer.frozen == frozen
        assertScopeWidgets(renderer, "detail", "a", displayed["a"])
        clickDetail(floating, "b")
        pins = observedSession.pins()
        waitFor(lambda: all(pins.read(view.frozen).state == "PINNED" for view in (renderer, floating)))
        hub.tick()
        for view, scope in ((renderer, "a"), (floating, "b")):
            pin = pins.read(view.frozen)
            assert pin.scope.result.identity == displayed[scope].result.identity
            assert set(view.displayed) == {scope}
            assertScopeWidgets(view, "detail", scope, displayed[scope])
            assert view.widgets[f"detail-{scope}"][f"detail-{scope}-judge"][1].text() == "NG"
        assert runtime._presentationOwner.assets.stats()["lease_handles"] == 2
        QTest.mouseClick(renderer.resumeButton, Qt.LeftButton)
        assertScopeWidgets(renderer, "detail", "a", newer["a"])
        assert renderer.widgets["detail-a"]["detail-a-judge"][1].text() == "OK"
        assertScopeWidgets(floating, "detail", "b", displayed["b"])
        QTest.mouseClick(floating.resumeButton, Qt.LeftButton)
        assertScopeWidgets(floating, "detail", "b", newer["b"])
        assert floating.widgets["detail-b"]["detail-b-judge"][1].text() == "OK"
        waitFor(lambda: runtime._presentationOwner.assets.stats()["lease_handles"] == 0 and not pins.entries)
        renderer.act(Action(type='freeze', resultScopeId='a'))
        frozen = renderer.frozen
        waitFor(lambda: pins.read(frozen).state == 'PINNED')
        retire(floating)
        floating = None
        floating = RuntimePages(renderer.config, hub=hub)
        floating.navigate(renderer.currentPageId)
        floating.show()
        qtApp.processEvents()
        assert renderer.frozen == frozen and hub.pins == {renderer: frozen}
        assert floating.frozen is None and floating.currentPageId == renderer.currentPageId
        assert floating.config == renderer.config
        assertScopeWidgets(renderer, 'detail', 'a', newer['a'])
        QTest.mouseClick(renderer.resumeButton, Qt.LeftButton)
        waitFor(lambda: runtime._presentationOwner.assets.stats()['lease_handles'] == 0 and not pins.entries)
        waitFor(lambda: runtime.jobRepository.get(job).isTerminal)
        assert runtime.jobRepository.get(job).status == "COMPLETED", jobFailureDetails(runtime, job)
        assert len(runtime.jobRepository.all()) == 1
    finally:
        gate.touch()
        if floating is not None:
            retire(floating)
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        window.close()
        runtime.close()
    assert all(not thread.is_alive() for thread in observedSession.threads)
    assert observedSession._pinStore is not None and not observedSession._pinStore.thread.is_alive()


def testSimulationAndNormalObserverWindowsHaveNoExecutionDeviceOrCounterEffects(qtApp, tmp_path, monkeypatch):
    """A21: guard real control / persistence boundaries around read-only UI flows."""
    from emo_master.plugins.builtins._huaray_imv import CameraSession
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / "synthetic-read-only"
    root.mkdir()
    document = sampleProject(root)
    document.workflows["main"].nodes[1].params["imagePath"] = str(root / "input.png")
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "jobs")
    window = MainWindow(RuntimeClient(runtime))
    calls = []
    def guarded(name):
        def forbidden(*args, **kwargs):
            calls.append(name)
            raise AssertionError("read-only UI attempted " + name)
        return forbidden
    start = runtime.StartJob
    monkeypatch.setattr(runtime, "StartJob", guarded("StartJob"))
    monkeypatch.setattr(runtime, "StopJob", guarded("StopJob"))
    monkeypatch.setattr(CameraSession, "open", guarded("CameraSession.open"))
    for name in ("applyGlobalCounter", "setGlobalCounter", "resetGlobalCounter"):
        monkeypatch.setattr(runtime.sqliteStore, name, guarded(name))
    observedSession = None
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        coordinator = window.pageCoordinator
        coordinator.showPages()
        beforeCounters = runtime.sqliteStore.listGlobalCounters(document.project.projectId)
        view = coordinator.preview.openObserver()
        for mode in ("OK", "NG", "WAITING", "ERROR", None):
            view.simulation.setCurrentIndex(view.simulation.findData(mode))
            qtApp.processEvents()
            assert view.simulationState == mode
        assert not runtime.jobRepository.all() and calls == []
        # Exactly one explicitly requested start establishes a real captured Job.
        monkeypatch.setattr(runtime, "StartJob", start)
        window.startJob()
        waitFor(lambda: window.currentJobId is not None or window.runtimePanelState.jobStatus == "FAILED")
        job = window.currentJobId
        assert job, window.runtimePanelState.jobMessage
        waitFor(lambda: not window.isJobRunning)
        monkeypatch.setattr(runtime, "StartJob", guarded("StartJob"))
        assert runtime.jobRepository.get(job).status == "COMPLETED", jobFailureDetails(runtime, job)
        coordinator.preview.watchCurrent()
        waitFor(lambda: coordinator.preview.hub is not None or coordinator.preview.error)
        assert coordinator.preview.error is None
        observedSession = coordinator.preview.session
        waitFor(lambda: displayedImagesReady(view))
        decoded = observedSession.stats["decoded"]
        conversions = coordinator.preview.hub.conversions
        threadOwners = tuple(observedSession.threads)
        assert decoded == 1
        for _ in range(10):
            floating = coordinator.preview.openObserver()
            assert floating is not None and floating.hub is coordinator.preview.hub
            assert floating.displayed
            coordinator.showFlow()
            assert not floating.isVisible()
            coordinator.showPages()
            assert len(coordinator.preview.hub.windows) == 1
            assert coordinator.preview.session is observedSession
            assert tuple(observedSession.threads) == threadOwners
            assert observedSession.stats["decoded"] == decoded
            assert coordinator.preview.hub.conversions == conversions
        view.hide()
        coordinator.preview.hub.lastToken = None
        coordinator.preview.hub.tick()
        assert not view.displayed
        assert observedSession.stats["decoded"] == decoded
        assert coordinator.preview.hub.conversions == conversions
        coordinator.preview.closeAsync()
        waitFor(lambda: not coordinator.preview.active())
        assert calls == []
        assert runtime.sqliteStore.listGlobalCounters(document.project.projectId) == beforeCounters
        assert len(runtime.jobRepository.all()) == 1
        assert not runtime._closed and runtime.jobRepository.get(job).status == "COMPLETED"
        assert all(not thread.is_alive() for thread in observedSession.threads)
        assert runtime._presentationOwner.assets.stats()["lease_handles"] == 0
    finally:
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        window.close()
        runtime.close()
