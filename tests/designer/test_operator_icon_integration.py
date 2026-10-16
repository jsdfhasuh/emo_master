from copy import deepcopy
import sys
import threading
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.ui import main_window as mainWindowModule
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from tests.designer.qt_wait import waitForCatalog, waitUntil
from tests.icon_fixtures import iconDefinition, iconReply
from tests.runtime.test_operator_icon_assets import iconService, network

pytest.importorskip("PySide2.QtWidgets")
from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QLabel, QMainWindow
import shiboken2


class Client:
    runtimeScope = "initial"

    def __init__(self):
        self.catalog = [SimpleNamespace(**iconDefinition(), editorSpec={"kind": "customUi"})]
        self.calls = 0
        self.fail = False

    def listOperators(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise OSError("offline")
        return self.catalog

    def getOperatorIconAsset(self, *_args, **kwargs):
        return iconReply()


def testFirstCatalogIsAsyncAndBusinessEntrypointsWait(monkeypatch):
    release = threading.Event()

    class Slow(Client):
        def listOperators(self, **kwargs):
            release.wait(3)
            return super().listOperators(**kwargs)

    client = Slow()
    window = mainWindowModule.MainWindow(client)
    calls = []
    editor = QLabel()
    monkeypatch.setattr(window.operatorEditorManager, "open", lambda **kwargs: (calls.append(kwargs), editor)[1])
    monkeypatch.setattr(mainWindowModule.QFileDialog, "getOpenFileName", lambda *_args: (calls.append("file"), ("", ""))[1])
    try:
        assert window.operatorCatalogController.state == "loading"
        window.addNodeFromOperatorPayload(iconDefinition())
        nodeId = next(n.nodeId for n in window.flowModel.nodes.values() if n.kind == "operator")
        for _ in range(30):
            window.refreshOperators()
        assert window.operatorCatalogWorker._sequence == 1
        window.openNodeParamDialog(nodeId)
        assert window.importWorkflowPackageAction() is None
        assert calls == [] and window.nodeParamDialog is None
        responsive = []
        QTimer.singleShot(0, lambda: responsive.append(True))
        waitUntil(lambda: bool(responsive))
        assert not window.operatorCatalogController.hasCatalog
        release.set()
        waitForCatalog(window)
        assert client.calls == 1
        window.openNodeParamDialog(nodeId)
        assert calls[0]["operatorDefinition"]["editorSpec"]["kind"] == "customUi"
    finally:
        release.set()
        window.close()


def testRefreshDoesNotMutateGraphAndKeepsCatalogOnFailure():
    client = Client()
    window = mainWindowModule.MainWindow(client)
    try:
        waitForCatalog(window)
        window.addNodeFromOperatorPayload(iconDefinition())
        node = next(n for n in window.flowModel.nodes.values() if n.kind == "operator")
        window.flowModel.selectNode(node.nodeId)
        window._refreshNodeDetailsView()
        item = window.flowScene._nodeItems[node.nodeId]
        sidebar = window._sidebarIconItems[0]
        item.setRuntimeState("RUNNING")
        waitUntil(lambda: window.nodeDetailIcon._operatorIconSource == "custom" and item._operatorIconSource == "custom")
        before = deepcopy((window.workflowStore.workflows, window.workflowStore.project, window.flowModel.nodes))
        rect, position, style = item.rect(), item.pos(), item.getVisualStyle()
        client.fail = True
        window.refreshOperators()
        assert window.operatorCatalogController.hasCatalog
        waitUntil(lambda: window.operatorCatalogController.state == "failed")
        assert window.operatorCatalog and window._requireOperatorCatalog()
        assert window.nodeDetailIcon._operatorIconSource == "custom"
        client.fail = False
        client.catalog = []
        window.refreshOperators()
        waitUntil(lambda: window.operatorCatalogController.state == "ready")
        assert item._operatorIconSource == sidebar._operatorIconSource == window.nodeDetailIcon._operatorIconSource == "fallback"
        assert before == (window.workflowStore.workflows, window.workflowStore.project, window.flowModel.nodes)
        assert window.flowModel.selectedNodeId == node.nodeId
        assert (item.rect(), item.pos(), item.getVisualStyle()) == (rect, position, style)
    finally:
        window.close()


def testWorkflowProjectAndRuntimeSessionRebindConsumers():
    client = Client()
    window = mainWindowModule.MainWindow(client)
    try:
        waitForCatalog(window)
        window.addNodeFromOperatorPayload(iconDefinition())
        nodeId = next(n.nodeId for n in window.flowModel.nodes.values() if n.kind == "operator")
        window.createWorkflow("Other")
        window.activateWorkflow("main")
        provider = window.operatorIconProvider
        item = window.flowScene._nodeItems[nodeId]
        old = provider._bindings[provider._targets[id(item)]]
        assert old.context == (window._projectInstanceToken, "main", nodeId)
        oldProjectToken = window._projectInstanceToken
        window._applyLoadedProjectState(None, None)
        new = provider._bindings[provider._targets[id(item)]]
        assert new.token > old.token and window._projectInstanceToken != oldProjectToken
        assert new.context == (window._projectInstanceToken, "main", nodeId)
        waitUntil(lambda: item._operatorIconSource == "custom")
        oldScope = provider.scope
        client.runtimeScope = "reconnected"
        waitUntil(lambda: provider.scope != oldScope)
        waitUntil(lambda: item._operatorIconSource == "custom")
        assert provider.scope == "reconnected" and client.calls == 2
        assert all(binding.request is None or binding.request.scope == "reconnected" for binding in provider._bindings.values())
    finally:
        window.close()


@pytest.mark.parametrize("failure", ["missing", "renderer"])
def testFullWindowStillStartsWithoutUsableQtSvg(monkeypatch, failure):
    icon.cache_clear()
    if failure == "missing":
        monkeypatch.setitem(sys.modules, "PySide2.QtSvg", None)
    else:
        import PySide2.QtSvg

        def broken(*_args):
            raise RuntimeError("renderer unavailable")

        monkeypatch.setattr(PySide2.QtSvg, "QSvgRenderer", broken)
    window = mainWindowModule.MainWindow(Client())
    try:
        assert mainWindowModule._nativeQt and isinstance(window, QMainWindow)
        waitForCatalog(window)
        window.addNodeFromOperatorPayload(iconDefinition())
        nodeId = next(n.nodeId for n in window.flowModel.nodes.values() if n.kind == "operator")
        target = window.flowScene._nodeItems[nodeId]
        waitUntil(lambda: any(f.code == "E_ICON_DECODE_FAILED" for f in window.operatorIconProvider._failures.values()))
        assert target._operatorIconSource == "fallback" and not target._operatorIcon.isNull()
        assert not icon("play").isNull()
        from emo_master.apps.package_selftest import _checkGuiIcons
        with pytest.raises((ImportError, RuntimeError)):
            _checkGuiIcons()
    finally:
        window.close()
        icon.cache_clear()


@pytest.mark.parametrize("remote", [False, True])
def testActualRuntimeContractFeedsCustomQtIcons(remote):
    def check(client):
        window = mainWindowModule.MainWindow(client)
        try:
            waitForCatalog(window)
            window.addNodeFromOperatorPayload(window.operatorCatalog[0])
            node = next(n for n in window.flowModel.nodes.values() if n.kind == "operator")
            item = window.flowScene._nodeItems[node.nodeId]
            waitUntil(lambda: item._operatorIconSource == "custom")
            assert item._operatorIconSha == window.operatorCatalog[0]["icon"]["sha256"]
        finally:
            window.close()

    if remote:
        with network(iconService()) as client:
            check(client)
    else:
        client = RuntimeClient(iconService())
        try:
            check(client)
        finally:
            client.close()


def testPendingCardBindingTimerDiesWithItsWidget():
    from PySide2.QtCore import QCoreApplication
    from emo_master.apps.designer.ui.operator_bubble import OperatorBubble
    for _ in range(12):
        bubble = OperatorBubble()
        bubble.setOperators([iconDefinition()])
        assert bubble._iconBindingTimer.isActive()
        shiboken2.delete(bubble)
        QCoreApplication.processEvents()


def testStartupCancellationShutsDownDisplayBeforeClient(monkeypatch):
    from emo_master.apps.designer import main
    from PySide2 import QtWidgets
    from PySide2.QtWidgets import QApplication

    events = []
    client = SimpleNamespace(close=lambda: events.append("client"))

    class Window:
        def __init__(self, actualClient, showStartupEntry):
            assert actualClient is client and showStartupEntry

        def showStartupProjectEntry(self):
            events.append("cancel")
            return False

        def shutdownOperatorDisplay(self):
            events.append("display")

        def show(self):
            pytest.fail("cancelled startup must not show the main window")

    app = QApplication.instance()
    monkeypatch.setattr(QtWidgets, "QApplication", lambda *_args: app)
    monkeypatch.setattr(main, "configureHighDpi", lambda: None)
    monkeypatch.setattr(main, "applyDesignerStyle", lambda *_args: None)
    monkeypatch.setattr(main, "resolveRuntimeTarget", lambda: "")
    monkeypatch.setattr(main, "RuntimeService", lambda: object())
    monkeypatch.setattr(main, "RuntimeClient", lambda **_kwargs: client)
    monkeypatch.setattr(mainWindowModule, "MainWindow", Window)
    main.runDesigner()
    assert events == ["cancel", "display", "client"]
