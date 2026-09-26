from __future__ import annotations

from emo_master.apps.designer.operator_editors import EditorKey, OperatorEditorManager
from emo_master.apps.designer.operator_editors.trust import isBuiltinController


class _Settings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


class _Runtime:
    pass


def testBuiltinControllerTrustUsesExactAllowlist() -> None:
    assert isBuiltinController(
        "emo_master.plugins.builtins.roi.editor:RoiEditorController"
    )
    assert not isBuiltinController(
        "emo_master.plugins.builtins.untrusted.editor:Controller"
    )


def testEditorManagerKeepsOneWindowPerProjectWorkflowNode(tmp_path) -> None:
    applied: list[tuple[EditorKey, dict[str, object]]] = []
    manager = OperatorEditorManager(
        runtimeClient=_Runtime(),
        settingsStore=_Settings(),
        applyParams=lambda key, params: not applied.append((key, params)),
        appendLog=lambda _level, _message: None,
        cacheRoot=tmp_path,
    )
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer", "default": 1}},
    }

    first = manager.open(
        projectId="project-a",
        workflowId="main",
        nodeId="node-1",
        operatorId="vision.value.number",
        displayName="Number",
        schema=schema,
        values={"value": 2},
    )
    repeated = manager.open(
        projectId="project-a",
        workflowId="main",
        nodeId="node-1",
        operatorId="vision.value.number",
        displayName="Number",
        schema=schema,
        values={"value": 99},
    )
    otherWorkflow = manager.open(
        projectId="project-a",
        workflowId="secondary",
        nodeId="node-1",
        operatorId="vision.value.number",
        displayName="Number",
        schema=schema,
        values={"value": 3},
    )

    assert repeated is first
    assert otherWorkflow is not first
    assert manager.count() == 2
    first.simulateApply()
    assert applied == [
        (EditorKey("project-a", "main", "node-1"), {"value": 2})
    ]

    manager.closeWorkflow("project-a", "main")
    assert manager.keys() == (EditorKey("project-a", "secondary", "node-1"),)
    manager.closeAll()
    assert manager.count() == 0


def testEditorManagerFallsBackWhenRuntimeHasNoEditorRpc(tmp_path) -> None:
    logs: list[tuple[str, str]] = []
    manager = OperatorEditorManager(
        runtimeClient=_Runtime(),
        settingsStore=_Settings(),
        applyParams=lambda _key, _params: True,
        appendLog=lambda level, message: logs.append((level, message)),
        cacheRoot=tmp_path,
    )
    window = manager.open(
        projectId="project-a",
        workflowId="main",
        nodeId="roi",
        operatorId="vision.preprocess.roi",
        displayName="ROI",
        schema={"type": "object", "properties": {}},
        values={},
        operatorDefinition={
            "version": "1.1.0",
            "editorSpec": {
                "kind": "customUi",
                "controllerEntry": "emo_master.plugins.builtins.roi.editor:RoiEditorController",
                "previewMode": "pure",
            },
        },
    )

    assert window is not None
    assert manager.count() == 1
    assert any("回退通用表单" in message for _level, message in logs)
    manager.closeAll()
