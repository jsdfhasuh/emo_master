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


def testDeleteUsesShortcutHandlerAndNoDeleteButton() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    assert not hasattr(window, "deleteSelectedButton")

    sourceNodeId = window.flowModel.addNode(
        operatorId="vision.demo.source",
        displayName="Source",
        inputPorts={},
        outputPorts={"imageOut": "image"},
    )
    targetNodeId = window.flowModel.addNode(
        operatorId="vision.demo.target",
        displayName="Target",
        inputPorts={"imageIn": "image"},
        outputPorts={},
    )
    edge = window.flowModel.connectNodes(
        sourceNodeId, "imageOut", targetNodeId, "imageIn"
    )

    window.flowScene.getSelectedEdgeKeys = lambda: [
        (edge.fromNode, edge.fromPort, edge.toNode, edge.toPort)
    ]
    window.flowScene.getSelectedNodeIds = lambda: []

    handleDeleteShortcut = getattr(window, "handleDeleteShortcut", None)
    assert callable(handleDeleteShortcut)
    handleDeleteShortcut()
    assert len(window.flowModel.edges) == 0
