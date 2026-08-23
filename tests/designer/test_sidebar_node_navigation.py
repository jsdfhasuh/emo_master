from emo_master.apps.designer.ui.main_window import MainWindow


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


class RuntimeClientStub:
    def listOperators(self):
        return []

    def loadProject(self, projectPath: str):
        _ = projectPath
        return type("Reply", (), {"ok": True, "message": "ok"})()


def testSidebarNodeListFollowsCreationOrder() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_loader",
            "displayName": "Image Loader",
            "inputPorts": {},
            "outputPorts": {"image": "image"},
            "paramSchema": {},
        }
    )
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_saver",
            "displayName": "Image Saver",
            "inputPorts": {"image": "image"},
            "outputPorts": {"result": "json"},
            "paramSchema": {},
        }
    )

    getSidebarNodeEntries = getattr(window, "getSidebarNodeEntries", None)
    assert callable(getSidebarNodeEntries)
    entries = getSidebarNodeEntries()
    assert isinstance(entries, list)
    assert len(entries) == 2
    assert entries[0]["displayName"] == "Image Loader"
    assert entries[1]["displayName"] == "Image Saver"


def testSidebarNodeDoubleClickCentersCanvas() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_loader",
            "displayName": "Image Loader",
            "inputPorts": {},
            "outputPorts": {"image": "image"},
            "paramSchema": {},
        },
        sceneX=320.0,
        sceneY=180.0,
    )
    entries = window.getSidebarNodeEntries()
    nodeId = str(entries[0]["nodeId"])

    centerCalls: list[tuple[float, float]] = []
    originalCenterOn = getattr(window.flowView, "centerOn", None)
    setattr(
        window.flowView,
        "centerOn",
        lambda x, y: centerCalls.append((float(x), float(y))),
    )
    try:
        navigateToNode = getattr(window, "navigateToNodeFromSidebar", None)
        assert callable(navigateToNode)
        navigateToNode(nodeId)
    finally:
        if originalCenterOn is not None:
            setattr(window.flowView, "centerOn", originalCenterOn)

    assert len(centerCalls) == 1
    selectedNodeId = window.flowScene.getSelectedNodeId()
    assert selectedNodeId == nodeId
