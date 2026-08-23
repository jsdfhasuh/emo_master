from emo_master.apps.designer.ui.flow_scene import (
    FlowEdgeViewModel,
    FlowNodeViewModel,
    FlowScene,
)


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


def testFlowSceneNodeDoubleClickHandlerTriggered() -> None:
    ensureQApp()
    scene = FlowScene()
    captured: list[str] = []

    scene.setNodeDoubleClickHandler(lambda nodeId: captured.append(nodeId))
    simulate = getattr(scene, "simulateNodeDoubleClick", None)
    if callable(simulate):
        simulate("node-x")
    else:
        handle = getattr(scene, "handleNodeDoubleClick", None)
        if callable(handle):
            handle("node-x")

    assert captured == ["node-x"]


def testFlowSceneCanvasClickHandlerTriggered() -> None:
    scene = FlowScene()
    captured = {"count": 0}

    scene.setCanvasClickHandler(
        lambda: captured.__setitem__("count", captured["count"] + 1)
    )
    simulate = getattr(scene, "simulateCanvasClick", None)
    if callable(simulate):
        simulate()

    assert captured["count"] == 1


def testFlowSceneOperatorDropHandlerTriggered() -> None:
    scene = FlowScene()
    captured: dict[str, object] = {}

    def dropHandler(payload: dict[str, object], x: float, y: float) -> None:
        captured["payload"] = payload
        captured["x"] = x
        captured["y"] = y

    scene.setOperatorDropHandler(dropHandler)
    simulateDrop = getattr(scene, "simulateOperatorDrop", None)
    if callable(simulateDrop):
        simulateDrop({"operatorId": "vision.edge.canny"}, 10.0, 20.0)

    payload = captured.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("operatorId") == "vision.edge.canny"


def testFlowSceneConnectionReasonShowsTypeMismatch() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-a",
            title="A",
            x=10.0,
            y=10.0,
            inputPorts={},
            outputPorts={"imageOut": "image"},
        )
    )
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-b",
            title="B",
            x=260.0,
            y=10.0,
            inputPorts={"maskIn": "mask"},
            outputPorts={},
        )
    )

    describeConnectionAttempt = getattr(scene, "describeConnectionAttempt", None)
    assert callable(describeConnectionAttempt)
    reason = describeConnectionAttempt("node-a", "imageOut", "node-b", "maskIn")
    assert isinstance(reason, str)
    assert "类型不匹配" in reason
    assert "image" in reason
    assert "mask" in reason


def testFlowScenePreviewConnectionTargetWithSnapHint() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-a",
            title="A",
            x=10.0,
            y=10.0,
            inputPorts={},
            outputPorts={"imageOut": "image"},
        )
    )
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-b",
            title="B",
            x=260.0,
            y=10.0,
            inputPorts={"imageIn": "image"},
            outputPorts={},
        )
    )

    previewConnectionTarget = getattr(scene, "previewConnectionTarget", None)
    assert callable(previewConnectionTarget)

    previewResult = previewConnectionTarget("node-a", "imageOut", "node-b", "imageIn")
    assert isinstance(previewResult, dict)
    assert previewResult.get("valid") is True
    assert previewResult.get("targetKey") == ("node-b", "input", "imageIn")


def testFlowSceneSelectedEdgeStyleIsEnhanced() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-a",
            title="A",
            x=10.0,
            y=10.0,
            inputPorts={},
            outputPorts={"imageOut": "image"},
        )
    )
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-b",
            title="B",
            x=260.0,
            y=10.0,
            inputPorts={"imageIn": "image"},
            outputPorts={},
        )
    )
    edge = FlowEdgeViewModel("node-a", "imageOut", "node-b", "imageIn")
    scene.renderEdge(edge)

    edgeKey = ("node-a", "imageOut", "node-b", "imageIn")
    getEdgeStyle = getattr(scene, "getEdgeStyle", None)
    setEdgeSelected = getattr(scene, "setEdgeSelected", None)
    assert callable(getEdgeStyle)
    assert callable(setEdgeSelected)

    normalStyle = getEdgeStyle(edgeKey)
    assert isinstance(normalStyle, dict)
    setEdgeSelected(edgeKey, True)
    selectedStyle = getEdgeStyle(edgeKey)
    assert isinstance(selectedStyle, dict)
    assert isinstance(normalStyle.get("width"), (int, float))
    assert isinstance(selectedStyle.get("width"), (int, float))
    assert float(selectedStyle["width"]) > float(normalStyle["width"])


