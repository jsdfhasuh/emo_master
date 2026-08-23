from pathlib import Path

from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel
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


def testMainWindowCanSaveAndLoadProjectDirectory(tmp_path: Path) -> None:
    ensureQApp()
    saveDir = tmp_path / "demo_project"

    window = MainWindow(RuntimeClientStub())
    sourceNode = window.flowModel.addNode(
        operatorId="vision.io.image_loader",
        displayName="Image Loader",
        inputPorts={},
        outputPorts={"image": "image"},
        paramSchema={"type": "object", "properties": {"imagePath": {"type": "string"}}},
    )
    targetNode = window.flowModel.addNode(
        operatorId="vision.edge.canny",
        displayName="Canny",
        inputPorts={"image": "image"},
        outputPorts={"edges": "image"},
        paramSchema={},
    )
    window.flowModel.setNodeParams(sourceNode, {"imagePath": "C:/tmp/a.png"})
    edge = window.flowModel.connectNodes(sourceNode, "image", targetNode, "image")

    nodeA = window.flowModel.nodes[sourceNode]
    nodeB = window.flowModel.nodes[targetNode]
    window.flowScene.addFlowNode(
        type(
            "N",
            (),
            {
                "nodeId": nodeA.nodeId,
                "title": nodeA.displayName,
                "x": 30.0,
                "y": 40.0,
                "inputPorts": nodeA.inputPorts,
                "outputPorts": nodeA.outputPorts,
            },
        )()
    )
    window.flowScene.addFlowNode(
        type(
            "N",
            (),
            {
                "nodeId": nodeB.nodeId,
                "title": nodeB.displayName,
                "x": 260.0,
                "y": 40.0,
                "inputPorts": nodeB.inputPorts,
                "outputPorts": nodeB.outputPorts,
            },
        )()
    )
    window.flowScene.renderEdge(
        FlowEdgeViewModel(edge.fromNode, edge.fromPort, edge.toNode, edge.toPort)
    )

    saveMethod = getattr(window, "saveProjectToDirectory", None)
    assert callable(saveMethod)
    assert saveMethod(str(saveDir)) is True
    assert (saveDir / "project.json").exists()

    windowLoaded = MainWindow(RuntimeClientStub())
    loadMethod = getattr(windowLoaded, "loadProjectDirectory", None)
    assert callable(loadMethod)
    assert loadMethod(str(saveDir)) is True
    assert len(windowLoaded.flowModel.nodes) == 2
    assert len(windowLoaded.flowModel.edges) == 1

    loadedSource = windowLoaded.flowModel.nodes[sourceNode]
    assert loadedSource.params.get("imagePath") == "C:/tmp/a.png"


def testSaveProjectActionOverwritesLoadedProjectWithoutChooser(tmp_path: Path) -> None:
    ensureQApp()
    projectDir = tmp_path / "loaded_project"
    window = MainWindow(RuntimeClientStub())
    assert window.saveProjectToDirectory(str(projectDir)) is True

    chooserCalled = {"value": False}

    def fakeChooser(title: str) -> str:
        _ = title
        chooserCalled["value"] = True
        return ""

    window._chooseProjectDirectory = fakeChooser  # type: ignore[method-assign]
    window.currentProjectDir = projectDir
    window.saveProjectAction()
    assert chooserCalled["value"] is False
