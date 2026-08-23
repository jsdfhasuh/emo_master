from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.presenters.node_details_presenter import (
    NodeDetailsPresenter,
)


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


def testCurrentNodePanelShowsEmptyState() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    getCurrentNodeSummary = getattr(window, "getCurrentNodeSummary", None)
    assert callable(getCurrentNodeSummary)
    summary = getCurrentNodeSummary()
    assert isinstance(summary, str)
    assert "未选中节点" in summary


def testNodeDetailsPresenterBuildsEmptyState() -> None:
    model = FlowGraphModel()
    presenter = NodeDetailsPresenter(flowModel=model, nodeRuntimeState={})
    summary = presenter.buildSummary()
    assert "未选中节点" in summary

    detailModel = presenter.buildModel()
    assert detailModel["state"] == "empty"
    assert "未选中节点" in str(detailModel["message"])


def testCurrentNodePanelShowsSelectedNodeSummary() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_loader",
            "displayName": "Image Loader",
            "inputPorts": {},
            "outputPorts": {"image": "image"},
            "paramSchema": {
                "type": "object",
                "required": ["imagePath"],
                "properties": {"imagePath": {"type": "string"}},
            },
        }
    )
    summary = window.getCurrentNodeSummary()
    assert "Image Loader" in summary
    assert "vision.io.image_loader" in summary
    assert "imagePath" in summary


def testNodeDetailsPresenterBuildsIfSummary() -> None:
    model = FlowGraphModel()
    nodeId = model.addNode(
        operatorId="vision.flow.if",
        displayName="If",
        inputPorts={"value": "object"},
        outputPorts={"true": "object", "false": "object"},
        paramSchema={
            "type": "object",
            "required": ["mode"],
            "properties": {
                "mode": {"type": "string"},
                "compareValue": {"type": "string"},
            },
        },
    )
    model.setNodeParams(nodeId, {"mode": "equals", "compareValue": "A"})
    model.selectNode(nodeId)
    presenter = NodeDetailsPresenter(
        flowModel=model,
        nodeRuntimeState={nodeId: {"status": "SKIPPED", "branch": "true"}},
    )
    summary = presenter.buildSummary()
    assert "If" in summary
    assert "SKIPPED" in summary
    assert "true" in summary

    detailModel = presenter.buildModel()
    assert detailModel["state"] == "selected"
    assert detailModel["title"] == "If"
    assert detailModel["runtimeStatus"] == "SKIPPED"
    assert detailModel["branch"] == "true"


def testNodeDetailsPresenterBuildsSwitchSummary() -> None:
    model = FlowGraphModel()
    nodeId = model.addNode(
        operatorId="vision.flow.switch",
        displayName="Switch",
        inputPorts={"value": "object"},
        outputPorts={
            "case0": "object",
            "case1": "object",
            "case2": "object",
            "case3": "object",
            "default": "object",
        },
        paramSchema={
            "type": "object",
            "properties": {
                "case0Value": {"type": "string"},
                "case1Value": {"type": "string"},
                "case2Value": {"type": "string"},
                "case3Value": {"type": "string"},
            },
        },
    )
    model.setNodeParams(nodeId, {"case1Value": "B"})
    model.selectNode(nodeId)
    presenter = NodeDetailsPresenter(
        flowModel=model,
        nodeRuntimeState={nodeId: {"status": "COMPLETED", "branch": "case1"}},
    )
    summary = presenter.buildSummary()
    assert "Switch" in summary
    assert "COMPLETED" in summary
    assert "case1" in summary

    detailModel = presenter.buildModel()
    assert detailModel["state"] == "selected"
    assert detailModel["title"] == "Switch"
    assert detailModel["branch"] == "case1"