def testFlowSceneDragPreviewSupportsSnapRadius() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-a",
            title="A",
            x=10.0,
            y=10.0,
            inputPorts={},
            outputPorts={"imageOut": "image"},
        )
    )
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-b",
            title="B",
            x=260.0,
            y=10.0,
            inputPorts={"imageIn": "image"},
            outputPorts={},
        )
    )

    previewDragAt = getattr(scene, "previewDragAt", None)
    assert callable(previewDragAt)

    nearTarget = previewDragAt("node-a", "imageOut", 268.0, 44.0)
    assert isinstance(nearTarget, dict)
    assert nearTarget.get("valid") is True
    assert nearTarget.get("targetKey") == ("node-b", "input", "imageIn")


def testFlowSceneDragHintOnlyVisibleDuringDrag() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-a",
            title="A",
            x=10.0,
            y=10.0,
            inputPorts={},
            outputPorts={"imageOut": "image"},
        )
    )
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-b",
            title="B",
            x=260.0,
            y=10.0,
            inputPorts={"imageIn": "image"},
            outputPorts={},
        )
    )

    beginDragPreview = getattr(scene, "beginDragPreview", None)
    updateDragPreview = getattr(scene, "updateDragPreview", None)
    endDragPreview = getattr(scene, "endDragPreview", None)
    getDragHintText = getattr(scene, "getDragHintText", None)
    assert callable(beginDragPreview)
    assert callable(updateDragPreview)
    assert callable(endDragPreview)
    assert callable(getDragHintText)

    assert getDragHintText() == ""
    beginDragPreview("node-a", "imageOut")
    hintText = updateDragPreview(268.0, 44.0)
    assert isinstance(hintText, str)
    assert "可连接" in hintText
    endDragPreview()
    assert getDragHintText() == ""


def testFlowSceneIfNodeStyleUsesSpecialPalette() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="if-node",
            title="If",
            x=20.0,
            y=30.0,
            inputPorts={"value": "object"},
            outputPorts={"true": "object", "false": "object"},
        )
    )

    getNodeVisualStyle = getattr(scene, "getNodeVisualStyle", None)
    assert callable(getNodeVisualStyle)
    style = getNodeVisualStyle("if-node")
    assert isinstance(style, dict)
    assert style.get("variant") == "if"


def testFlowSceneSwitchNodeStyleUsesSpecialPalette() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="switch-node",
            title="Switch",
            x=20.0,
            y=30.0,
            inputPorts={"value": "object"},
            outputPorts={
                "case0": "object",
                "case1": "object",
                "case2": "object",
                "case3": "object",
                "default": "object",
            },
            operatorId="vision.flow.switch",
        )
    )

    getNodeVisualStyle = getattr(scene, "getNodeVisualStyle", None)
    assert callable(getNodeVisualStyle)
    style = getNodeVisualStyle("switch-node")
    assert isinstance(style, dict)
    assert style.get("variant") == "switch"


def testFlowSceneDoesNotInferSwitchVariantForOtherOperator() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="custom-node",
            title="Custom",
            x=20.0,
            y=30.0,
            inputPorts={"value": "object"},
            outputPorts={
                "case0": "object",
                "case1": "object",
                "case2": "object",
                "case3": "object",
                "default": "object",
            },
            operatorId="custom.operator",
        )
    )

    getNodeVisualStyle = getattr(scene, "getNodeVisualStyle", None)
    assert callable(getNodeVisualStyle)
    style = getNodeVisualStyle("custom-node")
    assert isinstance(style, dict)
    assert style.get("variant") == "default"


def testFlowSceneNodeRuntimeStateChangesVisualStyle() -> None:
    ensureQApp()
    scene = FlowScene()
    scene.addFlowNode(
        FlowNodeViewModel(
            nodeId="node-a",
            title="Loader",
            x=20.0,
            y=30.0,
            inputPorts={},
            outputPorts={"image": "image"},
        )
    )

    setNodeRuntimeState = getattr(scene, "setNodeRuntimeState", None)
    getNodeVisualStyle = getattr(scene, "getNodeVisualStyle", None)
    assert callable(setNodeRuntimeState)
    assert callable(getNodeVisualStyle)

    setNodeRuntimeState("node-a", "RUNNING")
    runningStyle = getNodeVisualStyle("node-a")
    assert isinstance(runningStyle, dict)
    assert runningStyle.get("runtimeState") == "RUNNING"

    setNodeRuntimeState("node-a", "SKIPPED")
    skippedStyle = getNodeVisualStyle("node-a")
    assert skippedStyle.get("runtimeState") == "SKIPPED"