def testCurrentNodePanelUpdatesAfterParamChange() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_loader",
            "displayName": "Image Loader",
            "inputPorts": {},
            "outputPorts": {"image": "image"},
            "paramSchema": {
                "type": "object",
                "required": ["imagePath"],
                "properties": {"imagePath": {"type": "string"}},
            },
        }
    )
    nodeId = next(iter(window.flowModel.nodes.keys()))
    window.updateNodeParams(nodeId, {"imagePath": "C:/tmp/input.png"})
    summary = window.getCurrentNodeSummary()
    assert "C:/tmp/input.png" in summary


def testCurrentNodePanelShowsIfBranchInfo() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.flow.if",
            "displayName": "If",
            "inputPorts": {"value": "object"},
            "outputPorts": {"true": "object", "false": "object"},
            "paramSchema": {
                "type": "object",
                "required": ["mode"],
                "properties": {
                    "mode": {"type": "string"},
                    "compareValue": {"type": "string"},
                },
            },
        }
    )
    nodeId = next(iter(window.flowModel.nodes.keys()))
    window.updateNodeParams(nodeId, {"mode": "equals", "compareValue": "A"})

    setCurrentNodeRuntimeState = getattr(window, "setCurrentNodeRuntimeState", None)
    assert callable(setCurrentNodeRuntimeState)
    setCurrentNodeRuntimeState(nodeId, "SKIPPED", "true")

    summary = window.getCurrentNodeSummary()
    assert "SKIPPED" in summary
    assert "true" in summary


def testCurrentNodePanelUpdatesFromRuntimeEventPayload() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.flow.if",
            "displayName": "If",
            "inputPorts": {"value": "object"},
            "outputPorts": {"true": "object", "false": "object"},
            "paramSchema": {
                "type": "object",
                "required": ["mode"],
                "properties": {
                    "mode": {"type": "string"},
                    "compareValue": {"type": "string"},
                },
            },
        }
    )
    nodeId = next(iter(window.flowModel.nodes.keys()))
    applyRuntimeEventToNode = getattr(window, "applyRuntimeEventToNode", None)
    assert callable(applyRuntimeEventToNode)
    applyRuntimeEventToNode(
        {
            "nodeId": nodeId,
            "payload": {"status": "COMPLETED", "branch": "false"},
        }
    )
    summary = window.getCurrentNodeSummary()
    assert "COMPLETED" in summary
    assert "false" in summary


def testMainWindowUsesPresenterModelForRuntimePanelText() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.io.image_loader",
            "displayName": "Image Loader",
            "inputPorts": {},
            "outputPorts": {"image": "image"},
            "paramSchema": {
                "type": "object",
                "required": ["imagePath"],
                "properties": {"imagePath": {"type": "string"}},
            },
        }
    )
    refreshRuntimePanelView = getattr(window, "_refreshRuntimePanelView", None)
    assert callable(refreshRuntimePanelView)
    refreshRuntimePanelView()
    text = window.runtimeStatusOutput.toPlainText()
    assert text == ""
    assert "Image Loader" in window.nodeDetailTitleCard.text()
    assert "运行状态" in window.nodeDetailMetaCard.text()


def testMainWindowBuildsStructuredNodeDetailViewModel() -> None:
    ensureQApp()
    window = MainWindow(RuntimeClientStub())
    window.addNodeFromOperatorPayload(
        {
            "operatorId": "vision.flow.if",
            "displayName": "If",
            "inputPorts": {"value": "object"},
            "outputPorts": {"true": "object", "false": "object"},
            "paramSchema": {
                "type": "object",
                "required": ["mode"],
                "properties": {
                    "mode": {"type": "string"},
                    "compareValue": {"type": "string"},
                },
            },
        }
    )
    getCurrentNodeDetailViewModel = getattr(
        window, "getCurrentNodeDetailViewModel", None
    )
    assert callable(getCurrentNodeDetailViewModel)
    viewModel = getCurrentNodeDetailViewModel()
    assert isinstance(viewModel, dict)
    assert viewModel.get("state") == "selected"
    assert viewModel.get("title") == "If"
